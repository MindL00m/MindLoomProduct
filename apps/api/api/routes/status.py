"""Status endpoints: Ask-requested status trackers, plus the legacy open-work board."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from weaver.integrations import require_user_context
from core.models import FinishStatusItemResponse, OpenStatusResponse, StatusItemKind
from lens import status_trackers
from lens.status_board import get_open_status, mark_status_item_finished

router = APIRouter(prefix="/status", tags=["status"])


@router.get("/open", response_model=OpenStatusResponse)
async def open_status(
    ctx: tuple[str, str] = Depends(require_user_context),
) -> OpenStatusResponse:
    """List unfinished projects, reports, and action items from ingested knowledge."""

    org_id, user_id = ctx
    return await get_open_status(org_id, user_id)


@router.post(
    "/{kind}/{item_id}/finish",
    response_model=FinishStatusItemResponse,
)
async def finish_status_item(
    kind: StatusItemKind,
    item_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> FinishStatusItemResponse:
    """Mark a status-board item finished so it no longer appears as open."""

    org_id, user_id = ctx
    try:
        return await mark_status_item_finished(org_id, user_id, kind, item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class CreateTrackerInput(BaseModel):
    subject: str = Field(min_length=2, max_length=500)
    title: str | None = Field(default=None, max_length=120)


class UpdateTrackerInput(BaseModel):
    archived: bool


@router.get("/trackers")
async def list_trackers(ctx: tuple[str, str] = Depends(require_user_context)) -> list[dict]:
    """The caller's status trackers, most recently updated first."""

    return await status_trackers.list_trackers(*ctx)


@router.post("/trackers", status_code=201)
async def create_tracker(
    body: CreateTrackerInput,
    response: Response,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Start tracking something (returns the existing tracker for the same subject)."""

    tracker, created = await status_trackers.create_tracker(*ctx, body.subject, body.title)
    if not created:
        response.status_code = 200
    return {**tracker, "created": created}


@router.get("/trackers/{tracker_id}")
async def get_tracker(
    tracker_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """One tracker with its full update history, newest first."""

    return await status_trackers.get_tracker(*ctx, tracker_id)


@router.patch("/trackers/{tracker_id}")
async def update_tracker(
    tracker_id: str,
    body: UpdateTrackerInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Pause (archive) or resume a tracker."""

    return await status_trackers.set_archived(*ctx, tracker_id, body.archived)


@router.delete("/trackers/{tracker_id}", status_code=204)
async def delete_tracker(
    tracker_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> Response:
    """Stop tracking and delete the tracker's history."""

    await status_trackers.delete_tracker(*ctx, tracker_id)
    return Response(status_code=204)
