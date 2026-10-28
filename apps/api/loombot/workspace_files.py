"""Files shared into a workspace.

A workspace file stays in its owner's Files (``loombot_files``); ``workspace_files``
links it to the workspace. Every member can open, preview, download and edit the
same file through the workspace routes, so changes are shared instead of copied.
Access is checked here (workspace membership plus the link) and the file is then
read or written as its owner.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

from core.database import get_session_factory
from loombot import files as files_service

logger = logging.getLogger(__name__)


async def _require_member(org_id: str, user_id: str, workspace_id: str) -> dict:
    from loombot.workspaces import _require_member as require

    return await require(org_id, user_id, workspace_id)


async def list_workspace_files(org_id: str, user_id: str, workspace_id: str) -> list[dict[str, Any]]:
    await _require_member(org_id, user_id, workspace_id)
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text(f"""
            SELECT f.*, coalesce(owner.name, owner.email) AS owner_name,
                   coalesce(adder.name, adder.email) AS added_by_name, wf.added_at
            FROM workspace_files wf
            JOIN (
                SELECT {files_service._META_COLUMNS}, owner_user_id, org_id
                FROM loombot_files WHERE org_id=:org
            ) f ON f.file_id=wf.file_id AND f.org_id=wf.org_id
            LEFT JOIN users owner ON owner.user_id=f.owner_user_id
            LEFT JOIN users adder ON adder.user_id=wf.added_by
            WHERE wf.org_id=:org AND wf.workspace_id=:workspace
            ORDER BY f.updated_at DESC
        """), {"org": org_id, "workspace": workspace_id})).mappings().all()
    return [
        {
            **files_service._meta(row),
            "owner_user_id": row["owner_user_id"],
            "owner_name": row["owner_name"],
            "added_by_name": row["added_by_name"],
            "added_at": row["added_at"],
            "is_mine": row["owner_user_id"] == user_id,
        }
        for row in rows
    ]


async def file_owner(org_id: str, user_id: str, workspace_id: str, file_id: str) -> str:
    """The owner of a file shared in this workspace; 404 unless the caller is a member."""

    await _require_member(org_id, user_id, workspace_id)
    factory = get_session_factory()
    async with factory() as session:
        owner = (await session.execute(text("""
            SELECT f.owner_user_id FROM workspace_files wf
            JOIN loombot_files f ON f.file_id=wf.file_id AND f.org_id=wf.org_id
            WHERE wf.org_id=:org AND wf.workspace_id=:workspace AND wf.file_id=:file
        """), {"org": org_id, "workspace": workspace_id, "file": file_id})).scalar_one_or_none()
    if owner is None:
        raise HTTPException(status_code=404, detail="File not found in this workspace.")
    return str(owner)


async def link_files(
    org_id: str, user_id: str, workspace_id: str, file_ids: list[str], *, announce: bool = True
) -> list[dict[str, Any]]:
    """Share some of the caller's own files into the workspace."""

    workspace = await _require_member(org_id, user_id, workspace_id)
    linked: list[dict[str, Any]] = []
    factory = get_session_factory()
    async with factory() as session:
        for file_id in dict.fromkeys(file_ids):
            name = (await session.execute(text("""
                SELECT name FROM loombot_files
                WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
            """), {"org": org_id, "user": user_id, "id": file_id})).scalar_one_or_none()
            if name is None:
                raise HTTPException(status_code=404, detail="File not found in your Files.")
            inserted = (await session.execute(text("""
                INSERT INTO workspace_files (workspace_id, file_id, org_id, added_by, added_at)
                VALUES (:workspace, :file, :org, :user, now())
                ON CONFLICT (workspace_id, file_id) DO NOTHING
                RETURNING file_id
            """), {
                "workspace": workspace_id, "file": file_id, "org": org_id, "user": user_id,
            })).scalar_one_or_none()
            if inserted:
                linked.append({"file_id": file_id, "name": name})
        sharer = (await session.execute(text("""
            SELECT coalesce(name, email) FROM users WHERE org_id=:org AND user_id=:user
        """), {"org": org_id, "user": user_id})).scalar_one_or_none() or "Someone"
        await session.commit()
    if linked and announce:
        from loombot.workspaces import _insert_message

        names = ", ".join(f["name"] for f in linked)
        await _insert_message(
            org_id=org_id,
            workspace_id=workspace_id,
            sender_user_id=None,
            sender_type="system",
            sender_name="MindLoom",
            body=f"{sharer} shared {names} in {workspace['name']} Files.",
        )
    return linked


