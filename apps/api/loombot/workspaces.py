"""Org group-chat workspaces, including the default everyone room and @Loombot.

@Loombot in a workspace runs the same agent as Ask (files, email, meetings,
status tracking, web search, ...) as the member who mentioned it. The reply's
full response is stored with the message so every member sees the answer and
its sources, while only the member who asked can act on its cards.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from core.database import get_session_factory

logger = logging.getLogger(__name__)

LOOMBOT_NAME = "Loombot"
LOOMBOT_MODE_ORG = "org_knowledge"
LOOMBOT_MODE_CONTEXT = "context_only"
_LOOMBOT_MENTION = re.compile(
    r"(?:^|[\s([{])@loombot\b",
    re.IGNORECASE,
)
_LOOMBOT_STRIP = re.compile(r"@loombot\b", re.IGNORECASE)

_MESSAGE_COLUMNS = """message_id, sender_user_id, sender_type, sender_name,
                       body, created_at, payload, requested_by, card_state"""
_HISTORY_TURNS = 12
MAX_CARD_STATE_BYTES = 20_000


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
                  (workspace_id, org_id, name, kind, created_by,
                   loombot_mode, created_at, updated_at)
                VALUES
                  (:id, :org, 'Everyone', 'org_wide', :user,
                   'org_knowledge', now(), now())
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
                SELECT w.workspace_id, w.name, w.kind, w.created_by,
                       w.purpose, w.context_md, w.context_synced_at, w.loombot_mode,
                       w.created_at, w.updated_at
                FROM workspaces w
                JOIN workspace_members m ON m.workspace_id=w.workspace_id
                WHERE w.org_id=:org AND w.workspace_id=:id AND m.user_id=:user
            """), {"org": org_id, "id": workspace_id, "user": user_id})
        ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace was not found.")
    return dict(row)


def _serialize_workspace_row(row: dict, *, member_count: int | None = None) -> dict:
    out = {
        "workspace_id": row["workspace_id"],
        "name": row["name"],
        "kind": row["kind"],
        "created_by": row.get("created_by"),
        "purpose": row.get("purpose"),
        "context_md": row.get("context_md"),
        "context_synced_at": row.get("context_synced_at"),
        "loombot_mode": row.get("loombot_mode") or LOOMBOT_MODE_ORG,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    if member_count is not None:
        out["member_count"] = member_count
    return out


async def list_workspaces(org_id: str, user_id: str) -> list[dict]:
    await ensure_org_workspace(org_id, user_id)
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT w.workspace_id, w.name, w.kind, w.created_by,
                       w.purpose, w.context_md, w.context_synced_at, w.loombot_mode,
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
    purpose: str | None = None,
    context_md: str | None = None,
    loombot_mode: str | None = None,
) -> dict:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Workspace name is required.")
    mode = (loombot_mode or LOOMBOT_MODE_ORG).strip()
    if mode not in {LOOMBOT_MODE_ORG, LOOMBOT_MODE_CONTEXT}:
        raise HTTPException(status_code=400, detail="Invalid loombot_mode.")
    purpose_clean = " ".join((purpose or "").split()) or None
    context_clean = (context_md or "").strip() or None
    if mode == LOOMBOT_MODE_CONTEXT and not purpose_clean:
        purpose_clean = cleaned

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
        synced_at = (
            datetime.now(timezone.utc) if context_clean is not None else None
        )
        await session.execute(text("""
            INSERT INTO workspaces
              (workspace_id, org_id, name, kind, created_by,
               purpose, context_md, context_synced_at, loombot_mode,
               created_at, updated_at)
            VALUES
              (:id, :org, :name, 'group', :user,
               :purpose, :context_md, :synced_at,
               :mode, now(), now())
        """), {
            "id": workspace_id,
            "org": org_id,
            "name": cleaned,
            "user": user_id,
            "purpose": purpose_clean,
            "context_md": context_clean,
            "synced_at": synced_at,
            "mode": mode,
        })
        for member_id in members:
            await session.execute(text("""
                INSERT INTO workspace_members (workspace_id, user_id, joined_at)
                VALUES (:workspace, :member, now())
                ON CONFLICT DO NOTHING
            """), {"workspace": workspace_id, "member": member_id})
        row = (
            await session.execute(text("""
                SELECT workspace_id, name, kind, created_by,
                       purpose, context_md, context_synced_at, loombot_mode,
                       created_at, updated_at
                FROM workspaces WHERE workspace_id=:id
            """), {"id": workspace_id})
        ).mappings().one()
        await session.commit()
    return _serialize_workspace_row(dict(row), member_count=len(members))


