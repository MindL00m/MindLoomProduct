"""Adding workspace members by email, pending invites and removal."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from loombot import workspaces


def test_split_invite_emails_by_domain() -> None:
    inside, outside, invalid = workspaces.split_invite_emails(
        ["Priya@Acme.com, sam@acme.com; vendor@other.io", "not-an-email", "priya@acme.com"],
        "acme.com",
    )
    assert inside == ["priya@acme.com", "sam@acme.com"]
    assert outside == ["vendor@other.io"]
    assert invalid == ["not-an-email"]


def test_split_invite_emails_accepts_angle_brackets() -> None:
    inside, _, _ = workspaces.split_invite_emails(["<neha@acme.com>"], "acme.com")
    assert inside == ["neha@acme.com"]


async def test_invite_refused_for_everyone_room(monkeypatch) -> None:
    async def member(*args):
        return {"kind": "org_wide", "name": "Everyone", "created_by": "u1"}

    monkeypatch.setattr(workspaces, "_require_member", member)
    with pytest.raises(HTTPException) as exc:
        await workspaces.invite_members_by_email(
            org_id="o", user_id="u1", workspace_id="w", emails=["a@acme.com"]
        )
    assert exc.value.status_code == 400


async def test_only_creator_removes_others(monkeypatch) -> None:
    async def member(*args):
        return {"kind": "group", "name": "Launch", "created_by": "creator"}

    monkeypatch.setattr(workspaces, "_require_member", member)
    with pytest.raises(HTTPException) as exc:
        await workspaces.remove_member(
            org_id="o", user_id="someone", workspace_id="w", member_user_id="other"
        )
    assert exc.value.status_code == 403


async def test_nobody_is_removed_from_everyone(monkeypatch) -> None:
    async def member(*args):
        return {"kind": "org_wide", "name": "Everyone", "created_by": "u1"}

    monkeypatch.setattr(workspaces, "_require_member", member)
    with pytest.raises(HTTPException):
        await workspaces.remove_member(
            org_id="o", user_id="u1", workspace_id="w", member_user_id="u1"
        )


async def test_loombot_invites_directory_people_who_have_not_signed_in(monkeypatch) -> None:
    async def resolve(org_id, queries, exclude_user_id=None):
        return [], [{
            "name": "Neha", "email": "neha@acme.com",
            "reason": "In the company directory, but has not signed in to MindLoom yet.",
        }]

    invited_with: list = []

    async def invite(**kwargs):
        invited_with.append(kwargs)
        return {"invited": ["neha@acme.com"], "already_invited": [], "outside_organization": []}

    monkeypatch.setattr("loombot.workspace_context.resolve_members_from_queries", resolve)
    monkeypatch.setattr(workspaces, "invite_members_by_email", invite)
    result = await workspaces.add_members_by_query(
        org_id="o", user_id="u1", workspace_id="w", queries=["Neha"]
    )
    assert result["invited"] == ["neha@acme.com"]
    assert result["unmatched"] == []
    assert invited_with[0]["emails"] == ["neha@acme.com"]


async def test_loombot_invites_raw_email_addresses(monkeypatch) -> None:
    async def resolve(org_id, queries, exclude_user_id=None):
        return [], [{"name": "new@acme.com", "email": None, "reason": "No one matched"}]

    async def invite(**kwargs):
        return {"invited": kwargs["emails"], "already_invited": [], "outside_organization": []}

    monkeypatch.setattr("loombot.workspace_context.resolve_members_from_queries", resolve)
    monkeypatch.setattr(workspaces, "invite_members_by_email", invite)
    result = await workspaces.add_members_by_query(
        org_id="o", user_id="u1", workspace_id="w", queries=["new@acme.com"]
    )
    assert result["invited"] == ["new@acme.com"]