async def link_files_quietly(
    org_id: str, user_id: str, workspace_id: str, file_ids: list[str]
) -> list[dict[str, Any]]:
    """Link files Loombot created or a member attached; never fails the reply."""

    if not workspace_id or not file_ids:
        return []
    try:
        return await link_files(org_id, user_id, workspace_id, file_ids, announce=False)
    except Exception:  # noqa: BLE001
        logger.warning("Could not share files into workspace %s", workspace_id, exc_info=True)
        return []


async def unlink_file(org_id: str, user_id: str, workspace_id: str, file_id: str) -> None:
    """Stop sharing a file here. The file's owner or whoever shared it can do this."""

    owner = await file_owner(org_id, user_id, workspace_id, file_id)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        removed = (await session.execute(text("""
            DELETE FROM workspace_files
            WHERE org_id=:org AND workspace_id=:workspace AND file_id=:file
              AND (added_by=:user OR :user=:owner)
            RETURNING file_id
        """), {
            "org": org_id, "workspace": workspace_id, "file": file_id,
            "user": user_id, "owner": owner,
        })).scalar_one_or_none()
    if removed is None:
        raise HTTPException(
            status_code=403,
            detail="Only the file's owner or the person who shared it can remove it.",
        )


async def upload_to_workspace(
    org_id: str, user_id: str, workspace_id: str, filename: str, data: bytes
) -> dict[str, Any]:
    await _require_member(org_id, user_id, workspace_id)
    created = await files_service.upload_file(
        org_id, user_id, filename, data, auto_rename=True
    )
    await link_files(org_id, user_id, workspace_id, [created["file_id"]])
    return created


async def get_workspace_file(org_id: str, user_id: str, workspace_id: str, file_id: str) -> dict:
    owner = await file_owner(org_id, user_id, workspace_id, file_id)
    return await files_service.get_file(org_id, owner, file_id)


async def preview_workspace_file(org_id: str, user_id: str, workspace_id: str, file_id: str) -> dict:
    owner = await file_owner(org_id, user_id, workspace_id, file_id)
    return await files_service.preview_file(org_id, owner, file_id)


async def workspace_file_bytes(
    org_id: str, user_id: str, workspace_id: str, file_id: str
) -> tuple[bytes, dict[str, Any]]:
    owner = await file_owner(org_id, user_id, workspace_id, file_id)
    return await files_service.get_file_bytes(org_id, owner, file_id)


async def update_workspace_file(
    org_id: str, user_id: str, workspace_id: str, file_id: str, *, content: str
) -> dict:
    """Any member can edit the shared file's text content; the owner can undo it."""

    owner = await file_owner(org_id, user_id, workspace_id, file_id)
    return await files_service.update_file(org_id, owner, file_id, content=content)


async def replace_workspace_upload(
    org_id: str, user_id: str, workspace_id: str, file_id: str, filename: str, data: bytes
) -> dict:
    owner = await file_owner(org_id, user_id, workspace_id, file_id)
    return await files_service.replace_upload(org_id, owner, file_id, filename, data)


async def find_workspace_file(org_id: str, workspace_id: str, name: str) -> dict | None:
    """A workspace file by name (for Loombot), with its owner, or None."""

    cleaned = " ".join((name or "").split()).lower()
    if not workspace_id or not cleaned:
        return None
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text("""
            SELECT f.owner_user_id, f.name FROM workspace_files wf
            JOIN loombot_files f ON f.file_id=wf.file_id AND f.org_id=wf.org_id
            WHERE wf.org_id=:org AND wf.workspace_id=:workspace
        """), {"org": org_id, "workspace": workspace_id})).mappings().all()
    for row in rows:
        file_name = str(row["name"]).lower()
        if cleaned in {file_name, file_name.rsplit(".", 1)[0]}:
            found = await files_service.find_file_by_name(org_id, row["owner_user_id"], row["name"])
            if found is not None:
                return {**found, "owner_user_id": row["owner_user_id"]}
    return None