async def resync_workspace_context(
    *,
    org_id: str,
    user_id: str,
    workspace_id: str,
) -> dict:
    """Re-scrape the knowledge graph and rewrite CONTEXT.md."""

    workspace = await _require_member(org_id, user_id, workspace_id)
    mode = workspace.get("loombot_mode") or LOOMBOT_MODE_ORG
    if mode != LOOMBOT_MODE_CONTEXT:
        raise HTTPException(
            status_code=400,
            detail="Only context-scoped workspaces can resync CONTEXT.md.",
        )
    purpose = (workspace.get("purpose") or workspace.get("name") or "").strip()
    if not purpose:
        raise HTTPException(
            status_code=400,
            detail="Workspace has no purpose to resync from.",
        )

    members = await list_workspace_members(org_id, user_id, workspace_id)
    member_labels = [
        f"{m['name']} <{m['email']}>" for m in members
    ]

    from loombot.workspace_context import generate_workspace_context

    context_md = await generate_workspace_context(
        org_id=org_id,
        user_id=user_id,
        purpose=purpose,
        member_labels=member_labels,
    )

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("""
            UPDATE workspaces
            SET context_md=:context_md,
                context_synced_at=now(),
                updated_at=now()
            WHERE org_id=:org AND workspace_id=:id
        """), {
            "context_md": context_md,
            "org": org_id,
            "id": workspace_id,
        })
        row = (
            await session.execute(text("""
                SELECT workspace_id, name, kind, created_by,
                       purpose, context_md, context_synced_at, loombot_mode,
                       created_at, updated_at
                FROM workspaces WHERE workspace_id=:id
            """), {"id": workspace_id})
        ).mappings().one()
        await session.commit()

    result = _serialize_workspace_row(dict(row), member_count=len(members))
    result["status"] = "synced"
    return result


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
            await session.execute(text(f"""
                SELECT {_MESSAGE_COLUMNS}
                FROM workspace_messages
                WHERE org_id=:org AND workspace_id=:workspace
                ORDER BY created_at ASC
            """), {"org": org_id, "workspace": workspace_id})
        ).mappings().all()
    return [_serialize_message(row, viewer_id=user_id) for row in rows]


# What members other than the asker may see of a Loombot reply: the answer text
# (already posted to the room) and public web results. Sources, drafts, and file
# details can come from the asker's private mail, files, and access.
_SHARED_PAYLOAD_KEYS = (
    "answer", "confidence", "routed", "web_search_used", "web_search_query", "web_results",
)


def card_summaries(payload: dict[str, Any]) -> list[str]:
    """One phrase per card in a reply, without the card's contents."""

    out: list[str] = []
    if payload.get("proposed_email") or payload.get("proposed_message"):
        out.append("a message draft")
    meeting = payload.get("proposed_meeting")
    if meeting:
        out.append(f"a meeting draft (\u201c{meeting.get('title') or 'Meeting'}\u201d)")
    if payload.get("proposed_recurring_email"):
        out.append("a recurring email")
    if payload.get("proposed_workspace"):
        out.append("a workspace draft")
    if payload.get("proposed_pull_request"):
        out.append("a pull request draft")
    changes = payload.get("file_changes") or []
    if changes:
        out.append(
            f"{len(changes)} file{'s' if len(changes) != 1 else ''} added or updated in "
            "this workspace's Files"
        )
    if payload.get("status_tracker"):
        out.append("a status tracker on their Home page")
    return out


_OUTCOMES = (
    ("emailProposalState", "sent", "email sent"),
    ("proposalState", "sent", "Expert Message sent"),
    ("meetingState", "scheduled", "meeting scheduled"),
    ("recurringState", "scheduled", "recurring email scheduled"),
    ("workspaceProposalState", "sent", "workspace created"),
    ("prProposalState", "sent", "pull request opened"),
)


def card_outcomes(card_state: dict[str, Any]) -> list[str]:
    """What the asker did on the cards, without any of the cards' contents."""

    return [label for key, value, label in _OUTCOMES if card_state.get(key) == value]


