"""GitHub pull-request approval endpoints for Ask."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from github_client import create_pull_request_with_file
from integrations import require_user_context

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


@router.post("/pull-requests")
async def approve_pull_request(
    request: ApprovePullRequestInput,
    _ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict:
    """Create a branch, commit the approved file change, and open a PR."""

    return await create_pull_request_with_file(
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
