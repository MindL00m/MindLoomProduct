"""Workspace (group chat) API routes."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from weaver.integrations import require_user_context
from loombot.workspaces import (
    add_members,
    cancel_workspace_invite,
    create_workspace,
    invite_members_by_email,
    list_workspace_invites,
    list_workspace_members,
    list_workspace_messages,
    list_workspaces,
    remove_member,
    resync_workspace_context,
    send_workspace_message,
    update_card_state,
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
    # The sender's IANA timezone, so Loombot schedules meetings in their local time.
    timezone: str | None = Field(default=None, max_length=64)
    # LoomBot Files the sender attached for Loombot to read with this message.
    attached_file_names: list[str] = Field(default_factory=list, max_length=10)


class AddMembersInput(BaseModel):
    member_user_ids: list[str] = Field(min_length=1, max_length=100)


class InviteByEmailInput(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=50)
    send_email: bool = True


class CardStateInput(BaseModel):
    state: dict = Field(default_factory=dict)


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


@router.post("/{workspace_id}/members")
async def add_workspace_members(
    workspace_id: str,
    request: AddMembersInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Add signed-in colleagues to an existing workspace (any member can add people)."""

    return await add_members(
        org_id=ctx[0],
        user_id=ctx[1],
        workspace_id=workspace_id,
        member_user_ids=request.member_user_ids,
    )


@router.post("/{workspace_id}/members/by-email")
async def add_members_by_email(
    workspace_id: str,
    request: InviteByEmailInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Add colleagues by email; those who have not signed in yet are invited."""

    return await invite_members_by_email(
        org_id=ctx[0],
        user_id=ctx[1],
        workspace_id=workspace_id,
        emails=request.emails,
        send_email=request.send_email,
    )


@router.delete("/{workspace_id}/members/{member_user_id}")
async def remove_workspace_member(
    workspace_id: str,
    member_user_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Leave the workspace, or (as its creator) remove someone."""

    return await remove_member(
        org_id=ctx[0], user_id=ctx[1], workspace_id=workspace_id, member_user_id=member_user_id
    )


@router.get("/{workspace_id}/invites")
async def invites(
    workspace_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> list[dict]:
    return await list_workspace_invites(*ctx, workspace_id)


@router.delete("/{workspace_id}/invites/{invite_id}")
async def cancel_invite(
    workspace_id: str,
    invite_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await cancel_workspace_invite(
        org_id=ctx[0], user_id=ctx[1], workspace_id=workspace_id, invite_id=invite_id
    )


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
        user_timezone=request.timezone,
        attached_file_names=request.attached_file_names,
    )


@router.put("/{workspace_id}/messages/{message_id}/card-state")
async def card_state(
    workspace_id: str,
    message_id: str,
    request: CardStateInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Save what the asker did on a Loombot reply's cards (sent, scheduled, undone)."""

    return await update_card_state(
        org_id=ctx[0],
        user_id=ctx[1],
        workspace_id=workspace_id,
        message_id=message_id,
        state=request.state,
    )
