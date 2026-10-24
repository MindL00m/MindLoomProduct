"""Human review queues for conflicts, verification, and expert knowledge."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from core.database import get_session_factory
from core.models import Chunk, ChunkMetadata, Conversation, IncomingMessage, Participant
from brain.pipeline import DocumentInput, run_ingestion
from brain.source_registry import (
    ingest_external_source,
    mark_external_source_deleted,
)

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_review(
    *,
    org_id: str,
    review_type: str,
    title: str,
    description: str,
    created_by: str | None = None,
    owner_user_id: str | None = None,
    source_ids: list[str] | None = None,
    proposed_content: str | None = None,
    due_at: datetime | None = None,
) -> str:
    review_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await session.execute(text("""
                INSERT INTO knowledge_reviews
                  (review_id, org_id, review_type, status, title, description,
                   created_by, owner_user_id, source_ids_json, proposed_content,
                   due_at, created_at, updated_at)
                VALUES
                  (:id, :org, :type, 'open', :title, :description, :created_by,
                   :owner, :sources, :content, :due, now(), now())
            """), {
                "id": review_id, "org": org_id, "type": review_type,
                "title": title, "description": description, "created_by": created_by,
                "owner": owner_user_id, "sources": json.dumps(source_ids or []),
                "content": proposed_content, "due": due_at,
            })
    return review_id


async def create_expert_request(
    *,
    org_id: str,
    requester_user_id: str,
    question: str,
    expert_name: str,
    expert_email: str | None,
    source_ids: list[str],
) -> str | None:
    """Assign an unanswered question to the matching signed-in directory user."""

    factory = get_session_factory()
    async with factory() as session:
        if expert_email:
            lookup_sql = """
                SELECT user_id, email FROM users
                WHERE org_id=:org
                  AND (
                    lower(email)=lower(:email)
                    OR lower(coalesce(name, ''))=lower(:name)
                  )
                ORDER BY CASE WHEN lower(email)=lower(:email) THEN 0 ELSE 1 END
                LIMIT 1
            """
            lookup_params = {
                "org": org_id,
                "email": expert_email,
                "name": expert_name,
            }
        else:
            lookup_sql = """
                SELECT user_id, email FROM users
                WHERE org_id=:org
                  AND lower(coalesce(name, ''))=lower(:name)
                LIMIT 1
            """
            lookup_params = {"org": org_id, "name": expert_name}
        expert = (
            await session.execute(text(lookup_sql), lookup_params)
        ).mappings().one_or_none()
        if expert is None:
            return None
        expert_user_id = str(expert["user_id"])
        existing_pair = await consolidate_expert_pair(
            org_id=org_id,
            user_a=requester_user_id,
            user_b=expert_user_id,
        )
        if existing_pair:
            await send_expert_message(
                org_id=org_id,
                user_id=requester_user_id,
                review_id=existing_pair,
                body=question,
                message_type="routed_question",
            )
            return existing_pair
        existing = (
            await session.execute(text("""
                SELECT review_id FROM knowledge_reviews
                WHERE org_id=:org AND review_type='expert_request'
                  AND owner_user_id=:owner AND title=:title
                  AND status IN ('open','answered','drafted')
                LIMIT 1
            """), {
                "org": org_id, "owner": expert_user_id,
                "title": f"Expert question: {question}",
            })
        ).scalar_one_or_none()
    if existing:
        return str(existing)
    review_id = await create_review(
        org_id=org_id, review_type="expert_request",
        title=f"Expert question: {question}",
        description=(
            f"Company Brain could not answer this question and suggested {expert_name}. "
            "Reply in Messages. MindLoom will turn the useful answer into a reviewable knowledge draft."
        ),
        created_by=requester_user_id, owner_user_id=expert_user_id,
        source_ids=source_ids, due_at=_now() + timedelta(days=7),
    )
    await send_expert_message(
        org_id=org_id,
        user_id=requester_user_id,
        review_id=review_id,
        body=question,
        message_type="routed_question",
    )
    from core.durable_jobs import enqueue
    try:
        await enqueue(
            "expert_notification",
            org_id=org_id,
            conversation_id=f"expert-notification:{review_id}",
            payload={
                "review_id": review_id,
                "recipient": str(expert["email"]),
                "question": question,
            },
        )
    except Exception:
        # The in-app request is already durable. External delivery can be
        # retried from the notification record without losing the question.
        pass
    return review_id


async def _pair_thread_ids(
    session,
    *,
    org_id: str,
    user_a: str,
    user_b: str,
) -> list[str]:
    """All expert_request review ids between two users (either direction), oldest first."""

    rows = (
        await session.execute(text("""
            SELECT review_id FROM knowledge_reviews
            WHERE org_id=:org AND review_type='expert_request'
              AND (
                (created_by=:a AND owner_user_id=:b)
                OR (created_by=:b AND owner_user_id=:a)
              )
            ORDER BY created_at ASC, review_id ASC
        """), {"org": org_id, "a": user_a, "b": user_b})
    ).scalars().all()
    return [str(row) for row in rows]


async def consolidate_expert_pair(
    *,
    org_id: str,
    user_a: str,
    user_b: str,
) -> str | None:
    """Merge every thread between two people into the oldest one. Returns that id."""

    if user_a == user_b:
        return None
    factory = get_session_factory()
    async with factory() as session:
        ids = await _pair_thread_ids(
            session, org_id=org_id, user_a=user_a, user_b=user_b
        )
        if not ids:
            return None
        canonical = ids[0]
        extras = ids[1:]
        if extras:
            for extra_id in extras:
                await session.execute(text("""
                    UPDATE expert_messages SET review_id=:canonical
                    WHERE org_id=:org AND review_id=:extra
                """), {"canonical": canonical, "org": org_id, "extra": extra_id})
                await session.execute(text("""
                    UPDATE knowledge_reviews
                    SET status='resolved',
                        resolution_note='Merged into canonical expert conversation',
                        resolved_at=now(),
                        updated_at=now()
                    WHERE org_id=:org AND review_id=:extra
                """), {"org": org_id, "extra": extra_id})
            await session.execute(text("""
                UPDATE knowledge_reviews SET updated_at=now()
                WHERE org_id=:org AND review_id=:canonical
            """), {"org": org_id, "canonical": canonical})
            await session.commit()
        return canonical


async def _consolidate_all_pairs_for_user(org_id: str, user_id: str) -> None:
    """Collapse duplicate person-pair threads before listing the inbox."""

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT created_by, owner_user_id FROM knowledge_reviews
                WHERE org_id=:org AND review_type='expert_request'
                  AND (created_by=:user OR owner_user_id=:user)
                  AND status <> 'resolved'
            """), {"org": org_id, "user": user_id})
        ).mappings().all()
    seen: set[frozenset[str]] = set()
    for row in rows:
        a, b = str(row["created_by"]), str(row["owner_user_id"])
        if not a or not b:
            continue
        key = frozenset({a, b})
        if key in seen or len(key) < 2:
            continue
        seen.add(key)
        await consolidate_expert_pair(org_id=org_id, user_a=a, user_b=b)


