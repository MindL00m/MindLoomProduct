"""Admin setup for the per-organization Slack bot, plus the Events API webhook."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.durable_jobs import enqueue
from keeper.auth import get_user_by_id, require_admin_context, require_user_context
from weaver import slack

router = APIRouter(tags=["slack"])


class SlackCredentials(BaseModel):
    bot_token: str = Field(min_length=10)
    signing_secret: str = Field(min_length=8)


class SlackSettings(BaseModel):
    ingest_channel_ids: list[str] = Field(default_factory=list, max_length=200)
    ingest_mention_threads: bool = True
    answer_mentions: bool = True
    unlinked_user_policy: Literal["ignore", "org"] = "ignore"


class SlackBackfill(BaseModel):
    days: Literal[1, 7, 30] = 7


class SlackTestMessage(BaseModel):
    channel: str = Field(min_length=1, max_length=32)


@router.get("/integrations/slack")
async def slack_status(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    """Connection status. Secrets, the event URL, and the manifest are admin-only."""

    org_id, user_id = ctx
    user = await get_user_by_id(org_id, user_id)
    row = await slack.fetch_install_for_org(org_id)
    return slack.installation_view(row, admin=user is not None and user.role == "admin")


@router.post("/integrations/slack/draft")
async def slack_draft(
    ctx: Annotated[tuple[str, str], Depends(require_admin_context)],
) -> dict:
    """Reserve an installation id and return the manifest Slack should call."""

    return await slack.create_draft(ctx[0], ctx[1])


@router.put("/integrations/slack/credentials")
async def slack_credentials(
    body: SlackCredentials,
    ctx: Annotated[tuple[str, str], Depends(require_admin_context)],
) -> dict:
    return await slack.save_credentials(ctx[0], body.bot_token, body.signing_secret)


@router.get("/integrations/slack/channels")
async def slack_channels(
    ctx: Annotated[tuple[str, str], Depends(require_admin_context)],
) -> dict:
    return {"channels": await slack.list_joined_channels(ctx[0])}


@router.put("/integrations/slack/settings")
async def slack_settings(
    body: SlackSettings,
    ctx: Annotated[tuple[str, str], Depends(require_admin_context)],
) -> dict:
    return await slack.save_settings(ctx[0], body.model_dump())


@router.post("/integrations/slack/backfill")
async def slack_backfill(
    body: SlackBackfill,
    ctx: Annotated[tuple[str, str], Depends(require_admin_context)],
) -> dict:
    row = await slack.fetch_install_for_org(ctx[0])
    if row is None or row.get("status") != "active":
        raise HTTPException(status_code=409, detail="Connect Slack before importing history.")
    job_id = await enqueue(
        "slack_backfill",
        org_id=ctx[0],
        conversation_id=f"slack:{row['install_id']}",
        payload={"install_id": row["install_id"], "days": body.days},
    )
    return {"job_id": job_id}


@router.post("/integrations/slack/test")
async def slack_test(
    body: SlackTestMessage,
    ctx: Annotated[tuple[str, str], Depends(require_admin_context)],
) -> dict:
    return await slack.send_test_message(ctx[0], body.channel.strip())


@router.delete("/integrations/slack")
async def slack_disconnect(
    ctx: Annotated[tuple[str, str], Depends(require_admin_context)],
) -> dict:
    """Remove the Slack connection. Knowledge already ingested stays."""

    return await slack.disconnect(ctx[0])


@router.post("/webhooks/slack/events/{install_id}")
async def slack_events(
    install_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Slack Events API. Ack immediately; answer and ingest after the response."""

    body = await request.body()
    return await slack.accept_slack_request(
        install_id,
        body,
        request.headers,
        background_tasks.add_task,
    )
