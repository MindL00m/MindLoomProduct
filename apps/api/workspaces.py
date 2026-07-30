"""Org group-chat workspaces, including the default everyone room and @Loombot."""

from __future__ import annotations

import logging
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from database import get_session_factory

logger = logging.getLogger(__name__)

LOOMBOT_NAME = "Loombot"
_LOOMBOT_MENTION = re.compile(
    r"(?:^|[\s([{])@loombot\b",
    re.IGNORECASE,
)
_LOOMBOT_STRIP = re.compile(r"@loombot\b", re.IGNORECASE)


async def ensure_org_workspace(org_id: str, user_id: str) -> str:
    """Create the default Everyone workspace if missing and sync membership."""

    factory = get_session_factory()
    async with factory() as session:
        workspace_id = (
            await session.execute(text("""
                SELECT workspace_id FROM workspaces
                WHERE org_id=:org AND kind='org_wide'
                LIMIT 1
            """), {"org": org_id})
        ).scalar_one_or_none()
        if workspace_id is None:
            workspace_id = str(uuid4())
            await session.execute(text("""
                INSERT INTO workspaces
                  (workspace_id, org_id, name, kind, created_by, created_at, updated_at)
                VALUES
                  (:id, :org, 'Everyone', 'org_wide', :user, now(), now())
            """), {"id": workspace_id, "org": org_id, "user": user_id})
        else:
            workspace_id = str(workspace_id)

        # Keep org-wide membership equal to every signed-in user in the org.
        await session.execute(text("""
            INSERT INTO workspace_members (workspace_id, user_id, joined_at)
            SELECT :workspace, u.user_id, now()
            FROM users u
            WHERE u.org_id=:org
            ON CONFLICT (workspace_id, user_id) DO NOTHING
        """), {"workspace": workspace_id, "org": org_id})
        await session.commit()
    return workspace_id


async def _require_member(org_id: str, user_id: str, workspace_id: str) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(text("""
                SELECT w.workspace_id, w.name, w.kind, w.created_by, w.created_at, w.updated_at
                FROM workspaces w
                JOIN workspace_members m ON m.workspace_id=w.workspace_id
                WHERE w.org_id=:org AND w.workspace_id=:id AND m.user_id=:user
            """), {"org": org_id, "id": workspace_id, "user": user_id})
        ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace was not found.")
    return dict(row)


async def list_workspaces(org_id: str, user_id: str) -> list[dict]:
    await ensure_org_workspace(org_id, user_id)
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT w.workspace_id, w.name, w.kind, w.created_by,
                       w.created_at, w.updated_at,
                       coalesce(members.count, 0) AS member_count,
                       last_message.body AS last_message,
                       last_message.created_at AS last_message_at,
                       last_message.sender_name AS last_sender_name
                FROM workspaces w
                JOIN workspace_members me ON me.workspace_id=w.workspace_id AND me.user_id=:user
                LEFT JOIN LATERAL (
                    SELECT count(*) AS count FROM workspace_members
                    WHERE workspace_id=w.workspace_id
                ) members ON true
                LEFT JOIN LATERAL (
                    SELECT body, created_at, sender_name FROM workspace_messages
                    WHERE workspace_id=w.workspace_id
                    ORDER BY created_at DESC LIMIT 1
                ) last_message ON true
                WHERE w.org_id=:org
                ORDER BY
                  CASE WHEN w.kind='org_wide' THEN 0 ELSE 1 END,
                  coalesce(last_message.created_at, w.updated_at) DESC
            """), {"org": org_id, "user": user_id})
        ).mappings().all()
    return [dict(row) for row in rows]


async def create_workspace(
    *,
    org_id: str,
    user_id: str,
    name: str,
    member_user_ids: list[str] | None = None,
) -> dict:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Workspace name is required.")
    members = {user_id, *(member_user_ids or [])}
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT user_id FROM users WHERE org_id=:org
            """), {"org": org_id})
        ).scalars().all()
        org_user_ids = {str(row) for row in rows}
        if user_id not in org_user_ids:
            raise HTTPException(status_code=403, detail="Not allowed.")
        missing = members - org_user_ids
        if missing:
            raise HTTPException(
                status_code=400,
                detail="One or more members were not found in this organization.",
            )
        workspace_id = str(uuid4())
        await session.execute(text("""
            INSERT INTO workspaces
              (workspace_id, org_id, name, kind, created_by, created_at, updated_at)
            VALUES
              (:id, :org, :name, 'group', :user, now(), now())
        """), {"id": workspace_id, "org": org_id, "name": cleaned, "user": user_id})
        for member_id in members:
            await session.execute(text("""
                INSERT INTO workspace_members (workspace_id, user_id, joined_at)
                VALUES (:workspace, :member, now())
                ON CONFLICT DO NOTHING
            """), {"workspace": workspace_id, "member": member_id})
        await session.commit()
    return {
        "workspace_id": workspace_id,
        "name": cleaned,
        "kind": "group",
        "member_count": len(members),
    }


