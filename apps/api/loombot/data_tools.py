"""Read-only data lookups for Ask.

``count_emails`` reads the caller's own connected mailbox live (indexed mail is
only a recent sample, so it cannot answer "how many"). ``knowledge_inventory``
counts what MindLoom has indexed per source. ``project_status`` combines the
Status board with project entities from the knowledge graph. Every lookup is
scoped to the caller's connections and search permissions.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

import httpx
from fastapi import HTTPException

from core.database import get_neo4j_driver
from keeper.auth import get_user_access_tokens

logger = logging.getLogger(__name__)

MailProvider = Literal["auto", "gmail", "outlook"]
MailFolder = Literal["inbox", "all_received"]

_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_GRAPH_API = "https://graph.microsoft.com/v1.0/me"
_MAX_COUNTED_MESSAGES = 5000
_SAMPLE_SIZE = 5

_SOURCE_ALIASES = {
    "email": ["gmail", "outlook_email"],
    "mail": ["gmail", "outlook_email"],
    "outlook": ["outlook_email"],
    "outlook_mail": ["outlook_email"],
    "drive": ["google_drive"],
    "calendar": ["google_calendar", "outlook_calendar"],
    "teams": ["microsoft_teams", "microsoft_teams_chat"],
    "microsoft_teams": ["microsoft_teams", "microsoft_teams_chat"],
    "whatsapp": ["whatsapp_export"],
    "slack": ["slack"],
    "zoom_meetings": ["zoom"],
    "uploads": ["pdf", "word", "spreadsheet", "presentation", "upload"],
}


class _MailboxUnavailable(Exception):
    """The caller has no usable mailbox for this provider."""


def jsonable(value: Any) -> Any:
    """Convert Neo4j temporals, datetimes, and Pydantic models for json.dumps."""

    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        return to_native().isoformat()
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value


def _parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _day_bounds(start: date | None, end: date | None) -> tuple[datetime | None, datetime | None]:
    """UTC [start, end) bounds for inclusive calendar dates."""

    lower = datetime.combine(start, time.min, tzinfo=timezone.utc) if start else None
    upper = (
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
        if end
        else None
    )
    return lower, upper


async def _approved_mailbox(org_id: str, user_id: str, provider: str, resource_id: str) -> bool:
    from weaver.connection_setup import get_policy

    policy = await get_policy(org_id, user_id, provider)
    if policy is None:
        return False
    return resource_id in set(json.loads(policy.included_resources or "[]"))


# --- Mailbox counts ---------------------------------------------------------


async def _gmail_samples(
    client: httpx.AsyncClient, headers: dict[str, str], query: str
) -> list[dict[str, str]]:
    listed = await client.get(
        f"{_GMAIL_API}/messages",
        headers=headers,
        params={"q": query, "maxResults": str(_SAMPLE_SIZE)},
    )
    listed.raise_for_status()
    samples: list[dict[str, str]] = []
    for item in listed.json().get("messages") or []:
        response = await client.get(
            f"{_GMAIL_API}/messages/{item['id']}",
            headers=headers,
            params=[
                ("format", "metadata"),
                ("metadataHeaders", "Subject"),
                ("metadataHeaders", "From"),
                ("metadataHeaders", "Date"),
            ],
        )
        if response.status_code >= 400:
            continue
        values = {
            str(h.get("name") or "").lower(): str(h.get("value") or "")
            for h in (response.json().get("payload") or {}).get("headers") or []
        }
        samples.append({
            "subject": values.get("subject", "(No subject)"),
            "from": values.get("from", ""),
            "date": values.get("date", ""),
        })
    return samples


async def _count_gmail(
    org_id: str,
    user_id: str,
    *,
    lower: datetime | None,
    upper: datetime | None,
    folder: MailFolder,
    unread_only: bool,
    search: str,
) -> dict[str, Any]:
    from weaver.google_workspace import PROVIDER_GOOGLE_WORKSPACE, _workspace_access_token
    from weaver.integrations import _get_connection

    if await _get_connection(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE) is None:
        raise _MailboxUnavailable(
            "Google Workspace is not connected. Connect it from Import → Add connector."
        )
    if not await _approved_mailbox(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE, "mailbox:gmail"):
        raise _MailboxUnavailable(
            "Gmail is not an approved source on your Google Workspace connection. "
            "Enable it in Import → Google Workspace → Manage connection."
        )
    token, account = await _workspace_access_token(org_id, user_id)
    if token.startswith("dev:"):
        raise _MailboxUnavailable(
            "Google Workspace is connected in development mode, so there is no real mailbox to read."
        )
    headers = {"Authorization": f"Bearer {token}"}

    parts = ["in:inbox"] if folder == "inbox" else ["-in:sent", "-in:drafts", "-in:chats"]
    if lower:
        parts.append(f"after:{int(lower.timestamp())}")
    if upper:
        parts.append(f"before:{int(upper.timestamp())}")
    if unread_only:
        parts.append("is:unread")
    if search:
        parts.append(search)
    query = " ".join(parts)

    async with httpx.AsyncClient(timeout=30) as client:
        if folder == "inbox" and not (lower or upper or unread_only or search):
            response = await client.get(f"{_GMAIL_API}/labels/INBOX", headers=headers)
            response.raise_for_status()
            label = response.json()
            return {
                "provider": "gmail",
                "account": account,
                "scope": "Gmail inbox, all time",
                "count": int(label.get("messagesTotal") or 0),
                "unread": int(label.get("messagesUnread") or 0),
                "exact": True,
                "recent": await _gmail_samples(client, headers, query),
            }

        count = 0
        exact = True
        page_token: str | None = None
        while True:
            params = {"q": query, "maxResults": "500"}
            if page_token:
                params["pageToken"] = page_token
            response = await client.get(f"{_GMAIL_API}/messages", headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
            count += len(payload.get("messages") or [])
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
            if count >= _MAX_COUNTED_MESSAGES:
                exact = False
                break
        return {
            "provider": "gmail",
            "account": account,
            "scope": "Gmail inbox" if folder == "inbox" else "All received Gmail (excludes sent, drafts, spam, trash)",
            "gmail_query": query,
            "count": count,
            "exact": exact,
            "note": None if exact else f"Stopped counting at {count}; the real number is higher.",
            "recent": await _gmail_samples(client, headers, query),
        }


async def _count_outlook(
    org_id: str,
    user_id: str,
    *,
    lower: datetime | None,
    upper: datetime | None,
    unread_only: bool,
    search: str,
) -> dict[str, Any]:
    from weaver.integrations import _get_connection
    from weaver.microsoft_teams import PROVIDER_MICROSOFT_TEAMS, _teams_token

    if await _get_connection(org_id, user_id, PROVIDER_MICROSOFT_TEAMS) is None:
        raise _MailboxUnavailable(
            "Microsoft 365 is not connected. Connect it from Import → Add connector."
        )
    if not await _approved_mailbox(org_id, user_id, PROVIDER_MICROSOFT_TEAMS, "mailbox:outlook"):
        raise _MailboxUnavailable(
            "Outlook Inbox is not an approved source on your Microsoft 365 connection. "
            "Enable it in Import → Microsoft 365 → Manage connection."
        )
    token, account = await _teams_token(org_id, user_id)
    if token.startswith("dev:"):
        raise _MailboxUnavailable(
            "Microsoft 365 is connected in development mode, so there is no real mailbox to read."
        )
    headers = {"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"}

    async with httpx.AsyncClient(timeout=30) as client:
        if not (lower or upper or unread_only):
            response = await client.get(
                f"{_GRAPH_API}/mailFolders/inbox",
                headers=headers,
                params={"$select": "totalItemCount,unreadItemCount"},
            )
            response.raise_for_status()
            folder = response.json()
            count: int | None = int(folder.get("totalItemCount") or 0)
            unread: int | None = int(folder.get("unreadItemCount") or 0)
            listed = await client.get(
                f"{_GRAPH_API}/mailFolders/inbox/messages",
                headers=headers,
                params={
                    "$top": str(_SAMPLE_SIZE),
                    "$orderby": "receivedDateTime desc",
                    "$select": "subject,from,receivedDateTime",
                },
            )
        else:
            filters = []
            if lower:
                filters.append(f"receivedDateTime ge {lower.strftime('%Y-%m-%dT%H:%M:%SZ')}")
            if upper:
                filters.append(f"receivedDateTime lt {upper.strftime('%Y-%m-%dT%H:%M:%SZ')}")
            if unread_only:
                filters.append("isRead eq false")
            if not lower and not upper:
                filters.insert(0, "receivedDateTime ge 1900-01-01T00:00:00Z")
            listed = await client.get(
                f"{_GRAPH_API}/mailFolders/inbox/messages",
                headers=headers,
                params={
                    "$filter": " and ".join(filters),
                    "$count": "true",
                    "$top": str(_SAMPLE_SIZE),
                    "$orderby": "receivedDateTime desc",
                    "$select": "subject,from,receivedDateTime",
                },
            )
            count = None
            unread = None
        listed.raise_for_status()
        payload = listed.json()
        if count is None:
            count = int(payload.get("@odata.count") or 0)
    recent = [
        {
            "subject": str(item.get("subject") or "(No subject)"),
            "from": str(((item.get("from") or {}).get("emailAddress") or {}).get("address") or ""),
            "date": str(item.get("receivedDateTime") or ""),
        }
        for item in payload.get("value") or []
    ]
    result: dict[str, Any] = {
        "provider": "outlook",
        "account": account,
        "scope": "Outlook inbox",
        "count": count,
        "exact": True,
        "recent": recent,
    }
    if unread is not None:
        result["unread"] = unread
    if search:
        result["note"] = "Text search is applied to Gmail only; this Outlook count ignores it."
    return result


async def count_emails(
    org_id: str,
    user_id: str,
    *,
    provider: str = "auto",
    start_date: str | None = None,
    end_date: str | None = None,
    folder: str = "inbox",
    unread_only: bool = False,
    search: str | None = None,
) -> dict[str, Any]:
    """Count messages in the caller's connected Gmail and/or Outlook mailbox."""

    start, end = _parse_day(start_date), _parse_day(end_date)
    if start and end and end < start:
        return {"error": "end_date is before start_date."}
    lower, upper = _day_bounds(start, end)
    mail_folder: MailFolder = "all_received" if folder == "all_received" else "inbox"
    cleaned_search = " ".join(str(search or "").split())
    wanted = str(provider or "auto").strip().lower()
    providers = ["gmail", "outlook"] if wanted in {"", "auto", "all", "any"} else [wanted]

    mailboxes: list[dict[str, Any]] = []
    problems: list[str] = []
    for name in providers:
        try:
            if name == "gmail":
                mailboxes.append(await _count_gmail(
                    org_id, user_id, lower=lower, upper=upper, folder=mail_folder,
                    unread_only=unread_only, search=cleaned_search,
                ))
            elif name == "outlook":
                mailboxes.append(await _count_outlook(
                    org_id, user_id, lower=lower, upper=upper,
                    unread_only=unread_only, search=cleaned_search,
                ))
            else:
                problems.append(f"Unknown mail provider '{name}'. Use gmail or outlook.")
        except _MailboxUnavailable as exc:
            problems.append(str(exc))
        except HTTPException as exc:
            problems.append(f"{name}: {exc.detail}")
        except httpx.HTTPStatusError as exc:
            logger.warning("Mailbox count failed for %s: %s", name, exc.response.text[:300])
            status = exc.response.status_code
            hint = " Reconnect the account to grant mail read access." if status in (401, 403) else ""
            problems.append(f"{name}: the mail API returned {status}.{hint}")
        except httpx.HTTPError as exc:
            problems.append(f"{name}: could not reach the mail API ({exc.__class__.__name__}).")

    filters = {
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "folder": mail_folder,
        "unread_only": unread_only,
        "search": cleaned_search or None,
        "timezone": "UTC day boundaries",
    }
    if not mailboxes:
        return {
            "error": " ".join(problems) or (
                "No mailbox is connected. Connect Google Workspace or Microsoft 365 "
                "from Import → Add connector."
            ),
            "filters": filters,
        }
    return {"mailboxes": mailboxes, "unavailable": problems, "filters": filters}


