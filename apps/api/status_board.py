"""Query open projects, issues, and action items for the Status tab."""

from __future__ import annotations

import logging
from datetime import datetime

from auth import get_user_access_tokens
from database import get_neo4j_driver
from models import (
    OpenStatusResponse,
    StatusActionItem,
    StatusEvidence,
    StatusIssueItem,
    StatusProjectItem,
)

logger = logging.getLogger(__name__)

_OPEN_PROJECTS_CYPHER = """
MATCH (e:Entity {org_id: $org_id, type: 'project'})
WHERE coalesce(e.work_status, 'open') = 'open'
OPTIONAL MATCH (c:Chunk {org_id: $org_id})-[:RELATES_TO]->(e)
WITH e, c
WHERE c IS NULL
   OR size(coalesce(c.visible_to, [])) = 0
   OR any(token IN coalesce(c.visible_to, []) WHERE token IN $access_tokens)
WITH e, c
ORDER BY coalesce(c.end_time, e.last_signal_at) DESC
WITH e, collect(c)[0..3] AS chunks
RETURN e.entity_id AS entity_id,
       e.name AS name,
       coalesce(e.work_status, 'open') AS work_status,
       e.last_signal_at AS last_signal_at,
       [x IN chunks WHERE x IS NOT NULL | {
         chunk_id: x.chunk_id,
         summary: coalesce(x.summary, ''),
         source: coalesce(x.source, ''),
         source_label: coalesce(x.source_label, '')
       }] AS evidence
ORDER BY coalesce(e.last_signal_at, datetime('1970-01-01T00:00:00Z')) DESC
LIMIT 100
"""

_OPEN_ISSUES_CYPHER = """
MATCH (i:OpenIssue {org_id: $org_id, status: 'open'})
WHERE size(coalesce(i.visible_to, [])) = 0
   OR any(token IN coalesce(i.visible_to, []) WHERE token IN $access_tokens)
OPTIONAL MATCH (i)-[:ABOUT]->(project:Entity)
OPTIONAL MATCH (i)-[:EVIDENCED_BY]->(c:Chunk {org_id: $org_id})
WITH i, project, c
WHERE c IS NULL
   OR size(coalesce(c.visible_to, [])) = 0
   OR any(token IN coalesce(c.visible_to, []) WHERE token IN $access_tokens)
WITH i, project, c
ORDER BY coalesce(c.end_time, i.last_seen_at) DESC
WITH i, project, collect(c)[0..3] AS chunks
RETURN i.issue_id AS issue_id,
       i.title AS title,
       i.kind AS kind,
       i.status AS status,
       project.name AS project,
       i.last_seen_at AS last_seen_at,
       [x IN chunks WHERE x IS NOT NULL | {
         chunk_id: x.chunk_id,
         summary: coalesce(x.summary, ''),
         source: coalesce(x.source, ''),
         source_label: coalesce(x.source_label, '')
       }] AS evidence
ORDER BY coalesce(i.last_seen_at, datetime('1970-01-01T00:00:00Z')) DESC
LIMIT 100
"""

_OPEN_ACTIONS_CYPHER = """
MATCH (a:ActionItem {org_id: $org_id, status: 'open'})
WHERE size(coalesce(a.visible_to, [])) = 0
   OR any(token IN coalesce(a.visible_to, []) WHERE token IN $access_tokens)
OPTIONAL MATCH (a)-[:PART_OF]->(project:Entity)
OPTIONAL MATCH (a)-[:ASSIGNED_TO]->(person:Person)
OPTIONAL MATCH (a)-[:EVIDENCED_BY]->(c:Chunk {org_id: $org_id})
WITH a, project, person, c
WHERE c IS NULL
   OR size(coalesce(c.visible_to, [])) = 0
   OR any(token IN coalesce(c.visible_to, []) WHERE token IN $access_tokens)
WITH a, project, person, c
ORDER BY coalesce(c.end_time, a.last_signal_at, a.created_at) DESC
WITH a, project, person, collect(c)[0..3] AS chunks
RETURN a.action_item_id AS action_item_id,
       a.text AS text,
       a.status AS status,
       coalesce(a.assignee, person.name) AS assignee,
       project.name AS project,
       a.created_at AS created_at,
       a.last_signal_at AS last_signal_at,
       [x IN chunks WHERE x IS NOT NULL | {
         chunk_id: x.chunk_id,
         summary: coalesce(x.summary, ''),
         source: coalesce(x.source, ''),
         source_label: coalesce(x.source_label, '')
       }] AS evidence
ORDER BY coalesce(a.last_signal_at, a.created_at, datetime('1970-01-01T00:00:00Z')) DESC
LIMIT 100
"""


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
            )
        )
    return items


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


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

    projects = [
        StatusProjectItem(
            entity_id=str(row.get("entity_id") or ""),
            name=str(row.get("name") or "Untitled project"),
            work_status=row.get("work_status") or "open",
            last_signal_at=_as_datetime(row.get("last_signal_at")),
            evidence=_evidence_list(row.get("evidence")),
        )
        for row in project_rows
        if row.get("entity_id")
    ]
    issues = [
        StatusIssueItem(
            issue_id=str(row.get("issue_id") or ""),
            title=str(row.get("title") or "Untitled report"),
            kind=row.get("kind") or "problem_report",
            status=row.get("status") or "open",
            project=row.get("project"),
            last_seen_at=_as_datetime(row.get("last_seen_at")),
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
