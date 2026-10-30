"""Recurring workflows: emails sent from the user's Gmail at a fixed interval."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from keeper.auth import require_user_context
from shuttle import recurring_emails

from core.rate_limit import actions_limit, outbound_limit, rate_limit

router = APIRouter(prefix="/workflows/recurring-emails", tags=["workflows"])

Unit = Literal["minutes", "hours", "days", "weeks"]


class CreateRecurringEmail(BaseModel):
    recipient_email: str = Field(min_length=3, max_length=320)
    recipient_name: str | None = Field(default=None, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=20_000)
    interval_value: int = Field(ge=1, le=100_000)
    interval_unit: Unit
    start_at: datetime | None = None
    refresh_instructions: str | None = Field(default=None, max_length=1000)
    timezone: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=120)


class UpdateRecurringEmail(BaseModel):
    status: Literal["active", "paused"] | None = None
    recipient_email: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=20_000)
    interval_value: int | None = Field(default=None, ge=1, le=100_000)
    interval_unit: Unit | None = None
    refresh_instructions: str | None = Field(default=None, max_length=1000)


@router.get("")
async def list_workflows(ctx: tuple[str, str] = Depends(require_user_context)) -> list[dict]:
    return await recurring_emails.list_recurring_emails(*ctx)


@router.post("", status_code=201, dependencies=[rate_limit(outbound_limit, "outbound")])
async def create_workflow(
    body: CreateRecurringEmail,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Schedule a recurring email (the user approved it in Ask)."""

    return await recurring_emails.create_recurring_email(
        *ctx,
        recipient_email=body.recipient_email,
        recipient_name=body.recipient_name,
        subject=body.subject,
        body=body.body,
        interval_value=body.interval_value,
        interval_unit=body.interval_unit,
        start_at=body.start_at,
        refresh_instructions=body.refresh_instructions,
        timezone_name=body.timezone,
        name=body.name,
    )


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: str, ctx: tuple[str, str] = Depends(require_user_context)
) -> dict:
    return await recurring_emails.get_recurring_email(*ctx, workflow_id)


@router.patch("/{workflow_id}")
async def update_workflow(
    workflow_id: str,
    body: UpdateRecurringEmail,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await recurring_emails.update_recurring_email(
        *ctx, workflow_id, body.model_dump(exclude_unset=True)
    )


@router.post("/{workflow_id}/send-now", dependencies=[rate_limit(outbound_limit, "outbound")])
async def send_workflow_now(
    workflow_id: str, ctx: tuple[str, str] = Depends(require_user_context)
) -> dict:
    """Send one email right away; the schedule is unchanged."""

    return await recurring_emails.send_now(*ctx, workflow_id)


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: str, ctx: tuple[str, str] = Depends(require_user_context)
) -> Response:
    await recurring_emails.delete_recurring_email(*ctx, workflow_id)
    return Response(status_code=204)