async def start_expert_conversation(
    *,
    org_id: str,
    requester_user_id: str,
    expert_user_id: str,
    message: str,
) -> str:
    if requester_user_id == expert_user_id:
        raise HTTPException(status_code=400, detail="You cannot message yourself.")
    factory = get_session_factory()
    async with factory() as session:
        expert = (
            await session.execute(text("""
                SELECT user_id, email, coalesce(name, email) AS name
                FROM users WHERE org_id=:org AND user_id=:expert
            """), {"org": org_id, "expert": expert_user_id})
        ).mappings().one_or_none()
    if expert is None:
        raise HTTPException(status_code=404, detail="Expert was not found.")

    # One chat per person pair: reuse (and merge) any existing threads.
    review_id = await consolidate_expert_pair(
        org_id=org_id,
        user_a=requester_user_id,
        user_b=expert_user_id,
    )
    if review_id is None:
        review_id = await create_review(
            org_id=org_id,
            review_type="expert_request",
            title=f"Conversation with {expert['name']}",
            description="A direct employee-to-expert knowledge conversation.",
            created_by=requester_user_id,
            owner_user_id=expert_user_id,
            due_at=_now() + timedelta(days=7),
        )
    else:
        # Re-open if a prior merge left it resolved, or keep active chats open.
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("""
                UPDATE knowledge_reviews
                SET status='open',
                    resolution_note=NULL,
                    resolved_at=NULL,
                    resolved_by=NULL,
                    updated_at=now()
                WHERE org_id=:org AND review_id=:id
                  AND status IN ('resolved', 'rejected')
            """), {"org": org_id, "id": review_id})
            await session.commit()

    await send_expert_message(
        org_id=org_id,
        user_id=requester_user_id,
        review_id=review_id,
        body=message,
        message_type="manual_question",
    )
    from core.durable_jobs import enqueue
    try:
        await enqueue(
            "expert_notification",
            org_id=org_id,
            conversation_id=f"expert-notification:{review_id}",
            payload={
                "review_id": review_id,
                "recipient": str(expert["email"]),
                "question": message,
            },
        )
    except Exception:
        pass
    return review_id


async def send_expert_message(
    *,
    org_id: str,
    user_id: str,
    review_id: str,
    body: str,
    message_type: str = "text",
    attachment_name: str | None = None,
) -> dict:
    cleaned = body.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    factory = get_session_factory()
    async with factory() as session:
        thread = (
            await session.execute(text("""
                SELECT created_by, owner_user_id FROM knowledge_reviews
                WHERE org_id=:org AND review_id=:id
                  AND review_type='expert_request'
            """), {"org": org_id, "id": review_id})
        ).mappings().one_or_none()
        if thread is None:
            raise HTTPException(status_code=404, detail="Conversation was not found.")
        if user_id not in {thread["created_by"], thread["owner_user_id"]}:
            raise HTTPException(status_code=403, detail="You are not part of this conversation.")
        message_id = str(uuid4())
        await session.execute(text("""
            INSERT INTO expert_messages
              (message_id, org_id, review_id, sender_user_id, body,
               message_type, attachment_name, created_at)
            VALUES (:message, :org, :review, :sender, :body, :type, :attachment, now())
        """), {
            "message": message_id, "org": org_id, "review": review_id,
            "sender": user_id, "body": cleaned, "type": message_type,
            "attachment": attachment_name,
        })
        await session.execute(text("""
            UPDATE knowledge_reviews SET updated_at=now()
            WHERE org_id=:org AND review_id=:review
        """), {"org": org_id, "review": review_id})
        await session.commit()
    from core.durable_jobs import enqueue
    try:
        await enqueue(
            "expert_thread_ingest",
            org_id=org_id,
            conversation_id=f"expert_messages:{review_id}",
            payload={"review_id": review_id},
        )
    except Exception:
        pass
    return {"message_id": message_id, "status": "sent"}