# --- Indexed knowledge inventory -------------------------------------------

_VISIBLE_DOCUMENT = """
MATCH (d:Document {org_id: $org_id})
WHERE (size(coalesce(d.visible_to, [])) = 0
       OR any(token IN coalesce(d.visible_to, []) WHERE token IN $access_tokens))
  AND ($sources IS NULL
       OR toLower(coalesce(d.source, '')) IN $sources
       OR replace(toLower(coalesce(d.source_application, '')), ' ', '_') IN $sources)
  AND ($title IS NULL
       OR toLower(coalesce(d.title, d.source_label, d.original_filename, '')) CONTAINS $title)
WITH d, coalesce(d.source_created_at, d.uploaded_at) AS stamp
WHERE ($start IS NULL OR (stamp IS NOT NULL AND datetime(toString(stamp)) >= datetime($start)))
  AND ($end IS NULL OR (stamp IS NOT NULL AND datetime(toString(stamp)) < datetime($end)))
"""

_INVENTORY_GROUPS_CYPHER = _VISIBLE_DOCUMENT + """
RETURN coalesce(d.source, 'unknown') AS source,
       coalesce(d.source_application, '') AS application,
       count(d) AS documents,
       max(stamp) AS latest
ORDER BY documents DESC
"""

_INVENTORY_RECENT_CYPHER = _VISIBLE_DOCUMENT + """
RETURN coalesce(d.title, d.source_label, d.original_filename, 'Untitled') AS title,
       coalesce(d.source, 'unknown') AS source,
       stamp AS date,
       d.source_url AS url
ORDER BY stamp DESC
LIMIT 8
"""


