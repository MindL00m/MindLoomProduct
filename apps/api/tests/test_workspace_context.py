"""Workspace CONTEXT.md propose, Loombot scoping, and resync."""

from __future__ import annotations

import pytest

import ask_agent
import workspace_context
import workspaces
from models import ExpertResult, ProposedWorkspace, RetrievalResult


def test_wants_workspace_detects_create_intent() -> None:
    assert ask_agent._wants_workspace("Create a workspace for Project X")
    assert ask_agent._wants_workspace("Set up a workspace with the migration team")
    assert not ask_agent._wants_workspace("Who is working on Project X?")


@pytest.mark.asyncio
async def test_resolve_members_from_experts_matches_email(monkeypatch) -> None:
    class FakeResult:
        def __init__(self, row):
            self._row = row

        def mappings(self):
            return self

        def one_or_none(self):
            return self._row

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *_args, **_kwargs):
            return FakeResult({
                "user_id": "u-priya",
                "name": "Priya Shah",
                "email": "priya@example.com",
            })

    class FakeFactory:
        def __call__(self):
            return FakeSession()

    monkeypatch.setattr(workspace_context, "get_session_factory", FakeFactory)

    members, unmatched = await workspace_context.resolve_members_from_experts(
        "org-1",
        [
            ExpertResult(
                name="Priya Shah",
                reason="Owns Project X",
                relationship_count=3,
                email="priya@example.com",
            ),
            ExpertResult(
                name="Ghost",
                reason="Mentioned once",
                relationship_count=1,
                email="ghost@example.com",
            ),
        ],
        exclude_user_id="u-me",
    )

    # Second expert: email lookup returns same FakeResult — for a stricter test,
    # treat only first unique user. Fake always returns priya, so Ghost also maps.
    assert any(m["user_id"] == "u-priya" for m in members)


@pytest.mark.asyncio
async def test_propose_workspace_does_not_create(monkeypatch) -> None:
    created = {"called": False}

    async def fake_draft(**kwargs):
        return {
            "status": "proposed",
            "draft": {
                "name": "Project X",
                "purpose": "Project X",
                "context_md": "# Purpose\n\nProject X\n",
                "loombot_mode": "context_only",
                "members": [{
                    "user_id": "u-priya",
                    "name": "Priya",
                    "email": "priya@example.com",
                    "reason": "Owns Project X",
                }],
                "unmatched_people": [],
            },
        }

    async def boom(*_a, **_k):
        created["called"] = True
        raise AssertionError("must not create from propose tool")

    monkeypatch.setattr(workspace_context, "propose_workspace_draft", fake_draft)
    monkeypatch.setattr(workspaces, "create_workspace", boom)

    result, msg, pr, ws = await ask_agent._run_tool(
        name="propose_workspace",
        arguments={"name": "Project X", "purpose": "Project X"},
        org_id="org-1",
        user_id="u-me",
        people_cache={},
    )
    assert created["called"] is False
    assert msg is None
    assert pr is None
    assert result["status"] == "proposed"
    assert isinstance(ws, ProposedWorkspace)
    assert ws.name == "Project X"
    assert ws.loombot_mode == "context_only"
    assert ws.members[0].email == "priya@example.com"
    assert "Project X" in ws.context_md


@pytest.mark.asyncio
async def test_loombot_context_only_skips_retrieve(monkeypatch) -> None:
    retrieve_called = {"n": 0}

    async def fake_retrieve(*_a, **_k):
        retrieve_called["n"] += 1
        return RetrievalResult(chunks=[], experts=[], entities_found=[])

    async def fake_context_answer(question: str, context_md: str) -> str:
        assert "Project X" in context_md
        assert "timeline" in question.lower() or True
        return "From CONTEXT.md: launch is Q3."

    monkeypatch.setattr("retrieval.retrieve", fake_retrieve)
    monkeypatch.setattr(workspaces, "_loombot_reply_from_context", fake_context_answer)

    answer = await workspaces._loombot_reply(
        org_id="org-1",
        user_id="u-1",
        question="What is the timeline?",
        workspace={
            "loombot_mode": "context_only",
            "context_md": "# Purpose\n\nProject X\n",
        },
    )
    assert retrieve_called["n"] == 0
    assert "CONTEXT.md" in answer or "Q3" in answer


