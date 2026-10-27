"""Persistence and AI summarisation for approved browser screenshots.

Metadata lives in Postgres; screenshot bytes go through :mod:`blob_storage`.
A one-release JSONL read shim remains for pre-migration local files.
"""

from __future__ import annotations

import asyncio
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
from sqlalchemy import text

from core.blob_storage import get_blob_storage
from core.config import get_settings
from core.database import get_session_factory
from keeper.visibility_acl import (
    can_mutate_skill,
    can_view_skill,
    skill_acl_tokens,
    skill_visibility_from_row,
)
from core.models import (
    ActivitySessionCreate,
    ActivitySessionRecord,
    BrowserAnchor,
    CaptureCreate,
    CaptureRecord,
    CaptureSummary,
    Conversation,
    GuideStep,
    GuideSummary,
    IncomingMessage,
    Participant,
    SkillFileDraft,
    SkillFileReview,
    SkillFileUpdate,
    StepAnchor,
)
from brain.pipeline import DocumentInput
from brain.source_registry import ingest_external_source, mark_external_source_deleted

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
decision_guidance, follow_up_questions, step_anchor_ids.
Every list value must be a list of strings, except step_anchor_ids: a list the
same length as steps where each entry is the id of the recorded anchor (for
example "b3") for the control the step activates, or null if no anchor fits.
Only use anchor ids that appear next to a step candidate.
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
steps, important_fields, warnings, decision_guidance, follow_up_questions,
step_anchor_ids.
Every list value must be a list of strings, except step_anchor_ids: a list the
same length as steps where each entry is the id of the observed anchor (for
example "a3") that best marks the step as done, or null if no anchor fits.
Only use anchor ids that appear in the Anchors section.
Never invent confidential field values, identifiers, or typed content that were
not present in the summaries."""


# --- Optional in-memory backend for unit tests --------------------------------

_memory: dict[str, Any] | None = None


def use_memory_store(enabled: bool = True) -> None:
    """Enable/disable the in-process capture store (tests only)."""

    global _memory
    _memory = (
        {
            "captures": {},
            "summaries": {},
            "activity": {},
            "skills": {},
        }
        if enabled
        else None
    )


def _legacy_paths() -> tuple[Path, Path, Path, Path, Path]:
    root = Path(get_settings().capture_storage_root).resolve()
    images = root / "images"
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


def _record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a capture DB/memory row to the CaptureRecord dict shape."""

    return {
        "id": row["id"],
        "timestamp": int(row["timestamp"]),
        "url": row.get("url") or "",
        "tab_title": row.get("tab_title") or "",
        "browser_anchor": row.get("browser_anchor"),
        "window_id": row.get("window_id"),
        "filepath": row.get("filepath") or row.get("blob_key") or "",
        "session_id": row.get("session_id") or "",
        "note": row.get("note") or "",
        "redactions": row.get("redactions") or [],
        "org_id": row.get("org_id") or "",
        "user_id": row.get("user_id") or "",
    }


