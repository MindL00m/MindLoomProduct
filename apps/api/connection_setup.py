"""Controlled workspace setup: discovery, policy, preview, and activation."""

from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import DateTime, String, Text, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from auth import Base
from database import get_session_factory
from google_workspace import (
    SyncCursorRow,
    _workspace_access_token,
    setup_drive_watch,
    sync_drive,
)
from google_calendar_sync import sync_google_calendar
from integrations import _delete_connection, _get_connection
from microsoft_teams import _graph_get, _teams_token, setup_teams_channel_watch, sync_teams
from microsoft365_sources import (
    sync_outlook_calendar,
    sync_outlook_mail,
    sync_teams_chats,
)
from subscriptions import IntegrationSubscriptionRow, expiring_subscriptions
from sharepoint import discover_sharepoint_resources, setup_sharepoint_watch, sync_sharepoint
from models import (
    ConnectionPolicyInput,
    ConnectionPolicyResponse,
    ConnectionPreviewResponse,
    ConnectionResource,
    ConnectionResourcesResponse,
    JobStatus,
)

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = {"google_workspace", "microsoft_teams", "zoom"}


class ConnectionPolicyRow(Base):
    __tablename__ = "connection_policies"

    policy_id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    included_resources: Mapped[str] = mapped_column(Text, nullable=False)
    excluded_resources: Mapped[str] = mapped_column(Text, nullable=False)
    include_history: Mapped[bool]
    history_start_date: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_frequency: Mapped[str] = mapped_column(String, nullable=False)
    access_mode: Mapped[str] = mapped_column(String, nullable=False)
    allowed_departments: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_user_ids: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _validate_provider(provider: str) -> str:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported connection provider.")
    return provider


async def get_policy(
    org_id: str, user_id: str, provider: str
) -> ConnectionPolicyRow | None:
    _validate_provider(provider)
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ConnectionPolicyRow).where(
                ConnectionPolicyRow.org_id == org_id,
                ConnectionPolicyRow.user_id == user_id,
                ConnectionPolicyRow.provider == provider,
            )
        )
        return result.scalar_one_or_none()


def _dev_resources(provider: str) -> list[ConnectionResource]:
    if provider == "google_workspace":
        return [
            ConnectionResource(id="mailbox:gmail", name="Gmail", kind="mailbox"),
            ConnectionResource(id="calendar:primary", name="Primary Google Calendar", kind="calendar"),
            ConnectionResource(id="drive:company", name="Company Shared Drive", kind="drive"),
            ConnectionResource(
                id="folder:policies", name="Policies", kind="folder", parent_id="drive:company"
            ),
            ConnectionResource(
                id="folder:engineering",
                name="Engineering",
                kind="folder",
                parent_id="drive:company",
            ),
            ConnectionResource(
                id="folder:finance",
                name="Finance (restricted)",
                kind="folder",
                parent_id="drive:company",
                warning="Some members may not have access.",
            ),
        ]
    if provider == "zoom":
        return [
            ConnectionResource(
                id="recording:transcripts",
                name="Cloud recording transcripts and summaries",
                kind="transcript",
            ),
            ConnectionResource(
                id="recording:chat",
                name="In-meeting chat files",
                kind="recording",
            ),
        ]
    return [
        ConnectionResource(id="mailbox:outlook", name="Outlook Inbox", kind="mailbox"),
        ConnectionResource(id="calendar:outlook", name="Outlook Calendar", kind="calendar"),
        ConnectionResource(id="chat:all", name="Teams private and group chats", kind="chat"),
        ConnectionResource(id="site:company", name="Company SharePoint", kind="site"),
        ConnectionResource(
            id="library:company:documents",
            name="Shared Documents",
            kind="library",
            parent_id="site:company",
        ),
        ConnectionResource(id="team:engineering", name="Engineering", kind="team"),
        ConnectionResource(
            id="channel:engineering:general",
            name="General",
            kind="channel",
            parent_id="team:engineering",
        ),
        ConnectionResource(
            id="channel:engineering:incidents",
            name="Incidents",
            kind="channel",
            parent_id="team:engineering",
        ),
        ConnectionResource(id="team:operations", name="Operations", kind="team"),
        ConnectionResource(
            id="channel:operations:general",
            name="General",
            kind="channel",
            parent_id="team:operations",
        ),
    ]