def _shared_payload(payload: dict[str, Any], card_state: dict[str, Any]) -> dict[str, Any]:
    shared = {key: payload[key] for key in _SHARED_PAYLOAD_KEYS if key in payload}
    shared["sources"] = []
    shared["card_summaries"] = card_summaries(payload)
    shared["card_outcomes"] = card_outcomes(card_state)
    shared["shared_view"] = True
    return shared


def _load_json(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _serialize_message(row: Any, *, viewer_id: str | None = None) -> dict:
    """A message for ``viewer_id``; Loombot payloads are redacted for everyone but the asker."""

    out = dict(row)
    payload = _load_json(out.get("payload"))
    card_state = _load_json(out.get("card_state")) or {}
    if payload is not None and viewer_id is not None and viewer_id != out.get("requested_by"):
        payload = _shared_payload(payload, card_state)
        card_state = {}
    out["payload"] = payload
    out["card_state"] = card_state
    return out


async def _insert_message(
    *,
    org_id: str,
    workspace_id: str,
    sender_user_id: str | None,
    sender_type: str,
    sender_name: str,
    body: str,
    payload: dict | None = None,
    requested_by: str | None = None,
) -> dict:
    message_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("""
            INSERT INTO workspace_messages
              (message_id, workspace_id, org_id, sender_user_id, sender_type,
               sender_name, body, payload, requested_by, created_at)
            VALUES
              (:id, :workspace, :org, :sender, :type, :name, :body, :payload,
               :requested_by, clock_timestamp())
        """), {
            "id": message_id,
            "workspace": workspace_id,
            "org": org_id,
            "sender": sender_user_id,
            "type": sender_type,
            "name": sender_name,
            "body": body,
            "payload": json.dumps(payload, default=str) if payload is not None else None,
            "requested_by": requested_by,
        })
        await session.execute(text("""
            UPDATE workspaces SET updated_at=now()
            WHERE workspace_id=:workspace AND org_id=:org
        """), {"workspace": workspace_id, "org": org_id})
        created = (
            await session.execute(text(f"""
                SELECT {_MESSAGE_COLUMNS}
                FROM workspace_messages WHERE message_id=:id
            """), {"id": message_id})
        ).mappings().one()
        await session.commit()
    return _serialize_message(created)


def extract_loombot_question(body: str) -> str | None:
    """Return the question text when the message @mentions Loombot, else None."""

    if not _LOOMBOT_MENTION.search(f" {body}"):
        return None
    question = _LOOMBOT_STRIP.sub(" ", body)
    question = re.sub(r"\s+", " ", question)
    question = re.sub(r"\s+,", ",", question)
    question = question.strip(" \t\n\r:,-")
    return question or "What should the team know right now?"