async def list_expert_threads(org_id: str, user_id: str) -> list[dict]:
    await _consolidate_all_pairs_for_user(org_id, user_id)
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT r.review_id, r.status, r.title, r.description,
                       r.created_by, r.owner_user_id, r.created_at, r.updated_at,
                       requester.name AS requester_name, requester.email AS requester_email,
                       expert.name AS expert_name, expert.email AS expert_email,
                       last_message.body AS last_message,
                       last_message.created_at AS last_message_at,
                       coalesce(unread.count, 0) AS unread_count
                FROM knowledge_reviews r
                LEFT JOIN users requester ON requester.user_id=r.created_by
                LEFT JOIN users expert ON expert.user_id=r.owner_user_id
                LEFT JOIN LATERAL (
                    SELECT body, created_at FROM expert_messages
                    WHERE review_id=r.review_id ORDER BY created_at DESC LIMIT 1
                ) last_message ON true
                LEFT JOIN LATERAL (
                    SELECT count(*) AS count FROM expert_messages
                    WHERE review_id=r.review_id AND sender_user_id<>:user AND read_at IS NULL
                ) unread ON true
                WHERE r.org_id=:org AND r.review_type='expert_request'
                  AND (r.created_by=:user OR r.owner_user_id=:user)
                  AND coalesce(r.resolution_note, '') <> 'Merged into canonical expert conversation'
                ORDER BY coalesce(last_message.created_at, r.updated_at) DESC
            """), {"org": org_id, "user": user_id})
        ).mappings().all()
    return [dict(row) for row in rows]


async def get_expert_thread_messages(
    org_id: str, user_id: str, review_id: str
) -> list[dict]:
    factory = get_session_factory()
    async with factory() as session:
        participant = (
            await session.execute(text("""
                SELECT 1 FROM knowledge_reviews
                WHERE org_id=:org AND review_id=:review
                  AND review_type='expert_request'
                  AND (created_by=:user OR owner_user_id=:user)
            """), {"org": org_id, "review": review_id, "user": user_id})
        ).scalar_one_or_none()
        if participant is None:
            raise HTTPException(status_code=404, detail="Conversation was not found.")
        await session.execute(text("""
            UPDATE expert_messages SET read_at=now()
            WHERE org_id=:org AND review_id=:review
              AND sender_user_id<>:user AND read_at IS NULL
        """), {"org": org_id, "review": review_id, "user": user_id})
        await session.commit()
        rows = (
            await session.execute(text("""
                SELECT m.message_id, m.sender_user_id, m.body, m.message_type,
                       m.attachment_name, m.created_at, m.read_at,
                       coalesce(u.name, u.email) AS sender_name
                FROM expert_messages m
                JOIN users u ON u.user_id=m.sender_user_id
                WHERE m.org_id=:org AND m.review_id=:review
                ORDER BY m.created_at
            """), {"org": org_id, "review": review_id})
        ).mappings().all()
    return [dict(row) for row in rows]


async def expert_message_notification_count(org_id: str, user_id: str) -> int:
    factory = get_session_factory()
    async with factory() as session:
        value = (
            await session.execute(text("""
                SELECT count(*) FROM expert_messages m
                JOIN knowledge_reviews r ON r.review_id=m.review_id
                WHERE m.org_id=:org AND m.sender_user_id<>:user AND m.read_at IS NULL
                  AND (r.created_by=:user OR r.owner_user_id=:user)
            """), {"org": org_id, "user": user_id})
        ).scalar_one()
    return int(value)


async def list_message_contacts(org_id: str, user_id: str) -> list[dict]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT user_id, coalesce(name, email) AS name, email, role
                FROM users
                WHERE org_id=:org AND user_id<>:user
                ORDER BY coalesce(name, email)
            """), {"org": org_id, "user": user_id})
        ).mappings().all()
    return [dict(row) for row in rows]


async def _directory_people_matching_query(org_id: str, query: str) -> list[dict]:
    """Match org-directory Person nodes by title, name, or email."""

    cleaned = query.strip()
    if not cleaned:
        return []
    try:
        from core.database import get_neo4j_driver

        driver = get_neo4j_driver()
        async with driver.session() as graph:
            result = await graph.run(
                """
                MATCH (p:Person {org_id: $org_id})
                WHERE coalesce(p.canonical_email, p.email) IS NOT NULL
                  AND (
                    toLower(coalesce(p.title, '')) = toLower($q)
                    OR toLower(coalesce(p.name, '')) = toLower($q)
                    OR toLower(coalesce(p.canonical_email, p.email, '')) = toLower($q)
                    OR toLower(coalesce(p.title, '')) CONTAINS toLower($q)
                    OR toLower(coalesce(p.name, '')) CONTAINS toLower($q)
                    OR toLower(coalesce(p.canonical_email, p.email, '')) CONTAINS toLower($q)
                  )
                RETURN toLower(coalesce(p.canonical_email, p.email)) AS email,
                       p.name AS name,
                       p.title AS title,
                       p.department AS department,
                       CASE
                         WHEN toLower(coalesce(p.title, '')) = toLower($q) THEN 0
                         WHEN toLower(coalesce(p.name, '')) = toLower($q) THEN 1
                         WHEN toLower(coalesce(p.canonical_email, p.email, '')) = toLower($q) THEN 2
                         WHEN toLower(coalesce(p.title, '')) CONTAINS toLower($q) THEN 3
                         ELSE 4
                       END AS rank_score
                ORDER BY rank_score, p.name
                LIMIT 20
                """,
                org_id=org_id,
                q=cleaned,
            )
            return [dict(rec) async for rec in result]
    except Exception:  # noqa: BLE001 - messaging still works with Postgres-only match
        return []


