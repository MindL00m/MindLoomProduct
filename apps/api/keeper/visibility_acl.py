"""Shared org vs private ACL helpers for uploads and workflows."""

from __future__ import annotations

from typing import Any, Literal

ContentVisibility = Literal["private", "organization"]


def resolve_content_visibility(
    value: str | None,
    *,
    legacy_default: ContentVisibility = "organization",
) -> ContentVisibility:
    """Normalize a visibility value; missing/legacy → ``legacy_default``."""

    if value in ("private", "organization"):
        return value
    return legacy_default


def visibility_tokens(
    *,
    visibility: ContentVisibility,
    org_id: str,
    user_id: str,
) -> list[str]:
    if visibility == "private":
        return [f"user:{user_id}"]
    return [f"org:{org_id}"]


def permissions_for_upload(
    *,
    org_id: str,
    uploaded_by: str,
    visibility: ContentVisibility | None = None,
    permissions: list[str] | None = None,
) -> list[str]:
    """Resolve Ask ACL tokens for a manual upload.

    When ``visibility`` is set it wins (UI path). Otherwise non-empty
    ``permissions`` are kept for API callers. Default is private to uploader.
    """

    if visibility in ("private", "organization"):
        return visibility_tokens(
            visibility=visibility, org_id=org_id, user_id=uploaded_by
        )
    if permissions:
        # A member can share their upload with the org or keep it, but cannot aim it
        # at other people's access tokens (that would plant content in their answers).
        allowed = {f"user:{uploaded_by}", f"org:{org_id}"}
        kept = [token for token in permissions if token in allowed]
        return kept or [f"user:{uploaded_by}"]
    return [f"user:{uploaded_by}"]


def skill_visibility_from_row(row: dict[str, Any] | Any) -> ContentVisibility:
    """Legacy skills without a field stay organization-visible."""

    if isinstance(row, dict):
        value = row.get("visibility")
    else:
        value = getattr(row, "visibility", None)
    return resolve_content_visibility(value, legacy_default="organization")


def skill_acl_tokens(skill: Any) -> list[str]:
    visibility = skill_visibility_from_row(skill)
    org_id = getattr(skill, "org_id", None) or (
        skill.get("org_id") if isinstance(skill, dict) else ""
    )
    created_by = getattr(skill, "created_by", None) or (
        skill.get("created_by") if isinstance(skill, dict) else ""
    )
    return visibility_tokens(
        visibility=visibility,
        org_id=str(org_id),
        user_id=str(created_by),
    )


def can_view_skill(row: dict[str, Any], viewer_user_id: str) -> bool:
    if skill_visibility_from_row(row) == "organization":
        return True
    return str(row.get("created_by") or "") == viewer_user_id


def can_mutate_skill(
    row: dict[str, Any],
    actor_user_id: str,
    *,
    changing_visibility: bool = False,
    is_admin: bool = False,
) -> bool:
    """Who may edit, approve or reject a Skill File.

    Only its creator, or an org admin for anything but visibility. Approving makes a
    skill runnable on colleagues' machines and publishes it to Ask, so other members
    of the org must not be able to approve (or quietly rewrite) someone's skill.
    """

    created_by = str(row.get("created_by") or "")
    if created_by == actor_user_id:
        return True
    return is_admin and not changing_visibility
