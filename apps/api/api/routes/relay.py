"""The relay's side of ``weaver/relay.py``: collect waiting requests, post answers."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from weaver import relay
from weaver.integrations import require_user_context

router = APIRouter(tags=["relay"])

Context = Annotated[tuple[str, str], Depends(require_user_context)]


class RelayResult(BaseModel):
    status: int | None = Field(default=None, ge=100, le=599)
    headers: list[list[str]] = Field(default_factory=list, max_length=100)
    body_base64: str | None = Field(default=None, max_length=1_400_000)
    error: str | None = Field(default=None, max_length=500)


RelayKey = Annotated[str, Header(alias="X-MindLoom-Relay-Key", max_length=128)]


@router.get("/relay/requests")
async def relay_requests(ctx: Context, relay_key: RelayKey = "") -> dict:
    """Requests waiting for this person's paired relay. Calling this marks the relay as running."""

    return await relay.claim(*ctx, relay_key)


@router.post("/relay/requests/{request_id}/result")
async def relay_result(request_id: str, body: RelayResult, ctx: Context, relay_key: RelayKey = "") -> dict:
    return await relay.complete(*ctx, request_id, relay_key, status_code=body.status, headers=body.headers,
                                body_base64=body.body_base64, error=body.error)


@router.get("/relay/status")
async def relay_status(ctx: Context) -> dict:
    """Whether this person has a paired relay and it was seen in the last minute and a half."""

    return await relay.status(*ctx)
