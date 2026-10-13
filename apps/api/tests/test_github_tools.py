"""GitHub client and Ask-agent GitHub tool wiring."""

from __future__ import annotations

import base64

import pytest

from loombot import ask_agent
from shuttle import github_client
from weaver.github_access import (
    CAP_CONTENTS,
    CAP_METADATA,
    CAP_PULL_REQUESTS,
    GitHubAccess,
    GitHubPolicy,
    filter_listed_repos,
    parse_capabilities,
)


def _access(
    *,
    capabilities: tuple[str, ...] = (CAP_METADATA, CAP_CONTENTS, CAP_PULL_REQUESTS),
    repositories: tuple[str, ...] = ("acme/api",),
) -> GitHubAccess:
    return GitHubAccess(
        token="ghp_test",
        login="octocat",
        policy=GitHubPolicy(capabilities=capabilities, repositories=repositories),
    )


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


def test_parse_capabilities_adds_implied_permissions() -> None:
    assert parse_capabilities(["pull_requests"]) == [
        "metadata",
        "contents",
        "pull_requests",
    ]
    assert parse_capabilities(["contents"]) == ["metadata", "contents"]


def test_policy_denies_ungranted_repo() -> None:
    access = _access(repositories=("acme/api",))
    assert access.deny(CAP_METADATA, "acme", "secret")
    assert access.deny(CAP_METADATA, "acme", "api") is None


def test_filter_listed_repos_keep_allowlist_only() -> None:
    access = _access(repositories=("acme/api",))
    filtered = filter_listed_repos(
        [
            {"full_name": "acme/api"},
            {"full_name": "acme/other"},
        ],
        access.policy,
    )
    assert [item["full_name"] for item in filtered] == ["acme/api"]


@pytest.mark.asyncio
async def test_github_list_repos_maps_response(monkeypatch) -> None:
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

    result = await github_client.list_repos(token="ghp_test")
    assert result["count"] == 1
    assert result["repositories"][0]["full_name"] == "acme/api"


@pytest.mark.asyncio
async def test_github_get_file_decodes_base64(monkeypatch) -> None:
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

    result = await github_client.get_file_contents("ghp_test", "acme", "api", "README.md")
    assert result["type"] == "file"
    assert result["content"] == "# Hello\n"


@pytest.mark.asyncio
async def test_run_tool_requires_github_connection(monkeypatch) -> None:
    async def fake_load(*_args, **_kwargs):
        return None

    monkeypatch.setattr("weaver.github_access.load_github_access", fake_load)
    result, proposal, pr, ws, _email = await ask_agent._run_tool(
        name="github_list_repos",
        arguments={},
        org_id="org-1",
        user_id="u-1",
        people_cache={},
    )
    assert proposal is None
    assert pr is None
    assert ws is None
    assert "not connected" in result["error"].lower()


@pytest.mark.asyncio
async def test_run_tool_dispatches_github_list(monkeypatch) -> None:
    async def fake_load(*_args, **_kwargs):
        return _access()

    async def fake_list_repos(*, token, owner=None, per_page=30):
        assert token == "ghp_test"
        return {
            "count": 2,
            "repositories": [
                {"full_name": "acme/api"},
                {"full_name": "acme/other"},
            ],
            "owner": owner,
            "per_page": per_page,
        }

    monkeypatch.setattr("weaver.github_access.load_github_access", fake_load)
    monkeypatch.setattr("shuttle.github_client.list_repos", fake_list_repos)
    result, proposal, pr, ws, _email = await ask_agent._run_tool(
        name="github_list_repos",
        arguments={"owner": "acme", "per_page": 10},
        org_id="org-1",
        user_id="u-1",
        people_cache={},
    )
    assert proposal is None
    assert pr is None
    assert ws is None
    assert result["count"] == 1
    assert result["repositories"][0]["full_name"] == "acme/api"
    assert result["granted_repositories"] == ["acme/api"]