async def _recent_history(
    org_id: str, workspace_id: str, before_message_id: str, asker_id: str
) -> tuple[list, list[str]]:
    """Chat turns for Loombot, and what other members said, before ``before_message_id``.

    Only the asker's own messages and Loombot's replies to them become chat turns.
    Other members' messages are returned separately as context, so one member
    cannot steer tools that run with another member's mailbox and files.
    """

    from core.models import ChatMessage

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT sender_type, sender_name, body, sender_user_id, requested_by
                FROM workspace_messages
                WHERE org_id=:org AND workspace_id=:workspace AND message_id<>:current
                  AND created_at <= (
                      SELECT created_at FROM workspace_messages WHERE message_id=:current
                  )
                  AND sender_type IN ('user', 'bot')
                ORDER BY created_at DESC
                LIMIT :limit
            """), {
                "org": org_id, "workspace": workspace_id, "current": before_message_id,
                "limit": _HISTORY_TURNS,
            })
        ).mappings().all()
    return split_history([dict(row) for row in reversed(rows)], asker_id)


def split_history(rows: list[dict], asker_id: str) -> tuple[list, list[str]]:
    """(the asker's conversation with Loombot, other members' messages as context)."""

    from core.models import ChatMessage

    turns = []
    others: list[str] = []
    for row in rows:
        if row["sender_type"] == "bot":
            if row.get("requested_by") == asker_id:
                turns.append(ChatMessage(role="assistant", content=row["body"]))
        elif row.get("sender_user_id") == asker_id:
            turns.append(ChatMessage(role="user", content=row["body"]))
        else:
            others.append(f"{row['sender_name']}: {row['body']}")
    return turns, others


async def _loombot_reply(
    *,
    org_id: str,
    user_id: str,
    question: str,
    workspace: dict | None = None,
    requester_name: str = "",
    message_id: str | None = None,
    user_timezone: str | None = None,
    attached_file_names: list[str] | None = None,
) -> tuple[str, dict | None]:
    """Loombot's reply text and Ask's full response (cards, sources) for the message."""

    from core.models import EphemeralDocument, RetrievalResult
    from loombot.ask_agent import WorkspaceContext, request_expert_if_routed, run_ask_agent

    workspace = workspace or {}
    workspace_id = str(workspace.get("workspace_id") or "")
    mode = workspace.get("loombot_mode") or LOOMBOT_MODE_ORG
    context_md = (workspace.get("context_md") or "").strip()

    if mode == LOOMBOT_MODE_CONTEXT and not context_md:
        return (
            "This workspace is scoped to CONTEXT.md, but that file is empty. "
            "Use Resync in the workspace header to rebuild it from company knowledge."
        ), None

    try:
        history, others_said = (
            await _recent_history(org_id, workspace_id, message_id, user_id)
            if workspace_id and message_id else ([], [])
        )
        members = (
            await list_workspace_members(org_id, user_id, workspace_id) if workspace_id else []
        )
        shared_files: list[dict] = []
        if workspace_id:
            from loombot.workspace_files import list_workspace_files

            try:
                shared_files = [
                    {"name": f["name"], "kind": f["kind"], "owner_name": f.get("owner_name")}
                    for f in await list_workspace_files(org_id, user_id, workspace_id)
                ]
            except Exception:  # noqa: BLE001 - Loombot still answers without the list
                logger.warning("Could not list workspace files", exc_info=True)
        context = WorkspaceContext(
            workspace_id=workspace_id,
            name=str(workspace.get("name") or "Workspace"),
            kind=str(workspace.get("kind") or "group"),
            mode=mode,
            members=[
                {"user_id": m["user_id"], "name": m["name"], "email": m["email"]} for m in members
            ],
            requester_name=requester_name,
            others_said=others_said,
            shared_files=shared_files,
        )
        if mode == LOOMBOT_MODE_CONTEXT:
            # Knowledge answers come only from CONTEXT.md; tools still work.
            retrieval = RetrievalResult(chunks=[], experts=[], entities_found=[])
            ephemeral = [EphemeralDocument(
                document_id=f"context-{workspace_id}", filename="CONTEXT.md", text=context_md
            )]
        else:
            from brain.retrieval import retrieve
            from keeper.auth import get_user_access_tokens

            access_tokens = await get_user_access_tokens(org_id, user_id)
            retrieval = await retrieve(question, history, org_id, access_tokens)
            ephemeral = []
        response = await run_ask_agent(
            question=question,
            retrieval=retrieval,
            history=history,
            org_id=org_id,
            user_id=user_id,
            ephemeral_documents=ephemeral,
            attached_file_names=attached_file_names,
            user_timezone=user_timezone,
            workspace=context,
        )
        await request_expert_if_routed(
            response, org_id=org_id, user_id=user_id, question=question
        )
        answer = (response.answer or "").strip()
        if answer:
            return answer, response.model_dump(mode="json")
    except Exception:
        logger.exception("Loombot failed for org=%s user=%s", org_id, user_id)
    if mode == LOOMBOT_MODE_CONTEXT:
        return (
            "I couldn't answer from this workspace's CONTEXT.md. "
            "Try Resync, or rephrase the question."
        ), None
    return (
        "I couldn't find a confident answer in company knowledge. "
        "Try Ask for a deeper search, or rephrase the question."
    ), None


async def send_workspace_message(
    *,
    org_id: str,
    user_id: str,
    workspace_id: str,
    body: str,
    user_timezone: str | None = None,
    attached_file_names: list[str] | None = None,
) -> dict:
    cleaned = body.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    await ensure_org_workspace(org_id, user_id)
    workspace = await _require_member(org_id, user_id, workspace_id)

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
        answer, payload = await _loombot_reply(
            org_id=org_id,
            user_id=user_id,
            question=question,
            workspace=workspace,
            requester_name=str(sender["name"]),
            message_id=user_message["message_id"],
            user_timezone=user_timezone,
            attached_file_names=attached_file_names,
        )
        await _share_turn_files(
            org_id, user_id, workspace_id, attached_file_names or [], payload
        )
        bot_message = await _insert_message(
            org_id=org_id,
            workspace_id=workspace_id,
            sender_user_id=None,
            sender_type="bot",
            sender_name=LOOMBOT_NAME,
            body=answer,
            payload=payload,
            requested_by=user_id,
        )
    elif attached_file_names:
        await _share_turn_files(org_id, user_id, workspace_id, attached_file_names, None)

    return {"message": user_message, "bot_message": bot_message}


async def _share_turn_files(
    org_id: str,
    user_id: str,
    workspace_id: str,
    attached_file_names: list[str],
    payload: dict | None,
) -> None:
    """Files attached to the message, and files Loombot created or updated for it,
    go into the workspace's Files so every member can open and edit them."""

    from loombot import files as files_service
    from loombot.workspace_files import link_files_quietly

    file_ids: list[str] = []
    for name in attached_file_names:
        try:
            found = await files_service.find_file_by_name(org_id, user_id, name)
        except Exception:  # noqa: BLE001
            found = None
        if found:
            file_ids.append(found["file_id"])
    for change in (payload or {}).get("file_changes") or []:
        if change.get("action") in {"created", "updated"} and change.get("file_id"):
            file_ids.append(str(change["file_id"]))
    await link_files_quietly(org_id, user_id, workspace_id, list(dict.fromkeys(file_ids)))