async def discover_resources(
    org_id: str, user_id: str, provider: str
) -> ConnectionResourcesResponse:
    _validate_provider(provider)
    connection = await _get_connection(org_id, user_id, provider)
    if connection is None:
        raise HTTPException(status_code=409, detail="Authorize this application first.")
    if connection.access_token.startswith("dev:"):
        return ConnectionResourcesResponse(provider=provider, resources=_dev_resources(provider))

    resources: list[ConnectionResource] = []
    if provider == "zoom":
        return ConnectionResourcesResponse(
            provider=provider,
            resources=_dev_resources(provider),
        )
    if provider == "google_workspace":
        resources.extend([
            ConnectionResource(id="mailbox:gmail", name="Gmail", kind="mailbox"),
            ConnectionResource(id="calendar:primary", name="Primary Google Calendar", kind="calendar"),
        ])
        token, _ = await _workspace_access_token(org_id, user_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            drives_response = await client.get(
                "https://www.googleapis.com/drive/v3/drives",
                headers={"Authorization": f"Bearer {token}"},
                params={"pageSize": "100", "fields": "drives(id,name)"},
            )
            if drives_response.status_code >= 400:
                logger.warning(
                    "Could not list Google shared drives (%s); returning base resources",
                    drives_response.status_code,
                )
                return ConnectionResourcesResponse(provider=provider, resources=resources)
            for drive in drives_response.json().get("drives", []):
                drive_id = str(drive["id"])
                resources.append(
                    ConnectionResource(
                        id=f"drive:{drive_id}", name=str(drive.get("name") or "Shared Drive"), kind="drive"
                    )
                )
                folders_response = await client.get(
                    "https://www.googleapis.com/drive/v3/files",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "corpora": "drive",
                        "driveId": drive_id,
                        "includeItemsFromAllDrives": "true",
                        "supportsAllDrives": "true",
                        "q": "mimeType='application/vnd.google-apps.folder' and trashed=false",
                        "pageSize": "100",
                        "fields": "files(id,name,parents)",
                    },
                )
                if folders_response.status_code >= 400:
                    resources[-1].warning = "Folders could not be listed."
                    continue
                for folder in folders_response.json().get("files", []):
                    resources.append(
                        ConnectionResource(
                            id=f"folder:{folder['id']}",
                            name=str(folder.get("name") or "Folder"),
                            kind="folder",
                            parent_id=f"drive:{drive_id}",
                        )
                    )
    else:
        token, _ = await _teams_token(org_id, user_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resources.extend([
                ConnectionResource(id="mailbox:outlook", name="Outlook Inbox", kind="mailbox"),
                ConnectionResource(id="calendar:outlook", name="Outlook Calendar", kind="calendar"),
                ConnectionResource(id="chat:all", name="All Teams private and group chats", kind="chat"),
            ])
            teams = await _graph_get(client, token, "/me/joinedTeams", {"$select": "id,displayName"})
            for team in teams.get("value", []):
                team_id = str(team["id"])
                resources.append(
                    ConnectionResource(
                        id=f"team:{team_id}", name=str(team.get("displayName") or "Team"), kind="team"
                    )
                )
                channels = await _graph_get(
                    client, token, f"/teams/{team_id}/channels", {"$select": "id,displayName,membershipType"}
                )
                for channel in channels.get("value", []):
                    resources.append(
                        ConnectionResource(
                            id=f"channel:{team_id}:{channel['id']}",
                            name=str(channel.get("displayName") or "Channel"),
                            kind="channel",
                            parent_id=f"team:{team_id}",
                        )
                    )
            for resource in await discover_sharepoint_resources(client, token):
                resources.append(ConnectionResource(**resource))
    return ConnectionResourcesResponse(provider=provider, resources=resources)


async def preview_policy(
    org_id: str, user_id: str, provider: str, policy: ConnectionPolicyInput
) -> ConnectionPreviewResponse:
    discovered = await discover_resources(org_id, user_id, provider)
    available = {resource.id: resource for resource in discovered.resources}
    missing = [item for item in policy.included_resource_ids if item not in available]
    warnings = [f"{item} is no longer available." for item in missing]
    warnings.extend(
        resource.warning
        for item, resource in available.items()
        if item in policy.included_resource_ids and resource.warning
    )
    selected = len(policy.included_resource_ids) - len(missing)
    estimated_items = 0
    estimated_size = 0
    scanned = 0
    exact = True
    connection = await _get_connection(org_id, user_id, provider)
    if connection and connection.access_token.startswith("dev:"):
        estimated_items = selected * 10
        estimated_size = estimated_items * 8_000
    else:
        try:
            if provider == "google_workspace":
                token, _ = await _workspace_access_token(org_id, user_id)
            elif provider == "microsoft_teams":
                token, _ = await _teams_token(org_id, user_id)
            else:
                from zoom_workspace import _zoom_token
                token, _ = await _zoom_token(org_id, user_id)
            async with httpx.AsyncClient(timeout=45.0) as client:
                if provider == "zoom":
                    response = await client.get(
                        "https://api.zoom.us/v2/users/me/recordings",
                        headers={"Authorization": f"Bearer {token}"},
                        params={
                            "page_size": "300",
                            "from": policy.history_start_date
                            or (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat(),
                            "to": datetime.now(timezone.utc).date().isoformat(),
                        },
                    )
                    response.raise_for_status()
                    meetings = response.json().get("meetings", [])
                    estimated_items = len(meetings)
                    scanned = estimated_items
                    estimated_size = sum(
                        int(meeting.get("total_size") or 0) for meeting in meetings
                    )
                    exact = not bool(response.json().get("next_page_token"))
                for resource_id in policy.included_resource_ids:
                    if provider == "zoom":
                        continue
                    if resource_id in missing:
                        continue
                    resource_size = 0
                    if resource_id.startswith("drive:") and provider == "google_workspace":
                        drive_id = resource_id.split(":", 1)[1]
                        page_token: str | None = None
                        count = 0
                        while True:
                            response = await client.get(
                                "https://www.googleapis.com/drive/v3/files",
                                headers={"Authorization": f"Bearer {token}"},
                                params={
                                    "corpora": "drive", "driveId": drive_id,
                                    "includeItemsFromAllDrives": "true",
                                    "supportsAllDrives": "true", "pageSize": "1000",
                                    "q": "trashed=false",
                                    "fields": "nextPageToken,files(mimeType,size)",
                                    **({"pageToken": page_token} if page_token else {}),
                                },
                            )
                            response.raise_for_status()
                            page = response.json()
                            files = page.get("files", [])
                            count += sum(
                                1 for item in files
                                if item.get("mimeType") != "application/vnd.google-apps.folder"
                            )
                            resource_size += sum(int(item.get("size") or 0) for item in files)
                            page_token = page.get("nextPageToken")
                            if not page_token:
                                break
                    elif resource_id.startswith("folder:") and provider == "google_workspace":
                        pending = [resource_id.split(":", 1)[1]]
                        visited: set[str] = set()
                        count = 0
                        while pending:
                            folder_id = pending.pop()
                            if folder_id in visited:
                                continue
                            visited.add(folder_id)
                            page_token = None
                            while True:
                                response = await client.get(
                                    "https://www.googleapis.com/drive/v3/files",
                                    headers={"Authorization": f"Bearer {token}"},
                                    params={
                                        "q": f"'{folder_id}' in parents and trashed=false",
                                        "supportsAllDrives": "true",
                                        "includeItemsFromAllDrives": "true",
                                        "pageSize": "1000",
                                        "fields": "nextPageToken,files(id,mimeType,size)",
                                        **({"pageToken": page_token} if page_token else {}),
                                    },
                                )
                                response.raise_for_status()
                                page = response.json()
                                for item in page.get("files", []):
                                    if item.get("mimeType") == "application/vnd.google-apps.folder":
                                        pending.append(str(item["id"]))
                                    else:
                                        count += 1
                                        resource_size += int(item.get("size") or 0)
                                page_token = page.get("nextPageToken")
                                if not page_token:
                                    break
                    elif resource_id.startswith("library:") and provider == "microsoft_teams":
                        drive_id = resource_id.split(":", 2)[2]
                        next_url: str | None = (
                            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta"
                            "?$select=id,size,file,folder"
                        )
                        count = 0
                        while next_url:
                            response = await client.get(
                                next_url, headers={"Authorization": f"Bearer {token}"}
                            )
                            response.raise_for_status()
                            page = response.json()
                            files = [item for item in page.get("value", []) if item.get("file")]
                            count += len(files)
                            resource_size += sum(int(item.get("size") or 0) for item in files)
                            next_url = page.get("@odata.nextLink")
                    elif resource_id == "mailbox:gmail":
                        response = await client.get(
                            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                            headers={"Authorization": f"Bearer {token}"},
                            params={"maxResults": "1"},
                        )
                        count = int(response.json().get("resultSizeEstimate") or 0)
                        exact = False
                    elif resource_id == "calendar:primary":
                        response = await client.get(
                            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                            headers={"Authorization": f"Bearer {token}"},
                            params={"maxResults": "2500", "singleEvents": "true"},
                        )
                        count = len(response.json().get("items", []))
                        exact = exact and not bool(response.json().get("nextPageToken"))
                    elif resource_id == "mailbox:outlook":
                        response = await client.get(
                            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "ConsistencyLevel": "eventual",
                            },
                            params={"$top": "1", "$count": "true", "$select": "id"},
                        )
                        count = int(response.json().get("@odata.count") or 0)
                    elif resource_id == "calendar:outlook":
                        response = await client.get(
                            "https://graph.microsoft.com/v1.0/me/events",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "ConsistencyLevel": "eventual",
                            },
                            params={"$top": "1", "$count": "true", "$select": "id"},
                        )
                        count = int(response.json().get("@odata.count") or 0)
                    elif resource_id == "chat:all":
                        payload = await _graph_get(client, token, "/me/chats", {"$select": "id"})
                        count = len(payload.get("value", []))
                        exact = exact and not bool(payload.get("@odata.nextLink"))
                    else:
                        # Drive/SharePoint recursive enumeration can be large. The
                        # preview scans a safe page and clearly marks a capped result.
                        count = 0
                        exact = False
                    estimated_items += count
                    scanned += count
                    estimated_size += resource_size or count * 8_000
        except Exception as exc:  # provider preview should not lose the setup
            warnings.append(f"Some content could not be counted: {exc}")
            exact = False
    return ConnectionPreviewResponse(
        provider=provider,
        selected_resources=selected,
        estimated_items=estimated_items,
        estimated_size_bytes=estimated_size,
        permission_warnings=warnings,
        unsupported_items=max(0, selected * 2 if provider == "google_workspace" else 0),
        count_is_exact=exact,
        scanned_items=scanned,
    )