async def lookup_messageable_people(
    org_id: str,
    query: str,
    *,
    exclude_user_id: str | None = None,
    limit: int = 8,
) -> list[dict]:
    """Rank org users and directory emails matching name, email, or title/role."""

    cleaned = " ".join(query.strip().split())
    if not cleaned:
        return []
    pattern = f"%{cleaned}%"
    params: dict = {
        "org": org_id,
        "q": cleaned,
        "pattern": pattern,
        "limit": limit,
    }
    exclude_clause = ""
    if exclude_user_id:
        exclude_clause = "AND user_id <> CAST(:exclude AS text)"
        params["exclude"] = exclude_user_id

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text(f"""
                SELECT user_id,
                       coalesce(name, email) AS name,
                       email,
                       role,
                       CASE
                         WHEN lower(email) = lower(:q) THEN 0
                         WHEN lower(coalesce(name, '')) = lower(:q) THEN 1
                         WHEN lower(email) LIKE lower(:pattern) THEN 2
                         WHEN lower(coalesce(name, '')) LIKE lower(:pattern) THEN 3
                         ELSE 4
                       END AS rank_score
                FROM users
                WHERE org_id = CAST(:org AS text)
                  {exclude_clause}
                  AND (
                    lower(email) LIKE lower(:pattern)
                    OR lower(coalesce(name, '')) LIKE lower(:pattern)
                  )
                ORDER BY rank_score, coalesce(name, email)
                LIMIT :limit
            """), params)
        ).mappings().all()

    by_email: dict[str, dict] = {}
    for row in rows:
        email = str(row["email"] or "").lower()
        if not email:
            continue
        by_email[email] = {
            "user_id": row["user_id"],
            "name": row["name"],
            "email": row["email"],
            "role": row["role"],
            "rank_score": int(row["rank_score"]),
        }

    directory_hits = await _directory_people_matching_query(org_id, cleaned)
    directory_emails = [
        str(hit["email"]).lower()
        for hit in directory_hits
        if hit.get("email")
    ]
    missing_emails = [email for email in directory_emails if email not in by_email]
    if missing_emails:
        link_params: dict = {"org": org_id, "emails": missing_emails}
        link_exclude = ""
        if exclude_user_id:
            link_exclude = "AND user_id <> CAST(:exclude AS text)"
            link_params["exclude"] = exclude_user_id
        async with factory() as session:
            linked = (
                await session.execute(text(f"""
                    SELECT user_id, coalesce(name, email) AS name, email, role
                    FROM users
                    WHERE org_id = CAST(:org AS text)
                      {link_exclude}
                      AND lower(email) = ANY(:emails)
                """), link_params)
            ).mappings().all()
        for row in linked:
            email = str(row["email"] or "").lower()
            by_email[email] = {
                "user_id": row["user_id"],
                "name": row["name"],
                "email": row["email"],
                "role": row["role"],
                "rank_score": 5,
            }

    for hit in directory_hits:
        email = str(hit.get("email") or "").lower()
        if not email:
            continue
        if email not in by_email:
            by_email[email] = {
                "user_id": "",
                "name": hit.get("name") or email,
                "email": email,
                "role": None,
                "title": hit.get("title"),
                "department": hit.get("department"),
                "rank_score": int(hit.get("rank_score") or 5),
            }
            continue
        item = by_email[email]
        if hit.get("title"):
            item["title"] = hit["title"]
        if hit.get("department"):
            item["department"] = hit["department"]
        dir_rank = int(hit.get("rank_score") or 5)
        # Prefer exact title matches (e.g. "CTO") over weak name/email contains.
        item["rank_score"] = min(int(item.get("rank_score", 5)), dir_rank)

    # Enrich remaining Postgres hits with directory title/department.
    remaining = [
        email for email in by_email
        if "title" not in by_email[email]
    ]
    if remaining:
        try:
            from core.database import get_neo4j_driver

            driver = get_neo4j_driver()
            async with driver.session() as graph:
                neo = await graph.run(
                    """
                    MATCH (p:Person {org_id: $org_id})
                    WHERE toLower(coalesce(p.canonical_email, p.email, '')) IN $emails
                    RETURN toLower(coalesce(p.canonical_email, p.email)) AS email,
                           p.title AS title,
                           p.department AS department
                    """,
                    org_id=org_id,
                    emails=remaining,
                )
                async for rec in neo:
                    email = str(rec["email"])
                    if email in by_email:
                        if rec.get("title"):
                            by_email[email]["title"] = rec.get("title")
                        if rec.get("department"):
                            by_email[email]["department"] = rec.get("department")
        except Exception:  # noqa: BLE001
            pass

    ranked = sorted(
        by_email.values(),
        key=lambda item: (int(item.get("rank_score", 99)), str(item.get("name") or "")),
    )
    results: list[dict] = []
    for item in ranked[:limit]:
        results.append({k: v for k, v in item.items() if k != "rank_score"})
    if _EMAIL_RE.match(cleaned):
        typed = cleaned.lower()
        if not any(str(item.get("email") or "").lower() == typed for item in results):
            results.insert(
                0,
                {
                    "user_id": "",
                    "name": typed.split("@")[0],
                    "email": typed,
                },
            )
            results = results[:limit]
    return results


