"""NetSuite and SAP beyond connecting: approving a change Ask drafted, and knowledge imports."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.durable_jobs import enqueue
from core.models import ProposedErpChange
from core.rate_limit import actions_limit, outbound_limit, rate_limit
from loombot import erp_tools
from weaver import erp_audit, erp_knowledge
from weaver.integrations import require_user_context

router = APIRouter(tags=["erp"])

Context = Annotated[tuple[str, str], Depends(require_user_context)]


class ErpImportInput(BaseModel):
    provider: Literal["netsuite", "sap"]
    name: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=4000)
    options: dict[str, Any] | None = None
    max_rows: int = Field(default=erp_knowledge.DEFAULT_MAX_ROWS, ge=1, le=erp_knowledge.MAX_ROWS)
    visibility: Literal["me", "org"] = "me"


@router.post("/integrations/erp/apply", dependencies=[rate_limit(outbound_limit, "outbound")])
async def apply_erp_change(body: ProposedErpChange, ctx: Context) -> dict:
    """The person pressed Approve on a change Ask drafted: make it, as them."""

    return await erp_tools.apply_change(*ctx, body)


@router.get("/integrations/erp/changes")
async def list_erp_changes(ctx: Context, limit: int = 30) -> list[dict]:
    """The person's own recent NetSuite and SAP changes made through MindLoom."""

    return await erp_audit.list_changes(*ctx, limit)


@router.get("/integrations/erp/imports")
async def list_erp_imports(ctx: Context) -> list[dict]:
    return await erp_knowledge.list_imports(*ctx)


@router.post("/integrations/erp/imports", dependencies=[rate_limit(actions_limit, "actions")])
async def create_erp_import(body: ErpImportInput, ctx: Context) -> dict:
    """Save an import and queue its first run."""

    created = await erp_knowledge.create_import(*ctx, **body.model_dump())
    await _queue(ctx, created["import_id"])
    return created


@router.post("/integrations/erp/imports/{import_id}/run", dependencies=[rate_limit(actions_limit, "actions")])
async def run_erp_import(import_id: str, ctx: Context) -> dict:
    await erp_knowledge._row(*ctx, import_id)
    return {"job_id": await _queue(ctx, import_id), "status": "queued"}


@router.delete("/integrations/erp/imports/{import_id}")
async def delete_erp_import(import_id: str, ctx: Context) -> dict:
    return await erp_knowledge.delete_import(*ctx, import_id)


async def _queue(ctx: tuple[str, str], import_id: str) -> str:
    return await enqueue("erp_import", org_id=ctx[0], conversation_id=f"erp:{import_id}",
                         payload={"user_id": ctx[1], "import_id": import_id})