async def save_policy(
    org_id: str,
    user_id: str,
    provider: str,
    policy: ConnectionPolicyInput,
    *,
    status: str,
) -> ConnectionPolicyRow:
    _validate_provider(provider)
    now = datetime.now(timezone.utc)
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            statement = pg_insert(ConnectionPolicyRow).values(
                policy_id=str(uuid4()),
                org_id=org_id,
                user_id=user_id,
                provider=provider,
                included_resources=json.dumps(policy.included_resource_ids),
                excluded_resources=json.dumps(policy.excluded_resource_ids),
                include_history=policy.include_history,
                history_start_date=policy.history_start_date,
                sync_frequency=policy.sync_frequency,
                access_mode=policy.access_mode,
                allowed_departments=json.dumps(policy.allowed_departments),
                allowed_user_ids=json.dumps(policy.allowed_user_ids),
                status=status,
                created_at=now,
                updated_at=now,
            ).on_conflict_do_update(
                index_elements=["org_id", "user_id", "provider"],
                set_={
                    "included_resources": json.dumps(policy.included_resource_ids),
                    "excluded_resources": json.dumps(policy.excluded_resource_ids),
                    "include_history": policy.include_history,
                    "history_start_date": policy.history_start_date,
                    "sync_frequency": policy.sync_frequency,
                    "access_mode": policy.access_mode,
                    "allowed_departments": json.dumps(policy.allowed_departments),
                    "allowed_user_ids": json.dumps(policy.allowed_user_ids),
                    "status": status,
                    "last_error": None,
                    "updated_at": now,
                },
            )
            # A changed allowlist needs a fresh provider cursor so newly
            # included historical locations are considered by the next import.
            sync_providers = (
                ["gmail", "drive"] if provider == "google_workspace"
                else (["teams"] if provider == "microsoft_teams" else ["zoom"])
            )
            cursor_condition = SyncCursorRow.provider.in_(sync_providers)
            if provider == "microsoft_teams":
                cursor_condition = cursor_condition | SyncCursorRow.provider.like(
                    "sharepoint:%"
                )
            await session.execute(
                delete(SyncCursorRow).where(
                    SyncCursorRow.org_id == org_id,
                    SyncCursorRow.user_id == user_id,
                    cursor_condition,
                )
            )
            await session.execute(
                delete(IntegrationSubscriptionRow).where(
                    IntegrationSubscriptionRow.org_id == org_id,
                    IntegrationSubscriptionRow.user_id == user_id,
                    IntegrationSubscriptionRow.provider.in_(
                        [*sync_providers]
                        + (["sharepoint"] if provider == "microsoft_teams" else [])
                    ),
                )
            )
            await session.execute(statement)
        return (await session.execute(
            select(ConnectionPolicyRow).where(
                ConnectionPolicyRow.org_id == org_id,
                ConnectionPolicyRow.user_id == user_id,
                ConnectionPolicyRow.provider == provider,
            )
        )).scalar_one()


