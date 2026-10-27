"""Skill runs: the stored record of an approved Skill File being performed.

The API never performs a step. The Mac app claims a pending run and walks it; the
Chrome extension performs browser steps. Both report through
:func:`advance_skill_run`, which only accepts a report for the run's current step.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from core.database import get_session_factory
from core.models import GuideSummary, SkillRun, SkillRunAdvance, SkillRunDetail
from weaver.capture_service import get_visible_skill, guide_payload

ACTIVE_STATUSES = ("pending", "running", "waiting")
REPORTABLE_STATUSES = ("running", "waiting")
LIST_LIMIT = 50

_COLUMNS = (
    "run_id, org_id, user_id, skill_id, status, step_index, wait_reason, error, "
    "created_at, updated_at"
)


class SkillRunNotFound(LookupError):
    """The run, or the skill it would run, does not exist for this user."""


class SkillRunConflict(Exception):
    """The run is not in a state that accepts this change."""


# --- Optional in-memory backend for unit tests --------------------------------

_memory: dict[str, dict[str, Any]] | None = None


def use_memory_store(enabled: bool = True) -> None:
    """Enable/disable the in-process run store (tests only)."""

    global _memory
    _memory = {} if enabled else None


async def _insert(run: SkillRun) -> None:
    if _memory is not None:
        _memory[run.run_id] = run.model_dump()
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    f"INSERT INTO skill_runs ({_COLUMNS}) VALUES ("
                    ":run_id, :org_id, :user_id, :skill_id, :status, :step_index, "
                    ":wait_reason, :error, :created_at, :updated_at)"
                ),
                run.model_dump(),
            )


async def _fetch(run_id: str, *, org_id: str, user_id: str) -> SkillRun | None:
    if _memory is not None:
        row = _memory.get(run_id)
        if row is None or row["org_id"] != org_id or row["user_id"] != user_id:
            return None
        return SkillRun.model_validate(row)
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            text(
                f"SELECT {_COLUMNS} FROM skill_runs "
                "WHERE run_id = :run_id AND org_id = :org_id AND user_id = :user_id"
            ),
            {"run_id": run_id, "org_id": org_id, "user_id": user_id},
        )
        row = result.mappings().first()
    return SkillRun.model_validate(dict(row)) if row else None


async def _compare_and_set(expected: SkillRun, updated: SkillRun) -> bool:
    """Write ``updated`` only if the stored run still has ``expected``'s status and step."""

    if _memory is not None:
        row = _memory.get(expected.run_id)
        if (
            row is None
            or row["status"] != expected.status
            or row["step_index"] != expected.step_index
        ):
            return False
        _memory[expected.run_id] = updated.model_dump()
        return True
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                text(
                    """
                    UPDATE skill_runs SET
                        status = :status,
                        step_index = :step_index,
                        wait_reason = :wait_reason,
                        error = :error,
                        updated_at = :updated_at
                    WHERE run_id = :run_id AND org_id = :org_id AND user_id = :user_id
                      AND status = :expected_status AND step_index = :expected_step_index
                    """
                ),
                {
                    **updated.model_dump(
                        include={
                            "run_id",
                            "org_id",
                            "user_id",
                            "status",
                            "step_index",
                            "wait_reason",
                            "error",
                            "updated_at",
                        }
                    ),
                    "expected_status": expected.status,
                    "expected_step_index": expected.step_index,
                },
            )
    return result.rowcount == 1


async def _require(run_id: str, *, org_id: str, user_id: str) -> SkillRun:
    run = await _fetch(run_id, org_id=org_id, user_id=user_id)
    if run is None:
        raise SkillRunNotFound("Run was not found.")
    return run


async def _runnable_guide(skill_id: str, *, org_id: str, user_id: str) -> GuideSummary:
    """The guide to perform, only for an approved skill the user may view."""

    skill = await get_visible_skill(skill_id, org_id=org_id, viewer_user_id=user_id)
    if skill is None:
        raise SkillRunNotFound("Skill File was not found.")
    if skill.status != "approved":
        raise ValueError("Only approved Skill Files can run.")
    guide = guide_payload(skill)
    if not guide.steps:
        raise ValueError("This Skill File has no steps to run.")
    return guide


def _next(run: SkillRun, **changes: Any) -> SkillRun:
    return run.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})


