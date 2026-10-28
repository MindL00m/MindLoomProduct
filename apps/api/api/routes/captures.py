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
from weaver.skill_runs import (
    SkillRunConflict,
    SkillRunNotFound,
    advance_skill_run,
    claim_skill_run,
    create_skill_run,
    get_skill_run,
    list_skill_runs,
    stop_skill_run,
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
    SkillRun,
    SkillRunAdvance,
    SkillRunCreate,
    SkillRunDetail,
    SkillRunStatus,
)
from core.rate_limit import captures_limit, limit

router = APIRouter(prefix="/captures", tags=["browser captures"])


def _skill_http_error(exc: Exception) -> HTTPException:
    detail = str(exc)
    if isinstance(exc, SkillAccessError):
        return HTTPException(status_code=403, detail=detail)
    status = 404 if "not found" in detail.lower() else 400
    return HTTPException(status_code=status, detail=detail)


def _run_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SkillRunNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, SkillRunConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


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


# --- Skill runs ----------------------------------------------------------------
# The API only stores the run. The Mac app claims and walks it; the Chrome extension
# performs browser steps. Every route acts as, and only sees runs of, the caller.

_RUN_ERRORS = (SkillRunNotFound, SkillRunConflict, ValueError)


@router.post("/skill-runs", response_model=SkillRun, status_code=201)
async def start_skill_run(
    body: Annotated[SkillRunCreate, Body()],
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> SkillRun:
    """Queue a run of an approved skill the caller can view, at step 0."""

    org_id, user_id = ctx
    try:
        return await create_skill_run(body.skill_id, org_id=org_id, user_id=user_id)
    except _RUN_ERRORS as exc:
        raise _run_http_error(exc) from exc


@router.get("/skill-runs", response_model=list[SkillRun])
async def skill_runs(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    status: Annotated[SkillRunStatus | None, Query()] = None,
) -> list[SkillRun]:
    """The caller's runs, newest first; the Mac app polls ``?status=pending``."""

    org_id, user_id = ctx
    return await list_skill_runs(org_id=org_id, user_id=user_id, status=status)


@router.get("/skill-runs/{run_id}", response_model=SkillRunDetail)
async def skill_run(
    run_id: str,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> SkillRunDetail:
    org_id, user_id = ctx
    try:
        return await get_skill_run(run_id, org_id=org_id, user_id=user_id)
    except _RUN_ERRORS as exc:
        raise _run_http_error(exc) from exc


@router.post("/skill-runs/{run_id}/claim", response_model=SkillRun)
async def claim_run(
    run_id: str,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> SkillRun:
    """Mac app takes a pending run. A second claim gets 409."""

    org_id, user_id = ctx
    try:
        return await claim_skill_run(run_id, org_id=org_id, user_id=user_id)
    except _RUN_ERRORS as exc:
        raise _run_http_error(exc) from exc


@router.post("/skill-runs/{run_id}/advance", response_model=SkillRun)
async def advance_run(
    run_id: str,
    body: Annotated[SkillRunAdvance, Body()],
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> SkillRun:
    """Report the current step as done, waiting, or failed. Other steps get 409."""

    org_id, user_id = ctx
    try:
        return await advance_skill_run(run_id, body, org_id=org_id, user_id=user_id)
    except _RUN_ERRORS as exc:
        raise _run_http_error(exc) from exc


@router.post("/skill-runs/{run_id}/stop", response_model=SkillRun)
async def stop_run(
    run_id: str,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> SkillRun:
    org_id, user_id = ctx
    try:
        return await stop_skill_run(run_id, org_id=org_id, user_id=user_id)
    except _RUN_ERRORS as exc:
        raise _run_http_error(exc) from exc


# --- Automations: MindLoom performs a Skill File with tools ---------------------------

from pydantic import BaseModel, Field  # noqa: E402

from shuttle import skill_automation  # noqa: E402


class AutomationSettingsInput(BaseModel):
    auto_approve: bool | None = None
    schedule: bool | None = None
    interval_value: int | None = Field(default=None, ge=1, le=100_000)
    interval_unit: str | None = None
    default_inputs: dict[str, str] | None = None


class AutomationRunInput(BaseModel):
    inputs: dict[str, str] = Field(default_factory=dict)
    timezone: str | None = Field(default=None, max_length=64)


class AutomationReplyInput(BaseModel):
    action: str = Field(pattern="^(approve|reject|answer|continue)$")
    arguments: dict | None = None
    answer: str | None = Field(default=None, max_length=4000)
    reason: str | None = Field(default=None, max_length=500)


@router.get("/skill-files/{skill_id}/automation")
async def skill_automation_plan(
    skill_id: str, ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    """The caller's automation plan for a skill, or null when not prepared yet."""

    await skill_automation._skill(*ctx, skill_id)
    return {"automation": await skill_automation.get_automation(*ctx, skill_id),
            "runs": await skill_automation.list_runs(*ctx, skill_id, limit=10)}


@router.post("/skill-files/{skill_id}/automation")
@limit(captures_limit)
async def prepare_skill_automation(
    request: Request, skill_id: str, ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    """Write (or rewrite) the plan: which tool does each step, which inputs a run needs."""

    return await skill_automation.prepare_automation(*ctx, skill_id)


@router.patch("/skill-files/{skill_id}/automation")
async def skill_automation_settings(
    skill_id: str, body: AutomationSettingsInput,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    return await skill_automation.update_automation_settings(
        *ctx, skill_id, auto_approve=body.auto_approve, schedule=body.schedule,
        interval_value=body.interval_value, interval_unit=body.interval_unit,
        default_inputs=body.default_inputs,
    )


@router.post("/skill-files/{skill_id}/automation/runs", status_code=201)
@limit(captures_limit)
async def run_skill_automation(
    request: Request, skill_id: str, body: AutomationRunInput,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    """Start MindLoom doing the skill now; the run pauses for approvals and questions."""

    return await skill_automation.start_run(*ctx, skill_id, inputs=body.inputs, timezone_name=body.timezone)


@router.get("/automation-runs/{run_id}")
async def automation_run(
    run_id: str, ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    return await skill_automation.get_run(*ctx, run_id)


@router.post("/automation-runs/{run_id}/reply")
async def reply_to_automation_run(
    run_id: str, body: AutomationReplyInput,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    """Approve (optionally with edited details), reject, answer a question, or continue."""

    return await skill_automation.respond(
        *ctx, run_id, action=body.action, arguments=body.arguments, answer=body.answer,
        reason=body.reason,
    )


@router.post("/automation-runs/{run_id}/stop")
async def stop_automation_run(
    run_id: str, ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    return await skill_automation.stop_run(*ctx, run_id)