async def save_capture(
    payload: CaptureCreate,
    *,
    org_id: str,
    user_id: str,
) -> CaptureRecord:
    """Decode and persist one user-approved extension capture.

    ``org_id`` / ``user_id`` come from the authenticated JWT and overwrite any
    client-supplied body fields.
    """

    try:
        header, encoded = payload.data_url.split(",", 1)
        if ";base64" not in header:
            raise ValueError
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid base64 image data URL.") from exc

    safe_id = "".join(c for c in payload.id if c.isalnum() or c in "-_")
    if not safe_id:
        raise ValueError("Capture id contains no safe characters.")

    blob_key = f"captures/{org_id}/{safe_id}.png"
    storage = get_blob_storage()
    storage_path = await storage.put(blob_key, image_bytes)

    record = CaptureRecord(
        id=payload.id,
        timestamp=payload.timestamp,
        url=payload.url,
        tab_title=payload.tab_title,
        browser_anchor=payload.browser_anchor,
        window_id=payload.window_id,
        filepath=storage_path,
        session_id=payload.session_id,
        note=payload.note,
        redactions=payload.redactions,
        org_id=org_id,
        user_id=user_id,
    )

    if _memory is not None:
        _memory["captures"][record.id] = record.model_dump()
        return record

    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO captures (
                        capture_id, org_id, user_id, timestamp_ms, url, tab_title,
                        browser_anchor_json, window_id, blob_key, session_id, note,
                        redactions_json
                    ) VALUES (
                        :capture_id, :org_id, :user_id, :timestamp_ms, :url, :tab_title,
                        :browser_anchor_json, :window_id, :blob_key, :session_id, :note,
                        :redactions_json
                    )
                    ON CONFLICT (capture_id) DO UPDATE SET
                        org_id = EXCLUDED.org_id,
                        user_id = EXCLUDED.user_id,
                        timestamp_ms = EXCLUDED.timestamp_ms,
                        url = EXCLUDED.url,
                        tab_title = EXCLUDED.tab_title,
                        browser_anchor_json = EXCLUDED.browser_anchor_json,
                        window_id = EXCLUDED.window_id,
                        blob_key = EXCLUDED.blob_key,
                        session_id = EXCLUDED.session_id,
                        note = EXCLUDED.note,
                        redactions_json = EXCLUDED.redactions_json
                    """
                ),
                {
                    "capture_id": record.id,
                    "org_id": org_id,
                    "user_id": user_id,
                    "timestamp_ms": record.timestamp,
                    "url": record.url,
                    "tab_title": record.tab_title,
                    "browser_anchor_json": (
                        record.browser_anchor.model_dump_json()
                        if record.browser_anchor
                        else None
                    ),
                    "window_id": record.window_id,
                    "blob_key": storage_path,
                    "session_id": record.session_id,
                    "note": record.note,
                    "redactions_json": json.dumps(record.redactions),
                },
            )
    return record


async def list_captures(*, org_id: str | None = None) -> list[dict[str, Any]]:
    if _memory is not None:
        rows = list(_memory["captures"].values())
        if org_id is not None:
            rows = [r for r in rows if str(r.get("org_id") or "") == org_id]
        return rows

    session_factory = get_session_factory()
    async with session_factory() as session:
        if org_id is None:
            result = await session.execute(
                text(
                    """
                    SELECT capture_id AS id, timestamp_ms AS timestamp, url, tab_title,
                           browser_anchor_json, window_id, blob_key AS filepath,
                           session_id, note, redactions_json, org_id, user_id
                    FROM captures
                    ORDER BY timestamp_ms
                    """
                )
            )
        else:
            result = await session.execute(
                text(
                    """
                    SELECT capture_id AS id, timestamp_ms AS timestamp, url, tab_title,
                           browser_anchor_json, window_id, blob_key AS filepath,
                           session_id, note, redactions_json, org_id, user_id
                    FROM captures
                    WHERE org_id = :org_id
                    ORDER BY timestamp_ms
                    """
                ),
                {"org_id": org_id},
            )
        rows = []
        for mapping in result.mappings().all():
            row = dict(mapping)
            row["redactions"] = json.loads(row.pop("redactions_json") or "[]")
            anchor_json = row.pop("browser_anchor_json", None)
            row["browser_anchor"] = json.loads(anchor_json) if anchor_json else None
            rows.append(_record_from_row(row))

    _, captures_path, _, _, _ = _legacy_paths()
    if captures_path.exists():
        seen = {r["id"] for r in rows}
        for legacy in _read_jsonl(captures_path):
            if org_id is not None and str(legacy.get("org_id") or "") != org_id:
                continue
            if legacy.get("id") in seen:
                continue
            rows.append(legacy)
    return rows


async def list_summaries(*, org_id: str | None = None) -> list[dict[str, Any]]:
    if _memory is not None:
        rows = list(_memory["summaries"].values())
        if org_id is not None:
            rows = [r for r in rows if str(r.get("org_id") or "") == org_id]
        return rows

    session_factory = get_session_factory()
    async with session_factory() as session:
        if org_id is None:
            result = await session.execute(
                text("SELECT payload_json FROM capture_summaries ORDER BY created_at")
            )
        else:
            result = await session.execute(
                text(
                    "SELECT payload_json FROM capture_summaries "
                    "WHERE org_id = :org_id ORDER BY created_at"
                ),
                {"org_id": org_id},
            )
        rows = [json.loads(r[0]) for r in result.all()]

    _, _, summaries_path, _, _ = _legacy_paths()
    if summaries_path.exists():
        seen = {str(r.get("id")) for r in rows}
        for legacy in _read_jsonl(summaries_path):
            if org_id is not None and str(legacy.get("org_id") or "") != org_id:
                continue
            if str(legacy.get("id")) in seen:
                continue
            rows.append(legacy)
    return rows


async def save_activity_session(
    payload: ActivitySessionCreate,
    *,
    org_id: str,
    user_id: str,
) -> ActivitySessionRecord:
    """Persist on-device task summaries from the desktop Accessibility agent."""

    if not payload.session_id.strip():
        raise ValueError("sessionId is required.")
    if not payload.tasks:
        raise ValueError("At least one task summary is required.")
    if payload.ended_at < payload.started_at:
        raise ValueError("endedAt must be >= startedAt.")

    record = ActivitySessionRecord(
        session_id=payload.session_id,
        org_id=org_id,
        user_id=user_id,
        source=payload.source,
        started_at=payload.started_at,
        ended_at=payload.ended_at,
        tasks=payload.tasks,
        note=payload.note,
        received_at=datetime.now(timezone.utc),
    )
    dumped = record.model_dump(mode="json", by_alias=True)

    if _memory is not None:
        _memory["activity"][record.session_id] = dumped
        return record

    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO activity_sessions (session_id, org_id, user_id, payload_json)
                    VALUES (:session_id, :org_id, :user_id, :payload_json)
                    ON CONFLICT (session_id) DO UPDATE SET
                        org_id = EXCLUDED.org_id,
                        user_id = EXCLUDED.user_id,
                        payload_json = EXCLUDED.payload_json,
                        updated_at = now()
                    """
                ),
                {
                    "session_id": record.session_id,
                    "org_id": org_id,
                    "user_id": user_id,
                    "payload_json": json.dumps(dumped),
                },
            )
    return record