def parse_recipients(value: str) -> list[str]:
    """Addresses from "a@x.com, b@y.com"; raises 400 on any invalid one."""

    recipients = [part.strip().strip("<>") for part in re.split(r"[,;\s]+", value or "") if part.strip()]
    if not recipients:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    invalid = [item for item in recipients if not _EMAIL_RE.match(item)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Not a valid email address: {', '.join(invalid)}.")
    if len(recipients) > 25:
        raise HTTPException(status_code=400, detail="Send to at most 25 addresses at once.")
    return list(dict.fromkeys(recipients))


async def send_proposed_email(
    *,
    org_id: str,
    requester_user_id: str,
    recipient_email: str,
    subject: str,
    body: str,
    attachment_file_ids: list[str] | None = None,
) -> dict:
    """Send an Ask-confirmed email from the requester's Gmail, with Files attached."""

    recipient = ", ".join(parse_recipients(recipient_email))
    cleaned_subject = subject.strip()
    cleaned_body = body.strip()
    if not cleaned_subject or not cleaned_body:
        raise HTTPException(status_code=400, detail="Subject and body are required.")

    from weaver.integrations import has_google_workspace_connection
    from shuttle.notification_delivery import send_user_gmail

    if not await has_google_workspace_connection(org_id, requester_user_id):
        raise HTTPException(
            status_code=400,
            detail="Connect Google Workspace to send email.",
        )
    attachments = []
    if attachment_file_ids:
        from loombot.files import files_as_attachments

        attachments = await files_as_attachments(org_id, requester_user_id, attachment_file_ids)
    try:
        message_id = await send_user_gmail(
            org_id=org_id,
            user_id=requester_user_id,
            recipient=recipient,
            subject=cleaned_subject,
            body=cleaned_body,
            attachments=attachments or None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = {"status": "sent", "provider_message_id": message_id}
    if attachments:
        result["attachments"] = [name for name, _data, _mime in attachments]
    return result


async def send_proposed_expert_message(
    *,
    org_id: str,
    requester_user_id: str,
    recipient_user_id: str,
    message: str,
) -> dict:
    """Send an Ask-confirmed message via Expert Messages."""

    if recipient_user_id == requester_user_id:
        raise HTTPException(status_code=400, detail="You cannot message yourself.")
    review_id = await start_expert_conversation(
        org_id=org_id,
        requester_user_id=requester_user_id,
        expert_user_id=recipient_user_id,
        message=message,
    )
    return {"review_id": review_id, "status": "sent"}


async def _load_thread_for_ingest(org_id: str, review_id: str) -> dict | None:
    factory = get_session_factory()
    async with factory() as session:
        thread = (
            await session.execute(text("""
                SELECT r.review_id, r.title, r.created_by, r.owner_user_id,
                       requester.email AS requester_email,
                       coalesce(requester.name, requester.email) AS requester_name,
                       expert.email AS expert_email,
                       coalesce(expert.name, expert.email) AS expert_name
                FROM knowledge_reviews r
                LEFT JOIN users requester ON requester.user_id = r.created_by
                LEFT JOIN users expert ON expert.user_id = r.owner_user_id
                WHERE r.org_id = :org AND r.review_id = :review
                  AND r.review_type = 'expert_request'
            """), {"org": org_id, "review": review_id})
        ).mappings().one_or_none()
        if thread is None:
            return None
        messages = (
            await session.execute(text("""
                SELECT m.message_id, m.sender_user_id, m.body, m.created_at,
                       coalesce(u.name, u.email) AS sender_name,
                       u.email AS sender_email
                FROM expert_messages m
                JOIN users u ON u.user_id = m.sender_user_id
                WHERE m.org_id = :org AND m.review_id = :review
                ORDER BY m.created_at
            """), {"org": org_id, "review": review_id})
        ).mappings().all()
    return {"thread": dict(thread), "messages": [dict(m) for m in messages]}


async def ingest_expert_thread(org_id: str, review_id: str) -> None:
    """Re-ingest a full Expert Messages thread into the knowledge graph."""

    payload = await _load_thread_for_ingest(org_id, review_id)
    if payload is None or not payload["messages"]:
        return

    thread = payload["thread"]
    rows = payload["messages"]
    participants_by_id: dict[str, Participant] = {}
    for row in rows:
        sender_id = str(row["sender_user_id"])
        participants_by_id[sender_id] = Participant(
            id=sender_id,
            name=str(row["sender_name"] or row["sender_email"] or sender_id),
        )
    for user_id, name in (
        (thread.get("created_by"), thread.get("requester_name")),
        (thread.get("owner_user_id"), thread.get("expert_name")),
    ):
        if user_id and str(user_id) not in participants_by_id:
            participants_by_id[str(user_id)] = Participant(
                id=str(user_id),
                name=str(name or user_id),
            )

    messages = [
        IncomingMessage(
            id=str(row["message_id"]),
            sender=str(row["sender_user_id"]),
            timestamp=row["created_at"],
            text=str(row["body"]),
        )
        for row in rows
    ]
    conversation_id = f"expert_messages:{review_id}"
    title = str(thread.get("title") or "Expert Messages conversation")
    conversation = Conversation(
        source="expert_messages",
        conversation_id=conversation_id,
        title=title,
        participants=list(participants_by_id.values()),
        messages=messages,
    )
    visible_to = sorted({
        token
        for token in (
            thread.get("requester_email"),
            thread.get("expert_email"),
            f"user:{thread.get('created_by')}" if thread.get("created_by") else None,
            f"user:{thread.get('owner_user_id')}" if thread.get("owner_user_id") else None,
        )
        if token
    })
    transcript = "\n".join(
        f"{row['sender_name']}: {row['body']}" for row in rows
    ).encode("utf-8")
    version = str(rows[-1]["message_id"])
    document = DocumentInput(
        data=transcript,
        source="expert_messages",
        source_label=title,
        original_filename=f"{conversation_id}.txt",
        mime_type="text/plain",
        visible_to=visible_to,
        title=title,
        source_application="Expert Messages",
        source_location=conversation_id,
        version=version,
    )
    await ingest_external_source(
        org_id=org_id,
        provider="expert_messages",
        external_id=review_id,
        version=version,
        conversation=conversation,
        document=document,
    )


async def list_expert_requests(org_id: str, user_id: str) -> list[dict]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text("""
                SELECT review_id, review_type, status, title, description,
                       created_by, owner_user_id, source_ids_json,
                       proposed_content, due_at, resolution_note,
                       created_at, updated_at
                FROM knowledge_reviews
                WHERE org_id=:org AND review_type='expert_request'
                  AND owner_user_id=:user AND status IN ('open','answered')
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, created_at DESC
            """), {"org": org_id, "user": user_id})
        ).mappings().all()
        deliveries = (
            await session.execute(text("""
                SELECT review_id, channel, status, error
                FROM notification_deliveries
                WHERE org_id=:org
                  AND review_id IN (
                    SELECT jsonb_array_elements_text(CAST(:ids AS jsonb))
                  )
            """), {
                "org": org_id,
                "ids": json.dumps([str(row["review_id"]) for row in rows]),
            })
        ).mappings().all()
    by_review: dict[str, dict[str, dict]] = {}
    for delivery in deliveries:
        by_review.setdefault(str(delivery["review_id"]), {})[str(delivery["channel"])] = {
            "status": delivery["status"], "error": delivery["error"],
        }
    return [{
        **dict(row),
        "source_ids": json.loads(row["source_ids_json"] or "[]"),
        "deliveries": by_review.get(str(row["review_id"]), {}),
    } for row in rows]


async def expert_notification_count(org_id: str, user_id: str) -> int:
    factory = get_session_factory()
    async with factory() as session:
        value = (
            await session.execute(text("""
                SELECT count(*) FROM knowledge_reviews
                WHERE org_id=:org AND review_type='expert_request'
                  AND owner_user_id=:user AND status='open'
            """), {"org": org_id, "user": user_id})
        ).scalar_one()
    return int(value)


