"""GitHub per-user connector policy and inspect/connect helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from weaver.github_access import connect_github, parse_repositories


def test_parse_repositories_rejects_bad_names() -> None:
    with pytest.raises(HTTPException):
        parse_repositories(["not a repo"])
    assert parse_repositories(["Acme/API", "acme/api"]) == ["Acme/API"]


@pytest.mark.asyncio
async def test_connect_github_stores_encrypted_policy(monkeypatch) -> None:
    saved: dict = {}

    async def fake_inspect(token: str):
        assert token == "github_pat_test"
        return {
            "login": "octocat",
            "repositories": [
                {"full_name": "acme/api"},
                {"full_name": "acme/web"},
            ],
        }

    async def fake_save(**kwargs):
        saved.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr("weaver.github_access.inspect_github_token", fake_inspect)
    monkeypatch.setattr("weaver.github_access._save_connection", fake_save)

    result = await connect_github(
        org_id="org-1",
        user_id="u-1",
        token="github_pat_test",
        capabilities=["contents"],
        repositories=["acme/api"],
    )
    assert result["account_login"] == "octocat"
    assert result["repositories"] == ["acme/api"]
    assert "contents" in result["capabilities"]
    assert "metadata" in result["capabilities"]
    assert saved["provider"] == "github"
    assert saved["access_token"] == "github_pat_test"
    assert "acme/web" not in saved["scopes"]
    assert "acme/api" in saved["scopes"]


@pytest.mark.asyncio
async def test_connect_github_rejects_repo_token_cannot_see(monkeypatch) -> None:
    async def fake_inspect(token: str):
        return {"login": "octocat", "repositories": [{"full_name": "acme/api"}]}

    monkeypatch.setattr("weaver.github_access.inspect_github_token", fake_inspect)
    with pytest.raises(HTTPException) as exc:
        await connect_github(
            org_id="org-1",
            user_id="u-1",
            token="github_pat_test",
            capabilities=["metadata"],
            repositories=["acme/secret"],
        )
    assert exc.value.status_code == 400
    assert "not visible to the token" in str(exc.value.detail)