async def list_activity_sessions(*, org_id: str | None = None) -> list[dict[str, Any]]:
    if _memory is not None:
        rows = list(_memory["activity"].values())
        if org_id is not None:
            rows = [
                r
                for r in rows
                if str(r.get("orgId") or r.get("org_id") or "") == org_id
            ]
        return sorted(
            rows,
            key=lambda row: str(row.get("receivedAt") or row.get("received_at") or ""),
            reverse=True,
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        if org_id is None:
            result = await session.execute(
                text(
                    "SELECT payload_json FROM activity_sessions "
                    "ORDER BY received_at DESC"
                )
            )
        else:
            result = await session.execute(
                text(
                    "SELECT payload_json FROM activity_sessions "
                    "WHERE org_id = :org_id ORDER BY received_at DESC"
                ),
                {"org_id": org_id},
            )
        rows = [json.loads(r[0]) for r in result.all()]

    _, _, _, _, path = _legacy_paths()
    if path.exists():
        latest: dict[str, dict[str, Any]] = {
            str(r.get("sessionId") or r.get("session_id")): r for r in rows
        }
        for row in _read_jsonl(path):
            if org_id is not None:
                row_org = str(row.get("orgId") or row.get("org_id") or "")
                if row_org != org_id:
                    continue
            sid = str(row.get("sessionId") or row.get("session_id"))
            if sid not in latest:
                latest[sid] = row
        rows = sorted(
            latest.values(),
            key=lambda row: str(row.get("receivedAt") or row.get("received_at") or ""),
            reverse=True,
        )
    return rows


async def _get_activity_session(session_id: str, *, org_id: str) -> ActivitySessionRecord:
    for row in await list_activity_sessions(org_id=org_id):
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
    anchors = collect_session_anchors(session)
    if anchors:
        lines.append("## Anchors (observed controls; reference by id in step_anchor_ids)")
        lines.extend(
            f"[a{index}] {describe_anchor(anchor)} in {anchor.bundle_id}"
            for index, anchor in enumerate(anchors, 1)
        )
        lines.append("")
    return "\n".join(lines)


MAX_SESSION_ANCHORS = 80


def _anchor_key(anchor: StepAnchor) -> tuple[str, str, str, str, str]:
    return (
        anchor.bundle_id,
        anchor.action,
        anchor.role,
        anchor.identifier or "",
        anchor.title or anchor.window_title or "",
    )


def collect_session_anchors(session: ActivitySessionRecord) -> list[StepAnchor]:
    """Ordered, de-duplicated anchors across all task segments."""

    seen: set[tuple[str, str, str, str, str]] = set()
    anchors: list[StepAnchor] = []
    for task in session.tasks:
        for anchor in task.anchors:
            key = _anchor_key(anchor)
            if key in seen:
                continue
            seen.add(key)
            anchors.append(anchor)
            if len(anchors) >= MAX_SESSION_ANCHORS:
                return anchors
    return anchors


def session_bundle_ids(session: ActivitySessionRecord) -> list[str]:
    ids: list[str] = []
    for task in session.tasks:
        for bundle_id in [*task.bundle_ids, *(a.bundle_id for a in task.anchors)]:
            if bundle_id and bundle_id not in ids:
                ids.append(bundle_id)
    return ids


def describe_anchor(anchor: StepAnchor) -> str:
    label = anchor.title or anchor.identifier or anchor.role or "control"
    if anchor.action == "navigate":
        return f'Open window "{anchor.window_title or label}"'
    if anchor.action == "menu":
        return f'Choose menu "{label}"'
    if anchor.action in ("field_focus", "field_blur"):
        return f'Fill in "{label}"'
    if anchor.action == "click":
        return f'Click "{label}"'
    return f'Focus "{label}"'


def _anchor_position(anchor_ids: list[Any], index: int, prefix: str, count: int) -> int | None:
    """Index into the anchor list for ``<prefix><N>`` at ``anchor_ids[index]``, else None."""

    raw = anchor_ids[index] if index < len(anchor_ids) else None
    if not isinstance(raw, str):
        return None
    token = raw.strip().strip("[]").lower()
    if token.startswith(prefix) and token[1:].isdigit():
        position = int(token[1:]) - 1
        if 0 <= position < count:
            return position
    return None


def resolve_guide_steps(
    steps: list[str],
    anchor_ids: list[Any],
    anchors: list[StepAnchor],
) -> list[GuideStep]:
    """Attach anchors to LLM steps by ``a<N>`` id; unknown or missing ids become manual steps."""

    guide: list[GuideStep] = []
    for index, step in enumerate(steps):
        position = _anchor_position(anchor_ids, index, "a", len(anchors))
        anchor = anchors[position] if position is not None else None
        guide.append(GuideStep(text=step, anchor=anchor))
    return guide


def resolve_browser_guide_steps(
    steps: list[str],
    anchor_ids: list[Any],
    anchors: list[BrowserAnchor],
) -> list[GuideStep]:
    """Attach recorded page anchors by ``b<N>`` id; anything else stays a manual step."""

    guide: list[GuideStep] = []
    for index, step in enumerate(steps):
        position = _anchor_position(anchor_ids, index, "b", len(anchors))
        anchor = anchors[position] if position is not None else None
        guide.append(GuideStep(text=step, browser_anchor=anchor))
    return guide


def describe_browser_anchor(anchor: BrowserAnchor) -> str:
    label = f'"{anchor.name}"' if anchor.name else (anchor.selector or "control")
    if not anchor.name and not anchor.selector:
        return f"Open page {anchor.url}"
    if anchor.needs_value:
        return f"Fill in {anchor.role or 'field'} {label} on {anchor.url}"
    return f"Click {anchor.role or 'control'} {label} on {anchor.url}"


def realign_guide_steps(previous: list[GuideStep], steps: list[str]) -> list[GuideStep]:
    """Rebuild guide steps after a reviewer edit, keeping anchors only for unchanged text."""

    pool: dict[str, list[GuideStep]] = {}
    for item in previous:
        pool.setdefault(item.text, []).append(item)
    guide: list[GuideStep] = []
    for step in steps:
        candidates = pool.get(step)
        kept = candidates.pop(0) if candidates else None
        guide.append(
            GuideStep(
                text=step,
                anchor=kept.anchor if kept else None,
                browser_anchor=kept.browser_anchor if kept else None,
            )
        )
    return guide


async def _jpeg_data_url(storage_path: str) -> str:
    """Load capture bytes from blob storage (or legacy local path) as a JPEG data URL."""

    data: bytes | None = None
    if storage_path.startswith(("file://", "s3://")):
        data = await get_blob_storage().get(storage_path)
    else:
        path = Path(storage_path)
        if path.is_file():
            data = await asyncio.to_thread(path.read_bytes)
        else:
            try:
                data = await get_blob_storage().get(storage_path)
            except Exception as exc:  # noqa: BLE001
                raise FileNotFoundError(storage_path) from exc
    if data is None:
        raise FileNotFoundError(storage_path)

    def _encode() -> str:
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((768, 768))
            if image.mode != "RGB":
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85)
        encoded = base64.b64encode(output.getvalue()).decode()
        return f"data:image/jpeg;base64,{encoded}"

    return await asyncio.to_thread(_encode)