def _source_filter(source: str | None) -> list[str] | None:
    key = "_".join(str(source or "").strip().lower().split())
    if not key or key in {"all", "any", "everything"}:
        return None
    return _SOURCE_ALIASES.get(key, [key])


async def knowledge_inventory(
    org_id: str,
    user_id: str,
    *,
    source: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    title_contains: str | None = None,
) -> dict[str, Any]:
    """Count indexed documents the caller can see, grouped by source."""

    lower, upper = _day_bounds(_parse_day(start_date), _parse_day(end_date))
    title = " ".join(str(title_contains or "").lower().split()) or None
    params = {
        "org_id": org_id,
        "access_tokens": await get_user_access_tokens(org_id, user_id),
        "sources": _source_filter(source),
        "title": title,
        "start": lower.isoformat() if lower else None,
        "end": upper.isoformat() if upper else None,
    }

    async def _read(tx):  # type: ignore[no-untyped-def]
        groups = [r.data() async for r in await tx.run(_INVENTORY_GROUPS_CYPHER, **params)]
        recent = [r.data() async for r in await tx.run(_INVENTORY_RECENT_CYPHER, **params)]
        return groups, recent

    async with get_neo4j_driver().session() as session:
        groups, recent = await session.execute_read(_read)

    return jsonable({
        "note": (
            "Counts cover what MindLoom has indexed and you may search. Connector "
            "syncs import recent items, so this can be lower than the live source."
        ),
        "filters": {
            "sources": params["sources"],
            "title_contains": title,
            "start": params["start"],
            "end_exclusive": params["end"],
        },
        "total_documents": sum(int(group["documents"]) for group in groups),
        "by_source": groups,
        "most_recent": recent,
    })


