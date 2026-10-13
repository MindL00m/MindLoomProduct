"""GitHub connector and pull-request approval endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from weaver.github_access import (
    CAP_PULL_REQUESTS,
    connect_github,
    disconnect_github,
    get_github_connection,
    inspect_github_token,
    load_github_access,
    update_github_policy,
)
from shuttle.github_client import create_pull_request_with_file
from weaver.integrations import require_user_context
from core.rate_limit import ingest_limit, limit

router = APIRouter(prefix="/github", tags=["github"])


class ApprovePullRequestInput(BaseModel):
    """Payload echoed from a proposed Ask PR draft after user approval."""

    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    path: str = Field(min_length=1)
    base_branch: str = Field(default="main")
    branch_name: str = Field(min_length=1)
    new_content: str
    file_sha: str | None = None
    pr_title: str = Field(min_length=1)
    pr_body: str = ""
    commit_message: str = ""


class GitHubTokenInput(BaseModel):
    token: str = Field(min_length=1)


class GitHubConnectInput(BaseModel):
    token: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    repositories: list[str] = Field(min_length=1)


class GitHubPolicyInput(BaseModel):
    capabilities: list[str] = Field(min_length=1)
    repositories: list[str] = Field(min_length=1)


@router.post("/inspect")
@limit(ingest_limit)
async def inspect_github(
    request: Request,
    body: GitHubTokenInput,
    _ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    """Validate a PAT and return repositories it can see. The token is not stored."""

    return await inspect_github_token(body.token)


@router.get("/connection")
async def github_connection(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    connection = await get_github_connection(*ctx)
    if connection is None:
        return {"connected": False, "provider": "github"}
    return connection


@router.post("/connection")
@limit(ingest_limit)
async def save_github_connection(
    request: Request,
    body: GitHubConnectInput,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    org_id, user_id = ctx
    return await connect_github(
        org_id=org_id,
        user_id=user_id,
        token=body.token,
        capabilities=body.capabilities,
        repositories=body.repositories,
    )


@router.patch("/connection")
async def patch_github_connection(
    request: GitHubPolicyInput,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    org_id, user_id = ctx
    return await update_github_policy(
        org_id=org_id,
        user_id=user_id,
        capabilities=request.capabilities,
        repositories=request.repositories,
    )


@router.delete("/connection")
async def delete_github_connection(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict[str, str]:
    await disconnect_github(*ctx)
    return {"status": "disconnected"}


@router.post("/pull-requests")
async def approve_pull_request(
    request: ApprovePullRequestInput,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    """Create a branch, commit the approved file change, and open a PR."""

    org_id, user_id = ctx
    access = await load_github_access(org_id, user_id)
    if access is None:
        raise HTTPException(
            status_code=400,
            detail="GitHub is not connected. Add a token in Apps first.",
        )
    denied = access.deny(CAP_PULL_REQUESTS, request.owner, request.repo)
    if denied:
        raise HTTPException(status_code=403, detail=denied)

    return await create_pull_request_with_file(
        token=access.token,
        owner=request.owner,
        repo=request.repo,
        path=request.path,
        new_content=request.new_content,
        base_branch=request.base_branch,
        branch_name=request.branch_name,
        commit_message=request.commit_message or request.pr_title,
        pr_title=request.pr_title,
        pr_body=request.pr_body,
        file_sha=request.file_sha,
    )