async def update_card_state(
    *,
    org_id: str,
    user_id: str,
    workspace_id: str,
    message_id: str,
    state: dict,
) -> dict:
    """Record what the member who asked did on a Loombot reply's cards."""

    await _require_member(org_id, user_id, workspace_id)
    encoded = json.dumps(state or {}, default=str)
    if len(encoded.encode("utf-8")) > MAX_CARD_STATE_BYTES:
        raise HTTPException(status_code=413, detail="Card state is too large.")
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(text(f"""
                UPDATE workspace_messages SET card_state=:state
                WHERE org_id=:org AND workspace_id=:workspace AND message_id=:id
                  AND sender_type='bot' AND requested_by=:user
                RETURNING {_MESSAGE_COLUMNS}
            """), {
                "state": encoded, "org": org_id, "workspace": workspace_id,
                "id": message_id, "user": user_id,
            })
        ).mappings().one_or_none()
        await session.commit()
    if row is None:
        raise HTTPException(
            status_code=403, detail="Only the person who asked Loombot can act on this reply."
        )
    return _serialize_message(row)


async def add_members(
    *,
    org_id: str,
    user_id: str,
    workspace_id: str,
    member_user_ids: list[str],
) -> dict:
    """Add signed-in org members to a workspace and post a note in the chat."""

    workspace = await _require_member(org_id, user_id, workspace_id)
    if workspace["kind"] == "org_wide":
        raise HTTPException(
            status_code=400, detail="Everyone in the organization is already in this workspace."
        )
    wanted = list(dict.fromkeys(str(m).strip() for m in member_user_ids if str(m).strip()))
    if not wanted:
        raise HTTPException(status_code=400, detail="Choose at least one person to add.")
    factory = get_session_factory()
    async with factory() as session:
        people = (
            await session.execute(text("""
                SELECT user_id, coalesce(name, email) AS name, email FROM users
                WHERE org_id=:org AND user_id = ANY(:ids)
            """), {"org": org_id, "ids": wanted})
        ).mappings().all()
        found = {str(row["user_id"]): dict(row) for row in people}
        if set(wanted) - set(found):
            raise HTTPException(
                status_code=400,
                detail="One or more people were not found in this organization.",
            )
        existing = set((
            await session.execute(text("""
                SELECT user_id FROM workspace_members
                WHERE workspace_id=:workspace AND user_id = ANY(:ids)
            """), {"workspace": workspace_id, "ids": wanted})
        ).scalars().all())
        added = [found[uid] for uid in wanted if uid not in existing]
        for person in added:
            await session.execute(text("""
                INSERT INTO workspace_members (workspace_id, user_id, joined_at)
                VALUES (:workspace, :member, now())
                ON CONFLICT DO NOTHING
            """), {"workspace": workspace_id, "member": person["user_id"]})
        adder = (
            await session.execute(text("""
                SELECT coalesce(name, email) FROM users WHERE org_id=:org AND user_id=:user
            """), {"org": org_id, "user": user_id})
        ).scalar_one_or_none() or "Someone"
        await session.commit()
    if added:
        names = [person["name"] for person in added]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
        await _insert_message(
            org_id=org_id,
            workspace_id=workspace_id,
            sender_user_id=None,
            sender_type="system",
            sender_name="MindLoom",
            body=f"{adder} added {listed} to the workspace.",
        )
    return {
        "added": [{"user_id": p["user_id"], "name": p["name"], "email": p["email"]} for p in added],
        "already_members": [found[uid]["name"] for uid in wanted if uid in existing],
        "members": await list_workspace_members(org_id, user_id, workspace_id),
    }


