"""SharePoint document discovery and incremental ingestion via Microsoft Graph."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from pathlib import Path

import httpx

from file_extract import extract_file_text
from google_workspace import _get_cursor, _upsert_cursor
from microsoft_teams import (
    PROVIDER_MICROSOFT_TEAMS,
    _teams_token,
)
from config import get_settings
from models import Conversation, IncomingMessage, Participant
from pipeline import DocumentInput
from provider_http import graph_get_all, request_with_backoff
from source_registry import ingest_external_source, mark_external_source_deleted
from subscriptions import upsert_subscription

SYNC_PROVIDER_SHAREPOINT = "sharepoint"


def _graph_datetime(value: object) -> datetime | None:
    if not value:
        return None


def _microsoft_application(filename: str) -> str:
    return {
        ".docx": "Microsoft Word",
        ".pptx": "Microsoft PowerPoint",
        ".xlsx": "Microsoft Excel",
        ".pdf": "PDF",
        ".csv": "CSV",
    }.get(Path(filename).suffix.lower(), "Microsoft SharePoint")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


async def setup_sharepoint_watch(
    org_id: str, user_id: str, *, drive_id: str
) -> None:
    """Create/renew a Graph drive-item notification for a document library."""

    token, _ = await _teams_token(org_id, user_id)
    if token.startswith("dev:"):
        return
    settings = get_settings()
    if not settings.microsoft_graph_webhook_url:
        return
    expiration = datetime.now(timezone.utc) + timedelta(days=2)
    resource = f"/drives/{drive_id}/root"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await request_with_backoff(
            client,
            "POST",
            "https://graph.microsoft.com/v1.0/subscriptions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "changeType": "updated",
                "notificationUrl": settings.microsoft_graph_webhook_url,
                "resource": resource,
                "expirationDateTime": expiration.isoformat().replace("+00:00", "Z"),
                "clientState": settings.microsoft_graph_client_state,
            },
        )
    response.raise_for_status()
    payload = response.json()
    await upsert_subscription(
        org_id=org_id,
        user_id=user_id,
        provider=SYNC_PROVIDER_SHAREPOINT,
        external_id=str(payload["id"]),
        resource=resource,
        expiration=datetime.fromisoformat(
            str(payload.get("expirationDateTime")).replace("Z", "+00:00")
        ),
    )


async def discover_sharepoint_resources(
    client: httpx.AsyncClient, token: str
) -> list[dict[str, str | None]]:
    sites = await graph_get_all(
        client,
        token,
        "/sites?search=*",
        {"$select": "id,displayName,webUrl"},
        item_limit=500,
    )
    resources: list[dict[str, str | None]] = []
    for site in sites:
        site_id = str(site.get("id") or "")
        if not site_id:
            continue
        site_key = f"site:{site_id}"
        resources.append(
            {
                "id": site_key,
                "name": str(site.get("displayName") or site.get("webUrl") or "SharePoint site"),
                "kind": "site",
                "parent_id": None,
            }
        )
        drives = await graph_get_all(
            client,
            token,
            f"/sites/{site_id}/drives",
            {"$select": "id,name,webUrl"},
            item_limit=500,
        )
        for drive in drives:
            resources.append(
                {
                    "id": f"library:{site_id}:{drive.get('id')}",
                    "name": str(drive.get("name") or "Document library"),
                    "kind": "library",
                    "parent_id": site_key,
                }
            )
    return resources


async def _sharepoint_acl(
    client: httpx.AsyncClient,
    token: str,
    drive_id: str,
    item_id: str,
    fallback_email: str,
) -> list[str]:
    permissions = await graph_get_all(
        client,
        token,
        f"/drives/{drive_id}/items/{item_id}/permissions",
        item_limit=1000,
    )
    tokens: set[str] = set()
    for permission in permissions:
        identities = [
            permission.get("grantedToV2"),
            *(permission.get("grantedToIdentitiesV2") or []),
        ]
        for identity in identities:
            if not isinstance(identity, dict):
                continue
            user = identity.get("user") or identity.get("siteUser") or {}
            email = str(user.get("email") or user.get("loginName") or "").lower()
            if email and "@" in email:
                tokens.add(email)
        link = permission.get("link") or {}
        if link.get("scope") == "organization" and "@" in fallback_email:
            tokens.add(f"domain:{fallback_email.rsplit('@', 1)[1].lower()}")
    return sorted(tokens) or [fallback_email.lower()]


def _conversation(item: dict[str, Any], text: str) -> Conversation:
    item_id = str(item.get("id"))
    modified = str(item.get("lastModifiedDateTime") or "")
    try:
        timestamp = datetime.fromisoformat(modified.replace("Z", "+00:00"))
    except ValueError:
        timestamp = datetime.now(timezone.utc)
    return Conversation(
        source="sharepoint",
        conversation_id=f"sharepoint:{item_id}",
        title=str(item.get("name") or "SharePoint document"),
        participants=[Participant(id="sharepoint", name="SharePoint")],
        messages=[
            IncomingMessage(
                id=f"sharepoint:{item_id}:content",
                sender="sharepoint",
                timestamp=timestamp,
                text=text,
            )
        ],
    )


async def sync_sharepoint(org_id: str, user_id: str, *, max_results: int = 100) -> int:
    from connection_setup import get_policy, visibility_for_policy

    token, account_email = await _teams_token(org_id, user_id)
    if token.startswith("dev:"):
        return 0
    policy = await get_policy(org_id, user_id, PROVIDER_MICROSOFT_TEAMS)
    if not policy or policy.status == "paused":
        return 0
    included = set(json.loads(policy.included_resources))
    excluded = set(json.loads(policy.excluded_resources))
    ingested = 0

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resources = await discover_sharepoint_resources(client, token)
        selected_sites = {item for item in included if item.startswith("site:")}
        libraries = [
            item
            for item in resources
            if item["kind"] == "library"
            and (
                item["id"] in included
                or item["parent_id"] in selected_sites
            )
            and item["id"] not in excluded
            and item["parent_id"] not in excluded
        ]
        for library in libraries:
            _, site_id, drive_id = str(library["id"]).split(":", 2)
            cursor_provider = f"{SYNC_PROVIDER_SHAREPOINT}:{drive_id}"
            cursor = await _get_cursor(org_id, user_id, cursor_provider)
            delta_url = (
                cursor.cursor_value
                if cursor and cursor.cursor_value
                else f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta"
            )
            while delta_url and ingested < max_results:
                response = await request_with_backoff(
                    client,
                    "GET",
                    delta_url,
                    headers={"Authorization": f"Bearer {token}"},
                    params={"$select": "id,name,file,folder,deleted,createdDateTime,"
                    "lastModifiedDateTime,eTag,webUrl,parentReference,createdBy,lastModifiedBy"}
                    if "?" not in delta_url
                    else None,
                )
                response.raise_for_status()
                payload = response.json()
                for item in payload.get("value") or []:
                    item_id = str(item.get("id") or "")
                    if not item_id:
                        continue
                    if item.get("deleted"):
                        await mark_external_source_deleted(
                            org_id, SYNC_PROVIDER_SHAREPOINT, f"{drive_id}:{item_id}"
                        )
                        continue
                    if not item.get("file"):
                        continue
                    download = await request_with_backoff(
                        client,
                        "GET",
                        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if download.status_code >= 400:
                        continue
                    name = str(item.get("name") or item_id)
                    text = extract_file_text(name, download.content).strip()
                    if not text:
                        continue
                    visible_to = visibility_for_policy(
                        policy, org_id=org_id, source_account=account_email
                    )
                    if policy.access_mode == "respect_source_permissions":
                        visible_to = await _sharepoint_acl(
                            client, token, drive_id, item_id, account_email
                        )
                    created_identity = item.get("createdBy") or {}
                    modified_identity = item.get("lastModifiedBy") or {}
                    created_user = created_identity.get("user") or {}
                    modified_user = modified_identity.get("user") or {}
                    author = str(
                        created_user.get("email")
                        or created_user.get("displayName")
                        or ""
                    ) or None
                    contributors = sorted(
                        {
                            str(value)
                            for value in (
                                created_user.get("email")
                                or created_user.get("displayName"),
                                modified_user.get("email")
                                or modified_user.get("displayName"),
                            )
                            if value
                        }
                    )
                    created_at = _graph_datetime(item.get("createdDateTime"))
                    updated_at = _graph_datetime(item.get("lastModifiedDateTime"))
                    result = await ingest_external_source(
                        org_id=org_id,
                        provider=SYNC_PROVIDER_SHAREPOINT,
                        external_id=f"{drive_id}:{item_id}",
                        version=str(item.get("eTag") or item.get("lastModifiedDateTime") or ""),
                        conversation=_conversation(item, text),
                        document=DocumentInput(
                            data=download.content,
                            source="sharepoint",
                            source_label=name,
                            original_filename=name,
                            mime_type=str((item.get("file") or {}).get("mimeType") or "application/octet-stream"),
                            visible_to=visible_to,
                            title=name,
                            author=author,
                            owners=[author] if author else [],
                            source_created_at=created_at,
                            source_updated_at=updated_at,
                            source_application=_microsoft_application(name),
                            source_location=str(library.get("name") or "SharePoint document library"),
                            folder_path=str((item.get("parentReference") or {}).get("path") or ""),
                            version=str(item.get("eTag") or item.get("lastModifiedDateTime") or ""),
                            contributors=contributors,
                            permissions=visible_to,
                            source_url=str(item.get("webUrl") or "") or None,
                        ),
                    )
                    if result is not None:
                        ingested += 1
                    if ingested >= max_results:
                        break
                next_link = str(payload.get("@odata.nextLink") or "")
                delta_link = str(payload.get("@odata.deltaLink") or "")
                delta_url = next_link
                await _upsert_cursor(
                    org_id=org_id,
                    user_id=user_id,
                    provider=cursor_provider,
                    account_email=account_email.lower(),
                    cursor_value=next_link or delta_link,
                    mark_synced=True,
                )
                if delta_link and not next_link:
                    delta_url = ""
            if ingested >= max_results:
                break
    return ingested
