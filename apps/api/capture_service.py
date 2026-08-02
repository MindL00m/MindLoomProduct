"""Persistence and AI summarisation for approved browser screenshots."""

from __future__ import annotations

import base64
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from openai import AsyncOpenAI
from PIL import Image

from config import get_settings
from models import (
    ActivitySessionCreate,
    ActivitySessionRecord,
    CaptureCreate,
    CaptureRecord,
    CaptureSummary,
    Conversation,
    IncomingMessage,
    Participant,
    SkillFileDraft,
    SkillFileReview,
    SkillFileUpdate,
)
from pipeline import DocumentInput
from source_registry import ingest_external_source

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You analyze screenshots of a user's browser activity.
Return only JSON with these keys: app_or_site, action_summary, content_excerpt,
inferred_task_type, confidence. Confidence must be a number from 0 to 1."""

WORKFLOW_PROMPT = """Analyze this ordered sequence of approved browser screenshots as one
work workflow. Do not ask the user to explain each screen. Infer the application,
goal, ordered steps, important fields or warning indicators, decision guidance,
and links to visible projects/processes/customers/systems. Ask only concise
follow-up questions needed to resolve material uncertainty. Return JSON with:
title, purpose, application, context, steps, important_fields, warnings,
decision_guidance, follow_up_questions. Every list value must be a list of strings.
Never invent confidential values that are blurred or absent."""

ACTIVITY_WORKFLOW_PROMPT = """Analyze this ordered sequence of on-device desktop activity
task summaries as one work workflow. The summaries were produced from macOS
Accessibility interaction events (app focus, window/control identity, action type,
duration). Field values and keystroke content were never captured — only that a
field was interacted with and for how long.