async def add_members_by_query(
    *,
    org_id: str,
    user_id: str,
    workspace_id: str,
    queries: list[str],
) -> dict:
    """Resolve names, emails, or titles to signed-in users and add them (for Loombot)."""

    from loombot.workspace_context import resolve_members_from_queries

    matched, unmatched = await resolve_members_from_queries(
        org_id, queries, exclude_user_id=user_id
    )
    result: dict[str, Any] = {
        "added": [], "already_members": [], "invited": [], "unmatched": unmatched,
    }
    if matched:
        added = await add_members(
            org_id=org_id, user_id=user_id, workspace_id=workspace_id,
            member_user_ids=[m["user_id"] for m in matched],
        )
        result["added"] = added["added"]
        result["already_members"] = added["already_members"]
    # Directory people (or raw addresses) who have not signed in yet get an invite.
    invite_emails = [str(u["email"]) for u in unmatched if u.get("email")]
    invite_emails += [
        q.strip() for q in queries
        if _EMAIL_ADDRESS.match(q.strip().lower())
        and q.strip().lower() not in {str(u.get("email") or "").lower() for u in unmatched}
        and q.strip().lower() not in {str(m.get("email") or "").lower() for m in matched}
    ]
    if invite_emails:
        invited = await invite_members_by_email(
            org_id=org_id, user_id=user_id, workspace_id=workspace_id, emails=invite_emails,
        )
        done = set(invited["invited"]) | set(invited["already_invited"])
        result["invited"] = invited["invited"]
        result["unmatched"] = [
            u for u in unmatched if str(u.get("email") or "").lower() not in done
        ] + [
            {"name": e, "email": e, "reason": "Outside the organization's email domain."}
            for e in invited["outside_organization"]
        ]
    return result


# --- Members by email, invites and removal ------------------------------------------

_EMAIL_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MAX_INVITES_PER_REQUEST = 50


def split_invite_emails(
    emails: list[str], org_domain: str
) -> tuple[list[str], list[str], list[str]]:
    """Split raw addresses into (in the org's domain, other domains, not an address).

    Membership is domain-scoped: only addresses on the organization's domain can
    sign in and join, so invites to other domains are reported back instead.
    """

    domain = (org_domain or "").strip().lower()
    inside: list[str] = []
    outside: list[str] = []
    invalid: list[str] = []
    for raw in emails:
        for part in re.split(r"[,;\s]+", str(raw or "")):
            address = part.strip().strip("<>").lower()
            if not address:
                continue
            if not _EMAIL_ADDRESS.match(address):
                invalid.append(part.strip())
            elif address.rsplit("@", 1)[1] == domain:
                inside.append(address)
            else:
                outside.append(address)
    dedupe = lambda items: list(dict.fromkeys(items))  # noqa: E731
    return dedupe(inside), dedupe(outside), dedupe(invalid)


def _invite_email_body(inviter: str, workspace: str, link: str) -> str:
    return (
        f"{inviter} invited you to the \"{workspace}\" workspace in MindLoom.\n\n"
        f"Sign in with your work Google account to join:\n{link}\n\n"
        "You will be added to the workspace automatically when you sign in."
    )


