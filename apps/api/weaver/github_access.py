"""Per-user GitHub connector: encrypted PAT, repo allowlist, and capability ACL."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException

from shuttle.github_client import inspect_token, list_repos, repo_full_name
from weaver.integrations import (
    _delete_connection,
    _get_connection,
    _save_connection,
)

PROVIDER_GITHUB = "github"

Capability = Literal["metadata", "contents", "pull_requests"]

CAP_METADATA: Capability = "metadata"
CAP_CONTENTS: Capability = "contents"
CAP_PULL_REQUESTS: Capability = "pull_requests"
ALL_CAPABILITIES: tuple[Capability, ...] = (
    CAP_METADATA,
    CAP_CONTENTS,
    CAP_PULL_REQUESTS,
)

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _norm_repo(value: str) -> str:
    return value.strip().lower()


def parse_capabilities(raw: object) -> list[Capability]:
    values: list[str]
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
    else:
        values = []
    allowed = set(ALL_CAPABILITIES)
    out: list[Capability] = []
    seen: set[str] = set()
    for item in values:
        if item in allowed and item not in seen:
            out.append(item)  # type: ignore[arg-type]
            seen.add(item)
    if CAP_PULL_REQUESTS in out and CAP_CONTENTS not in out:
        out.insert(0, CAP_CONTENTS)
    if (CAP_CONTENTS in out or CAP_PULL_REQUESTS in out) and CAP_METADATA not in out:
        out.insert(0, CAP_METADATA)
    return out


def parse_repositories(raw: object) -> list[str]:
    values: list[str]
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
    else:
        values = []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not _REPO_RE.match(item):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid repository name {item!r}. Use owner/name.",
            )
        key = _norm_repo(item)
        if key not in seen:
            out.append(item.strip())
            seen.add(key)
    return out


@dataclass(frozen=True)
class GitHubPolicy:
    capabilities: tuple[Capability, ...]
    repositories: tuple[str, ...]

    def as_json(self) -> str:
        return json.dumps(
            {
                "capabilities": list(self.capabilities),
                "repositories": list(self.repositories),
            },
            separators=(",", ":"),
        )

    def repo_set(self) -> set[str]:
        return {_norm_repo(name) for name in self.repositories}

    def allows_capability(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def allows_repo(self, owner: str, repo: str) -> bool:
        return _norm_repo(f"{owner}/{repo}") in self.repo_set()


def policy_from_scopes(raw: str | None) -> GitHubPolicy:
    if not raw or not raw.strip():
        return GitHubPolicy(capabilities=(), repositories=())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return GitHubPolicy(capabilities=(), repositories=())
    if not isinstance(data, dict):
        return GitHubPolicy(capabilities=(), repositories=())
    caps = parse_capabilities(data.get("capabilities"))
    repos = []
    for item in data.get("repositories") or []:
        text = str(item).strip()
        if _REPO_RE.match(text):
            repos.append(text)
    return GitHubPolicy(capabilities=tuple(caps), repositories=tuple(repos))


@dataclass(frozen=True)
class GitHubAccess:
    token: str
    login: str
    policy: GitHubPolicy

    def deny(self, capability: Capability, owner: str = "", repo: str = "") -> str | None:
        if not self.policy.allows_capability(capability):
            labels = {
                CAP_METADATA: "list or inspect repositories",
                CAP_CONTENTS: "read files",
                CAP_PULL_REQUESTS: "open pull requests",
            }
            return (
                "MindLoom is not allowed to "
                f"{labels[capability]} with your GitHub connection. "
                "Update permissions in Apps."
            )
        if owner and repo and not self.policy.allows_repo(owner, repo):
            return (
                f"Repository {owner}/{repo} is not in the repositories you granted "
                "to MindLoom. Update the GitHub allowlist in Apps."
            )
        return None


def not_connected_error() -> dict[str, Any]:
    return {
        "error": (
            "GitHub is not connected. Open Apps, paste a fine-grained personal "
            "access token, and choose which repositories and actions MindLoom may use."
        )
    }


_not_connected_error = not_connected_error


async def load_github_access(org_id: str, user_id: str) -> GitHubAccess | None:
    row = await _get_connection(org_id, user_id, PROVIDER_GITHUB)
    if row is None or not (row.access_token or "").strip():
        return None
    return GitHubAccess(
        token=row.access_token,
        login=row.account_email or "",
        policy=policy_from_scopes(row.scopes),
    )


async def inspect_github_token(token: str) -> dict[str, Any]:
    token = token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="A GitHub token is required.")
    result = await inspect_token(token)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=str(result["error"]))
    return result


async def connect_github(
    *,
    org_id: str,
    user_id: str,
    token: str,
    capabilities: object,
    repositories: object,
) -> dict[str, Any]:
    token = token.strip()
    inspected = await inspect_github_token(token)
    policy = _validated_policy(
        capabilities,
        repositories,
        available={_norm_repo(str(item["full_name"])) for item in inspected["repositories"]},
    )
    login = str(inspected.get("login") or "")
    await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_GITHUB,
        account_email=login or None,
        access_token=token,
        refresh_token=None,
        token_expiry=None,
        scopes=policy.as_json(),
    )
    return connection_public_view(login, policy)


async def update_github_policy(
    *,
    org_id: str,
    user_id: str,
    capabilities: object,
    repositories: object,
) -> dict[str, Any]:
    access = await load_github_access(org_id, user_id)
    if access is None:
        raise HTTPException(status_code=404, detail="GitHub is not connected.")
    listed = await list_repos(token=access.token, owner=None, per_page=100)
    if listed.get("error"):
        raise HTTPException(status_code=400, detail=str(listed["error"]))
    available = {
        _norm_repo(str(item.get("full_name") or ""))
        for item in listed.get("repositories") or []
        if item.get("full_name")
    }
    policy = _validated_policy(capabilities, repositories, available=available)
    await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_GITHUB,
        account_email=access.login or None,
        access_token=access.token,
        refresh_token=None,
        token_expiry=None,
        scopes=policy.as_json(),
    )
    return connection_public_view(access.login, policy)


async def get_github_connection(org_id: str, user_id: str) -> dict[str, Any] | None:
    access = await load_github_access(org_id, user_id)
    if access is None:
        return None
    return connection_public_view(access.login, access.policy)


async def disconnect_github(org_id: str, user_id: str) -> None:
    await _delete_connection(org_id, user_id, PROVIDER_GITHUB)


def connection_public_view(login: str, policy: GitHubPolicy) -> dict[str, Any]:
    return {
        "provider": PROVIDER_GITHUB,
        "connected": True,
        "account_login": login,
        "capabilities": list(policy.capabilities),
        "repositories": list(policy.repositories),
        "selected_resource_count": len(policy.repositories),
        "setup_status": "active" if policy.repositories and policy.capabilities else "setup_required",
    }


def integration_info_from_row(row: Any) -> dict[str, Any]:
    policy = policy_from_scopes(getattr(row, "scopes", None))
    login = getattr(row, "account_email", None) or ""
    public = connection_public_view(login, policy)
    created = getattr(row, "created_at", None)
    return {
        **public,
        "label": "GitHub",
        "account_email": login or None,
        "connected_at": created.isoformat() if created is not None else None,
        "last_synced_at": None,
    }


def _validated_policy(
    capabilities: object,
    repositories: object,
    *,
    available: set[str],
) -> GitHubPolicy:
    caps = parse_capabilities(capabilities)
    repos = parse_repositories(repositories)
    if not caps:
        raise HTTPException(
            status_code=400,
            detail="Choose at least one permission (metadata, contents, or pull requests).",
        )
    if not repos:
        raise HTTPException(
            status_code=400,
            detail="Choose at least one repository MindLoom may access.",
        )
    unknown = [_norm_repo(name) for name in repos if _norm_repo(name) not in available]
    if unknown and available:
        raise HTTPException(
            status_code=400,
            detail=(
                "These repositories are not visible to the token: "
                + ", ".join(sorted(unknown))
                + ". Create a fine-grained token that includes them, then retry."
            ),
        )
    return GitHubPolicy(capabilities=tuple(caps), repositories=tuple(repos))


def filter_listed_repos(repos: list[dict[str, Any]], policy: GitHubPolicy) -> list[dict[str, Any]]:
    allowed = policy.repo_set()
    return [
        item
        for item in repos
        if _norm_repo(repo_full_name(item.get("full_name"), item.get("owner"), item.get("name")))
        in allowed
    ]
