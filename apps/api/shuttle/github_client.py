"""GitHub REST helpers for the Ask agent (read + PR creation)."""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx
from fastapi import HTTPException

from core.provider_http import request_with_backoff

_API = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"


def _headers(token: str) -> dict[str, str]:
    secret = token.strip()
    if not secret:
        raise ValueError("GitHub token is missing.")
    return {
        "Authorization": f"Bearer {secret}",
        "Accept": _ACCEPT,
        "X-GitHub-Api-Version": _API_VERSION,
        "User-Agent": "CompanyBrain-MindLoom",
    }


def repo_full_name(full_name: object, owner: object = None, name: object = None) -> str:
    if isinstance(full_name, str) and full_name.strip():
        return full_name.strip()
    owner_s = str(owner or "").strip()
    name_s = str(name or "").strip()
    if owner_s and name_s:
        return f"{owner_s}/{name_s}"
    return ""


def _github_error(status: int, text: str) -> dict[str, Any]:
    return {"error": f"GitHub API error ({status}): {text[:400]}"}


def _payload_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    message = str(payload.get("message") or "").strip()
    if message:
        parts.append(message)
    for item in payload.get("errors") or []:
        if isinstance(item, dict) and item.get("message"):
            parts.append(str(item["message"]).strip())
        elif isinstance(item, str) and item.strip():
            parts.append(item.strip())
    return " ".join(part for part in parts if part)


def _raise_github(
    status: int,
    payload: Any,
    fallback: str,
    *,
    write: bool = False,
) -> None:
    github_msg = _payload_message(payload)
    detail = fallback
    if github_msg and github_msg.lower() not in detail.lower():
        detail = f"{fallback} {github_msg}"
    if status in {401, 403} and write:
        detail += (
            " A fine-grained token needs Contents: Read and write (not Read only) "
            "and Pull requests: Read and write on this repository. "
            "If the org uses SAML SSO, authorize the token for that organization."
        )
    code = status if status in {401, 403, 404, 409, 422} else 502
    raise HTTPException(status_code=code, detail=detail.strip())


async def inspect_token(token: str) -> dict[str, Any]:
    """Validate a PAT and list repositories it can currently see."""

    async with httpx.AsyncClient(timeout=30.0) as client:
        user_response = await request_with_backoff(
            client,
            "GET",
            f"{_API}/user",
            headers=_headers(token),
        )
        if user_response.status_code == 401:
            return {"error": "GitHub rejected this token. Check that it is valid and not expired."}
        if user_response.status_code >= 400:
            return _github_error(user_response.status_code, user_response.text)
        profile = user_response.json()
        listed = await list_repos(token=token, owner=None, per_page=100)
        if listed.get("error"):
            return listed
        return {
            "login": profile.get("login") or "",
            "name": profile.get("name") or "",
            "html_url": profile.get("html_url") or "",
            "repositories": listed.get("repositories") or [],
        }


async def list_repos(
    *,
    token: str,
    owner: str | None = None,
    per_page: int = 30,
) -> dict[str, Any]:
    """List repositories for the authenticated token, or for a given owner/org."""

    per_page = max(1, min(per_page, 100))
    async with httpx.AsyncClient(timeout=30.0) as client:
        if owner and owner.strip():
            login = owner.strip()
            response = await request_with_backoff(
                client,
                "GET",
                f"{_API}/users/{login}/repos",
                headers=_headers(token),
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
                    headers=_headers(token),
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
                headers=_headers(token),
                params={
                    "per_page": str(per_page),
                    "sort": "updated",
                    "direction": "desc",
                    "affiliation": "owner,collaborator,organization_member",
                },
            )

    if response.status_code >= 400:
        return _github_error(response.status_code, response.text)

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


async def get_repo(token: str, owner: str, repo: str) -> dict[str, Any]:
    """Fetch metadata for a single repository."""

    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        return {"error": "owner and repo are required."}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await request_with_backoff(
            client,
            "GET",
            f"{_API}/repos/{owner}/{repo}",
            headers=_headers(token),
        )

    if response.status_code == 404:
        return {"error": f"Repository {owner}/{repo} not found or not accessible."}
    if response.status_code >= 400:
        return _github_error(response.status_code, response.text)

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
    token: str,
    owner: str,
    repo: str,
    path: str,
    *,
    ref: str | None = None,
) -> dict[str, Any]:
    """Read a file (or list a directory) from a repository."""

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
            headers=_headers(token),
            params=params or None,
        )

    if response.status_code == 404:
        return {"error": f"Path {path} not found in {owner}/{repo}."}
    if response.status_code >= 400:
        return _github_error(response.status_code, response.text)

    data = response.json()

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
    token: str,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> tuple[int, Any]:
    response = await request_with_backoff(
        client,
        method,
        url,
        headers=_headers(token),
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
    token: str,
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

    if not token.strip():
        raise HTTPException(
            status_code=400,
            detail="GitHub is not connected. Add a token in Apps first.",
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
            client, "GET", f"{_API}/repos/{owner}/{repo}", token=token
        )
        if status == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Repository {owner}/{repo} not found or not accessible.",
            )
        if status >= 400:
            _raise_github(status, repo_data, f"GitHub repo lookup failed ({status}).")
        if not base_branch:
            base_branch = str((repo_data or {}).get("default_branch") or "main")

        status, ref_data = await _gh_json(
            client,
            "GET",
            f"{_API}/repos/{owner}/{repo}/git/ref/heads/{base_branch}",
            token=token,
        )
        if status >= 400:
            _raise_github(
                status, ref_data, f"Could not resolve base branch '{base_branch}' ({status})."
            )
        base_sha = ((ref_data or {}).get("object") or {}).get("sha")
        if not base_sha:
            raise HTTPException(status_code=502, detail="Base branch SHA missing.")

        status, ref_create = await _gh_json(
            client,
            "POST",
            f"{_API}/repos/{owner}/{repo}/git/refs",
            token=token,
            json_body={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
        if status == 422:
            suffix = base_sha[:7]
            branch_name = f"{branch_name}-{suffix}"
            status, ref_create = await _gh_json(
                client,
                "POST",
                f"{_API}/repos/{owner}/{repo}/git/refs",
                token=token,
                json_body={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
            )
        if status >= 400:
            _raise_github(
                status,
                ref_create,
                f"Could not create branch '{branch_name}' ({status}).",
                write=True,
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
            token=token,
            json_body=put_body,
        )
        if status >= 400:
            _raise_github(
                status,
                put_data,
                f"Could not commit file change ({status}).",
                write=True,
            )

        status, pr_data = await _gh_json(
            client,
            "POST",
            f"{_API}/repos/{owner}/{repo}/pulls",
            token=token,
            json_body={
                "title": pr_title.strip(),
                "body": (pr_body or "").strip()
                or "Proposed by MindLoom Ask. Review the diff before merging.",
                "head": branch_name,
                "base": base_branch,
            },
        )
        if status >= 400:
            _raise_github(
                status,
                pr_data,
                f"Could not open pull request ({status}).",
                write=True,
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
