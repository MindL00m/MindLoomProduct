"""Human review queues for conflicts, verification, and expert knowledge."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from database import get_session_factory
from models import Chunk, ChunkMetadata, Conversation, IncomingMessage, Participant
from pipeline import DocumentInput, run_ingestion
from source_registry import (
    ingest_external_source,
    mark_external_source_deleted,
)


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
        expert = (
            await session.execute(text("""
                SELECT user_id, email FROM users
                WHERE org_id=:org
                  AND (
                    (:email IS NOT NULL AND lower(email)=lower(:email))
                    OR lower(coalesce(name, ''))=lower(:name)
                  )
                ORDER BY CASE WHEN lower(email)=lower(coalesce(:email, '')) THEN 0 ELSE 1 END
                LIMIT 1
            """), {"org": org_id, "email": expert_email, "name": expert_name})
        ).mappings().one_or_none()
        if expert is None:
            return None
        existing = (
            await session.execute(text("""
                SELECT review_id FROM knowledge_reviews
                WHERE org_id=:org AND review_type='expert_request'
                  AND owner_user_id=:owner AND title=:title
                  AND status IN ('open','answered','drafted')
                LIMIT 1
            """), {
                "org": org_id, "owner": str(expert["user_id"]),
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
            "Reply in Messages. Loom will turn the useful answer into a reviewable knowledge draft."
        ),
        created_by=requester_user_id, owner_user_id=str(expert["user_id"]),
        source_ids=source_ids, due_at=_now() + timedelta(days=7),
    )
    await send_expert_message(
        org_id=org_id,
        user_id=requester_user_id,
        review_id=review_id,
        body=question,
        message_type="routed_question",
    )
    from durable_jobs import enqueue
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


async def start_expert_conversation(
    *,
    org_id: str,
    requester_user_id: str,
    expert_user_id: str,
    message: str,
) -> str:
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
    review_id = await create_review(
        org_id=org_id,
        review_type="expert_request",
        title=f"Conversation with {expert['name']}",
        description="A direct employee-to-expert knowledge conversation.",
        created_by=requester_user_id,
        owner_user_id=expert_user_id,
        due_at=_now() + timedelta(days=7),
    )
    await send_expert_message(
        org_id=org_id,
        user_id=requester_user_id,
        review_id=review_id,
        body=message,
        message_type="manual_question",
    )
    from durable_jobs import enqueue
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
    return {"message_id": message_id, "status": "sent"}


async def list_expert_threads(org_id: str, user_id: str) -> list[dict]:
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
    from capture_service import create_skill_file_from_expert_answer

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


async def record_claims_and_detect_conflicts(
    org_id: str, chunk: Chunk, metadata: ChunkMetadata
) -> None:
    """Queue only strong numeric/negation contradictions for administrator review."""

    if not metadata.factual_claims:
        return
    entity_key = "|".join(sorted(item.lower() for item in metadata.entities[:5]))
    factory = get_session_factory()
    async with factory() as session:
        for claim in metadata.factual_claims:
            prior = (
                await session.execute(text("""
                    SELECT claim_id, chunk_id, claim_text
                    FROM knowledge_claims
                    WHERE org_id = :org AND entity_key = :key AND chunk_id <> :chunk
                    ORDER BY created_at DESC LIMIT 20
                """), {"org": org_id, "key": entity_key, "chunk": chunk.chunk_id})
            ).mappings().all()
            current_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", claim))
            current_negative = bool(re.search(r"\b(no|not|never|cannot|won't|isn't)\b", claim, re.I))
            for old in prior:
                old_claim = str(old["claim_text"])
                old_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", old_claim))
                old_negative = bool(re.search(r"\b(no|not|never|cannot|won't|isn't)\b", old_claim, re.I))
                contradiction = (
                    bool(current_numbers and old_numbers and current_numbers != old_numbers)
                    or current_negative != old_negative
                )
                if contradiction:
                    await create_review(
                        org_id=org_id, review_type="conflict",
                        title="Potentially conflicting company knowledge",
                        description=f"New claim: {claim}\nExisting claim: {old_claim}",
                        source_ids=[chunk.chunk_id, str(old["chunk_id"])],
                    )
                    break
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
