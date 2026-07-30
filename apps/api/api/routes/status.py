"""Status board endpoints for open projects, reports, and action items."""

from fastapi import APIRouter, Depends, HTTPException

from integrations import require_user_context
from models import FinishStatusItemResponse, OpenStatusResponse, StatusItemKind
from status_board import get_open_status, mark_status_item_finished

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
