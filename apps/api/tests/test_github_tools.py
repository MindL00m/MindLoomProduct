"""GitHub client and Ask-agent GitHub tool wiring."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

import ask_agent
import github_client


def test_wants_github_detects_repo_intent() -> None:
    assert ask_agent._wants_github("List my GitHub repos")
    assert ask_agent._wants_github("Show the README for octocat/Hello-World")
    assert ask_agent._wants_github("What repositories do I have?")
    assert not ask_agent._wants_github("What is our pricing model?")


def test_split_owner_repo_accepts_slash_form() -> None:
    assert ask_agent._split_owner_repo({"owner": "octocat/Hello-World"}) == (
        "octocat",
        "Hello-World",
    )
    assert ask_agent._split_owner_repo(
        {"owner": "octocat", "repo": "Hello-World"}
    ) == ("octocat", "Hello-World")


@pytest.mark.asyncio
async def test_github_list_repos_requires_token(monkeypatch) -> None:
    monkeypatch.setattr(
        github_client,
        "get_settings",
        lambda: SimpleNamespace(github_token=""),
    )
    result = await github_client.list_repos()
    assert "GITHUB_TOKEN" in result["error"]


@pytest.mark.asyncio
async def test_github_list_repos_maps_response(monkeypatch) -> None:
    monkeypatch.setattr(
        github_client,
        "get_settings",
        lambda: SimpleNamespace(github_token="ghp_test"),
    )

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return [
                {
                    "full_name": "acme/api",
                    "description": "API service",
                    "private": True,
                    "default_branch": "main",
                    "html_url": "https://github.com/acme/api",
                    "language": "Python",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "stargazers_count": 3,
                }
            ]

    async def fake_request(_client, _method, url, **_kwargs):
        assert url.endswith("/user/repos")
        return FakeResponse()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(github_client.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(github_client, "request_with_backoff", fake_request)

    result = await github_client.list_repos()
    assert result["count"] == 1
    assert result["repositories"][0]["full_name"] == "acme/api"


@pytest.mark.asyncio
async def test_github_get_file_decodes_base64(monkeypatch) -> None:
    monkeypatch.setattr(
        github_client,
        "get_settings",
        lambda: SimpleNamespace(github_token="ghp_test"),
    )
    payload = base64.b64encode(b"# Hello\n").decode("ascii")

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "type": "file",
                "path": "README.md",
                "size": 8,
                "html_url": "https://github.com/acme/api/blob/main/README.md",
                "encoding": "base64",
                "content": payload,
            }

    async def fake_request(_client, _method, url, **_kwargs):
        assert "contents/README.md" in url
        return FakeResponse()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(github_client.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(github_client, "request_with_backoff", fake_request)

    result = await github_client.get_file_contents("acme", "api", "README.md")
    assert result["type"] == "file"
    assert result["content"] == "# Hello\n"


@pytest.mark.asyncio
async def test_run_tool_dispatches_github_list(monkeypatch) -> None:
    async def fake_list_repos(*, owner=None, per_page=30):
        return {"count": 0, "repositories": [], "owner": owner, "per_page": per_page}

    monkeypatch.setattr("github_client.list_repos", fake_list_repos)
    result, proposal, pr, ws = await ask_agent._run_tool(
        name="github_list_repos",
        arguments={"owner": "acme", "per_page": 10},
        org_id="org-1",
        user_id="u-1",
        people_cache={},
    )
    assert proposal is None
    assert pr is None
    assert ws is None
    assert result["owner"] == "acme"
    assert result["per_page"] == 10


@pytest.mark.asyncio
async def test_propose_github_pr_builds_draft(monkeypatch) -> None:
    async def fake_get_repo(owner, repo):
        return {"default_branch": "main", "full_name": f"{owner}/{repo}"}

    async def fake_get_file(owner, repo, path, *, ref=None):
        return {
            "type": "file",
            "path": path,
            "sha": "abc123",
            "html_url": f"https://github.com/{owner}/{repo}/blob/main/{path}",
            "content": "hello\n",
        }

    monkeypatch.setattr("github_client.get_repo", fake_get_repo)
    monkeypatch.setattr("github_client.get_file_contents", fake_get_file)

    result, proposal, pr, ws = await ask_agent._run_tool(
        name="propose_github_pr",
        arguments={
            "owner": "acme",
            "repo": "api",
            "path": "README.md",
            "new_content": "hello world\n",
            "pr_title": "Update README",
        },
        org_id="org-1",
        user_id="u-1",
        people_cache={},
    )
    assert proposal is None
    assert ws is None
    assert result["status"] == "proposed"
    assert pr is not None
    assert pr.old_content == "hello\n"
    assert pr.new_content == "hello world\n"
    assert pr.file_sha == "abc123"
    assert pr.base_branch == "main"
    assert pr.branch_name.startswith("loom/")


@pytest.mark.asyncio
async def test_propose_github_pr_does_not_create_pr(monkeypatch) -> None:
    called = {"create": False}

    async def fake_get_repo(owner, repo):
        return {"default_branch": "main"}

    async def fake_get_file(owner, repo, path, *, ref=None):
        return {"type": "file", "path": path, "sha": "sha", "content": "a\n"}

    async def fake_create(**_kwargs):
        called["create"] = True
        return {}

    monkeypatch.setattr("github_client.get_repo", fake_get_repo)
    monkeypatch.setattr("github_client.get_file_contents", fake_get_file)
    monkeypatch.setattr("github_client.create_pull_request_with_file", fake_create)

    _, _, pr, ws = await ask_agent._run_tool(
        name="propose_github_pr",
        arguments={
            "owner": "acme",
            "repo": "api",
            "path": "a.txt",
            "new_content": "b\n",
            "pr_title": "Change a",
        },
        org_id="org-1",
        user_id="u-1",
        people_cache={},
    )
    assert pr is not None
    assert ws is None
    assert called["create"] is False


@pytest.mark.asyncio
async def test_create_pull_request_with_file(monkeypatch) -> None:
    monkeypatch.setattr(
        github_client,
        "get_settings",
        lambda: SimpleNamespace(github_token="ghp_test"),
    )
    calls: list[tuple[str, str]] = []

    async def fake_gh_json(_client, method, url, **kwargs):
        calls.append((method, url))
        if url.endswith("/repos/acme/api") and method == "GET":
            return 200, {"default_branch": "main"}
        if "/git/ref/heads/main" in url:
            return 200, {"object": {"sha": "base-sha"}}
        if url.endswith("/git/refs") and method == "POST":
            return 201, {}
        if "/contents/" in url and method == "PUT":
            return 200, {"commit": {"sha": "commit-sha"}}
        if url.endswith("/pulls") and method == "POST":
            return 201, {
                "number": 42,
                "html_url": "https://github.com/acme/api/pull/42",
            }
        return 500, {"message": f"unexpected {method} {url}"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(github_client.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(github_client, "_gh_json", fake_gh_json)

    result = await github_client.create_pull_request_with_file(
        owner="acme",
        repo="api",
        path="README.md",
        new_content="# Hi\n",
        base_branch="main",
        branch_name="loom/update-readme",
        commit_message="Update README",
        pr_title="Update README",
        pr_body="Body",
        file_sha="old-sha",
    )
    assert result["pr_number"] == 42
    assert result["pr_url"].endswith("/pull/42")
    assert any(m == "POST" and u.endswith("/pulls") for m, u in calls)
