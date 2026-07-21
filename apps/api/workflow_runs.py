"""Run approved workflows by driving the OpenClaw browser agent.

Loom owns run orchestration, persistence, and the Workflows UI; OpenClaw owns
browser execution. A run shells out to the OpenClaw CLI (``openclaw agent``),
which is the gateway client, so the browser turn happens on the host that has
the right Chrome session. Runs are persisted as JSONL under the capture storage
root (latest-wins per ``run_id``), mirroring how skill files are stored.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import get_settings
from models import SkillFileDraft, WorkflowRun, WorkflowRunStep
from openclaw_export import is_extension_skill, render_skill_md

logger = logging.getLogger(__name__)


def _runs_path() -> Path:
    root = Path(get_settings().capture_storage_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / "workflow_runs.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value) + "\n")


def _persist(run: WorkflowRun) -> None:
    _append_jsonl(_runs_path(), run.model_dump(mode="json"))


def list_runs(skill_id: str | None = None) -> list[WorkflowRun]:
    """Return runs (latest state per run_id), newest first, optionally filtered."""

    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(_runs_path()):
        latest[str(row["run_id"])] = row
    runs = [WorkflowRun.model_validate(row) for row in latest.values()]
    if skill_id is not None:
        runs = [run for run in runs if run.skill_id == skill_id]
    return sorted(runs, key=lambda run: run.updated_at, reverse=True)


def get_run(run_id: str) -> WorkflowRun | None:
    return next((run for run in list_runs() if run.run_id == run_id), None)


def _workflow_instructions(skill: SkillFileDraft) -> str:
    """The SKILL.md body (without YAML frontmatter) as run instructions."""

    rendered = render_skill_md(skill)
    parts = rendered.split("---\n", 2)
    return parts[2].strip() if len(parts) == 3 else rendered.strip()


def build_prompt(skill: SkillFileDraft) -> str:
    return (
        "Execute the following approved company workflow using the OpenClaw "
        "`browser` tool.\n\n"
        "The `browser` tool IS available and already controls a real Chrome window "
        "that is signed in to the required accounts. Do not claim the browser tool "
        "is missing or unavailable, and do not run any onboarding — begin immediately "
        "by opening the workflow's starting URL with the `browser` tool. This is an "
        "authorized run of an approved company workflow; proceed unless a genuine "
        "on-page blocker (login, 2FA, permission prompt, or a listed warning) stops "
        "you.\n\n"
        "Rules:\n"
        "- Use the `browser` tool to actually perform each step in a real browser; "
        "do not just describe the steps.\n"
        "- Snapshot the page before acting, and again after each navigation or form "
        "submission, so you act on fresh element refs.\n"
        "- Follow the steps in order. Stop and ask if a step is ambiguous, a warning "
        "applies, or the page requires manual login, 2FA, or a permission prompt.\n"
        "- Do not take destructive actions beyond what the workflow specifies.\n"
        "- When finished, reply with a short summary of what you did and the final "
        "on-screen result.\n\n"
        f"{_workflow_instructions(skill)}"
    )


def create_run(skill: SkillFileDraft) -> WorkflowRun:
    """Create a queued run record for an approved extension workflow."""

    now = datetime.now(timezone.utc)
    settings = get_settings()
    run = WorkflowRun(
        run_id=str(uuid4()),
        skill_id=skill.skill_id,
        skill_title=skill.title,
        application=skill.application,
        status="queued",
        model=settings.openclaw_run_model,
        browser_profile=settings.openclaw_browser_profile,
        prompt=build_prompt(skill),
        created_at=now,
        updated_at=now,
        org_id=skill.org_id,
    )
    _persist(run)
    return run


def _parse_agent_json(stdout: str) -> dict[str, Any] | None:
    """Parse the ``openclaw agent --json`` object from stdout."""

    stdout = stdout.strip()
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(stdout[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def _apply_result(run: WorkflowRun, payload: dict[str, Any], returncode: int) -> None:
    result = payload.get("result") if isinstance(payload, dict) else None
    result = result if isinstance(result, dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    payloads = result.get("payloads") if isinstance(result.get("payloads"), list) else []

    steps: list[WorkflowRunStep] = []
    screenshots: list[str] = []
    for item in payloads:
        if not isinstance(item, dict):
            continue
        media = item.get("mediaUrl")
        text = str(item.get("text") or "").strip()
        if media:
            screenshots.append(str(media))
        if text or media:
            steps.append(WorkflowRunStep(text=text, screenshot_url=str(media) if media else None))

    run.summary = str(payload.get("summary") or "")
    run.result_text = str(meta.get("finalAssistantVisibleText") or "")
    run.stop_reason = str(meta.get("stopReason") or "")
    run.steps = steps
    run.screenshots = screenshots

    status = str(payload.get("status") or "")
    if returncode == 0 and status == "ok":
        run.status = "succeeded"
    else:
        run.status = "failed"
        run.error = run.error or f"OpenClaw run status={status or 'unknown'} (exit {returncode})."


async def execute_run(run_id: str) -> None:
    """Drive one workflow run to completion. Safe to call as a background task."""

    run = get_run(run_id)
    if run is None:
        logger.error("Workflow run not found for execution: %s", run_id)
        return

    settings = get_settings()
    run.status = "running"
    run.updated_at = datetime.now(timezone.utc)
    _persist(run)

    args = [
        settings.openclaw_cli,
        "agent",
        "--agent",
        settings.openclaw_agent_id,
        "--session-key",
        f"agent:{settings.openclaw_agent_id}:loom-run-{run_id}",
        "--model",
        run.model,
        "--timeout",
        str(settings.openclaw_run_timeout_seconds),
        "--json",
        "--message",
        run.prompt,
    ]
    if settings.openclaw_gateway_url:
        args += ["--url", settings.openclaw_gateway_url]
    if settings.openclaw_token:
        args += ["--token", settings.openclaw_token]

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Allow generous headroom over the agent's own --timeout before we give up.
        stdout_b, stderr_b = await asyncio.wait_for(
            process.communicate(),
            timeout=settings.openclaw_run_timeout_seconds + 60,
        )
        stdout = stdout_b.decode("utf-8", "replace")
        stderr = stderr_b.decode("utf-8", "replace")
        payload = _parse_agent_json(stdout)
        if payload is None:
            run.status = "failed"
            run.error = (stderr.strip() or stdout.strip() or "No output from OpenClaw agent.")[-2000:]
        else:
            _apply_result(run, payload, process.returncode or 0)
    except asyncio.TimeoutError:
        run.status = "failed"
        run.error = f"Workflow run timed out after {settings.openclaw_run_timeout_seconds}s."
    except FileNotFoundError:
        run.status = "failed"
        run.error = (
            f"OpenClaw CLI '{settings.openclaw_cli}' not found. Install OpenClaw on "
            "the host running the Loom API, or set OPENCLAW_CLI."
        )
    except Exception as exc:  # noqa: BLE001 - never let a run crash the worker
        logger.exception("Workflow run failed: %s", run_id)
        run.status = "failed"
        run.error = str(exc)

    run.updated_at = datetime.now(timezone.utc)
    _persist(run)
    logger.info("Workflow run %s finished: %s", run_id, run.status)
