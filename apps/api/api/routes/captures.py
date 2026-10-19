"""Browser and desktop activity capture HTTP routes."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request

from keeper.auth import require_user_context
from weaver.capture_service import (
    SkillAccessError,
    analyze_activity_session,
    analyze_capture_session,
    ensure_activity_skill,
    get_guide,
    list_activity_sessions,
    list_captures,
    list_guides,
    list_skill_files,
    list_summaries,
    review_skill_file,
    save_activity_session,
    save_capture,
    summarize_capture,
    update_skill_file,
)
from core.models import (
    ActivitySessionCreate,
    ActivitySessionRecord,
    CaptureCreate,
    CaptureRecord,
    GuideSummary,
    SkillFileDraft,
    SkillFileReview,
    SkillFileUpdate,
)
from core.rate_limit import captures_limit, limit

router = APIRouter(prefix="/captures", tags=["browser captures"])


def _skill_http_error(exc: Exception) -> HTTPException:
    detail = str(exc)
    if isinstance(exc, SkillAccessError):
        return HTTPException(status_code=403, detail=detail)
    status = 404 if "not found" in detail.lower() else 400
    return HTTPException(status_code=status, detail=detail)


@router.post("", response_model=CaptureRecord, status_code=201)
@limit(captures_limit)
async def create_capture(
    request: Request,
    capture: Annotated[CaptureCreate, Body()],
    background_tasks: BackgroundTasks,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> CaptureRecord:
    """Persist a user-approved screenshot and queue its vision summary."""

    org_id, user_id = ctx
    try:
        record = await save_capture(capture, org_id=org_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(summarize_capture, record)
    return record


@router.get("")
async def get_captures(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> list[dict[str, object]]:
    org_id, _ = ctx
    return await list_captures(org_id=org_id)


@router.get("/summaries")
async def get_capture_summaries(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> list[dict[str, object]]:
    org_id, _ = ctx
    return await list_summaries(org_id=org_id)


@router.post("/activity-sessions", response_model=ActivitySessionRecord, status_code=201)
@limit(captures_limit)
async def create_activity_session(
    request: Request,
    session: Annotated[ActivitySessionCreate, Body()],
    background_tasks: BackgroundTasks,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> ActivitySessionRecord:
    """Persist on-device desktop activity task summaries (no raw events or pixels).

    Also queues a Skill File draft so the session appears under Workflows without a
    separate analyze click (explicit /analyze remains idempotent).
    """

    org_id, user_id = ctx
    try:
        record = await save_activity_session(session, org_id=org_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(ensure_activity_skill, record.session_id, org_id=org_id)
    return record


@router.get("/activity-sessions")
async def get_activity_sessions(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> list[dict[str, object]]:
    org_id, _ = ctx
    return await list_activity_sessions(org_id=org_id)


@router.post("/activity-sessions/{session_id}/analyze", response_model=SkillFileDraft)
@limit(captures_limit)
async def analyze_activity(
    request: Request,
    session_id: str,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> SkillFileDraft:
    """Draft a Skill File from desktop activity aggregates (text-only)."""

    org_id, _ = ctx
    try:
        return await analyze_activity_session(session_id, org_id=org_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/analyze", response_model=SkillFileDraft)
@limit(captures_limit)
async def analyze_session(
    request: Request,
    session_id: str,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> SkillFileDraft:
    org_id, _ = ctx
    try:
        return await analyze_capture_session(session_id, org_id=org_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/skill-files")
async def skill_files(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> list[dict[str, object]]:
    org_id, user_id = ctx
    return await list_skill_files(org_id=org_id, viewer_user_id=user_id)


@router.patch("/skill-files/{skill_id}", response_model=SkillFileDraft)
async def patch_skill(
    skill_id: str,
    update: Annotated[SkillFileUpdate, Body()],
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> SkillFileDraft:
    org_id, user_id = ctx
    try:
        return await update_skill_file(
            skill_id, update, org_id=org_id, actor_user_id=user_id
        )
    except (ValueError, SkillAccessError) as exc:
        raise _skill_http_error(exc) from exc


@router.get("/guides", response_model=list[GuideSummary])
async def guides(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    bundle_id: Annotated[str | None, Query()] = None,
) -> list[GuideSummary]:
    """Approved Skill Files shaped for Loom Capture walkthroughs."""

    org_id, user_id = ctx
    return await list_guides(org_id=org_id, viewer_user_id=user_id, bundle_id=bundle_id)


@router.get("/guides/{skill_id}", response_model=GuideSummary)
async def guide(
    skill_id: str,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> GuideSummary:
    org_id, user_id = ctx
    try:
        return await get_guide(skill_id, org_id=org_id, viewer_user_id=user_id)
    except ValueError as exc:
        raise _skill_http_error(exc) from exc


@router.post("/skill-files/{skill_id}/review", response_model=SkillFileDraft)
async def review_skill(
    skill_id: str,
    review: Annotated[SkillFileReview, Body()],
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> SkillFileDraft:
    org_id, user_id = ctx
    try:
        return await review_skill_file(
            skill_id, review, org_id=org_id, actor_user_id=user_id
        )
    except (ValueError, SkillAccessError) as exc:
        raise _skill_http_error(exc) from exc
