"""Browser and desktop activity capture HTTP routes."""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from capture_service import (
    analyze_activity_session,
    analyze_capture_session,
    list_activity_sessions,
    list_captures,
    list_skill_files,
    list_summaries,
    review_skill_file,
    save_activity_session,
    save_capture,
    summarize_capture,
    update_skill_file,
)
from models import (
    ActivitySessionCreate,
    ActivitySessionRecord,
    CaptureCreate,
    CaptureRecord,
    SkillFileDraft,
    SkillFileReview,
    SkillFileUpdate,
)

router = APIRouter(prefix="/captures", tags=["browser captures"])


@router.post("", response_model=CaptureRecord, status_code=201)
async def create_capture(
    capture: CaptureCreate,
    background_tasks: BackgroundTasks,
) -> CaptureRecord:
    """Persist a user-approved screenshot and queue its vision summary."""

    try:
        record = save_capture(capture)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(summarize_capture, record)
    return record


@router.get("")
async def get_captures() -> list[dict[str, object]]:
    return list_captures()


@router.get("/summaries")
async def get_capture_summaries() -> list[dict[str, object]]:
    return list_summaries()


@router.post("/activity-sessions", response_model=ActivitySessionRecord, status_code=201)
async def create_activity_session(session: ActivitySessionCreate) -> ActivitySessionRecord:
    """Persist on-device desktop activity task summaries (no raw events or pixels)."""

    try:
        return save_activity_session(session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/activity-sessions")
async def get_activity_sessions() -> list[dict[str, object]]:
    return list_activity_sessions()


@router.post("/activity-sessions/{session_id}/analyze", response_model=SkillFileDraft)
async def analyze_activity(session_id: str) -> SkillFileDraft:
    """Draft a Skill File from desktop activity aggregates (text-only)."""

    try:
        return await analyze_activity_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/analyze", response_model=SkillFileDraft)
async def analyze_session(session_id: str) -> SkillFileDraft:
    try:
        return await analyze_capture_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/skill-files")
async def skill_files() -> list[dict[str, object]]:
    return list_skill_files()


@router.patch("/skill-files/{skill_id}", response_model=SkillFileDraft)
async def patch_skill(skill_id: str, update: SkillFileUpdate) -> SkillFileDraft:
    try:
        return await update_skill_file(skill_id, update)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc


@router.post("/skill-files/{skill_id}/review", response_model=SkillFileDraft)
async def review_skill(skill_id: str, review: SkillFileReview) -> SkillFileDraft:
    try:
        return await review_skill_file(skill_id, review)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