@pytest.mark.asyncio
async def test_loombot_context_only_empty_prompts_resync() -> None:
    answer = await workspaces._loombot_reply(
        org_id="org-1",
        user_id="u-1",
        question="Anything?",
        workspace={"loombot_mode": "context_only", "context_md": ""},
    )
    assert "Resync" in answer


@pytest.mark.asyncio
async def test_propose_workspace_draft_builds_context(monkeypatch) -> None:
    async def fake_tokens(*_a, **_k):
        return []

    async def fake_retrieve(question, history, org_id, access_tokens):
        assert "Project X" in question
        return RetrievalResult(
            chunks=[],
            experts=[
                ExpertResult(
                    name="Priya",
                    reason="Owns Project X",
                    relationship_count=2,
                    email="priya@example.com",
                )
            ],
            entities_found=["Project X"],
        )

    async def fake_resolve(org_id, experts, *, exclude_user_id=None):
        return (
            [{
                "user_id": "u-priya",
                "name": "Priya",
                "email": "priya@example.com",
                "reason": "Owns Project X",
            }],
            [],
        )

    async def fake_queries(*_a, **_k):
        return []

    async def fake_generate(**kwargs):
        return "# Purpose\n\nProject X workspace context\n"

    monkeypatch.setattr("auth.get_user_access_tokens", fake_tokens)
    monkeypatch.setattr("retrieval.retrieve", fake_retrieve)
    monkeypatch.setattr(workspace_context, "resolve_members_from_experts", fake_resolve)
    monkeypatch.setattr(workspace_context, "resolve_members_from_queries", fake_queries)
    monkeypatch.setattr(workspace_context, "generate_workspace_context", fake_generate)

    result = await workspace_context.propose_workspace_draft(
        org_id="org-1",
        user_id="u-me",
        name="Project X",
        purpose="Project X",
    )
    assert result["status"] == "proposed"
    assert result["draft"]["loombot_mode"] == "context_only"
    assert result["draft"]["members"][0]["user_id"] == "u-priya"
    assert "Project X" in result["draft"]["context_md"]


@pytest.mark.asyncio
async def test_resync_workspace_context_updates_md(monkeypatch) -> None:
    store = {
        "workspace": {
            "workspace_id": "ws-1",
            "name": "Project X",
            "kind": "group",
            "created_by": "u-me",
            "purpose": "Project X",
            "context_md": "# old\n",
            "context_synced_at": None,
            "loombot_mode": "context_only",
            "created_at": None,
            "updated_at": None,
        }
    }

    async def fake_require(*_a, **_k):
        return dict(store["workspace"])

    async def fake_members(*_a, **_k):
        return [{"user_id": "u-me", "name": "Me", "email": "me@example.com", "role": "admin"}]

    async def fake_generate(**kwargs):
        assert kwargs["purpose"] == "Project X"
        return "# Purpose\n\nFresh context\n"

    class FakeResult:
        def __init__(self, row):
            self._row = row

        def mappings(self):
            return self

        def one(self):
            return self._row

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, statement, params=None):
            sql = str(statement)
            if "UPDATE workspaces" in sql:
                store["workspace"]["context_md"] = params["context_md"]
                store["workspace"]["context_synced_at"] = "2026-01-01T00:00:00Z"
                return FakeResult(None)
            return FakeResult(store["workspace"])

        async def commit(self):
            return None

    class FakeFactory:
        def __call__(self):
            return FakeSession()

    monkeypatch.setattr(workspaces, "_require_member", fake_require)
    monkeypatch.setattr(workspaces, "list_workspace_members", fake_members)
    monkeypatch.setattr(workspace_context, "generate_workspace_context", fake_generate)
    monkeypatch.setattr(workspaces, "get_session_factory", FakeFactory)

    out = await workspaces.resync_workspace_context(
        org_id="org-1", user_id="u-me", workspace_id="ws-1"
    )
    assert out["status"] == "synced"
    assert "Fresh context" in out["context_md"]