Infer the application, goal, ordered steps, important field labels (not values),
warnings, decision guidance, and links to projects/processes/systems suggested by
app and control names. Ask only concise follow-up questions needed to resolve
material uncertainty. Return JSON with: title, purpose, application, context,
steps, important_fields, warnings, decision_guidance, follow_up_questions.
Every list value must be a list of strings.
Never invent confidential field values, identifiers, or typed content that were
not present in the summaries."""


def _paths() -> tuple[Path, Path, Path, Path, Path]:
    root = Path(get_settings().capture_storage_root).resolve()
    images = root / "images"
    images.mkdir(parents=True, exist_ok=True)
    return (
        images,
        root / "captures.jsonl",
        root / "summaries.jsonl",
        root / "skill_files.jsonl",
        root / "activity_sessions.jsonl",
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value) + "\n")


def save_capture(payload: CaptureCreate) -> CaptureRecord:
    """Decode and persist one user-approved extension capture."""

    try:
        header, encoded = payload.data_url.split(",", 1)
        if ";base64" not in header:
            raise ValueError
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid base64 image data URL.") from exc

    images, captures_path, _, _, _ = _paths()
    safe_id = "".join(c for c in payload.id if c.isalnum() or c in "-_")
    if not safe_id:
        raise ValueError("Capture id contains no safe characters.")
    image_path = images / f"{payload.timestamp}_{safe_id}.png"
    image_path.write_bytes(image_bytes)

    record = CaptureRecord(
        id=payload.id,
        timestamp=payload.timestamp,
        url=payload.url,
        tab_title=payload.tab_title,
        window_id=payload.window_id,
        filepath=str(image_path),
        session_id=payload.session_id,
        note=payload.note,
        redactions=payload.redactions,
        org_id=payload.org_id,
        user_id=payload.user_id,
    )
    _append_jsonl(captures_path, record.model_dump())
    return record


def list_captures() -> list[dict[str, Any]]:
    _, path, _, _, _ = _paths()
    return _read_jsonl(path)


def list_summaries() -> list[dict[str, Any]]:
    _, _, path, _, _ = _paths()
    return _read_jsonl(path)


def save_activity_session(payload: ActivitySessionCreate) -> ActivitySessionRecord:
    """Persist on-device task summaries from the desktop Accessibility agent."""

    if not payload.session_id.strip():
        raise ValueError("sessionId is required.")
    if not payload.tasks:
        raise ValueError("At least one task summary is required.")
    if payload.ended_at < payload.started_at:
        raise ValueError("endedAt must be >= startedAt.")

    record = ActivitySessionRecord(
        session_id=payload.session_id,
        org_id=payload.org_id,
        user_id=payload.user_id,
        source=payload.source,
        started_at=payload.started_at,
        ended_at=payload.ended_at,
        tasks=payload.tasks,
        note=payload.note,
        received_at=datetime.now(timezone.utc),
    )
    _, _, _, _, path = _paths()
    _append_jsonl(path, record.model_dump(mode="json", by_alias=True))
    return record


def list_activity_sessions() -> list[dict[str, Any]]:
    _, _, _, _, path = _paths()
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        latest[str(row.get("sessionId") or row.get("session_id"))] = row
    return sorted(
        latest.values(),
        key=lambda row: str(row.get("receivedAt") or row.get("received_at") or ""),
        reverse=True,
    )


def _get_activity_session(session_id: str) -> ActivitySessionRecord:
    for row in list_activity_sessions():
        sid = str(row.get("sessionId") or row.get("session_id") or "")
        if sid == session_id:
            return ActivitySessionRecord.model_validate(row)
    raise ValueError("No activity session was found for this session id.")


def _format_activity_session_for_prompt(session: ActivitySessionRecord) -> str:
    lines = [
        f"Session: {session.session_id}",
        f"Source: {session.source}",
        f"Window: {session.started_at.isoformat()} → {session.ended_at.isoformat()}",
        f"Note: {session.note or '(none)'}",
        "",
    ]
    for index, task in enumerate(session.tasks, 1):
        lines.extend([
            f"## Task {index}: {task.task_id}",
            f"Primary app: {task.primary_app}",
            f"Apps: {', '.join(task.apps) if task.apps else task.primary_app}",
            f"Time: {task.started_at.isoformat()} → {task.ended_at.isoformat()}",
            f"Events: {task.stats.event_count}; active_ms: {task.stats.active_ms}",
            "Step hints:",
        ])
        if task.step_hints:
            lines.extend(f"- {hint}" for hint in task.step_hints)
        else:
            lines.append("- (none)")
        lines.append("Field interactions (labels only, no values):")
        if task.field_interactions:
            for field in task.field_interactions:
                lines.append(
                    f"- role={field.role or '?'} label={field.label or '[redacted]'} "
                    f"durationMs={field.duration_ms}"
                )
        else:
            lines.append("- (none)")
        lines.append("")
    return "\n".join(lines)


def _jpeg_data_url(filepath: str) -> str:
    with Image.open(filepath) as image:
        image.thumbnail((768, 768))
        if image.mode != "RGB":
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85)
    encoded = base64.b64encode(output.getvalue()).decode()
    return f"data:image/jpeg;base64,{encoded}"


async def summarize_capture(record: CaptureRecord) -> None:
    """Summarize a capture in the background; failures retain the original."""

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    context = (
        f"url: {record.url}\n"
        f"tabTitle: {record.tab_title}\n"
        f"timestamp: {record.timestamp}\n"
        "Analyze this screenshot."
    )
    try:
        response = await client.chat.completions.create(
            model=settings.capture_vision_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": context},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": _jpeg_data_url(record.filepath),
                                "detail": "low",
                            },
                        },
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=400,
        )
        summary = CaptureSummary.model_validate_json(
            response.choices[0].message.content or "{}"
        )
        _, _, summaries_path, _, _ = _paths()
        _append_jsonl(summaries_path, {**record.model_dump(), **summary.model_dump()})
        logger.info("Capture summary completed: %s", record.id)
    except Exception:  # noqa: BLE001
        logger.exception("Capture summary failed: %s", record.id)


def list_skill_files() -> list[dict[str, Any]]:
    _, _, _, path, _ = _paths()
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        latest[str(row["skill_id"])] = row
    return sorted(latest.values(), key=lambda row: row["updated_at"], reverse=True)


async def analyze_capture_session(session_id: str) -> SkillFileDraft:
    captures = [
        CaptureRecord.model_validate(row)
        for row in list_captures()
        if row.get("session_id") == session_id
    ]
    captures.sort(key=lambda item: item.timestamp)
    if not captures:
        raise ValueError("No approved screenshots were found for this capture session.")
    settings = get_settings()
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            f"Session {session_id}. The screenshots are ordered oldest to newest. "
            "Employee notes:\n"
            + "\n".join(item.note for item in captures if item.note)
        ),
    }]
    for index, capture in enumerate(captures, 1):
        content.extend([
            {"type": "text", "text": f"Step candidate {index}: {capture.tab_title} ({capture.url})"},
            {"type": "image_url", "image_url": {"url": _jpeg_data_url(capture.filepath), "detail": "low"}},
        ])
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    response = await client.chat.completions.create(
        model=settings.capture_vision_model,
        messages=[
            {"role": "system", "content": WORKFLOW_PROMPT},
            {"role": "user", "content": content},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=1200,
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    now = datetime.now(timezone.utc)
    draft = SkillFileDraft(
        skill_id=str(uuid4()),
        session_id=session_id,
        title=str(payload.get("title") or "Captured browser workflow"),
        purpose=str(payload.get("purpose") or ""),
        application=str(payload.get("application") or ""),
        context=[str(item) for item in payload.get("context") or []],
        steps=[str(item) for item in payload.get("steps") or []],
        important_fields=[str(item) for item in payload.get("important_fields") or []],
        warnings=[str(item) for item in payload.get("warnings") or []],
        decision_guidance=[str(item) for item in payload.get("decision_guidance") or []],
        follow_up_questions=[str(item) for item in payload.get("follow_up_questions") or []],
        source_capture_ids=[item.id for item in captures],
        source="browser",
        created_at=now,
        updated_at=now,
        org_id=captures[0].org_id,
        created_by=captures[0].user_id,
    )
    _, _, _, path, _ = _paths()
    _append_jsonl(path, draft.model_dump(mode="json"))
    return draft


async def analyze_activity_session(session_id: str) -> SkillFileDraft:
    """Draft a Skill File from desktop activity task summaries (text-only, no vision)."""

    session = _get_activity_session(session_id)
    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    response = await client.chat.completions.create(
        model=settings.capture_vision_model,
        messages=[
            {"role": "system", "content": ACTIVITY_WORKFLOW_PROMPT},
            {"role": "user", "content": _format_activity_session_for_prompt(session)},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=1200,
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    now = datetime.now(timezone.utc)
    primary_apps = [task.primary_app for task in session.tasks if task.primary_app]
    draft = SkillFileDraft(
        skill_id=str(uuid4()),
        session_id=session_id,
        title=str(payload.get("title") or "Captured desktop workflow"),
        purpose=str(payload.get("purpose") or ""),
        application=str(
            payload.get("application")
            or (primary_apps[0] if primary_apps else "Desktop")
        ),
        context=[str(item) for item in payload.get("context") or []],
        steps=[str(item) for item in payload.get("steps") or []],
        important_fields=[str(item) for item in payload.get("important_fields") or []],
        warnings=[str(item) for item in payload.get("warnings") or []],
        decision_guidance=[str(item) for item in payload.get("decision_guidance") or []],
        follow_up_questions=[str(item) for item in payload.get("follow_up_questions") or []],
        source_capture_ids=[task.task_id for task in session.tasks],
        source="desktop_ax",
        created_at=now,
        updated_at=now,
        org_id=session.org_id,
        created_by=session.user_id,
    )
    _, _, _, path, _ = _paths()
    _append_jsonl(path, draft.model_dump(mode="json"))
    return draft


async def create_skill_file_from_expert_answer(
    *,
    org_id: str,
    expert_user_id: str,
    request_id: str,
    question: str,
    answer: str,
) -> SkillFileDraft:
    """Turn a real employee question and expert response into a reviewable skill."""

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    response = await client.chat.completions.create(
        model=settings.capture_vision_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Convert an employee question and expert answer into a reusable "
                    "company Skill File. Return JSON keys title, purpose, application, "
                    "context, steps, important_fields, warnings, decision_guidance, "
                    "follow_up_questions. Ask follow-ups only for missing facts that "
                    "would make the instructions unsafe or materially ambiguous."
                ),
            },
            {"role": "user", "content": f"Question: {question}\nExpert answer: {answer}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=1000,
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    now = datetime.now(timezone.utc)
    draft = SkillFileDraft(
        skill_id=str(uuid4()),
        session_id=f"expert-request:{request_id}",
        title=str(payload.get("title") or question),
        purpose=str(payload.get("purpose") or answer),
        application=str(payload.get("application") or "Company Brain"),
        context=[str(item) for item in payload.get("context") or []],
        steps=[str(item) for item in payload.get("steps") or [answer]],
        important_fields=[str(item) for item in payload.get("important_fields") or []],
        warnings=[str(item) for item in payload.get("warnings") or []],
        decision_guidance=[str(item) for item in payload.get("decision_guidance") or []],
        follow_up_questions=[str(item) for item in payload.get("follow_up_questions") or []],
        source_capture_ids=[],
        source="expert",
        created_at=now,
        updated_at=now,
        org_id=org_id,
        created_by=expert_user_id,
    )
    _, _, _, path, _ = _paths()
    _append_jsonl(path, draft.model_dump(mode="json"))
    return draft


async def update_skill_file(skill_id: str, update: SkillFileUpdate) -> SkillFileDraft:
    """Update Skill File metadata without changing approval status."""

    current_data = next(
        (row for row in list_skill_files() if row["skill_id"] == skill_id),
        None,
    )
    if current_data is None:
        raise ValueError("Skill File was not found.")
    current = SkillFileDraft.model_validate(current_data)
    updates = update.model_dump(exclude_none=True)
    if "title" in updates:
        title = str(updates["title"]).strip()
        if not title:
            raise ValueError("Skill name cannot be empty.")
        updates["title"] = title
    updates["updated_at"] = datetime.now(timezone.utc)
    updated = current.model_copy(update=updates)
    _, _, _, path, _ = _paths()
    _append_jsonl(path, updated.model_dump(mode="json"))
    return updated


async def review_skill_file(skill_id: str, review: SkillFileReview) -> SkillFileDraft:
    current_data = next(
        (row for row in list_skill_files() if row["skill_id"] == skill_id),
        None,
    )
    if current_data is None:
        raise ValueError("Skill File was not found.")
    current = SkillFileDraft.model_validate(current_data)
    updates = review.model_dump(exclude_none=True)
    updates["updated_at"] = datetime.now(timezone.utc)
    updated = current.model_copy(update=updates)
    _, _, _, path, _ = _paths()
    _append_jsonl(path, updated.model_dump(mode="json"))
    if updated.status == "approved":
        text = "\n".join([
            f"Skill: {updated.title}",
            f"Purpose: {updated.purpose}",
            f"Application: {updated.application}",
            "Steps:",
            *[f"{index}. {step}" for index, step in enumerate(updated.steps, 1)],
            "Important fields:",
            *updated.important_fields,
            "Warnings:",
            *updated.warnings,
            "Decision guidance:",
            *updated.decision_guidance,
            f"Expert notes: {updated.expert_notes}",
        ])
        now = datetime.now(timezone.utc)
        conversation = Conversation(
            source="skill_file",
            conversation_id=f"skill:{updated.skill_id}",
            title=updated.title,
            participants=[Participant(id="expert", name="Approving expert")],
            messages=[IncomingMessage(id=updated.skill_id, sender="expert", timestamp=now, text=text)],
        )
        await ingest_external_source(
            org_id=updated.org_id,
            provider="skill_file",
            external_id=updated.skill_id,
            version=updated.updated_at.isoformat(),
            conversation=conversation,
            document=DocumentInput(
                data=text.encode(), source="skill_file", source_label=updated.title,
                original_filename=f"{updated.skill_id}.md", mime_type="text/markdown",
                visible_to=[f"org:{updated.org_id}"], title=updated.title,
                source_application=updated.application or "Chrome",
                source_location=f"Capture session {updated.session_id}",
                source_created_at=updated.created_at, source_updated_at=updated.updated_at,
                version=updated.updated_at.isoformat(),
                permissions=[f"org:{updated.org_id}"],
            ),
        )
    return updated
