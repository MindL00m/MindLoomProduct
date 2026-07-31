"""Workspace (group chat) API routes."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from integrations import require_user_context
from workspaces import (
    create_workspace,
    list_workspace_members,
    list_workspace_messages,
    list_workspaces,
    resync_workspace_context,
    send_workspace_message,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class CreateWorkspaceInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    member_user_ids: list[str] = Field(default_factory=list)
    purpose: str | None = None
    context_md: str | None = None
    loombot_mode: str | None = None


class SendWorkspaceMessageInput(BaseModel):
    message: str = Field(min_length=1)


@router.get("")
async def workspaces(
    ctx: tuple[str, str] = Depends(require_user_context),
) -> list[dict]:
    return await list_workspaces(*ctx)


@router.post("")
async def create(
    request: CreateWorkspaceInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await create_workspace(
        org_id=ctx[0],
        user_id=ctx[1],
        name=request.name,
        member_user_ids=request.member_user_ids,
        purpose=request.purpose,
        context_md=request.context_md,
        loombot_mode=request.loombot_mode,
    )


@router.post("/{workspace_id}/resync-context")
async def resync_context(
    workspace_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await resync_workspace_context(
        org_id=ctx[0],
        user_id=ctx[1],
        workspace_id=workspace_id,
    )


@router.get("/{workspace_id}/members")
async def members(
    workspace_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> list[dict]:
    return await list_workspace_members(*ctx, workspace_id)


@router.get("/{workspace_id}/messages")
async def messages(
    workspace_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> list[dict]:
    return await list_workspace_messages(*ctx, workspace_id)


@router.post("/{workspace_id}/messages")
async def send_message(
    workspace_id: str,
    request: SendWorkspaceMessageInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await send_workspace_message(
        org_id=ctx[0],
        user_id=ctx[1],
        workspace_id=workspace_id,
        body=request.message,
    )
