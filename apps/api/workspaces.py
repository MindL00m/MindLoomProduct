"""Org group-chat workspaces, including the default everyone room and @Loombot."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from database import get_session_factory

logger = logging.getLogger(__name__)

LOOMBOT_NAME = "Loombot"
LOOMBOT_MODE_ORG = "org_knowledge"
LOOMBOT_MODE_CONTEXT = "context_only"
_LOOMBOT_MENTION = re.compile(
    r"(?:^|[\s([{])@loombot\b",
    re.IGNORECASE,
)
_LOOMBOT_STRIP = re.compile(r"@loombot\b", re.IGNORECASE)

_CONTEXT_ANSWER_SYSTEM = """\
You are Loombot, answering only from the workspace CONTEXT.md below.
Do not use outside knowledge. If the answer is not in CONTEXT.md, say you do
not have that in this workspace context and suggest Resync or Ask.

CONTEXT.md:
{context_md}
"""


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

    from workspace_context import generate_workspace_context

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


async def _loombot_reply_from_context(question: str, context_md: str) -> str:
    from openai import AsyncOpenAI

    from config import get_settings

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": _CONTEXT_ANSWER_SYSTEM.format(context_md=context_md),
            },
            {"role": "user", "content": question},
        ],
    )
    return (response.choices[0].message.content or "").strip()


async def _loombot_reply(
    *,
    org_id: str,
    user_id: str,
    question: str,
    workspace: dict | None = None,
) -> str:
    mode = (workspace or {}).get("loombot_mode") or LOOMBOT_MODE_ORG
    context_md = ((workspace or {}).get("context_md") or "").strip()

    if mode == LOOMBOT_MODE_CONTEXT:
        if not context_md:
            return (
                "This workspace is scoped to CONTEXT.md, but that file is empty. "
                "Use Resync in the workspace header to rebuild it from company knowledge."
            )
        try:
            answer = await _loombot_reply_from_context(question, context_md)
            if answer:
                return answer
        except Exception:
            logger.exception(
                "Context-only Loombot failed for org=%s user=%s", org_id, user_id
            )
        return (
            "I couldn't answer from this workspace's CONTEXT.md. "
            "Try Resync, or rephrase the question."
        )

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
        answer = await _loombot_reply(
            org_id=org_id,
            user_id=user_id,
            question=question,
            workspace=workspace,
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