async def _guide_or_fail(run: SkillRun) -> GuideSummary:
    """Re-check the skill on every transition; a run whose skill can no longer run fails."""

    try:
        return await _runnable_guide(run.skill_id, org_id=run.org_id, user_id=run.user_id)
    except (SkillRunNotFound, ValueError) as exc:
        reason = f"This Skill File can no longer run: {exc}"
        await _compare_and_set(
            run, _next(run, status="failed", wait_reason=None, error=reason)
        )
        raise SkillRunConflict(reason) from exc


async def create_skill_run(skill_id: str, *, org_id: str, user_id: str) -> SkillRun:
    await _runnable_guide(skill_id, org_id=org_id, user_id=user_id)
    now = datetime.now(timezone.utc)
    run = SkillRun(
        run_id=str(uuid4()),
        org_id=org_id,
        user_id=user_id,
        skill_id=skill_id,
        created_at=now,
        updated_at=now,
    )
    await _insert(run)
    return run


async def get_skill_run(run_id: str, *, org_id: str, user_id: str) -> SkillRunDetail:
    run = await _require(run_id, org_id=org_id, user_id=user_id)
    try:
        guide = await _runnable_guide(run.skill_id, org_id=org_id, user_id=user_id)
    except (SkillRunNotFound, ValueError):
        guide = None
    return SkillRunDetail(**run.model_dump(), guide=guide)


async def list_skill_runs(
    *, org_id: str, user_id: str, status: str | None = None
) -> list[SkillRun]:
    """The user's own runs, newest first."""

    if _memory is not None:
        rows = [
            SkillRun.model_validate(row)
            for row in _memory.values()
            if row["org_id"] == org_id
            and row["user_id"] == user_id
            and (status is None or row["status"] == status)
        ]
        rows.sort(key=lambda run: run.created_at, reverse=True)
        return rows[:LIST_LIMIT]
    where = "org_id = :org_id AND user_id = :user_id"
    params: dict[str, Any] = {"org_id": org_id, "user_id": user_id, "limit": LIST_LIMIT}
    if status is not None:
        where += " AND status = :status"
        params["status"] = status
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            text(
                f"SELECT {_COLUMNS} FROM skill_runs WHERE {where} "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            params,
        )
        return [SkillRun.model_validate(dict(row)) for row in result.mappings().all()]


async def claim_skill_run(run_id: str, *, org_id: str, user_id: str) -> SkillRun:
    """Move a pending run to running. Only one claim can win."""

    run = await _require(run_id, org_id=org_id, user_id=user_id)
    if run.status != "pending":
        raise SkillRunConflict("This run was already claimed.")
    await _guide_or_fail(run)
    claimed = _next(run, status="running", wait_reason=None, error=None)
    if not await _compare_and_set(run, claimed):
        raise SkillRunConflict("This run was already claimed.")
    return claimed


async def advance_skill_run(
    run_id: str, advance: SkillRunAdvance, *, org_id: str, user_id: str
) -> SkillRun:
    """Apply a report for the current step. Reports for any other step are rejected."""

    run = await _require(run_id, org_id=org_id, user_id=user_id)
    if run.status not in REPORTABLE_STATUSES:
        raise SkillRunConflict(f"This run is {run.status}.")
    if advance.step_index != run.step_index:
        raise SkillRunConflict(
            f"Step {advance.step_index} is not the current step ({run.step_index})."
        )
    guide = await _guide_or_fail(run)

    if advance.outcome == "done":
        following = run.step_index + 1
        updated = _next(
            run,
            status="completed" if following >= len(guide.steps) else "running",
            step_index=following,
            wait_reason=None,
            error=None,
        )
    elif advance.outcome == "waiting":
        updated = _next(
            run, status="waiting", wait_reason=advance.wait_reason, error=advance.error
        )
    else:
        updated = _next(run, status="failed", wait_reason=None, error=advance.error)

    if not await _compare_and_set(run, updated):
        raise SkillRunConflict("The run changed before this report arrived.")
    return updated


async def stop_skill_run(run_id: str, *, org_id: str, user_id: str) -> SkillRun:
    """Stop a run that has not finished. Stopping twice is a no-op."""

    for _ in range(3):
        run = await _require(run_id, org_id=org_id, user_id=user_id)
        if run.status == "stopped":
            return run
        if run.status not in ACTIVE_STATUSES:
            raise SkillRunConflict(f"This run already {run.status}.")
        stopped = _next(run, status="stopped", wait_reason=None, error=None)
        if await _compare_and_set(run, stopped):
            return stopped
    raise SkillRunConflict("The run kept changing; try stopping it again.")
