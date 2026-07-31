"""Query open projects, issues, and action items for the Status tab."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from auth import get_user_access_tokens
from database import get_neo4j_driver
from models import (
    FinishStatusItemResponse,
    OpenStatusResponse,
    StatusActionItem,
    StatusEvidence,
    StatusIssueItem,
    StatusItemKind,
    StatusProjectItem,
)

logger = logging.getLogger(__name__)

_EVIDENCE_MAP = """
{
  chunk_id: x.chunk_id,
  summary: coalesce(x.summary, ''),
  source: coalesce(x.source, ''),
  source_label: coalesce(x.source_label, ''),
  knowledge_type: coalesce(x.knowledge_type, ''),
  end_time: x.end_time,
  excerpt: left(coalesce(x.raw_text, ''), 500)
}
"""

_OPEN_PROJECTS_CYPHER = f"""
MATCH (e:Entity {{org_id: $org_id, type: 'project'}})
WHERE coalesce(e.work_status, 'open') = 'open'
OPTIONAL MATCH (c:Chunk {{org_id: $org_id}})-[:RELATES_TO]->(e)
WITH e, c
WHERE c IS NULL
   OR size(coalesce(c.visible_to, [])) = 0
   OR any(token IN coalesce(c.visible_to, []) WHERE token IN $access_tokens)
WITH e, c
ORDER BY coalesce(c.end_time, e.last_signal_at) DESC
WITH e, collect(c)[0..16] AS chunks
OPTIONAL MATCH (issue:OpenIssue {{org_id: $org_id}})-[:ABOUT]->(e)
WHERE size(coalesce(issue.visible_to, [])) = 0
   OR any(token IN coalesce(issue.visible_to, []) WHERE token IN $access_tokens)
OPTIONAL MATCH (issue)-[:EVIDENCED_BY]->(ic:Chunk {{org_id: $org_id}})
WITH e, chunks, issue, ic
WHERE ic IS NULL
   OR size(coalesce(ic.visible_to, [])) = 0
   OR any(token IN coalesce(ic.visible_to, []) WHERE token IN $access_tokens)
WITH e, chunks, issue, ic
ORDER BY coalesce(ic.end_time, issue.last_seen_at) DESC
WITH e, chunks,
     collect(DISTINCT issue)[0..8] AS issues,
     collect(ic)[0..8] AS issue_chunks
RETURN e.entity_id AS entity_id,
       e.name AS name,
       coalesce(e.work_status, 'open') AS work_status,
       e.last_signal_at AS last_signal_at,
       e.closed_at AS closed_at,
       [x IN chunks WHERE x IS NOT NULL | {_EVIDENCE_MAP}] AS chunk_evidence,
       [i IN issues WHERE i IS NOT NULL | {{
         chunk_id: 'issue:' + i.issue_id,
         summary: coalesce(i.title, ''),
         source: 'connected_sources',
         source_label: CASE coalesce(i.kind, '')
           WHEN 'status_update' THEN 'Status update'
           WHEN 'problem_report' THEN 'Problem report'
           ELSE 'Project update'
         END,
         knowledge_type: coalesce(i.kind, 'status_update'),
         end_time: i.last_seen_at,
         excerpt: coalesce(i.title, '')
       }}] AS issue_evidence,
       [x IN issue_chunks WHERE x IS NOT NULL | {_EVIDENCE_MAP}] AS issue_chunk_evidence
ORDER BY coalesce(e.last_signal_at, datetime('1970-01-01T00:00:00Z')) DESC
LIMIT 100
"""

_OPEN_ISSUES_CYPHER = f"""
MATCH (i:OpenIssue {{org_id: $org_id, status: 'open'}})
WHERE size(coalesce(i.visible_to, [])) = 0
   OR any(token IN coalesce(i.visible_to, []) WHERE token IN $access_tokens)
OPTIONAL MATCH (i)-[:ABOUT]->(project:Entity)
OPTIONAL MATCH (i)-[:EVIDENCED_BY]->(c:Chunk {{org_id: $org_id}})
WITH i, project, c
WHERE c IS NULL
   OR size(coalesce(c.visible_to, [])) = 0
   OR any(token IN coalesce(c.visible_to, []) WHERE token IN $access_tokens)
WITH i, project, c
ORDER BY coalesce(c.end_time, i.last_seen_at) DESC
WITH i, project, collect(c)[0..8] AS chunks
RETURN i.issue_id AS issue_id,
       i.title AS title,
       i.kind AS kind,
       i.status AS status,
       project.name AS project,
       i.created_at AS created_at,
       i.last_seen_at AS last_seen_at,
       i.closed_at AS closed_at,
       [x IN chunks WHERE x IS NOT NULL | {_EVIDENCE_MAP}] AS evidence