# --- Project status ---------------------------------------------------------

_PROJECT_GRAPH_CYPHER = """
MATCH (e:Entity {org_id: $org_id, type: 'project'})
WHERE toLower(e.name) CONTAINS $needle
OPTIONAL MATCH (c:Chunk {org_id: $org_id})-[:RELATES_TO]->(e)
WHERE size(coalesce(c.visible_to, [])) = 0
   OR any(token IN coalesce(c.visible_to, []) WHERE token IN $access_tokens)
WITH e, c
ORDER BY c.end_time DESC
WITH e, collect(c)[0..6] AS chunks
WHERE size(chunks) > 0
RETURN e.entity_id AS entity_id,
       e.name AS name,
       coalesce(e.work_status, 'open') AS work_status,
       e.closed_at AS closed_at,
       e.last_signal_at AS last_signal_at,
       [x IN chunks | {
         chunk_id: x.chunk_id,
         summary: coalesce(x.summary, ''),
         knowledge_type: coalesce(x.knowledge_type, ''),
         source_label: coalesce(x.source_label, x.source, ''),
         date: x.end_time
       }] AS recent_mentions
ORDER BY coalesce(e.last_signal_at, datetime('1970-01-01T00:00:00Z')) DESC
LIMIT 5
"""


def _project_needle(project: str | None) -> str:
    words = str(project or "").strip().strip("\"'").lower().split()
    while words and words[0] in {"the", "project"}:
        words.pop(0)
    while words and words[-1] == "project":
        words.pop()
    return " ".join(words)


