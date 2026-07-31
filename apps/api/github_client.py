"""GitHub REST helpers for the Ask agent (read + PR creation)."""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx
from fastapi import HTTPException

from config import get_settings
from provider_http import request_with_backoff

_API = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"


def github_configured() -> bool:
    return bool(get_settings().github_token.strip())


def _headers() -> dict[str, str]:
    token = get_settings().github_token.strip()
    if not token:
        raise ValueError("GITHUB_TOKEN is not configured.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": _ACCEPT,
        "X-GitHub-Api-Version": _API_VERSION,
        "User-Agent": "CompanyBrain-Loom",
    }


def _not_configured() -> dict[str, Any]:
    return {
        "error": (
            "GITHUB_TOKEN is not configured. Add it to the project root .env file "
            "and restart the API."
        )
    }


async def list_repos(
    *,
    owner: str | None = None,
    per_page: int = 30,
) -> dict[str, Any]:
    """List repositories for the authenticated user, or for a given owner/org."""

    if not github_configured():
        return _not_configured()

    per_page = max(1, min(per_page, 100))
    async with httpx.AsyncClient(timeout=30.0) as client:
        if owner and owner.strip():
            login = owner.strip()
            response = await request_with_backoff(
                client,
                "GET",
                f"{_API}/users/{login}/repos",
                headers=_headers(),
                params={
                    "per_page": str(per_page),
                    "sort": "updated",
                    "direction": "desc",
                },
            )
            if response.status_code == 404:
                response = await request_with_backoff(
                    client,
                    "GET",
                    f"{_API}/orgs/{login}/repos",
                    headers=_headers(),
                    params={
                        "per_page": str(per_page),
                        "sort": "updated",
                        "direction": "desc",
                    },
                )
        else:
            response = await request_with_backoff(
                client,
                "GET",
                f"{_API}/user/repos",
                headers=_headers(),
                params={
                    "per_page": str(per_page),
                    "sort": "updated",
                    "direction": "desc",
                    "affiliation": "owner,collaborator,organization_member",
                },
            )

    if response.status_code >= 400:
        return {
            "error": f"GitHub API error ({response.status_code}): {response.text[:400]}"
        }

    repos = response.json()
    if not isinstance(repos, list):
        return {"error": "Unexpected GitHub response for repository list."}

    return {
        "count": len(repos),
        "repositories": [
            {
                "full_name": r.get("full_name"),
                "description": r.get("description"),
                "private": r.get("private"),
                "default_branch": r.get("default_branch"),
                "html_url": r.get("html_url"),
                "language": r.get("language"),
                "updated_at": r.get("updated_at"),
                "stars": r.get("stargazers_count"),
            }
            for r in repos
        ],
    }


async def get_repo(owner: str, repo: str) -> dict[str, Any]:
    """Fetch metadata for a single repository."""

    if not github_configured():
        return _not_configured()

    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        return {"error": "owner and repo are required."}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await request_with_backoff(
            client,
            "GET",
            f"{_API}/repos/{owner}/{repo}",
            headers=_headers(),
        )

    if response.status_code == 404:
        return {"error": f"Repository {owner}/{repo} not found or not accessible."}
    if response.status_code >= 400:
        return {
            "error": f"GitHub API error ({response.status_code}): {response.text[:400]}"
        }

    data = response.json()
    return {
        "full_name": data.get("full_name"),
        "description": data.get("description"),
        "private": data.get("private"),
        "default_branch": data.get("default_branch"),
        "html_url": data.get("html_url"),
        "language": data.get("language"),
        "topics": data.get("topics") or [],
        "open_issues": data.get("open_issues_count"),
        "updated_at": data.get("updated_at"),
        "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
    }


async def get_file_contents(
    owner: str,
    repo: str,
    path: str,
    *,
    ref: str | None = None,
) -> dict[str, Any]:
    """Read a file (or list a directory) from a repository."""

    if not github_configured():
        return _not_configured()

    owner = owner.strip()
    repo = repo.strip()
    path = path.strip().lstrip("/")
    if not owner or not repo or not path:
        return {"error": "owner, repo, and path are required."}

    params: dict[str, str] = {}
    if ref and ref.strip():
        params["ref"] = ref.strip()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await request_with_backoff(
            client,
            "GET",
            f"{_API}/repos/{owner}/{repo}/contents/{path}",
            headers=_headers(),
            params=params or None,
        )

    if response.status_code == 404:
        return {"error": f"Path {path} not found in {owner}/{repo}."}
    if response.status_code >= 400:
        return {
            "error": f"GitHub API error ({response.status_code}): {response.text[:400]}"
        }

    data = response.json()

    # Directory listing
    if isinstance(data, list):
        return {
            "type": "dir",
            "path": path,
            "entries": [
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "type": item.get("type"),
                    "size": item.get("size"),
                }
                for item in data
            ],
        }

    if data.get("type") != "file":
        return {
            "type": data.get("type"),
            "path": data.get("path"),
            "html_url": data.get("html_url"),
            "note": "Not a regular file; open html_url or list the directory.",
        }

    encoding = data.get("encoding")
    content = data.get("content") or ""
    text: str | None = None
    if encoding == "base64" and isinstance(content, str):
        raw = base64.b64decode(content)
        # Cap returned text so tool payloads stay small.
        if len(raw) > 80_000:
            return {
                "type": "file",
                "path": data.get("path"),
                "size": data.get("size"),
                "html_url": data.get("html_url"),
                "error": "File is too large to inline; open html_url instead.",
            }
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "type": "file",
                "path": data.get("path"),
                "size": data.get("size"),
                "html_url": data.get("html_url"),
                "error": "File is binary and cannot be shown as text.",
            }

    return {
        "type": "file",
        "path": data.get("path"),
        "size": data.get("size"),
        "sha": data.get("sha"),
        "html_url": data.get("html_url"),
        "content": text,
    }


