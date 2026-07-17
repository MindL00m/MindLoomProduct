"""Redis queue plus Postgres-backed durable job status."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import text

from config import get_settings
from database import get_session_factory
from models import Conversation, JobStatus

QUEUE_NAME = "loom:jobs"
PROCESSING_QUEUE_NAME = "loom:jobs:processing"


async def set_job(job: JobStatus) -> None:
    factory = get_session_factory()
    result_json = job.result.model_dump_json() if job.result else None
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO ingestion_jobs
                      (job_id, org_id, conversation_id, status, progress, error,
                       result_json, created_at, updated_at)
                    VALUES
                      (:job_id, :org_id, :conversation_id, :status, :progress, :error,
                       :result_json, now(), now())
                    ON CONFLICT (job_id) DO UPDATE SET
                      status = EXCLUDED.status, progress = EXCLUDED.progress,
                      error = EXCLUDED.error, result_json = EXCLUDED.result_json,
                      updated_at = now()
                    """
                ),
                {
                    "job_id": job.job_id,
                    "org_id": job.org_id,
                    "conversation_id": job.conversation_id,
                    "status": job.status,
                    "progress": job.progress,
                    "error": job.error,
                    "result_json": result_json,
                },
            )


async def get_job(job_id: str, org_id: str) -> JobStatus | None:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT job_id, org_id, conversation_id, status, progress, error,
                           result_json
                    FROM ingestion_jobs WHERE job_id = :job_id AND org_id = :org_id
                    """
                ),
                {"job_id": job_id, "org_id": org_id},
            )
        ).mappings().one_or_none()
    if row is None:
        return None
    payload = dict(row)
    result_json = payload.pop("result_json")
    if result_json:
        payload["result"] = json.loads(result_json)
    return JobStatus.model_validate(payload)


async def enqueue(
    kind: str,
    *,
    org_id: str,
    conversation_id: str,
    payload: dict[str, Any],
) -> str:
    job_id = str(uuid4())
    await set_job(
        JobStatus(
            job_id=job_id,
            org_id=org_id,
            status="queued",
            conversation_id=conversation_id,
            progress="Queued",
        )
    )
    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await redis.rpush(
            QUEUE_NAME,
            json.dumps(
                {"job_id": job_id, "kind": kind, "org_id": org_id, "payload": payload}
            ),
        )
    finally:
        await redis.aclose()
    return job_id


async def execute(message: dict[str, Any]) -> None:
    job_id = str(message["job_id"])
    org_id = str(message["org_id"])
    kind = str(message["kind"])
    payload = dict(message.get("payload") or {})
    local: dict[str, JobStatus] = {
        job_id: JobStatus(
            job_id=job_id,
            org_id=org_id,
            status="processing",
            conversation_id=str(payload.get("conversation_id") or kind),
            progress="Processing",
        )
    }
    await set_job(local[job_id])
    try:
        if kind == "conversation":
            from pipeline import run_ingestion_background

            await run_ingestion_background(
                job_id, Conversation.model_validate(payload["conversation"]), org_id, local
            )
        elif kind == "pdf":
            from pipeline import run_pdf_ingestion_background

            await run_pdf_ingestion_background(
                job_id,
                base64.b64decode(payload["data"]),
                str(payload["filename"]),
                org_id,
                list(payload.get("visible_to") or []),
                local,
            )
        elif kind == "document":
            from document_ingestion import run_document_ingestion_background
            from models import DocumentMetadataInput

            await run_document_ingestion_background(
                job_id=job_id,
                data=base64.b64decode(payload["data"]),
                filename=str(payload["filename"]),
                mime_type=str(payload["mime_type"]),
                org_id=org_id,
                uploaded_by=str(payload["uploaded_by"]),
                metadata=DocumentMetadataInput.model_validate(payload.get("metadata") or {}),
                job_store=local,
            )
        elif kind == "whatsapp_export":
            from models import DocumentMetadataInput
            from whatsapp import run_whatsapp_ingestion_background

            await run_whatsapp_ingestion_background(
                job_id=job_id, data=base64.b64decode(payload["data"]),
                filename=str(payload["filename"]), org_id=org_id,
                uploaded_by=str(payload["uploaded_by"]),
                timezone_name=str(payload.get("timezone_name") or "UTC"),
                metadata=DocumentMetadataInput.model_validate(payload.get("metadata") or {}),
                job_store=local,
            )
        elif kind == "zoom_sync":
            from zoom_workspace import sync_zoom

            job = local[job_id]
            job.status = "processing"
            job.progress = "Synchronizing Zoom recordings and transcripts"
            try:
                count = await sync_zoom(
                    org_id,
                    str(payload["user_id"]),
                    max_results=int(payload.get("max_results") or 100),
                    meeting_uuid=payload.get("meeting_uuid"),
                )
                job.status = "complete"
                job.progress = f"Zoom synchronization complete: {count} updated meeting(s)"
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.progress = None
                job.error = str(exc)
        elif kind == "google_sync":
            from google_workspace import run_workspace_sync_background

            await run_workspace_sync_background(
                job_id=job_id,
                org_id=org_id,
                user_id=str(payload["user_id"]),
                source=str(payload["source"]),
                job_store=local,
                max_results=int(payload.get("max_results", 25)),
                override_history_id=payload.get("override_history_id"),
            )
        elif kind == "microsoft_sync":
            from microsoft_teams import run_teams_sync_background
            from microsoft365_sources import (
                sync_outlook_calendar,
                sync_outlook_mail,
                sync_teams_chats,
            )
            from sharepoint import sync_sharepoint

            await run_teams_sync_background(
                job_id=job_id,
                org_id=org_id,
                user_id=str(payload["user_id"]),
                job_store=local,
                max_results=int(payload.get("max_results", 25)),
            )
            if local[job_id].status == "complete":
                count = await sync_sharepoint(
                    org_id,
                    str(payload["user_id"]),
                    max_results=int(payload.get("max_results", 25)),
                )
                local[job_id].progress += f"; SharePoint {count} item(s)"
                mail = await sync_outlook_mail(
                    org_id, str(payload["user_id"]),
                    max_results=int(payload.get("max_results", 25)),
                )
                calendar = await sync_outlook_calendar(
                    org_id, str(payload["user_id"]),
                    max_results=int(payload.get("max_results", 25)),
                )
                chats = await sync_teams_chats(
                    org_id, str(payload["user_id"]),
                    max_results=int(payload.get("max_results", 25)),
                )
                local[job_id].progress += (
                    f"; Outlook mail {mail}, calendar {calendar}, private chats {chats} item(s)"
                )
        elif kind == "initialize_connection":
            from connection_setup import ConnectionPolicyRow, initialize_connection

            factory = get_session_factory()
            async with factory() as session:
                row = await session.get(ConnectionPolicyRow, str(payload["policy_id"]))
            if row is None:
                raise RuntimeError("Connection policy no longer exists.")
            await initialize_connection(row, job_id, local)
        elif kind == "expert_notification":
            from notification_delivery import deliver_expert_request

            outcomes = await deliver_expert_request(
                org_id=org_id,
                review_id=str(payload["review_id"]),
                recipient=str(payload["recipient"]),
                question=str(payload["question"]),
            )
            local[job_id].status = "complete"
            local[job_id].progress = (
                "Expert notification delivery: "
                + ", ".join(f"{channel}={status}" for channel, status in outcomes.items())
            )
        else:
            raise ValueError(f"Unknown durable job type: {kind}")
    except Exception as exc:  # noqa: BLE001
        local[job_id].status = "failed"
        local[job_id].progress = None
        local[job_id].error = str(exc)
    await set_job(local[job_id])