def _evidence_rows(items: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": item.chunk_id,
            "summary": item.summary,
            "kind": item.knowledge_type,
            "source": item.source_label or item.source,
            "date": item.end_time,
        }
        for item in items
    ]


def _board_project(project: Any) -> dict[str, Any]:
    return {
        "name": project.name,
        "work_status": project.work_status,
        "current_status": project.current_status,
        "last_update_at": project.last_signal_at,
        "recent_updates": _evidence_rows(project.recent_updates),
    }


def _board_issue(issue: Any) -> dict[str, Any]:
    return {
        "title": issue.title,
        "kind": issue.kind,
        "project": issue.project,
        "last_seen_at": issue.last_seen_at,
        "evidence": _evidence_rows(issue.evidence),
    }


def _board_action(action: Any) -> dict[str, Any]:
    return {
        "text": action.text,
        "assignee": action.assignee,
        "project": action.project,
        "last_update_at": action.last_signal_at,
        "evidence": _evidence_rows(action.evidence),
    }


async def project_status(
    org_id: str, user_id: str, *, project: str | None = None
) -> dict[str, Any]:
    """Status-board view of one project, or an overview of all open work."""

    from lens.status_board import get_open_status

    board = await get_open_status(org_id, user_id)
    needle = _project_needle(project)
    if not needle:
        return jsonable({
            "totals": {
                "open_projects": len(board.projects),
                "open_issues": len(board.issues),
                "open_action_items": len(board.action_items),
            },
            "open_projects": [_board_project(p) for p in board.projects[:15]],
            "open_issues": [_board_issue(i) for i in board.issues[:10]],
            "open_action_items": [_board_action(a) for a in board.action_items[:10]],
        })

    def _matches(*values: str | None) -> bool:
        return any(needle in str(value or "").lower() for value in values)

    projects = [p for p in board.projects if _matches(p.name)]
    issues = [i for i in board.issues if _matches(i.project, i.title)]
    actions = [a for a in board.action_items if _matches(a.project, a.text)]

    access_tokens = await get_user_access_tokens(org_id, user_id)

    async def _read(tx):  # type: ignore[no-untyped-def]
        result = await tx.run(
            _PROJECT_GRAPH_CYPHER,
            org_id=org_id,
            needle=needle,
            access_tokens=access_tokens,
        )
        return [record.data() async for record in result]

    async with get_neo4j_driver().session() as session:
        graph_projects = await session.execute_read(_read)

    if not (projects or issues or actions or graph_projects):
        return {
            "found": False,
            "project": needle,
            "note": (
                f"No project matching '{needle}' is on the Status board or in the "
                "knowledge graph you can see."
            ),
        }
    return jsonable({
        "found": True,
        "project": needle,
        "status_board": {
            "projects": [_board_project(p) for p in projects],
            "open_issues": [_board_issue(i) for i in issues],
            "open_action_items": [_board_action(a) for a in actions],
        },
        "knowledge_graph_projects": graph_projects,
        "note": (
            "Status board items are open work detected from connected sources. "
            "knowledge_graph_projects include closed projects and their latest mentions."
        ),
    })