ORDER BY coalesce(i.last_seen_at, datetime('1970-01-01T00:00:00Z')) DESC
LIMIT 100
"""

_OPEN_ACTIONS_CYPHER = f"""
MATCH (a:ActionItem {{org_id: $org_id, status: 'open'}})
WHERE size(coalesce(a.visible_to, [])) = 0
   OR any(token IN coalesce(a.visible_to, []) WHERE token IN $access_tokens)
OPTIONAL MATCH (a)-[:PART_OF]->(project:Entity)
OPTIONAL MATCH (a)-[:ASSIGNED_TO]->(person:Person)
OPTIONAL MATCH (a)-[:EVIDENCED_BY]->(c:Chunk {{org_id: $org_id}})
WITH a, project, person, c
WHERE c IS NULL
   OR size(coalesce(c.visible_to, [])) = 0
   OR any(token IN coalesce(c.visible_to, []) WHERE token IN $access_tokens)
WITH a, project, person, c
ORDER BY coalesce(c.end_time, a.last_signal_at, a.created_at) DESC
WITH a, project, person, collect(c)[0..8] AS chunks
RETURN a.action_item_id AS action_item_id,
       a.text AS text,
       a.status AS status,
       coalesce(a.assignee, person.name) AS assignee,
       project.name AS project,
       a.created_at AS created_at,
       a.last_signal_at AS last_signal_at,
       a.closed_at AS closed_at,
       [x IN chunks WHERE x IS NOT NULL | {_EVIDENCE_MAP}] AS evidence
ORDER BY coalesce(a.last_signal_at, a.created_at, datetime('1970-01-01T00:00:00Z')) DESC
LIMIT 100
"""

_FINISH_PROJECT_CYPHER = """
MATCH (e:Entity {org_id: $org_id, entity_id: $item_id, type: 'project'})
WHERE coalesce(e.work_status, 'open') = 'open'
SET e.work_status = 'closed',
    e.closed_at = $ts,
    e.last_signal_at = $ts
RETURN e.entity_id AS item_id, e.work_status AS status
"""

_FINISH_ISSUE_CYPHER = """
MATCH (i:OpenIssue {org_id: $org_id, issue_id: $item_id})
WHERE i.status = 'open'
  AND (
    size(coalesce(i.visible_to, [])) = 0
    OR any(token IN coalesce(i.visible_to, []) WHERE token IN $access_tokens)
  )
SET i.status = 'closed',
    i.closed_at = $ts,
    i.last_seen_at = $ts
RETURN i.issue_id AS item_id, i.status AS status
"""

_FINISH_ACTION_CYPHER = """
MATCH (a:ActionItem {org_id: $org_id, action_item_id: $item_id})
WHERE a.status = 'open'
  AND (
    size(coalesce(a.visible_to, [])) = 0
    OR any(token IN coalesce(a.visible_to, []) WHERE token IN $access_tokens)
  )
SET a.status = 'done',
    a.closed_at = $ts,
    a.last_signal_at = $ts