async def invite_members_by_email(
    *,
    org_id: str,
    user_id: str,
    workspace_id: str,
    emails: list[str],
    send_email: bool = True,
) -> dict:
    """Add people by email address.

    Signed-in colleagues are added straight away. Colleagues on the org's domain who
    have not signed in yet get a pending invite (and an invite email from the
    inviter's Gmail when it is connected); they join on first sign-in.
    """

    workspace = await _require_member(org_id, user_id, workspace_id)
    if workspace["kind"] == "org_wide":
        raise HTTPException(
            status_code=400, detail="Everyone in the organization is already in this workspace."
        )
    factory = get_session_factory()
    async with factory() as session:
        org_domain = (
            await session.execute(text(
                "SELECT domain FROM organizations WHERE org_id=:org"
            ), {"org": org_id})
        ).scalar_one_or_none() or ""
    inside, outside, invalid = split_invite_emails(emails, org_domain)
    if not inside and not outside and not invalid:
        raise HTTPException(status_code=400, detail="Enter at least one email address.")
    if len(inside) > MAX_INVITES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Invite at most {MAX_INVITES_PER_REQUEST} people at a time.",
        )

    async with factory() as session:
        users = (
            await session.execute(text("""
                SELECT user_id, lower(email) AS email FROM users
                WHERE org_id=:org AND lower(email) = ANY(:emails)
            """), {"org": org_id, "emails": inside})
        ).mappings().all()
    by_email = {row["email"]: str(row["user_id"]) for row in users}
    signed_in_ids = [by_email[e] for e in inside if e in by_email]
    to_invite = [e for e in inside if e not in by_email]

    result: dict[str, Any] = {
        "added": [],
        "already_members": [],
        "invited": [],
        "already_invited": [],
        "outside_organization": outside,
        "invalid": invalid,
        "invite_emails_sent": 0,
    }
    if signed_in_ids:
        added = await add_members(
            org_id=org_id, user_id=user_id, workspace_id=workspace_id,
            member_user_ids=signed_in_ids,
        )
        result["added"] = added["added"]
        result["already_members"] = added["already_members"]

    invited: list[str] = []
    async with factory() as session:
        for email in to_invite:
            inserted = (
                await session.execute(text("""
                    INSERT INTO workspace_invites
                      (invite_id, workspace_id, org_id, email, invited_by, created_at)
                    VALUES (:id, :workspace, :org, :email, :user, now())
                    ON CONFLICT (workspace_id, email) DO NOTHING
                    RETURNING invite_id
                """), {
                    "id": str(uuid4()), "workspace": workspace_id, "org": org_id,
                    "email": email, "user": user_id,
                })
            ).scalar_one_or_none()
            if inserted:
                invited.append(email)
            else:
                result["already_invited"].append(email)
        inviter = (
            await session.execute(text("""
                SELECT coalesce(name, email) FROM users WHERE org_id=:org AND user_id=:user
            """), {"org": org_id, "user": user_id})
        ).scalar_one_or_none() or "A colleague"
        await session.commit()
    result["invited"] = invited

    if invited:
        listed = invited[0] if len(invited) == 1 else ", ".join(invited[:-1]) + f" and {invited[-1]}"
        await _insert_message(
            org_id=org_id,
            workspace_id=workspace_id,
            sender_user_id=None,
            sender_type="system",
            sender_name="MindLoom",
            body=f"{inviter} invited {listed}. They join when they first sign in to MindLoom.",
        )
        if send_email:
            result["invite_emails_sent"] = await _send_invite_emails(
                org_id=org_id, user_id=user_id, inviter=inviter,
                workspace_name=workspace["name"], emails=invited,
            )
    result["members"] = await list_workspace_members(org_id, user_id, workspace_id)
    result["invites"] = await list_workspace_invites(org_id, user_id, workspace_id)
    return result


async def _send_invite_emails(
    *, org_id: str, user_id: str, inviter: str, workspace_name: str, emails: list[str]
) -> int:
    """Email each invitee separately from the inviter's Gmail. Best effort."""

    from core.config import get_settings
    from shuttle.notification_delivery import send_user_gmail
    from weaver.integrations import has_google_workspace_connection

    try:
        if not await has_google_workspace_connection(org_id, user_id):
            return 0
    except Exception:  # noqa: BLE001 - invites still work without the email
        return 0
    link = f"{get_settings().frontend_url.rstrip('/')}/setup/signin"
    sent = 0
    for email in emails:
        try:
            await send_user_gmail(
                org_id=org_id,
                user_id=user_id,
                recipient=email,
                subject=f"{inviter} invited you to {workspace_name} on MindLoom",
                body=_invite_email_body(inviter, workspace_name, link),
            )
            sent += 1
        except Exception:  # noqa: BLE001
            logger.warning("Workspace invite email to %s failed", email, exc_info=True)
    return sent


