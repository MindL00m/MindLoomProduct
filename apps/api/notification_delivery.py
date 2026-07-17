"""Outbound expert-request delivery through Gmail, Outlook, and Teams."""

from __future__ import annotations

import base64
import html
from email.message import EmailMessage
from typing import Any, Awaitable, Callable
from uuid import uuid4

import httpx
from sqlalchemy import text

from database import get_session_factory
from google_workspace import _workspace_access_token
from microsoft_teams import _teams_token
from provider_http import request_with_backoff


async def _connection_user(org_id: str, provider: str) -> str | None:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(text("""
                SELECT user_id FROM app_connections
                WHERE org_id=:org AND provider=:provider
                ORDER BY updated_at DESC LIMIT 1
            """), {"org": org_id, "provider": provider})
        ).scalar_one_or_none()


async def _record(
    *,
    org_id: str,
    review_id: str,
    channel: str,
    status: str,
    provider_message_id: str | None = None,
    error: str | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await session.execute(text("""
                INSERT INTO notification_deliveries
                  (delivery_id, org_id, review_id, channel, status,
                   provider_message_id, error, attempted_at, delivered_at)
                VALUES
                  (:id,:org,:review,:channel,:status,:message,:error,now(),
                   CASE WHEN :status='delivered' THEN now() ELSE NULL END)
                ON CONFLICT (review_id, channel) DO UPDATE SET
                  status=EXCLUDED.status,
                  provider_message_id=EXCLUDED.provider_message_id,
                  error=EXCLUDED.error, attempted_at=now(),
                  delivered_at=EXCLUDED.delivered_at
            """), {
                "id": str(uuid4()), "org": org_id, "review": review_id,
                "channel": channel, "status": status,
                "message": provider_message_id, "error": error,
            })


def _notification_text(question: str) -> str:
    return (
        "Company Brain could not answer a question and suggested you as the expert.\n\n"
        f"Question: {question}\n\n"
        "Open Company Brain and select Expert inbox to answer it. "
        "Your answer becomes searchable immediately."
    )


async def _send_gmail(org_id: str, recipient: str, question: str) -> str:
    user_id = await _connection_user(org_id, "google_workspace")
    if not user_id:
        raise RuntimeError("Google Workspace is not connected.")
    token, sender = await _workspace_access_token(org_id, user_id)
    if token.startswith("dev:"):
        raise RuntimeError("Gmail delivery is unavailable in development connection mode.")
    message = EmailMessage()
    message["To"] = recipient
    message["From"] = sender
    message["Subject"] = "Company Brain needs your expertise"
    message.set_content(_notification_text(question))
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await request_with_backoff(
            client, "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"raw": raw},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Gmail send failed ({response.status_code}).")
    return str(response.json().get("id") or "")


async def _send_outlook(org_id: str, recipient: str, question: str) -> str:
    user_id = await _connection_user(org_id, "microsoft_teams")
    if not user_id:
        raise RuntimeError("Microsoft 365 is not connected.")
    token, _ = await _teams_token(org_id, user_id)
    if token.startswith("dev:"):
        raise RuntimeError("Outlook delivery is unavailable in development connection mode.")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await request_with_backoff(
            client, "POST", "https://graph.microsoft.com/v1.0/me/sendMail",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": {
                    "subject": "Company Brain needs your expertise",
                    "body": {"contentType": "Text", "content": _notification_text(question)},
                    "toRecipients": [{"emailAddress": {"address": recipient}}],
                },
                "saveToSentItems": True,
            },
        )
    if response.status_code != 202:
        raise RuntimeError(f"Outlook send failed ({response.status_code}).")
    return "accepted"


async def _send_teams(org_id: str, recipient: str, question: str) -> str:
    user_id = await _connection_user(org_id, "microsoft_teams")
    if not user_id:
        raise RuntimeError("Microsoft 365 is not connected.")
    token, _ = await _teams_token(org_id, user_id)
    if token.startswith("dev:"):
        raise RuntimeError("Teams delivery is unavailable in development connection mode.")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        me = await request_with_backoff(
            client, "GET", "https://graph.microsoft.com/v1.0/me",
            headers=headers, params={"$select": "id"},
        )
        target = await request_with_backoff(
            client, "GET",
            f"https://graph.microsoft.com/v1.0/users/{recipient}",
            headers=headers, params={"$select": "id"},
        )
        if me.status_code >= 400 or target.status_code >= 400:
            raise RuntimeError("Could not resolve Microsoft users for Teams delivery.")
        sender_id = str(me.json()["id"])
        target_id = str(target.json()["id"])
        chat = await request_with_backoff(
            client, "POST", "https://graph.microsoft.com/v1.0/chats",
            headers=headers,
            json={
                "chatType": "oneOnOne",
                "members": [
                    {
                        "@odata.type": "#microsoft.graph.aadUserConversationMember",
                        "roles": ["owner"],
                        "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{sender_id}')",
                    },
                    {
                        "@odata.type": "#microsoft.graph.aadUserConversationMember",
                        "roles": ["owner"],
                        "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{target_id}')",
                    },
                ],
            },
        )
        if chat.status_code not in {200, 201}:
            raise RuntimeError(f"Teams chat creation failed ({chat.status_code}).")
        chat_id = str(chat.json().get("id") or "")
        content = html.escape(_notification_text(question)).replace("\n", "<br>")
        sent = await request_with_backoff(
            client, "POST",
            f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
            headers=headers,
            json={"body": {"contentType": "html", "content": content}},
        )
        if sent.status_code != 201:
            raise RuntimeError(f"Teams message send failed ({sent.status_code}).")
        return str(sent.json().get("id") or "")


async def deliver_expert_request(
    *, org_id: str, review_id: str, recipient: str, question: str
) -> dict[str, str]:
    """Attempt every configured channel and persist each independent result."""

    channels: list[tuple[str, Callable[[str, str, str], Awaitable[str]]]] = [
        ("gmail", _send_gmail),
        ("outlook", _send_outlook),
        ("teams", _send_teams),
    ]
    outcomes: dict[str, str] = {}
    for channel, sender in channels:
        try:
            message_id = await sender(org_id, recipient, question)
            outcomes[channel] = "delivered"
            await _record(
                org_id=org_id, review_id=review_id, channel=channel,
                status="delivered", provider_message_id=message_id,
            )
        except Exception as exc:  # one provider must not suppress another
            outcomes[channel] = "failed"
            await _record(
                org_id=org_id, review_id=review_id, channel=channel,
                status="failed", error=str(exc)[:1000],
            )
    return outcomes