async def _publish_expert_answer(
    *,
    org_id: str,
    review_id: str,
    expert_user_id: str,
    question: str,
    answer: str,
    version: str,
) -> None:
    timestamp = _now()
    text_content = f"Question: {question}\n\nExpert answer: {answer}"
    conversation = Conversation(
        source="expert_answer", conversation_id=f"expert-answer:{review_id}",
        title=question,
        participants=[Participant(id=expert_user_id, name=expert_user_id)],
        messages=[IncomingMessage(
            id=f"{review_id}:{version}", sender=expert_user_id,
            timestamp=timestamp, text=text_content,
        )],
    )
    await ingest_external_source(
        org_id=org_id, provider="expert_answer", external_id=review_id,
        version=version, conversation=conversation,
        document=DocumentInput(
            data=text_content.encode(), source="expert_answer",
            source_label=question, original_filename=f"{review_id}.txt",
            mime_type="text/plain", visible_to=[f"org:{org_id}"],
            title=question, author=expert_user_id, owners=[expert_user_id],
            source_created_at=timestamp, source_updated_at=timestamp,
            source_application="Company Brain Expert Messages",
            source_location="Expert answers", version=version,
            contributors=[expert_user_id], permissions=[f"org:{org_id}"],
        ),
    )


async def answer_expert_request(
    org_id: str, user_id: str, review_id: str, answer: str
) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(text("""
                SELECT * FROM knowledge_reviews
                WHERE org_id=:org AND review_id=:id
                  AND review_type='expert_request' AND owner_user_id=:user
                  AND status='open'
            """), {"org": org_id, "id": review_id, "user": user_id})
        ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Open expert request not found.")
    question = str(row["title"]).removeprefix("Expert question: ")
    await send_expert_message(
        org_id=org_id,
        user_id=user_id,
        review_id=review_id,
        body=answer,
        message_type="expert_answer",
    )
    from weaver.capture_service import create_skill_file_from_expert_answer

    skill = await create_skill_file_from_expert_answer(
        org_id=org_id,
        expert_user_id=user_id,
        request_id=review_id,
        question=question,
        answer=answer,
    )
    async with factory() as session:
        async with session.begin():
            await session.execute(text("""
                UPDATE knowledge_reviews SET status='drafted',
                  proposed_content=:answer, resolved_by=:user,
                  resolution_note=:note, updated_at=now()
                WHERE org_id=:org AND review_id=:id
            """), {
                "answer": answer, "user": user_id, "org": org_id, "id": review_id,
                "note": f"Proposed Skill File {skill.skill_id}",
            })
    return {
        "review_id": review_id,
        "status": "drafted",
        "skill_id": skill.skill_id,
        "follow_up_questions": skill.follow_up_questions,
    }


async def moderate_expert_answer(
    *,
    org_id: str,
    admin_user_id: str,
    review_id: str,
    action: str,
    answer: str | None,
) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(text("""
                SELECT * FROM knowledge_reviews
                WHERE org_id=:org AND review_id=:id
                  AND review_type='expert_request' AND status='answered'
            """), {"org": org_id, "id": review_id})
        ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Published expert answer not found.")
    if action == "edit":
        cleaned = (answer or "").strip()
        if not cleaned:
            raise HTTPException(status_code=400, detail="Edited answer cannot be empty.")
        question = str(row["title"]).removeprefix("Expert question: ")
        await _publish_expert_answer(
            org_id=org_id, review_id=review_id,
            expert_user_id=str(row["owner_user_id"]), question=question,
            answer=cleaned, version=f"admin-edit:{_now().isoformat()}",
        )
        status = "answered"
        content = cleaned
    elif action == "remove":
        await mark_external_source_deleted(org_id, "expert_answer", review_id)
        status = "removed"
        content = row["proposed_content"]
    else:
        raise HTTPException(status_code=400, detail="Unsupported moderation action.")
    async with factory() as session:
        async with session.begin():
            await session.execute(text("""
                UPDATE knowledge_reviews SET status=:status,
                  proposed_content=:content, resolved_by=:admin,
                  resolution_note=:note, updated_at=now()
                WHERE org_id=:org AND review_id=:id
            """), {
                "status": status, "content": content, "admin": admin_user_id,
                "note": f"Administrator {action}", "org": org_id, "id": review_id,
            })
    return {"review_id": review_id, "status": status}


_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\b")
_NEGATION_RE = re.compile(r"\b(?:no|not|never|cannot|can't|won't|isn't|aren't|wasn't|doesn't|didn't)\b", re.I)
_CLAIM_WORD_RE = re.compile(r"[a-z][a-z'-]{2,}")
_CLAIM_STOPWORDS = frozenset("""
the and was were are has have had will would should could can may might for with from
this that these those its their there here than then into onto over under about after
before when which who whom whose what also been being being per via our your his her
they them she him it's not no never
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec monday tuesday wednesday thursday
friday saturday sunday today yesterday tomorrow
""".split())
# Candidate pairs must share at least this share of their subject words.
_MIN_SUBJECT_OVERLAP = 0.34
_MAX_JUDGED_PER_CLAIM = 3

_CONFLICT_SYSTEM = """\
You check whether two statements from a company's knowledge base contradict each other.
They contradict only when both describe the SAME attribute of the SAME thing (the same
item, person, project, document, order, or event) and cannot both be true.
- Different facts about one thing are compatible: a receipt's total and its receipt
  number; a project's owner and its deadline; an order's date and its amount.
- Facts about two different things are compatible: two receipts, two orders, two meetings.
- A value that changed over time, where the later statement updates the earlier one, is
  an update, not a contradiction.
Reply with JSON only: {"verdict": "contradiction" | "update" | "compatible",
"reason": "<one short sentence>"}
"""


def _claim_terms(claim: str) -> set[str]:
    """Subject words of a claim: no numbers, dates, or filler words."""

    return {
        word.strip("'-") for word in _CLAIM_WORD_RE.findall(claim.lower())
        if word.strip("'-") not in _CLAIM_STOPWORDS
    }