async def _persist_summary(payload: dict[str, Any]) -> None:
    if _memory is not None:
        _memory["summaries"][str(payload.get("id"))] = payload
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO capture_summaries (capture_id, org_id, payload_json)
                    VALUES (:capture_id, :org_id, :payload_json)
                    ON CONFLICT (capture_id) DO UPDATE SET
                        org_id = EXCLUDED.org_id,
                        payload_json = EXCLUDED.payload_json
                    """
                ),
                {
                    "capture_id": str(payload["id"]),
                    "org_id": str(payload.get("org_id") or ""),
                    "payload_json": json.dumps(payload),
                },
            )


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
        image_url = await _jpeg_data_url(record.filepath)
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
                                "url": image_url,
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
        await _persist_summary({**record.model_dump(), **summary.model_dump()})
        logger.info("Capture summary completed: %s", record.id)
    except Exception:  # noqa: BLE001
        logger.exception("Capture summary failed: %s", record.id)


async def _list_skill_files_raw(*, org_id: str | None = None) -> list[dict[str, Any]]:
    """Return all skill payloads for an org (no viewer ACL filter)."""

    if _memory is not None:
        rows = list(_memory["skills"].values())
        if org_id is not None:
            rows = [r for r in rows if str(r.get("org_id") or "") == org_id]
        return sorted(rows, key=lambda row: row["updated_at"], reverse=True)

    session_factory = get_session_factory()
    async with session_factory() as session:
        if org_id is None:
            result = await session.execute(
                text("SELECT payload_json FROM skill_files ORDER BY updated_at DESC")
            )
        else:
            result = await session.execute(
                text(
                    "SELECT payload_json FROM skill_files "
                    "WHERE org_id = :org_id ORDER BY updated_at DESC"
                ),
                {"org_id": org_id},
            )
        rows = [json.loads(r[0]) for r in result.all()]

    _, _, _, path, _ = _legacy_paths()
    if path.exists():
        latest = {str(r["skill_id"]): r for r in rows}
        for row in _read_jsonl(path):
            if org_id is not None and str(row.get("org_id") or "") != org_id:
                continue
            sid = str(row["skill_id"])
            if sid not in latest:
                latest[sid] = row
        rows = sorted(latest.values(), key=lambda row: row["updated_at"], reverse=True)
    return rows


def _normalize_skill_row(row: dict[str, Any]) -> dict[str, Any]:
    """Ensure visibility is always present for API consumers."""

    normalized = dict(row)
    normalized["visibility"] = skill_visibility_from_row(row)
    return normalized


async def list_skill_files(
    *,
    org_id: str | None = None,
    viewer_user_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = [_normalize_skill_row(row) for row in await _list_skill_files_raw(org_id=org_id)]
    if viewer_user_id is None:
        return rows
    return [row for row in rows if can_view_skill(row, viewer_user_id)]


async def _get_skill_row(skill_id: str, *, org_id: str) -> dict[str, Any] | None:
    for row in await _list_skill_files_raw(org_id=org_id):
        if row.get("skill_id") == skill_id:
            return _normalize_skill_row(row)
    return None


async def _persist_skill(draft: SkillFileDraft) -> None:
    dumped = draft.model_dump(mode="json")
    if _memory is not None:
        _memory["skills"][draft.skill_id] = dumped
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO skill_files (
                        skill_id, org_id, session_id, payload_json, updated_at
                    ) VALUES (
                        :skill_id, :org_id, :session_id, :payload_json, :updated_at
                    )
                    ON CONFLICT (skill_id) DO UPDATE SET
                        org_id = EXCLUDED.org_id,
                        session_id = EXCLUDED.session_id,
                        payload_json = EXCLUDED.payload_json,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "skill_id": draft.skill_id,
                    "org_id": draft.org_id,
                    "session_id": draft.session_id,
                    "payload_json": json.dumps(dumped),
                    "updated_at": draft.updated_at,
                },
            )


async def analyze_capture_session(session_id: str, *, org_id: str) -> SkillFileDraft:
    captures = [
        CaptureRecord.model_validate(row)
        for row in await list_captures(org_id=org_id)
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
    anchors: list[BrowserAnchor] = []
    for index, capture in enumerate(captures, 1):
        candidate = f"Step candidate {index}: {capture.tab_title} ({capture.url})"
        if capture.browser_anchor is not None:
            anchors.append(capture.browser_anchor)
            candidate += (
                f"\nAnchor [b{len(anchors)}]: {describe_browser_anchor(capture.browser_anchor)}"
            )
        content.extend([
            {"type": "text", "text": candidate},
            {
                "type": "image_url",
                "image_url": {
                    "url": await _jpeg_data_url(capture.filepath),
                    "detail": "low",
                },
            },
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
    steps = [str(item) for item in payload.get("steps") or []]
    raw_anchor_ids = payload.get("step_anchor_ids")
    guide_steps = (
        resolve_browser_guide_steps(
            steps,
            raw_anchor_ids if isinstance(raw_anchor_ids, list) else [],
            anchors,
        )
        if anchors
        else []
    )
    draft = SkillFileDraft(
        skill_id=str(uuid4()),
        session_id=session_id,
        title=str(payload.get("title") or "Captured browser workflow"),
        purpose=str(payload.get("purpose") or ""),
        application=str(payload.get("application") or ""),
        context=[str(item) for item in payload.get("context") or []],
        steps=steps,
        guide_steps=guide_steps,
        important_fields=[str(item) for item in payload.get("important_fields") or []],
        warnings=[str(item) for item in payload.get("warnings") or []],
        decision_guidance=[str(item) for item in payload.get("decision_guidance") or []],
        follow_up_questions=[str(item) for item in payload.get("follow_up_questions") or []],
        source_capture_ids=[item.id for item in captures],
        source="browser",
        created_at=now,
        updated_at=now,
        org_id=org_id,
        created_by=captures[0].user_id,
    )
    await _persist_skill(draft)
    return draft


async def find_skill_for_session(*, org_id: str, session_id: str) -> dict[str, Any] | None:
    for row in await _list_skill_files_raw(org_id=org_id):
        if str(row.get("session_id") or "") == session_id:
            return _normalize_skill_row(row)
    return None


def _heuristic_activity_skill(session: ActivitySessionRecord, *, org_id: str) -> SkillFileDraft:
    """Deterministic Skill File when the LLM draft is unavailable."""

    now = datetime.now(timezone.utc)
    primary_apps = [task.primary_app for task in session.tasks if task.primary_app]
    app = primary_apps[0] if primary_apps else "Desktop"
    steps: list[str] = []
    fields: list[str] = []
    for task in session.tasks:
        for hint in task.step_hints:
            if hint and hint not in steps:
                steps.append(hint)
        for field in task.field_interactions:
            label = field.label or field.role or "field"
            line = f"{label} ({field.role})" if field.role else label
            if line not in fields:
                fields.append(line)
    anchors = [a for a in collect_session_anchors(session) if a.action != "field_blur"]
    guide_steps = [GuideStep(text=describe_anchor(a), anchor=a) for a in anchors[:40]]
    if guide_steps:
        steps = [item.text for item in guide_steps]
    if not steps:
        steps = [f"Worked in {app}"]
    title = f"{app} workflow" if app != "Desktop" else "Captured desktop workflow"
    purpose = session.note.strip() or f"Desktop activity captured in {app}."
    return SkillFileDraft(
        skill_id=str(uuid4()),
        session_id=session.session_id,
        title=title,
        purpose=purpose,
        application=app,
        context=[
            f"Captured {len(session.tasks)} task segment(s) via Accessibility.",
            f"Window: {session.started_at.isoformat()} → {session.ended_at.isoformat()}",
        ],
        steps=steps[:40],
        important_fields=fields[:20],
        warnings=["Drafted without LLM enrichment; review steps before approving."],
        decision_guidance=[],
        follow_up_questions=[],
        source_capture_ids=[task.task_id for task in session.tasks],
        bundle_ids=session_bundle_ids(session),
        guide_steps=guide_steps,
        source="desktop_ax",
        created_at=now,
        updated_at=now,
        org_id=org_id,
        created_by=session.user_id,
    )


async def analyze_activity_session(session_id: str, *, org_id: str) -> SkillFileDraft:
    """Draft a Skill File from desktop activity task summaries (text-only, no vision)."""

    existing = await find_skill_for_session(org_id=org_id, session_id=session_id)
    if existing:
        return SkillFileDraft.model_validate(existing)

    session = await _get_activity_session(session_id, org_id=org_id)
    settings = get_settings()
    try:
        if not (settings.openai_api_key or "").strip():
            raise RuntimeError("OPENAI_API_KEY is not configured")
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
        steps = [str(item) for item in payload.get("steps") or []]
        raw_anchor_ids = payload.get("step_anchor_ids")
        guide_steps = resolve_guide_steps(
            steps,
            raw_anchor_ids if isinstance(raw_anchor_ids, list) else [],
            collect_session_anchors(session),
        )
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
            steps=steps,
            important_fields=[str(item) for item in payload.get("important_fields") or []],
            warnings=[str(item) for item in payload.get("warnings") or []],
            decision_guidance=[str(item) for item in payload.get("decision_guidance") or []],
            follow_up_questions=[str(item) for item in payload.get("follow_up_questions") or []],
            source_capture_ids=[task.task_id for task in session.tasks],
            bundle_ids=session_bundle_ids(session),
            guide_steps=guide_steps,
            source="desktop_ax",
            created_at=now,
            updated_at=now,
            org_id=org_id,
            created_by=session.user_id,
        )
        await _persist_skill(draft)
        return draft
    except Exception as exc:  # noqa: BLE001 — always surface a reviewable draft
        logger.warning(
            "LLM desktop skill draft failed for session %s; using heuristic draft: %s",
            session_id,
            exc,
        )
        draft = _heuristic_activity_skill(session, org_id=org_id)
        await _persist_skill(draft)
        return draft


async def ensure_activity_skill(session_id: str, *, org_id: str) -> None:
    """Background helper: ensure a Workflows skill exists for an uploaded session."""

    try:
        await analyze_activity_session(session_id, org_id=org_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to auto-draft skill for activity session %s (org %s)",
            session_id,
            org_id,
        )


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
    await _persist_skill(draft)
    return draft


class SkillAccessError(PermissionError):
    """Raised when a caller may not view or mutate a skill."""


async def update_skill_file(
    skill_id: str,
    update: SkillFileUpdate,
    *,
    org_id: str,
    actor_user_id: str,
) -> SkillFileDraft:
    """Update Skill File metadata without changing approval status."""

    current_data = await _get_skill_row(skill_id, org_id=org_id)
    if current_data is None or not can_view_skill(current_data, actor_user_id):
        raise ValueError("Skill File was not found.")
    changing_visibility = update.visibility is not None
    if not can_mutate_skill(
        current_data, actor_user_id, changing_visibility=changing_visibility
    ):
        raise SkillAccessError(
            "Only the creator can change visibility."
            if changing_visibility
            else "You do not have permission to edit this Skill File."
        )
    current = SkillFileDraft.model_validate(current_data)
    updates = update.model_dump(exclude_none=True)
    if "title" in updates:
        title = str(updates["title"]).strip()
        if not title:
            raise ValueError("Skill name cannot be empty.")
        updates["title"] = title
    updates["updated_at"] = datetime.now(timezone.utc)
    updated = current.model_copy(update=updates)
    await _persist_skill(updated)
    # Keep the knowledge graph in sync when an already-published workflow changes
    # (including visibility / Ask ACL updates).
    if updated.status == "approved":
        await publish_skill_to_knowledge_graph(updated)
    return updated


def format_skill_for_knowledge_graph(skill: SkillFileDraft) -> str:
    """Deterministic markdown body used for Neo4j / embedding ingest."""

    source_label = {
        "browser": "Browser capture",
        "desktop_ax": "Desktop capture",
        "expert": "Expert answer",
    }.get(skill.source, skill.source or "Workflow")

    lines = [
        f"# Workflow: {skill.title}",
        "",
        f"This is an approved organizational workflow skill file ({source_label}).",
        "Use it to answer how-to questions about this process.",
        "",
        f"Purpose: {skill.purpose or '(not specified)'}",
        f"Application: {skill.application or '(not specified)'}",
        f"Capture source: {source_label}",
        f"Session: {skill.session_id}",
        "",
        "## Context",
    ]
    if skill.context:
        lines.extend(f"- {item}" for item in skill.context)
    else:
        lines.append("- (none)")

    lines.extend(["", "## Steps"])
    if skill.steps:
        lines.extend(f"{index}. {step}" for index, step in enumerate(skill.steps, 1))
    else:
        lines.append("1. (no steps recorded)")

    lines.extend(["", "## Important fields"])
    if skill.important_fields:
        lines.extend(f"- {item}" for item in skill.important_fields)
    else:
        lines.append("- (none)")

    lines.extend(["", "## Warnings"])
    if skill.warnings:
        lines.extend(f"- {item}" for item in skill.warnings)
    else:
        lines.append("- (none)")

    lines.extend(["", "## Decision guidance"])
    if skill.decision_guidance:
        lines.extend(f"- {item}" for item in skill.decision_guidance)
    else:
        lines.append("- (none)")

    lines.extend(["", "## Follow-up questions"])
    if skill.follow_up_questions:
        lines.extend(f"- {item}" for item in skill.follow_up_questions)
    else:
        lines.append("- (none)")

    lines.extend(["", "## Expert notes", skill.expert_notes or "(none)"])
    return "\n".join(lines)


async def publish_skill_to_knowledge_graph(skill: SkillFileDraft) -> None:
    """Upsert an approved workflow into Document/Chunk storage for Ask retrieval."""

    text_body = format_skill_for_knowledge_graph(skill)
    now = datetime.now(timezone.utc)
    conversation = Conversation(
        source="skill_file",
        conversation_id=f"skill:{skill.skill_id}",
        title=skill.title,
        participants=[Participant(id="workflow", name="Approved workflow")],
        messages=[
            IncomingMessage(
                id=skill.skill_id,
                sender="workflow",
                timestamp=now,
                text=text_body,
            )
        ],
    )
    await ingest_external_source(
        org_id=skill.org_id,
        provider="skill_file",
        external_id=skill.skill_id,
        version=skill.updated_at.isoformat(),
        conversation=conversation,
        document=DocumentInput(
            data=text_body.encode("utf-8"),
            source="skill_file",
            source_label=skill.title,
            original_filename=f"{skill.skill_id}.md",
            mime_type="text/markdown",
            visible_to=skill_acl_tokens(skill),
            title=skill.title,
            source_application=skill.application or "Workflow",
            source_location=f"Workflow session {skill.session_id}",
            source_created_at=skill.created_at,
            source_updated_at=skill.updated_at,
            version=skill.updated_at.isoformat(),
            permissions=skill_acl_tokens(skill),
        ),
    )


async def unpublish_skill_from_knowledge_graph(skill: SkillFileDraft) -> None:
    """Remove a rejected/unpublished workflow from Ask retrieval."""

    await mark_external_source_deleted(skill.org_id, "skill_file", skill.skill_id)


async def review_skill_file(
    skill_id: str,
    review: SkillFileReview,
    *,
    org_id: str,
    actor_user_id: str,
) -> SkillFileDraft:
    current_data = await _get_skill_row(skill_id, org_id=org_id)
    if current_data is None or not can_view_skill(current_data, actor_user_id):
        raise ValueError("Skill File was not found.")
    changing_visibility = review.visibility is not None
    if not can_mutate_skill(
        current_data, actor_user_id, changing_visibility=changing_visibility
    ):
        raise SkillAccessError(
            "Only the creator can change visibility."
            if changing_visibility
            else "You do not have permission to review this Skill File."
        )
    current = SkillFileDraft.model_validate(current_data)
    updates = review.model_dump(exclude_none=True)
    updates["updated_at"] = datetime.now(timezone.utc)
    if "steps" in updates and current.guide_steps:
        updates["guide_steps"] = realign_guide_steps(current.guide_steps, updates["steps"])
    updated = current.model_copy(update=updates)

    # Publish/unpublish BEFORE persisting terminal status so a KG failure does not
    # leave the skill marked approved without searchable knowledge.
    if updated.status == "approved":
        try:
            await publish_skill_to_knowledge_graph(updated)
        except Exception as exc:
            logger.exception(
                "Failed to publish skill %s to knowledge graph", updated.skill_id
            )
            raise ValueError(
                f"Could not store this workflow in the knowledge graph: {exc}"
            ) from exc
    elif updated.status == "rejected":
        try:
            await unpublish_skill_from_knowledge_graph(updated)
        except Exception:  # noqa: BLE001 — rejection should still succeed locally
            logger.exception(
                "Failed to unpublish skill %s from knowledge graph", updated.skill_id
            )

    await _persist_skill(updated)
    return updated


def guide_payload(skill: SkillFileDraft) -> GuideSummary:
    """Shape a skill for walkthrough playback; legacy skills become manual-only steps."""

    steps = skill.guide_steps or [GuideStep(text=step) for step in skill.steps]
    return GuideSummary(
        skill_id=skill.skill_id,
        title=skill.title,
        purpose=skill.purpose,
        application=skill.application,
        bundle_ids=skill.bundle_ids,
        steps=steps,
        warnings=skill.warnings,
        important_fields=skill.important_fields,
        updated_at=skill.updated_at,
    )


async def list_guides(
    *,
    org_id: str,
    viewer_user_id: str,
    bundle_id: str | None = None,
) -> list[GuideSummary]:
    """Approved, viewer-visible skills with at least one step."""

    guides: list[GuideSummary] = []
    for row in await list_skill_files(org_id=org_id, viewer_user_id=viewer_user_id):
        if row.get("status") != "approved":
            continue
        skill = SkillFileDraft.model_validate(row)
        if bundle_id and bundle_id not in skill.bundle_ids:
            continue
        guide = guide_payload(skill)
        if guide.steps:
            guides.append(guide)
    return guides


async def get_visible_skill(
    skill_id: str, *, org_id: str, viewer_user_id: str
) -> SkillFileDraft | None:
    """The skill when it exists in the org and the viewer may see it."""

    row = await _get_skill_row(skill_id, org_id=org_id)
    if row is None or not can_view_skill(row, viewer_user_id):
        return None
    return SkillFileDraft.model_validate(row)


async def get_guide(skill_id: str, *, org_id: str, viewer_user_id: str) -> GuideSummary:
    row = await _get_skill_row(skill_id, org_id=org_id)
    if row is None or not can_view_skill(row, viewer_user_id) or row.get("status") != "approved":
        raise ValueError("Guide was not found.")
    return guide_payload(SkillFileDraft.model_validate(row))