def _slug_branch(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = (slug or "loom-change")[:48].rstrip("-")
    return f"loom/{slug}"


async def _gh_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> tuple[int, Any]:
    response = await request_with_backoff(
        client,
        method,
        url,
        headers=_headers(),
        json=json_body,
        params=params,
    )
    try:
        payload = response.json() if response.content else None
    except ValueError:
        payload = {"raw": response.text[:400]}
    return response.status_code, payload


async def create_pull_request_with_file(
    *,
    owner: str,
    repo: str,
    path: str,
    new_content: str,
    base_branch: str,
    branch_name: str,
    commit_message: str,
    pr_title: str,
    pr_body: str,
    file_sha: str | None = None,
) -> dict[str, Any]:
    """Create a branch, commit one file change, and open a pull request.

    Only called after explicit user approval — never from the propose tool.
    """

    if not github_configured():
        raise HTTPException(
            status_code=400,
            detail="GITHUB_TOKEN is not configured. Add it to .env and restart the API.",
        )

    owner = owner.strip()
    repo = repo.strip()
    path = path.strip().lstrip("/")
    base_branch = (base_branch or "").strip()
    branch_name = (branch_name or "").strip() or _slug_branch(pr_title)
    if not owner or not repo or not path:
        raise HTTPException(status_code=400, detail="owner, repo, and path are required.")
    if not pr_title.strip():
        raise HTTPException(status_code=400, detail="pr_title is required.")
    if not commit_message.strip():
        commit_message = pr_title.strip()

    async with httpx.AsyncClient(timeout=45.0) as client:
        status, repo_data = await _gh_json(
            client, "GET", f"{_API}/repos/{owner}/{repo}"
        )
        if status == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Repository {owner}/{repo} not found or not accessible.",
            )
        if status >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"GitHub repo lookup failed ({status}).",
            )
        if not base_branch:
            base_branch = str((repo_data or {}).get("default_branch") or "main")

        status, ref_data = await _gh_json(
            client,
            "GET",
            f"{_API}/repos/{owner}/{repo}/git/ref/heads/{base_branch}",
        )
        if status >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Could not resolve base branch '{base_branch}' ({status}).",
            )
        base_sha = ((ref_data or {}).get("object") or {}).get("sha")
        if not base_sha:
            raise HTTPException(status_code=502, detail="Base branch SHA missing.")

        status, _ = await _gh_json(
            client,
            "POST",
            f"{_API}/repos/{owner}/{repo}/git/refs",
            json_body={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
        if status == 422:
            # Branch may already exist — try a unique suffix.
            suffix = base_sha[:7]
            branch_name = f"{branch_name}-{suffix}"
            status, _ = await _gh_json(
                client,
                "POST",
                f"{_API}/repos/{owner}/{repo}/git/refs",
                json_body={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
            )
        if status >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Could not create branch '{branch_name}' ({status}).",
            )

        put_body: dict[str, Any] = {
            "message": commit_message.strip(),
            "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
            "branch": branch_name,
        }
        if file_sha and file_sha.strip():
            put_body["sha"] = file_sha.strip()

        status, put_data = await _gh_json(
            client,
            "PUT",
            f"{_API}/repos/{owner}/{repo}/contents/{path}",
            json_body=put_body,
        )
        if status >= 400:
            detail = (put_data or {}).get("message") if isinstance(put_data, dict) else None
            raise HTTPException(
                status_code=502,
                detail=detail or f"Could not commit file change ({status}).",
            )

        status, pr_data = await _gh_json(
            client,
            "POST",
            f"{_API}/repos/{owner}/{repo}/pulls",
            json_body={
                "title": pr_title.strip(),
                "body": (pr_body or "").strip()
                or "Proposed by Loom Ask. Review the diff before merging.",
                "head": branch_name,
                "base": base_branch,
            },
        )
        if status >= 400:
            detail = (pr_data or {}).get("message") if isinstance(pr_data, dict) else None
            raise HTTPException(
                status_code=502,
                detail=detail or f"Could not open pull request ({status}).",
            )

    return {
        "status": "opened",
        "owner": owner,
        "repo": repo,
        "path": path,
        "branch": branch_name,
        "base_branch": base_branch,
        "pr_number": (pr_data or {}).get("number"),
        "pr_url": (pr_data or {}).get("html_url"),
        "commit_sha": ((put_data or {}).get("commit") or {}).get("sha"),
    }
