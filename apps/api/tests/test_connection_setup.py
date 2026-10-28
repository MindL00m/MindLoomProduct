"""Unit tests for controlled workspace connection policies."""

import json
from types import SimpleNamespace

from weaver.connection_setup import _dev_resources, visibility_for_policy


def _policy(access_mode: str, users=None, departments=None):
    return SimpleNamespace(
        access_mode=access_mode,
        allowed_user_ids=json.dumps(users or []),
        allowed_departments=json.dumps(departments or []),
    )


def test_dev_discovery_has_selectable_google_hierarchy():
    resources = _dev_resources("google_workspace")
    drive = next(item for item in resources if item.kind == "drive")
    assert any(item.parent_id == drive.id for item in resources)


def test_dev_discovery_has_selectable_teams_hierarchy():
    resources = _dev_resources("microsoft_teams")
    team = next(item for item in resources if item.kind == "team")
    assert any(item.kind == "channel" and item.parent_id == team.id for item in resources)


def test_visibility_defaults_to_source_account():
    assert visibility_for_policy(
        _policy("respect_source_permissions"),
        org_id="org-1",
        source_account="Admin@Example.com",
    ) == ["admin@example.com"]


def test_visibility_supports_org_and_selected_audiences():
    assert visibility_for_policy(
        _policy("organization"), org_id="org-1", source_account="admin@example.com"
    ) == ["org:org-1"]
    assert visibility_for_policy(
        _policy("selected", users=["u1"], departments=["Engineering"]),
        org_id="org-1",
        source_account="admin@example.com",
    ) == ["admin@example.com", "user:u1", "department:engineering"]
    # Nobody chosen stays with the connected account; [] would mean the whole org.
    assert visibility_for_policy(
        _policy("selected"), org_id="org-1", source_account="Admin@example.com"
    ) == ["admin@example.com"]


def test_gmail_is_polled_every_few_minutes_only_without_push() -> None:
    from datetime import datetime, timedelta, timezone

    from weaver.connection_setup import should_poll_gmail

    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    stale, fresh = now - timedelta(minutes=6), now - timedelta(minutes=2)
    base = {"provider": "google_workspace", "sync_frequency": "realtime", "push_configured": False, "now": now}
    assert should_poll_gmail(**base, last_poll=stale)
    assert not should_poll_gmail(**base, last_poll=fresh)
    assert not should_poll_gmail(**{**base, "push_configured": True}, last_poll=stale)
    assert not should_poll_gmail(**{**base, "sync_frequency": "daily"}, last_poll=stale)
    assert not should_poll_gmail(**{**base, "provider": "microsoft_teams"}, last_poll=stale)