@pytest.mark.asyncio
async def test_run_tool_blocks_ungranted_repo(monkeypatch) -> None:
    async def fake_load(*_args, **_kwargs):
        return _access(repositories=("acme/api",), capabilities=(CAP_METADATA, CAP_CONTENTS))

    monkeypatch.setattr("weaver.github_access.load_github_access", fake_load)
    result, *_rest = await ask_agent._run_tool(
        name="github_get_file",
        arguments={"owner": "acme", "repo": "secrets", "path": "README.md"},
        org_id="org-1",
        user_id="u-1",
        people_cache={},
    )
    assert "not in the repositories" in result["error"]


@pytest.mark.asyncio
async def test_run_tool_blocks_ungranted_capability(monkeypatch) -> None:
    async def fake_load(*_args, **_kwargs):
        return _access(capabilities=(CAP_METADATA,))

    monkeypatch.setattr("weaver.github_access.load_github_access", fake_load)
    result, *_rest = await ask_agent._run_tool(
        name="github_get_file",
        arguments={"owner": "acme", "repo": "api", "path": "README.md"},
        org_id="org-1",
        user_id="u-1",
        people_cache={},
    )
    assert "not allowed to read files" in result["error"]


@pytest.mark.asyncio
async def test_propose_github_pr_builds_draft(monkeypatch) -> None:
    async def fake_load(*_args, **_kwargs):
        return _access()

    async def fake_get_repo(token, owner, repo):
        assert token == "ghp_test"
        return {"default_branch": "main", "full_name": f"{owner}/{repo}"}

    async def fake_get_file(token, owner, repo, path, *, ref=None):
        return {
            "type": "file",
            "path": path,
            "sha": "abc123",
            "html_url": f"https://github.com/{owner}/{repo}/blob/main/{path}",
            "content": "hello\n",
        }

    monkeypatch.setattr("weaver.github_access.load_github_access", fake_load)
    monkeypatch.setattr("shuttle.github_client.get_repo", fake_get_repo)
    monkeypatch.setattr("shuttle.github_client.get_file_contents", fake_get_file)

    result, proposal, pr, ws, _email = await ask_agent._run_tool(
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

    async def fake_load(*_args, **_kwargs):
        return _access()

    async def fake_get_repo(token, owner, repo):
        return {"default_branch": "main"}

    async def fake_get_file(token, owner, repo, path, *, ref=None):
        return {"type": "file", "path": path, "sha": "sha", "content": "a\n"}

    async def fake_create(**_kwargs):
        called["create"] = True
        return {}

    monkeypatch.setattr("weaver.github_access.load_github_access", fake_load)
    monkeypatch.setattr("shuttle.github_client.get_repo", fake_get_repo)
    monkeypatch.setattr("shuttle.github_client.get_file_contents", fake_get_file)
    monkeypatch.setattr("shuttle.github_client.create_pull_request_with_file", fake_create)

    _, _, pr, ws, _email = await ask_agent._run_tool(
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
    calls: list[tuple[str, str]] = []

    async def fake_gh_json(_client, method, url, **kwargs):
        assert kwargs["token"] == "ghp_test"
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
        token="ghp_test",
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


@pytest.mark.asyncio
async def test_create_branch_403_explains_write_permissions(monkeypatch) -> None:
    async def fake_gh_json(_client, method, url, **kwargs):
        if url.endswith("/repos/acme/api") and method == "GET":
            return 200, {"default_branch": "main"}
        if "/git/ref/heads/main" in url:
            return 200, {"object": {"sha": "base-sha"}}
        if url.endswith("/git/refs") and method == "POST":
            return 403, {"message": "Resource not accessible by personal access token"}
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

    with pytest.raises(github_client.HTTPException) as raised:
        await github_client.create_pull_request_with_file(
            token="ghp_test",
            owner="acme",
            repo="api",
            path="README.md",
            new_content="# Hi\n",
            base_branch="main",
            branch_name="loom/update-readme",
            commit_message="Update README",
            pr_title="Update README",
            pr_body="Body",
        )
    assert raised.value.status_code == 403
    assert "Contents: Read and write" in raised.value.detail
    assert "Resource not accessible by personal access token" in raised.value.detail
