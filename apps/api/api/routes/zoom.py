"""Zoom connection, manual synchronization, and webhook routes."""

import hashlib
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from auth import UserRow
from config import get_settings
from database import get_session_factory
from durable_jobs import enqueue
from integrations import AppConnectionRow, list_integrations, require_admin_context
from models import IntegrationsListResponse, OAuthAuthorizeResponse
from zoom_workspace import (
    PROVIDER_ZOOM,
    connect_zoom_dev,
    handle_zoom_callback,
    start_zoom_oauth,
    validate_zoom_webhook,
)

router = APIRouter(tags=["zoom"])


@router.get("/integrations/zoom/authorize", response_model=OAuthAuthorizeResponse)
async def authorize(
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> OAuthAuthorizeResponse:
    return OAuthAuthorizeResponse(authorization_url=await start_zoom_oauth(*ctx))


@router.post("/integrations/zoom/connect-dev", response_model=IntegrationsListResponse)
async def connect_dev(
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> IntegrationsListResponse:
    factory = get_session_factory()
    async with factory() as session:
        email = (await session.execute(
            select(UserRow.email).where(UserRow.user_id == ctx[1])
        )).scalar_one()
    await connect_zoom_dev(*ctx, email)
    return await list_integrations(*ctx)


@router.get("/integrations/zoom/callback")
async def callback(code: str, state: str) -> RedirectResponse:
    return RedirectResponse(await handle_zoom_callback(code, state))


@router.post("/integrations/zoom/sync")
async def sync_now(
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> dict[str, str]:
    job_id = await enqueue(
        "zoom_sync",
        org_id=ctx[0],
        conversation_id=f"zoom:{ctx[1]}",
        payload={"user_id": ctx[1], "max_results": 100},
    )
    return {"job_id": job_id, "status": "queued"}


@router.post("/webhooks/zoom")
async def webhook(
    request: Request,
    x_zm_request_timestamp: str = Header(default="", alias="x-zm-request-timestamp"),
    x_zm_signature: str = Header(default="", alias="x-zm-signature"),
) -> dict:
    body = await request.body()
    payload = __import__("json").loads(body)
    if payload.get("event") == "endpoint.url_validation":
        plain = str(payload.get("payload", {}).get("plainToken") or "")
        encrypted = hmac.new(
            get_settings().zoom_webhook_secret_token.encode(),
            plain.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {"plainToken": plain, "encryptedToken": encrypted}
    if not validate_zoom_webhook(body, x_zm_request_timestamp, x_zm_signature):
        raise HTTPException(status_code=401, detail="Invalid Zoom webhook signature.")
    if payload.get("event") not in {
        "recording.completed",
        "recording.transcript_completed",
    }:
        return {"accepted": True, "queued": False}
    obj = payload.get("payload", {}).get("object", {})
    host_email = str(obj.get("host_email") or "")
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(
            select(AppConnectionRow).where(
                AppConnectionRow.provider == PROVIDER_ZOOM,
                AppConnectionRow.account_email == host_email,
            )
        )).scalars().first()
    if row is None:
        return {"accepted": True, "queued": False}
    job_id = await enqueue(
        "zoom_sync",
        org_id=row.org_id,
        conversation_id=f"zoom:{obj.get('uuid') or obj.get('id')}",
        payload={
            "user_id": row.user_id,
            "meeting_uuid": str(obj.get("uuid") or obj.get("id")),
            "max_results": 1,
        },
    )
    return {"accepted": True, "queued": True, "job_id": job_id}