def _normalize_claim(claim: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", claim.lower()))


def is_conflict_candidate(new_claim: str, old_claim: str) -> bool:
    """Cheap filter: both claims talk about the same attribute and disagree on a value or a negation.

    "The total paid was $21.40" and "The receipt number is 2290-2314-6727" share no
    subject words, so they are never compared, even though their numbers differ.
    """

    if _normalize_claim(new_claim) == _normalize_claim(old_claim):
        return False
    new_terms, old_terms = _claim_terms(new_claim), _claim_terms(old_claim)
    shared = new_terms & old_terms
    if not shared:
        return False
    overlap = len(shared) / len(new_terms | old_terms)
    if overlap < _MIN_SUBJECT_OVERLAP:
        return False
    new_numbers = set(_NUMBER_RE.findall(new_claim))
    old_numbers = set(_NUMBER_RE.findall(old_claim))
    numbers_differ = bool(new_numbers and old_numbers and new_numbers != old_numbers)
    negation_differs = bool(_NEGATION_RE.search(new_claim)) != bool(_NEGATION_RE.search(old_claim))
    return numbers_differ or negation_differs


async def judge_conflict(
    new_claim: str, old_claim: str, *, new_at: datetime | None = None, old_at: datetime | None = None
) -> tuple[str, str] | None:
    """(verdict, reason) from the cheap model, or None when it could not decide."""

    from core.config import get_settings
    from core.llm_provider import get_default_openai_client

    def when(value: datetime | None) -> str:
        return f" (recorded {value.date().isoformat()})" if value else ""

    try:
        client = get_default_openai_client().with_model(get_settings().model_router_model, "none")
        response = await client.chat.completions.create(
            model="",
            messages=[
                {"role": "system", "content": _CONFLICT_SYSTEM},
                {"role": "user", "content": (
                    f"Earlier statement{when(old_at)}: {old_claim}\n"
                    f"New statement{when(new_at)}: {new_claim}"
                )},
            ],
            temperature=0,
            max_tokens=80,
            response_format={"type": "json_object"},
        )
        match = re.search(r"\{.*\}", response.choices[0].message.content or "", re.S)
        parsed = json.loads(match.group(0)) if match else {}
    except Exception:  # noqa: BLE001 - without a verdict nothing is queued
        logger.warning("Conflict check failed", exc_info=True)
        return None
    verdict = str(parsed.get("verdict") or "").strip().lower() if isinstance(parsed, dict) else ""
    if verdict not in {"contradiction", "update", "compatible"}:
        return None
    return verdict, " ".join(str(parsed.get("reason") or "").split())[:300]


async def record_claims_and_detect_conflicts(
    org_id: str, chunk: Chunk, metadata: ChunkMetadata
) -> None:
    """Record a chunk's claims and queue reviews only for real contradictions.

    A pair is reviewed only when the claims share their subject and disagree on a
    value or negation (``is_conflict_candidate``) AND the cheap model confirms they
    describe the same attribute of the same thing and cannot both be true.
    """

    if not metadata.factual_claims:
        return
    entity_key = "|".join(sorted(item.lower() for item in metadata.entities[:5]))
    factory = get_session_factory()
    async with factory() as session:
        prior = (
            await session.execute(text("""
                SELECT claim_id, chunk_id, claim_text, created_at
                FROM knowledge_claims
                WHERE org_id = :org AND entity_key = :key AND chunk_id <> :chunk
                ORDER BY created_at DESC LIMIT 40
            """), {"org": org_id, "key": entity_key, "chunk": chunk.chunk_id})
        ).mappings().all()

    for claim in metadata.factual_claims:
        candidates = [old for old in prior if is_conflict_candidate(claim, str(old["claim_text"]))]
        for old in candidates[:_MAX_JUDGED_PER_CLAIM]:
            old_claim = str(old["claim_text"])
            judged = await judge_conflict(
                claim, old_claim, new_at=chunk.end_time, old_at=old.get("created_at")
            )
            if judged is None or judged[0] != "contradiction":
                continue
            description = f"New claim: {claim}\nExisting claim: {old_claim}"
            if judged[1]:
                description += f"\nWhy they conflict: {judged[1]}"
            async with factory() as session:
                duplicate = (
                    await session.execute(text("""
                        SELECT 1 FROM knowledge_reviews
                        WHERE org_id=:org AND review_type='conflict' AND status='open'
                          AND starts_with(description, :prefix)
                        LIMIT 1
                    """), {
                        "org": org_id,
                        "prefix": f"New claim: {claim}\nExisting claim: {old_claim}",
                    })
                ).scalar_one_or_none()
            if duplicate is None:
                await create_review(
                    org_id=org_id, review_type="conflict",
                    title="Potentially conflicting company knowledge",
                    description=description,
                    source_ids=[chunk.chunk_id, str(old["chunk_id"])],
                )
            break

    async with factory() as session:
        for claim in metadata.factual_claims:
            await session.execute(text("""
                INSERT INTO knowledge_claims
                  (claim_id, org_id, chunk_id, entity_key, claim_text, created_at)
                VALUES (:id, :org, :chunk, :key, :claim, now())
                ON CONFLICT DO NOTHING
            """), {
                "id": str(uuid4()), "org": org_id, "chunk": chunk.chunk_id,
                "key": entity_key, "claim": claim,
            })
        await session.commit()


async def resolve_all_reviews(
    org_id: str,
    *,
    actor_user_id: str,
    review_types: list[str] | None = None,
    note: str | None = None,
) -> dict:
    """Resolve every open conflict and verification review at once.

    Expert-proposed answers are left open: approving one publishes it as company
    knowledge, so each needs its own decision.
    """

    allowed = {"conflict", "verification"}
    types = sorted(set(review_types or allowed) & allowed)
    if not types:
        raise HTTPException(status_code=400, detail="Only conflicts and verifications can be resolved in bulk.")
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            result = await session.execute(text("""
                UPDATE knowledge_reviews SET status='resolved', resolved_by=:actor,
                  resolution_note=:note, resolved_at=now(), updated_at=now()
                WHERE org_id=:org AND status='open' AND review_type = ANY(:types)
            """), {
                "actor": actor_user_id, "note": note or "Resolved in bulk",
                "org": org_id, "types": types,
            })
    return {"resolved": int(result.rowcount or 0), "review_types": types}


async def list_reviews(org_id: str, *, review_type: str | None = None) -> list[dict]:
    factory = get_session_factory()
    query = """
        SELECT review_id, review_type, status, title, description, created_by,
               owner_user_id, source_ids_json, proposed_content, due_at,
               resolution_note, created_at, updated_at
        FROM knowledge_reviews
        WHERE org_id = :org
    """
    params: dict[str, object] = {"org": org_id}
    if review_type:
        query += " AND review_type = :type"
        params["type"] = review_type
    query += " ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, created_at DESC"
    async with factory() as session:
        rows = (await session.execute(text(query), params)).mappings().all()
    return [
        {**dict(row), "source_ids": json.loads(row["source_ids_json"] or "[]")}
        for row in rows
    ]


async def resolve_review(
    org_id: str, review_id: str, *, status: str, actor_user_id: str, note: str | None
) -> dict:
    if status not in {"approved", "rejected", "resolved"}:
        raise HTTPException(status_code=400, detail="Unsupported review decision.")
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(text("""
                SELECT * FROM knowledge_reviews
                WHERE org_id = :org AND review_id = :id
            """), {"org": org_id, "id": review_id})
        ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    if row["review_type"] == "proposal" and status == "approved":
        content = str(row["proposed_content"] or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="Proposal has no answer to approve.")
        timestamp = _now()
        conversation = Conversation(
            source="expert_proposal", conversation_id=f"proposal:{review_id}",
            title=str(row["title"]),
            participants=[Participant(id=actor_user_id, name=actor_user_id)],
            messages=[IncomingMessage(
                id=review_id, sender=actor_user_id, timestamp=timestamp, text=content
            )],
        )
        await run_ingestion(
            conversation, org_id,
            document=DocumentInput(
                data=content.encode(), source="expert_proposal",
                source_label=str(row["title"]), original_filename=f"{review_id}.txt",
                mime_type="text/plain", visible_to=[f"org:{org_id}"],
                title=str(row["title"]), author=actor_user_id, owners=[actor_user_id],
                source_created_at=timestamp, source_updated_at=timestamp,
                source_application="Company Brain Expert Review",
                source_location="Approved expert knowledge",
                version="approved-1", contributors=[actor_user_id],
                permissions=[f"org:{org_id}"],
            ),
        )
    async with factory() as session:
        async with session.begin():
            await session.execute(text("""
                UPDATE knowledge_reviews SET status=:status, resolved_by=:actor,
                  resolution_note=:note, resolved_at=now(), updated_at=now()
                WHERE org_id=:org AND review_id=:id
            """), {
                "status": status, "actor": actor_user_id, "note": note,
                "org": org_id, "id": review_id,
            })
    return {"review_id": review_id, "status": status}


async def schedule_expiry_reviews() -> int:
    """Create owner verification requests for documents whose review date has arrived."""

    factory = get_session_factory()
    async with factory() as session:
        scheduled = await session.execute(text("""
            INSERT INTO knowledge_reviews
              (review_id, org_id, review_type, status, title, description,
               owner_user_id, source_ids_json, due_at, created_at, updated_at)
            SELECT md5(random()::text || clock_timestamp()::text), s.org_id,
                   'verification', 'open', 'Scheduled knowledge verification',
                   CASE WHEN s.expires_at IS NOT NULL AND s.expires_at <= now()
                        THEN 'This source has expired. Approve a current version or retire it.'
                        ELSE 'Confirm this source is accurate and current.' END,
                   s.owner_user_id, json_build_array(s.source_id)::text,
                   now() + interval '14 days', now(), now()
            FROM knowledge_review_schedules s
            WHERE s.active AND (s.next_review_at <= now() OR s.expires_at <= now())
              AND NOT EXISTS (
                SELECT 1 FROM knowledge_reviews kr
                WHERE kr.org_id=s.org_id AND kr.review_type='verification'
                  AND kr.source_ids_json LIKE '%' || s.source_id || '%'
                  AND kr.status='open'
              )
        """))
        await session.execute(text("""
            UPDATE knowledge_review_schedules
            SET next_review_at = now() + make_interval(days => interval_days),
                updated_at=now()
            WHERE active AND next_review_at <= now()
        """))
        fallback = await session.execute(text("""
            INSERT INTO knowledge_reviews
              (review_id, org_id, review_type, status, title, description,
               owner_user_id, source_ids_json, due_at, created_at, updated_at)
            SELECT md5(random()::text || clock_timestamp()::text), es.org_id,
                   'verification', 'open', 'Verify source is still current',
                   'This source reached its scheduled review date. Confirm it, update it, or retire it.',
                   NULL, json_build_array(es.document_id)::text, now() + interval '14 days',
                   now(), now()
            FROM external_sources es
            WHERE es.status='active'
              AND es.updated_at < now() - interval '180 days'
              AND NOT EXISTS (
                SELECT 1 FROM knowledge_reviews kr
                WHERE kr.org_id=es.org_id AND kr.review_type='verification'
                  AND kr.source_ids_json LIKE '%' || es.document_id || '%'
                  AND kr.status='open'
              )
        """))
        await session.commit()
    return int(scheduled.rowcount or 0) + int(fallback.rowcount or 0)


async def upsert_review_schedule(
    *,
    org_id: str,
    source_id: str,
    owner_user_id: str,
    interval_days: int,
    next_review_at: datetime,
    expires_at: datetime | None,
) -> str:
    schedule_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            row = (
                await session.execute(text("""
                    INSERT INTO knowledge_review_schedules
                      (schedule_id, org_id, source_id, owner_user_id, interval_days,
                       next_review_at, expires_at, active, created_at, updated_at)
                    VALUES (:id,:org,:source,:owner,:days,:next,:expires,TRUE,now(),now())
                    ON CONFLICT (org_id, source_id) DO UPDATE SET
                      owner_user_id=EXCLUDED.owner_user_id,
                      interval_days=EXCLUDED.interval_days,
                      next_review_at=EXCLUDED.next_review_at,
                      expires_at=EXCLUDED.expires_at,
                      active=TRUE, updated_at=now()
                    RETURNING schedule_id
                """), {
                    "id": schedule_id, "org": org_id, "source": source_id,
                    "owner": owner_user_id, "days": interval_days,
                    "next": next_review_at, "expires": expires_at,
                })
            ).scalar_one()
    return str(row)