async def list_workspace_invites(
    org_id: str, user_id: str, workspace_id: str
) -> list[dict]:
    await _require_member(org_id, user_id, workspace_id)
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT i.invite_id, i.email, i.created_at,
                       coalesce(u.name, u.email) AS invited_by
                FROM workspace_invites i
                LEFT JOIN users u ON u.user_id=i.invited_by
                WHERE i.org_id=:org AND i.workspace_id=:workspace AND i.accepted_at IS NULL
                ORDER BY i.created_at
            """), {"org": org_id, "workspace": workspace_id})
        ).mappings().all()
    return [
        {**dict(row), "created_at": row["created_at"].isoformat() if row["created_at"] else None}
        for row in rows
    ]


async def cancel_workspace_invite(
    *, org_id: str, user_id: str, workspace_id: str, invite_id: str
) -> dict:
    await _require_member(org_id, user_id, workspace_id)
    factory = get_session_factory()
    async with factory() as session:
        deleted = (
            await session.execute(text("""
                DELETE FROM workspace_invites
                WHERE org_id=:org AND workspace_id=:workspace AND invite_id=:invite
                  AND accepted_at IS NULL
                RETURNING invite_id
            """), {"org": org_id, "workspace": workspace_id, "invite": invite_id})
        ).scalar_one_or_none()
        await session.commit()
    if deleted is None:
        raise HTTPException(status_code=404, detail="Invite not found.")
    return {"invites": await list_workspace_invites(org_id, user_id, workspace_id)}


async def remove_member(
    *, org_id: str, user_id: str, workspace_id: str, member_user_id: str
) -> dict:
    """Leave a workspace, or remove someone else (only the workspace's creator can)."""

    workspace = await _require_member(org_id, user_id, workspace_id)
    if workspace["kind"] == "org_wide":
        raise HTTPException(
            status_code=400, detail="No one can be removed from the Everyone workspace."
        )
    leaving = member_user_id == user_id
    if not leaving and str(workspace.get("created_by") or "") != user_id:
        raise HTTPException(
            status_code=403,
            detail="Only the person who created this workspace can remove other members.",
        )
    factory = get_session_factory()
    async with factory() as session:
        removed = (
            await session.execute(text("""
                DELETE FROM workspace_members m
                USING users u
                WHERE m.workspace_id=:workspace AND m.user_id=:member
                  AND u.user_id=m.user_id AND u.org_id=:org
                RETURNING coalesce(u.name, u.email)
            """), {"workspace": workspace_id, "member": member_user_id, "org": org_id})
        ).scalar_one_or_none()
        remover = (
            await session.execute(text("""
                SELECT coalesce(name, email) FROM users WHERE org_id=:org AND user_id=:user
            """), {"org": org_id, "user": user_id})
        ).scalar_one_or_none() or "Someone"
        await session.commit()
    if removed is None:
        raise HTTPException(status_code=404, detail="That person is not in this workspace.")
    await _insert_message(
        org_id=org_id,
        workspace_id=workspace_id,
        sender_user_id=None,
        sender_type="system",
        sender_name="MindLoom",
        body=f"{removed} left the workspace." if leaving else f"{remover} removed {removed}.",
    )
    if leaving:
        return {"left": True, "members": []}
    return {"left": False, "members": await list_workspace_members(org_id, user_id, workspace_id)}


async def accept_workspace_invites(org_id: str, user_id: str, email: str) -> int:
    """Turn pending invites for this email into memberships. Called on sign-in."""

    address = (email or "").strip().lower()
    if not address:
        return 0
    factory = get_session_factory()
    async with factory() as session:
        accepted = (
            await session.execute(text("""
                UPDATE workspace_invites SET accepted_at=now()
                WHERE org_id=:org AND email=:email AND accepted_at IS NULL
                RETURNING workspace_id
            """), {"org": org_id, "email": address})
        ).scalars().all()
        for workspace_id in accepted:
            await session.execute(text("""
                INSERT INTO workspace_members (workspace_id, user_id, joined_at)
                VALUES (:workspace, :user, now())
                ON CONFLICT DO NOTHING
            """), {"workspace": workspace_id, "user": user_id})
        await session.commit()
    return len(accepted)
