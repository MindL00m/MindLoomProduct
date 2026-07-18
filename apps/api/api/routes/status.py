"""Status board endpoints for open projects, reports, and action items."""

from fastapi import APIRouter, Depends

from integrations import require_user_context
from models import OpenStatusResponse
from status_board import get_open_status

router = APIRouter(prefix="/status", tags=["status"])


@router.get("/open", response_model=OpenStatusResponse)
async def open_status(
    ctx: tuple[str, str] = Depends(require_user_context),
) -> OpenStatusResponse:
    """List unfinished projects, reports, and action items from ingested knowledge."""

    org_id, user_id = ctx
    return await get_open_status(org_id, user_id)