async def _set_policy_result(
    row: ConnectionPolicyRow, *, status: str, error: str | None = None
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        current = await session.get(ConnectionPolicyRow, row.policy_id)
        if current:
            current.status = status
            current.last_error = error
            current.updated_at = datetime.now(timezone.utc)
            if status == "active":
                current.last_synced_at = current.updated_at
            await session.commit()


async def set_policy_status(
    org_id: str, user_id: str, provider: str, status: str
) -> ConnectionPolicyRow:
    row = await get_policy(org_id, user_id, provider)
    if row is None:
        raise HTTPException(status_code=404, detail="Connection setup was not found.")
    await _set_policy_result(row, status=status)
    refreshed = await get_policy(org_id, user_id, provider)
    assert refreshed is not None
    return refreshed


async def disconnect_controlled_connection(
    org_id: str, user_id: str, provider: str
) -> None:
    """Remove credentials and policy. Provider-side watches expire naturally."""

    _validate_provider(provider)
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await session.execute(
                delete(ConnectionPolicyRow).where(
                    ConnectionPolicyRow.org_id == org_id,
                    ConnectionPolicyRow.user_id == user_id,
                    ConnectionPolicyRow.provider == provider,
                )
            )
            sync_providers = (
                ["gmail", "drive"] if provider == "google_workspace"
                else (["teams"] if provider == "microsoft_teams" else ["zoom"])
            )
            cursor_condition = SyncCursorRow.provider.in_(sync_providers)
            if provider == "microsoft_teams":
                cursor_condition = cursor_condition | SyncCursorRow.provider.like(
                    "sharepoint:%"
                )
            await session.execute(
                delete(SyncCursorRow).where(
                    SyncCursorRow.org_id == org_id,
                    SyncCursorRow.user_id == user_id,
                    cursor_condition,
                )
            )
            await session.execute(
                delete(IntegrationSubscriptionRow).where(
                    IntegrationSubscriptionRow.org_id == org_id,
                    IntegrationSubscriptionRow.user_id == user_id,
                    IntegrationSubscriptionRow.provider.in_(
                        [*sync_providers]
                        + (["sharepoint"] if provider == "microsoft_teams" else [])
                    ),
                )
            )
    await _delete_connection(org_id, user_id, provider)


async def initialize_connection(
    row: ConnectionPolicyRow, job_id: str, job_store: dict[str, JobStatus]
) -> None:
    job = job_store[job_id]
    job.status = "processing"
    job.progress = "Running initial import and enabling continuous updates"
    included = json.loads(row.included_resources)
    from sync_reporting import finish_sync_run, start_sync_run
    sync_run_id = await start_sync_run(row.org_id, row.provider, "initial_import")
    details: dict[str, dict] = {}
    try:
        if row.provider == "google_workspace":
            from google_workspace import sync_gmail
            count = 0
            if "mailbox:gmail" in included:
                source_count = await sync_gmail(row.org_id, row.user_id, max_results=100)
                count += source_count
                details["gmail"] = {"imported": source_count}
            if any(item.startswith(("drive:", "folder:")) for item in included):
                source_count = await sync_drive(row.org_id, row.user_id, max_results=100)
                count += source_count
                details["drive"] = {"imported": source_count}
            if any(item.startswith("calendar:") for item in included):
                source_count = await sync_google_calendar(row.org_id, row.user_id, max_results=100)
                count += source_count
                details["google_calendar"] = {"imported": source_count}
            if any(item.startswith(("drive:", "folder:")) for item in included):
                try:
                    await setup_drive_watch(row.org_id, row.user_id)
                except Exception as watch_error:  # initial import still remains useful
                    logger.warning("Drive watch not enabled: %s", watch_error)
        elif row.provider == "microsoft_teams":
            teams_count = await sync_teams(row.org_id, row.user_id, max_results=100)
            details["teams_channels"] = {"imported": teams_count}
            sharepoint_count = await sync_sharepoint(
                row.org_id, row.user_id, max_results=100
            )
            details["sharepoint"] = {"imported": sharepoint_count}
            count = teams_count + sharepoint_count
            mail_count = await sync_outlook_mail(row.org_id, row.user_id, max_results=100)
            calendar_count = await sync_outlook_calendar(row.org_id, row.user_id, max_results=100)
            chat_count = await sync_teams_chats(row.org_id, row.user_id, max_results=100)
            details["outlook_mail"] = {"imported": mail_count}
            details["outlook_calendar"] = {"imported": calendar_count}
            details["teams_chats"] = {"imported": chat_count}
            count += mail_count + calendar_count + chat_count
            discovered = await discover_resources(row.org_id, row.user_id, row.provider)
            selected_sites = {item for item in included if item.startswith("site:")}
            selected_libraries = {
                resource.id
                for resource in discovered.resources
                if resource.kind == "library"
                and (
                    resource.id in included
                    or resource.parent_id in selected_sites
                )
            }
            for resource_id in selected_libraries:
                try:
                    _, _, drive_id = resource_id.split(":", 2)
                    await setup_sharepoint_watch(
                        row.org_id, row.user_id, drive_id=drive_id
                    )
                except Exception as watch_error:
                    logger.warning(
                        "SharePoint watch not enabled for %s: %s",
                        resource_id,
                        watch_error,
                    )
            selected_teams = {item for item in included if item.startswith("team:")}
            watch_resources = {
                item for item in included if item.startswith("channel:")
            }
            watch_resources.update(
                resource.id
                for resource in discovered.resources
                if resource.kind == "channel" and resource.parent_id in selected_teams
            )
            for resource in watch_resources:
                if resource.startswith("channel:"):
                    _, team_id, channel_id = resource.split(":", 2)
                    try:
                        await setup_teams_channel_watch(
                            row.org_id, row.user_id, team_id=team_id, channel_id=channel_id
                        )
                    except Exception as watch_error:
                        logger.warning("Teams watch not enabled for %s: %s", resource, watch_error)
        else:
            from zoom_workspace import sync_zoom

            count = await sync_zoom(row.org_id, row.user_id, max_results=100)
            details["zoom"] = {"imported": count}
        await _set_policy_result(row, status="active")
        await finish_sync_run(sync_run_id, details=details)
        job.status = "complete"
        job.progress = f"Initial import complete ({count} item(s)); connection active"
    except Exception as exc:  # noqa: BLE001
        await finish_sync_run(sync_run_id, details=details, error=str(exc))
        await _set_policy_result(row, status="error", error=str(exc))
        job.status = "failed"
        job.error = str(exc)
        job.progress = None
        logger.exception("Connection initialization failed: %s", row.provider)


async def run_periodic_connection_checks() -> None:
    """Run safety syncs for active policies.

    Webhooks provide near-real-time updates. This lightweight scheduler supplies
    the periodic reconciliation path needed when notifications are delayed or
    when an administrator selected hourly/daily updates.
    """

    intervals = {"realtime": 6 * 3600, "hourly": 3600, "daily": 24 * 3600}
    while True:
        await asyncio.sleep(300)
        try:
            from review_workflows import schedule_expiry_reviews
            await schedule_expiry_reviews()
            factory = get_session_factory()
            async with factory() as session:
                rows = list(
                    (
                        await session.execute(
                            select(ConnectionPolicyRow).where(
                                ConnectionPolicyRow.status.in_(["active", "warning"])
                            )
                        )
                    ).scalars()
                )
                now = datetime.now(timezone.utc)
                # Provider watches expire. Renew them ahead of expiry even when
                # the administrator selected a slower reconciliation interval.
                gmail_rows = list(
                    (
                        await session.execute(
                        select(SyncCursorRow).where(
                            SyncCursorRow.provider == "gmail",
                            SyncCursorRow.status == "active",
                            SyncCursorRow.watch_expiration.is_not(None),
                            SyncCursorRow.watch_expiration <= now + timedelta(days=1),
                        )
                        )
                    ).scalars()
                )
                drive_rows = list(
                    (
                        await session.execute(
                        select(SyncCursorRow).where(
                            SyncCursorRow.provider == "drive",
                            SyncCursorRow.status == "active",
                            SyncCursorRow.watch_expiration.is_not(None),
                            SyncCursorRow.watch_expiration <= now + timedelta(days=1),
                        )
                        )
                    ).scalars()
                )
            from google_workspace import setup_gmail_watch

            for cursor in gmail_rows:
                try:
                    await setup_gmail_watch(cursor.org_id, cursor.user_id)
                except Exception:  # noqa: BLE001
                    logger.exception("Could not renew Gmail watch for %s", cursor.account_email)
            for cursor in drive_rows:
                try:
                    await setup_drive_watch(cursor.org_id, cursor.user_id)
                except Exception:  # noqa: BLE001
                    logger.exception("Could not renew Drive watch for %s", cursor.account_email)

            for subscription in await expiring_subscriptions(
                "teams", now + timedelta(minutes=15)
            ):
                try:
                    _, team_id, channel_id, *_ = subscription.resource.strip("/").split("/")
                    await setup_teams_channel_watch(
                        subscription.org_id,
                        subscription.user_id,
                        team_id=team_id,
                        channel_id=channel_id,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Could not renew Teams watch %s", subscription.resource)
            for subscription in await expiring_subscriptions(
                "sharepoint", now + timedelta(hours=12)
            ):
                try:
                    drive_id = subscription.resource.split("/")[2]
                    await setup_sharepoint_watch(
                        subscription.org_id,
                        subscription.user_id,
                        drive_id=drive_id,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Could not renew SharePoint watch %s", subscription.resource
                    )

            for row in rows:
                last = row.last_synced_at or row.created_at
                if (now - last).total_seconds() < intervals.get(row.sync_frequency, 3600):
                    continue
                try:
                    if row.provider == "google_workspace":
                        from google_workspace import sync_gmail
                        await sync_gmail(row.org_id, row.user_id, max_results=100)
                        await sync_drive(row.org_id, row.user_id, max_results=100)
                        await sync_google_calendar(row.org_id, row.user_id, max_results=100)
                    elif row.provider == "microsoft_teams":
                        await sync_teams(row.org_id, row.user_id, max_results=100)
                        await sync_sharepoint(row.org_id, row.user_id, max_results=100)
                        await sync_outlook_mail(row.org_id, row.user_id, max_results=100)
                        await sync_outlook_calendar(row.org_id, row.user_id, max_results=100)
                        await sync_teams_chats(row.org_id, row.user_id, max_results=100)
                    else:
                        from zoom_workspace import sync_zoom
                        await sync_zoom(row.org_id, row.user_id, max_results=100)
                    await _set_policy_result(row, status="active")
                except Exception as exc:  # noqa: BLE001
                    await _set_policy_result(row, status="warning", error=str(exc))
                    logger.exception("Scheduled connection check failed: %s", row.provider)
        except Exception:  # noqa: BLE001
            logger.exception("Periodic connection scheduler iteration failed")


def policy_response(
    row: ConnectionPolicyRow, *, initial_job_ids: list[str] | None = None
) -> ConnectionPolicyResponse:
    return ConnectionPolicyResponse(
        provider=row.provider,
        included_resource_ids=json.loads(row.included_resources),
        excluded_resource_ids=json.loads(row.excluded_resources),
        include_history=row.include_history,
        history_start_date=row.history_start_date,
        sync_frequency=row.sync_frequency,
        access_mode=row.access_mode,
        allowed_departments=json.loads(row.allowed_departments),
        allowed_user_ids=json.loads(row.allowed_user_ids),
        status=row.status,
        updated_at=row.updated_at.isoformat(),
        initial_job_ids=initial_job_ids or [],
    )


def visibility_for_policy(
    row: ConnectionPolicyRow | None, *, org_id: str, source_account: str
) -> list[str]:
    """Translate an admin policy into chunk-level search access tokens."""

    if row is None or row.access_mode == "respect_source_permissions":
        # Until a connector imports each source ACL, default to the connected
        # account only. This is deliberately restrictive, never permissive.
        return [source_account.lower()]
    if row.access_mode == "organization":
        return [f"org:{org_id}"]
    return [
        *(f"user:{user_id}" for user_id in json.loads(row.allowed_user_ids)),
        *(f"department:{name.lower()}" for name in json.loads(row.allowed_departments)),
    ]