async def list_workspace_members(
    org_id: str, user_id: str, workspace_id: str
) -> list[dict]:
    await _require_member(org_id, user_id, workspace_id)
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT u.user_id, coalesce(u.name, u.email) AS name, u.email, u.role
                FROM workspace_members m
                JOIN users u ON u.user_id=m.user_id
                WHERE m.workspace_id=:workspace
                ORDER BY coalesce(u.name, u.email)
            """), {"workspace": workspace_id})
        ).mappings().all()
    return [dict(row) for row in rows]


async def list_workspace_messages(
    org_id: str, user_id: str, workspace_id: str
) -> list[dict]:
    await ensure_org_workspace(org_id, user_id)
    await _require_member(org_id, user_id, workspace_id)
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT message_id, sender_user_id, sender_type, sender_name,
                       body, created_at
                FROM workspace_messages
                WHERE org_id=:org AND workspace_id=:workspace
                ORDER BY created_at ASC
            """), {"org": org_id, "workspace": workspace_id})
        ).mappings().all()
    return [dict(row) for row in rows]


async def _insert_message(
    *,
    org_id: str,
    workspace_id: str,
    sender_user_id: str | None,
    sender_type: str,
    sender_name: str,
    body: str,
) -> dict:
    message_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("""
            INSERT INTO workspace_messages
              (message_id, workspace_id, org_id, sender_user_id, sender_type,
               sender_name, body, created_at)
            VALUES
              (:id, :workspace, :org, :sender, :type, :name, :body, now())
        """), {
            "id": message_id,
            "workspace": workspace_id,
            "org": org_id,
            "sender": sender_user_id,
            "type": sender_type,
            "name": sender_name,
            "body": body,
        })
        await session.execute(text("""
            UPDATE workspaces SET updated_at=now()
            WHERE workspace_id=:workspace AND org_id=:org
        """), {"workspace": workspace_id, "org": org_id})
        created = (
            await session.execute(text("""
                SELECT message_id, sender_user_id, sender_type, sender_name,
                       body, created_at
                FROM workspace_messages WHERE message_id=:id
            """), {"id": message_id})
        ).mappings().one()
        await session.commit()
    return dict(created)


def extract_loombot_question(body: str) -> str | None:
    """Return the question text when the message @mentions Loombot, else None."""

    if not _LOOMBOT_MENTION.search(f" {body}"):
        return None
    question = _LOOMBOT_STRIP.sub(" ", body)
    question = re.sub(r"\s+", " ", question)
    question = re.sub(r"\s+,", ",", question)
    question = question.strip(" \t\n\r:,-")
    return question or "What should the team know right now?"


async def _loombot_reply(
    *,
    org_id: str,
    user_id: str,
    question: str,
) -> str:
    from auth import get_user_access_tokens
    from answerer import generate_answer
    from retrieval import retrieve

    try:
        access_tokens = await get_user_access_tokens(org_id, user_id)
        retrieval = await retrieve(question, [], org_id, access_tokens)
        response = await generate_answer(question, retrieval, [], org_id)
        answer = (response.answer or "").strip()
        if answer:
            return answer
    except Exception:
        logger.exception("Loombot failed for org=%s user=%s", org_id, user_id)
    return (
        "I couldn't find a confident answer in company knowledge. "
        "Try Ask for a deeper search, or rephrase the question."
    )


async def send_workspace_message(
    *,
    org_id: str,
    user_id: str,
    workspace_id: str,
    body: str,
) -> dict:
    cleaned = body.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    await ensure_org_workspace(org_id, user_id)
    await _require_member(org_id, user_id, workspace_id)

    factory = get_session_factory()
    async with factory() as session:
        sender = (
            await session.execute(text("""
                SELECT coalesce(name, email) AS name FROM users
                WHERE org_id=:org AND user_id=:user
            """), {"org": org_id, "user": user_id})
        ).mappings().one_or_none()
    if sender is None:
        raise HTTPException(status_code=403, detail="Not allowed.")

    user_message = await _insert_message(
        org_id=org_id,
        workspace_id=workspace_id,
        sender_user_id=user_id,
        sender_type="user",
        sender_name=str(sender["name"]),
        body=cleaned,
    )

    bot_message = None
    question = extract_loombot_question(cleaned)
    if question is not None:
        answer = await _loombot_reply(
            org_id=org_id, user_id=user_id, question=question
        )
        bot_message = await _insert_message(
            org_id=org_id,
            workspace_id=workspace_id,
            sender_user_id=None,
            sender_type="bot",
            sender_name=LOOMBOT_NAME,
            body=answer,
        )

    return {"message": user_message, "bot_message": bot_message}