RETURN a.action_item_id AS item_id, a.status AS status
"""

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _evidence_list(raw: object) -> list[StatusEvidence]:
    if not isinstance(raw, list):
        return []
    items: list[StatusEvidence] = []
    for row in raw:
        if not isinstance(row, dict) or not row.get("chunk_id"):
            continue
        items.append(
            StatusEvidence(
                chunk_id=str(row["chunk_id"]),
                summary=str(row.get("summary") or ""),
                source=str(row.get("source") or ""),
                source_label=str(row.get("source_label") or ""),
                knowledge_type=str(row.get("knowledge_type") or ""),
                end_time=_as_datetime(row.get("end_time")),
                excerpt=str(row.get("excerpt") or ""),
            )
        )
    return items


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


def _update_sort_key(item: StatusEvidence) -> tuple[datetime, int]:
    """Prefer fresher signals; break ties toward explicit status updates."""

    stamp = item.end_time or _EPOCH
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    kind_boost = 1 if item.knowledge_type in {"status_update", "problem_report"} else 0
    return (stamp, kind_boost)


def merge_project_updates(*groups: list[StatusEvidence]) -> list[StatusEvidence]:
    """Dedupe and sort project updates from chunks and related issues."""

    by_id: dict[str, StatusEvidence] = {}
    for group in groups:
        for item in group:
            existing = by_id.get(item.chunk_id)
            if existing is None or _update_sort_key(item) > _update_sort_key(existing):
                by_id[item.chunk_id] = item
    return sorted(by_id.values(), key=_update_sort_key, reverse=True)[:16]


def derive_current_status(updates: list[StatusEvidence]) -> str:
    """Human-readable latest status line from connected-source updates."""

    for item in updates:
        text = (item.summary or item.excerpt or "").strip()
        if text:
            return text
    return "No recent updates from connected sources yet."


async def get_open_status(org_id: str, user_id: str) -> OpenStatusResponse:
    """Return open projects, issues, and action items visible to the user."""

    access_tokens = await get_user_access_tokens(org_id, user_id)
    driver = get_neo4j_driver()

    async def _read(tx) -> tuple[list[dict], list[dict], list[dict]]:  # type: ignore[no-untyped-def]
        projects = [
            record.data()
            async for record in await tx.run(
                _OPEN_PROJECTS_CYPHER, org_id=org_id, access_tokens=access_tokens
            )
        ]
        issues = [
            record.data()
            async for record in await tx.run(
                _OPEN_ISSUES_CYPHER, org_id=org_id, access_tokens=access_tokens
            )
        ]
        actions = [
            record.data()
            async for record in await tx.run(
                _OPEN_ACTIONS_CYPHER, org_id=org_id, access_tokens=access_tokens
            )
        ]
        return projects, issues, actions

    async with driver.session() as session:
        project_rows, issue_rows, action_rows = await session.execute_read(_read)

    projects = []
    for row in project_rows:
        if not row.get("entity_id"):
            continue
        updates = merge_project_updates(
            _evidence_list(row.get("chunk_evidence")),
            _evidence_list(row.get("issue_evidence")),
            _evidence_list(row.get("issue_chunk_evidence")),
            # Back-compat if an older query shape somehow appears.
            _evidence_list(row.get("evidence")),
        )
        latest_at = updates[0].end_time if updates else None
        entity_signal = _as_datetime(row.get("last_signal_at"))
        if latest_at and entity_signal:
            last_signal = max(latest_at, entity_signal)
        else:
            last_signal = latest_at or entity_signal
        projects.append(
            StatusProjectItem(
                entity_id=str(row.get("entity_id") or ""),
                name=str(row.get("name") or "Untitled project"),
                work_status=row.get("work_status") or "open",
                current_status=derive_current_status(updates),
                last_signal_at=last_signal,
                closed_at=_as_datetime(row.get("closed_at")),
                recent_updates=updates,
                evidence=updates,
            )
        )
    issues = [
        StatusIssueItem(
            issue_id=str(row.get("issue_id") or ""),
            title=str(row.get("title") or "Untitled report"),
            kind=row.get("kind") or "problem_report",
            status=row.get("status") or "open",
            project=row.get("project"),
            created_at=_as_datetime(row.get("created_at")),
            last_seen_at=_as_datetime(row.get("last_seen_at")),
            closed_at=_as_datetime(row.get("closed_at")),
            evidence=_evidence_list(row.get("evidence")),
        )
        for row in issue_rows
        if row.get("issue_id")
    ]
    action_items = [
        StatusActionItem(
            action_item_id=str(row.get("action_item_id") or ""),
            text=str(row.get("text") or ""),
            status=row.get("status") or "open",
            assignee=row.get("assignee"),
            project=row.get("project"),
            created_at=_as_datetime(row.get("created_at")),
            last_signal_at=_as_datetime(row.get("last_signal_at")),
            closed_at=_as_datetime(row.get("closed_at")),
            evidence=_evidence_list(row.get("evidence")),
        )
        for row in action_rows
        if row.get("action_item_id") and row.get("text")
    ]

    logger.info(
        "Status board for org %s: %d projects, %d issues, %d actions",
        org_id,
        len(projects),
        len(issues),
        len(action_items),
    )
    return OpenStatusResponse(
        projects=projects, issues=issues, action_items=action_items
    )


async def mark_status_item_finished(
    org_id: str,
    user_id: str,
    kind: StatusItemKind,
    item_id: str,
) -> FinishStatusItemResponse:
    """Mark a project, issue, or action item finished so it leaves the open board."""

    cleaned_id = item_id.strip()
    if not cleaned_id:
        raise ValueError("Item id is required.")

    access_tokens = await get_user_access_tokens(org_id, user_id)
    ts = datetime.now(timezone.utc)
    driver = get_neo4j_driver()

    if kind == "project":
        cypher = _FINISH_PROJECT_CYPHER
        params: dict = {"org_id": org_id, "item_id": cleaned_id, "ts": ts}
    elif kind == "issue":
        cypher = _FINISH_ISSUE_CYPHER
        params = {
            "org_id": org_id,
            "item_id": cleaned_id,
            "ts": ts,
            "access_tokens": access_tokens,
        }
    else:
        cypher = _FINISH_ACTION_CYPHER
        params = {
            "org_id": org_id,
            "item_id": cleaned_id,
            "ts": ts,
            "access_tokens": access_tokens,
        }

    async with driver.session() as session:
        result = await session.run(cypher, **params)
        record = await result.single()

    if record is None:
        raise LookupError(f"No open {kind.replace('_', ' ')} found to finish.")

    return FinishStatusItemResponse(
        kind=kind,
        item_id=str(record["item_id"]),
        status=str(record["status"]),
    )
