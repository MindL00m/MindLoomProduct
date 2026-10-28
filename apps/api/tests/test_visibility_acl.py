"""Tests for org vs private upload/workflow ACL helpers."""

from keeper.visibility_acl import (
    can_mutate_skill,
    can_view_skill,
    permissions_for_upload,
    resolve_content_visibility,
    skill_acl_tokens,
    visibility_tokens,
)


def test_visibility_tokens():
    assert visibility_tokens(
        visibility="private", org_id="org-1", user_id="u-1"
    ) == ["user:u-1"]
    assert visibility_tokens(
        visibility="organization", org_id="org-1", user_id="u-1"
    ) == ["org:org-1"]


def test_permissions_for_upload_prefers_visibility_over_raw_permissions():
    assert permissions_for_upload(
        org_id="org-1",
        uploaded_by="u-1",
        visibility="organization",
        permissions=["someone@example.com"],
    ) == ["org:org-1"]
    assert permissions_for_upload(
        org_id="org-1",
        uploaded_by="u-1",
        visibility="private",
    ) == ["user:u-1"]
    # A member cannot aim an upload at other people's access tokens.
    assert permissions_for_upload(
        org_id="org-1",
        uploaded_by="u-1",
        permissions=["eng@example.com", "user:u-2"],
    ) == ["user:u-1"]
    assert permissions_for_upload(
        org_id="org-1", uploaded_by="u-1", permissions=["org:org-1"],
    ) == ["org:org-1"]
    assert permissions_for_upload(org_id="org-1", uploaded_by="u-1") == [
        "user:u-1"
    ]


def test_legacy_skill_visibility_defaults_to_organization():
    assert resolve_content_visibility(None) == "organization"
    assert can_view_skill(
        {"created_by": "u-a", "visibility": "private"}, "u-a"
    )
    assert not can_view_skill(
        {"created_by": "u-a", "visibility": "private"}, "u-b"
    )
    assert can_view_skill({"created_by": "u-a"}, "u-b")
    assert skill_acl_tokens(
        {"org_id": "org-1", "created_by": "u-a", "visibility": "private"}
    ) == ["user:u-a"]
    assert skill_acl_tokens({"org_id": "org-1", "created_by": "u-a"}) == [
        "org:org-1"
    ]


def test_only_creator_can_change_visibility():
    row = {
        "created_by": "u-a",
        "visibility": "organization",
        "org_id": "org-1",
    }
    # Only the creator (or an admin, except for visibility) may edit or approve.
    assert not can_mutate_skill(row, "u-b", changing_visibility=False)
    assert can_mutate_skill(row, "u-b", changing_visibility=False, is_admin=True)
    assert not can_mutate_skill(row, "u-b", changing_visibility=True, is_admin=True)
    assert can_mutate_skill(row, "u-a", changing_visibility=True)
    private = {**row, "visibility": "private"}
    assert not can_mutate_skill(private, "u-b", changing_visibility=False)
