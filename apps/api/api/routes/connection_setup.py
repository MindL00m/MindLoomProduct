"""Controlled setup routes shared by Google and Microsoft connections."""

from fastapi import APIRouter, Depends

from connection_setup import (
    discover_resources,
    disconnect_controlled_connection,
    get_policy,
    initialize_connection,
    policy_response,
    preview_policy,
    save_policy,
    set_policy_status,
)
from integrations import require_admin_context
from durable_jobs import enqueue
from models import (
    ConnectionPolicyInput,
    ConnectionPolicyResponse,
    ConnectionPreviewRequest,
    ConnectionPreviewResponse,
    ConnectionResourcesResponse,
    JobStatus,
)
from sync_reporting import list_sync_runs

router = APIRouter(prefix="/integrations/{provider}/setup", tags=["connection setup"])


@router.get("/sync-runs")
async def sync_runs(
    provider: str,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> list[dict]:
    return await list_sync_runs(ctx[0], provider)


@router.get("/resources", response_model=ConnectionResourcesResponse)
async def resources(
    provider: str,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> ConnectionResourcesResponse:
    return await discover_resources(*ctx, provider)


@router.get("/policy", response_model=ConnectionPolicyResponse | None)
async def current_policy(
    provider: str,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> ConnectionPolicyResponse | None:
    row = await get_policy(*ctx, provider)
    return policy_response(row) if row else None


@router.post("/preview", response_model=ConnectionPreviewResponse)
async def preview(
    provider: str,
    request: ConnectionPreviewRequest,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> ConnectionPreviewResponse:
    return await preview_policy(*ctx, provider, request)


@router.post("/confirm", response_model=ConnectionPolicyResponse)
async def confirm(
    provider: str,
    request: ConnectionPolicyInput,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> ConnectionPolicyResponse:
    org_id, user_id = ctx
    row = await save_policy(org_id, user_id, provider, request, status="importing")
    job_id = await enqueue(
        "initialize_connection",
        org_id=org_id,
        conversation_id=f"connection:{provider}",
        payload={"policy_id": row.policy_id},
    )
    return policy_response(row, initial_job_ids=[job_id])


@router.post("/pause", response_model=ConnectionPolicyResponse)
async def pause(
    provider: str,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> ConnectionPolicyResponse:
    return policy_response(await set_policy_status(*ctx, provider, "paused"))


@router.post("/resume", response_model=ConnectionPolicyResponse)
async def resume(
    provider: str,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> ConnectionPolicyResponse:
    return policy_response(await set_policy_status(*ctx, provider, "active"))


@router.delete("")
async def disconnect(
    provider: str,
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> dict[str, str]:
    await disconnect_controlled_connection(*ctx, provider)
    return {"status": "disconnected"}
