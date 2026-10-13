"""Ask agent with RAG plus Expert Messages and GitHub repository tools."""

from __future__ import annotations

import json
import logging
import re
from typing import Any


from loombot.answerer import generate_answer
from core.config import get_settings
from core.llm_provider import get_llm_client
from core.models import (
    ChatMessage,
    EphemeralDocument,
    MessageablePerson,
    ProposedEmail,
    ProposedExpertMessage,
    ProposedPullRequest,
    ProposedWorkspace,
    ProposedWorkspaceMember,
    ProposedWorkspaceUnmatched,
    QueryResponse,
    RetrievalResult,
)
from keeper.review_workflows import lookup_messageable_people

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_MAX_TOOL_ROUNDS = 5

_AGENT_SYSTEM = """\
You are MindLoom, a company knowledge assistant that can draft Expert Messages,
draft emails, work with GitHub repositories when a token is configured, and
propose project workspaces with a CONTEXT.md file for Loombot.

You have retrieval context below for factual questions. Use it for knowledge answers
and cite sources with [SOURCE: chunk_id] when applicable.

Messaging rules:
- When the user wants to notify, tell, message, email, or ask a coworker, you MUST use tools.
- Always call lookup_person first with the role/title/name/email from the request
  (e.g. query "CTO" when they say "send a message to the CTO").
- After lookup, call propose_email with a subject and body. If lookup found an address,
  pass it as recipient_email; if not, leave recipient_email empty so the user can type one.
- If lookup_person returns a signed-in MindLoom user (non-empty user_id), also call
  propose_expert_message for the in-app option.
- Do NOT write the draft email/message in your reply text. The UI shows a compose card.
  Your final text should only say a draft is ready for approval.
- If multiple people match, still call propose_email with the best guess or an empty
  recipient_email and list the candidates; the user will correct the address.
- propose_email and propose_expert_message do NOT send anything. Never say a message
  or email was sent.
- Expert Messages require a signed-in MindLoom user. Email can go to any address.
- Google Workspace email sending is {gmail_status}.

GitHub rules:
- GitHub tools use this user's Apps connector, not a server token. They may only
  see repositories and perform actions the user granted (metadata, file contents,
  and/or pull requests).
- When the user asks about GitHub repos, code, READMEs, or file contents, use the
  github_* tools. Prefer github_list_repos, then github_get_repo / github_get_file.
- When the user asks you to change, update, fix, edit, or open a PR for a file:
  1) Call github_get_file (or github_get_repo first if owner/repo is unclear).
  2) Call propose_github_pr with the FULL updated file contents in new_content.
- propose_github_pr does NOT create a PR. The UI shows a diff for approval.
  Your final text should only say a draft PR is ready for review — never claim a PR
  was opened or a file was committed.
- For owner/repo, accept "owner/repo" or separate owner and repo fields.
- Summarize tool results clearly; quote short snippets when useful.
- If a tool says GitHub is not connected or a repository/permission was not granted,
  tell the user to open Apps, paste a fine-grained personal access token, and choose
  exactly which repositories and actions MindLoom may use.

Workspace rules:
- When the user asks to create, make, set up, or spin up a workspace for a project
  or topic, you MUST call propose_workspace.
- Use a clear workspace name and a purpose that captures the project/topic.
- Optionally pass member_queries for specific people by name/email/title; the tool
  also resolves people from knowledge-graph experts for the purpose.
- propose_workspace does NOT create the workspace. The UI shows members + CONTEXT.md
  for approval. Your final text should only say a draft workspace is ready.
- Never claim a workspace was created.

- For ordinary knowledge questions that are not messaging, GitHub, or workspace
  creation, answer from context without tools.

Context:
{context_string}
"""

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_person",
            "description": (
                "Find people by name, email, or directory title/role "
                "(e.g. CTO, engineering manager). Returns signed-in MindLoom users "
                "and directory emails that may not have a MindLoom account."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Name, email, or job title/role to search "
                            "(examples: 'Priya', 'cto', 'head of sales')."
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_expert_message",
            "description": (
                "Draft an Expert Message for user confirmation. Does not send."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_user_id": {
                        "type": "string",
                        "description": "user_id from lookup_person results.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message body to propose for approval.",
                    },
                },
                "required": ["recipient_user_id", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_email",
            "description": (
                "Draft an email (recipient, subject, body) for user confirmation. "
                "Does not send. Use for any email address, including people who "
                "are not signed-in MindLoom users. Leave recipient_email empty if unknown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_email": {
                        "type": "string",
                        "description": "To address. Empty if the user must enter one.",
                    },
                    "recipient_name": {
                        "type": "string",
                        "description": "Optional display name for the recipient.",
                    },
                    "recipient_user_id": {
                        "type": "string",
                        "description": "Optional MindLoom user_id from lookup_person.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body to propose for approval.",
                    },
                },
                "required": ["subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_repos",
            "description": (
                "List GitHub repositories accessible with the configured token. "
                "Optionally filter to a user or organization login."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {
                        "type": "string",
                        "description": (
                            "Optional GitHub user or org login. Omit to list repos "
                            "for the authenticated token owner."
                        ),
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Max repos to return (1-100, default 30).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_get_repo",
            "description": "Get metadata for a single GitHub repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {
                        "type": "string",
                        "description": "Repository owner login.",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository name.",
                    },
                },
                "required": ["owner", "repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_get_file",
            "description": (
                "Read a file or list a directory from a GitHub repository "
                "(e.g. README.md, src/main.py)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {
                        "type": "string",
                        "description": "Repository owner login.",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository name.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path within the repo.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Optional branch, tag, or commit SHA.",
                    },
                },
                "required": ["owner", "repo", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_github_pr",
            "description": (
                "Draft a single-file change for user approval. Fetches the current "
                "file, builds a diff, and returns a proposed PR. Does NOT create a "
                "branch or open a pull request."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {
                        "type": "string",
                        "description": "Repository owner login (or owner/repo).",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository name.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path to create or update.",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "Full updated file contents after the change.",
                    },
                    "pr_title": {
                        "type": "string",
                        "description": "Pull request title.",
                    },
                    "pr_body": {
                        "type": "string",
                        "description": "Optional pull request description.",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Optional commit message (defaults to pr_title).",
                    },
                    "base_branch": {
                        "type": "string",
                        "description": "Base branch (defaults to the repo default).",
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "Optional head branch name for the PR.",
                    },
                },
                "required": ["owner", "repo", "path", "new_content", "pr_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_workspace",
            "description": (
                "Draft a project workspace with members and a CONTEXT.md file "
                "scraped from company knowledge. Does NOT create the workspace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Workspace display name (e.g. Project X).",
                    },
                    "purpose": {
                        "type": "string",
                        "description": (
                            "Topic/intent used to scrape knowledge and seed CONTEXT.md "
                            "(e.g. 'Project X migration')."
                        ),
                    },
                    "member_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional name/email/title queries for people to include "
                            "as members, in addition to graph experts."
                        ),
                    },
                },
                "required": ["name", "purpose"],
            },
        },
    },
]


def _history_messages(history: list[ChatMessage]) -> list[dict[str, str]]:
    recent = history[-12:]
    return [{"role": turn.role, "content": turn.content} for turn in recent]


def _wants_messaging(question: str) -> bool:
    lowered = question.lower()
    triggers = (
        "message ",
        "tell ",
        "notify ",
        "ping ",
        "ask ",
        "send ",
        "let know",
        "reach out",
        "dm ",
        "text ",
        "an email",
        "e-mail",
    )
    if any(token in lowered for token in triggers):
        return True
    return bool(
        re.search(
            r"\be-?mail\s+(?:to\s+)?(?:the\s+)?(?!policy\b|address\b|settings\b|server\b)\S+",
            lowered,
        )
    )
    return any(token in lowered for token in triggers)


def _wants_github(question: str) -> bool:
    lowered = question.lower()
    triggers = (
        "github",
        "gh repo",
        "repository",
        "repositories",
        "pull request",
        "readme",
        "my repos",
        "list repos",
        "list repositories",
        "repo ",
        "repos ",
        "repos?",
        "codebase",
        "source code",
        " open a pr",
        "create a pr",
        "submit a pr",
        "make a pr",
        "open pr",
        "create pr",
        "submit pr",
        "make a change",
        "update the file",
        "edit the file",
        "fix the file",
        "change the file",
    )
    return any(token in lowered for token in triggers)


def _wants_workspace(question: str) -> bool:
    lowered = question.lower()
    triggers = (
        "create a workspace",
        "create workspace",
        "make a workspace",
        "make workspace",
        "set up a workspace",
        "setup a workspace",
        "spin up a workspace",
        "new workspace",
        "workspace for",
        "start a workspace",
    )
    return any(token in lowered for token in triggers)


def _extract_recipient_query(question: str) -> str | None:
    """Best-effort recipient hint from a messaging ask (title/name)."""

    patterns = (
        r"(?:send(?:\s+a)?\s+message\s+to)\s+(?:the\s+)?(.+?)\s+(?:saying|that|about|regarding)\b",
        r"(?:send(?:\s+a)?\s+message\s+to)\s+(?:the\s+)?(.+)$",
        r"(?:message|tell|notify|ping|text|email)\s+(?:to\s+)?(?:the\s+)?(.+?)\s+(?:saying|that|about|regarding)\b",
        r"(?:ask)\s+(?:the\s+)?(.+?)\s+(?:saying|that|about|regarding)\b",
    )
    lowered = question.strip()
    for pattern in patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if match:
            candidate = " ".join(match.group(1).strip(" .,!?:;").split())
            candidate = re.sub(r"^(to|the)\s+", "", candidate, flags=re.IGNORECASE)
            if candidate:
                return candidate
    return None


def _draft_message_from_question(question: str) -> str:
    match = re.search(
        r"\b(?:saying|that|about|regarding)\s+(.+)$",
        question.strip(),
        flags=re.IGNORECASE,
    )
    if match:
        body = match.group(1).strip(" .")
        if body:
            return body[0].upper() + body[1:]
    return question.strip()


def _draft_subject_from_question(question: str) -> str:
    body = _draft_message_from_question(question)
    first_line = body.split("\n", 1)[0].strip()
    if len(first_line) <= 72:
        return first_line or "Message from MindLoom"
    return first_line[:69].rstrip() + "..."


def _person_cache_key(person: dict) -> str:
    uid = str(person.get("user_id") or "").strip()
    if uid:
        return uid
    email = str(person.get("email") or "").strip().lower()
    return f"email:{email}" if email else ""


def _store_person(people_cache: dict[str, dict], person: dict) -> None:
    uid = str(person.get("user_id") or "").strip()
    email = str(person.get("email") or "").strip().lower()
    if uid:
        people_cache[uid] = person
    if email:
        people_cache[f"email:{email}"] = person


def _as_messageable(person: dict) -> MessageablePerson:
    return MessageablePerson(
        user_id=str(person.get("user_id") or ""),
        name=str(person.get("name") or person.get("email") or ""),
        email=str(person.get("email") or ""),
        title=person.get("title"),
        department=person.get("department"),
    )


def _candidates_from_cache(people_cache: dict[str, dict]) -> list[MessageablePerson]:
    seen: set[str] = set()
    out: list[MessageablePerson] = []
    for person in people_cache.values():
        key = _person_cache_key(person)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(_as_messageable(person))
    return out


def _proposal_from_person(person: dict, message: str) -> ProposedExpertMessage:
    return ProposedExpertMessage(
        recipient_user_id=str(person["user_id"]),
        recipient_name=str(person["name"]),
        recipient_email=str(person["email"]),
        message=message,
        candidates=[_as_messageable(person)],
    )


def _email_from_person(
    person: dict | None,
    *,
    question: str,
    google_connected: bool,
    candidates: list[MessageablePerson] | None = None,
    subject: str | None = None,
    body: str | None = None,
) -> ProposedEmail:
    drafted_body = body or _draft_message_from_question(question)
    drafted_subject = subject or _draft_subject_from_question(question)
    uid = str((person or {}).get("user_id") or "").strip() or None
    return ProposedEmail(
        recipient_email=str((person or {}).get("email") or ""),
        recipient_name=str((person or {}).get("name") or ""),
        recipient_user_id=uid,
        subject=drafted_subject,
        body=drafted_body,
        google_connected=google_connected,
        candidates=candidates or ([_as_messageable(person)] if person else []),
    )


async def _ensure_proposals(
    *,
    question: str,
    org_id: str,
    user_id: str,
    people_cache: dict[str, dict],
    proposal: ProposedExpertMessage | None,
    email_proposal: ProposedEmail | None,
    google_connected: bool,
) -> tuple[ProposedExpertMessage | None, ProposedEmail | None]:
    """Fill Expert Message and email drafts when the model skipped a tool."""

    people: list[dict] = []
    if len(people_cache) >= 1:
        seen: set[str] = set()
        for person in people_cache.values():
            key = _person_cache_key(person)
            if not key or key in seen:
                continue
            seen.add(key)
            people.append(person)
    hint = _extract_recipient_query(question)
    if not people and hint:
        people = await lookup_messageable_people(
            org_id, hint, exclude_user_id=user_id, limit=5
        )
        for person in people:
            _store_person(people_cache, person)

    unique_people = people
    person = unique_people[0] if len(unique_people) == 1 else None
    candidates = [_as_messageable(item) for item in unique_people]
    loom_person = None
    if person and str(person.get("user_id") or "").strip():
        loom_person = person
    elif len(unique_people) == 1 and str(unique_people[0].get("user_id") or "").strip():
        loom_person = unique_people[0]

    if proposal is None and loom_person is not None:
        proposal = _proposal_from_person(
            loom_person, _draft_message_from_question(question)
        )

    if email_proposal is None:
        email_proposal = _email_from_person(
            person,
            question=question,
            google_connected=google_connected,
            candidates=candidates,
        )
    else:
        email_proposal = email_proposal.model_copy(
            update={"google_connected": google_connected}
        )
        if not email_proposal.candidates and candidates:
            email_proposal = email_proposal.model_copy(update={"candidates": candidates})

    return proposal, email_proposal


def _split_owner_repo(arguments: dict[str, Any]) -> tuple[str, str]:
    owner = str(arguments.get("owner") or "").strip()
    repo = str(arguments.get("repo") or "").strip()
    if "/" in owner and not repo:
        parts = owner.split("/", 1)
        return parts[0].strip(), parts[1].strip()
    if "/" in repo and not owner:
        parts = repo.split("/", 1)
        return parts[0].strip(), parts[1].strip()
    return owner, repo


def _slug_branch(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = (slug or "loom-change")[:48].rstrip("-")
    return f"loom/{slug}"


async def _propose_github_pr(
    arguments: dict[str, Any],
    *,
    org_id: str,
    user_id: str,
) -> tuple[Any, ProposedPullRequest | None]:
    from weaver.github_access import CAP_CONTENTS, CAP_PULL_REQUESTS, load_github_access, _not_connected_error
    from shuttle.github_client import get_file_contents, get_repo

    access = await load_github_access(org_id, user_id)
    if access is None:
        return _not_connected_error(), None

    owner, repo = _split_owner_repo(arguments)
    path = str(arguments.get("path") or "").strip().lstrip("/")
    new_content = arguments.get("new_content")
    pr_title = str(arguments.get("pr_title") or "").strip()
    if not owner or not repo or not path:
        return {"error": "owner, repo, and path are required."}, None
    if new_content is None:
        return {"error": "new_content is required."}, None
    if not isinstance(new_content, str):
        new_content = str(new_content)
    if not pr_title:
        return {"error": "pr_title is required."}, None

    denied = access.deny(CAP_PULL_REQUESTS, owner, repo) or access.deny(
        CAP_CONTENTS, owner, repo
    )
    if denied:
        return {"error": denied}, None

    base_branch = str(arguments.get("base_branch") or "").strip()
    if not base_branch:
        meta = await get_repo(access.token, owner, repo)
        if meta.get("error"):
            return meta, None
        base_branch = str(meta.get("default_branch") or "main")

    existing = await get_file_contents(access.token, owner, repo, path, ref=base_branch)
    old_content = ""
    file_sha: str | None = None
    html_url: str | None = None
    if existing.get("error") and "not found" in str(existing.get("error")).lower():
        # New file on this branch.
        pass
    elif existing.get("error"):
        return existing, None
    elif existing.get("type") == "dir":
        return {"error": f"{path} is a directory; pick a file path."}, None
    elif existing.get("type") == "file":
        if existing.get("content") is None and existing.get("error"):
            return existing, None
        old_content = str(existing.get("content") or "")
        file_sha = str(existing["sha"]) if existing.get("sha") else None
        html_url = existing.get("html_url")
    elif "error" in existing:
        return existing, None

    if old_content == new_content:
        return {
            "error": "new_content is identical to the current file; nothing to propose."
        }, None

    branch_name = str(arguments.get("branch_name") or "").strip() or _slug_branch(pr_title)
    commit_message = str(arguments.get("commit_message") or "").strip() or pr_title
    pr_body = str(arguments.get("pr_body") or "").strip()
    if not pr_body:
        pr_body = (
            f"Proposed change to `{path}` in `{owner}/{repo}`.\n\n"
            "Review the diff in MindLoom before merging."
        )

    proposal = ProposedPullRequest(
        owner=owner,
        repo=repo,
        path=path,
        base_branch=base_branch,
        branch_name=branch_name,
        old_content=old_content,
        new_content=new_content,
        file_sha=file_sha,
        pr_title=pr_title,
        pr_body=pr_body,
        commit_message=commit_message,
        html_url=html_url,
    )
    return {
        "status": "proposed",
        "note": "Draft PR ready. User must approve the diff in the UI before anything is pushed.",
        "owner": owner,
        "repo": repo,
        "path": path,
        "base_branch": base_branch,
        "branch_name": branch_name,
        "pr_title": pr_title,
        "creating_new_file": file_sha is None,
    }, proposal


async def _run_tool(
    *,
    name: str,
    arguments: dict[str, Any],
    org_id: str,
    user_id: str,
    people_cache: dict[str, dict],
    google_connected: bool = False,
) -> tuple[
    Any,
    ProposedExpertMessage | None,
    ProposedPullRequest | None,
    ProposedWorkspace | None,
    ProposedEmail | None,
]:
    if name == "lookup_person":
        query = str(arguments.get("query") or "").strip()
        people = await lookup_messageable_people(
            org_id, query, exclude_user_id=user_id
        )
        for person in people:
            _store_person(people_cache, person)
        return {
            "matches": [
                {
                    "user_id": p.get("user_id") or "",
                    "name": p["name"],
                    "email": p["email"],
                    "title": p.get("title"),
                    "department": p.get("department"),
                }
                for p in people
            ]
        }, None, None, None, None

    if name == "propose_expert_message":
        recipient_user_id = str(arguments.get("recipient_user_id") or "").strip()
        message = str(arguments.get("message") or "").strip()
        if not recipient_user_id or not message:
            return (
                {"error": "recipient_user_id and message are required."},
                None, None, None, None,
            )
        person = people_cache.get(recipient_user_id)
        if person is None:
            from core.database import get_session_factory
            from sqlalchemy import text

            factory = get_session_factory()
            async with factory() as session:
                row = (
                    await session.execute(text("""
                        SELECT user_id, coalesce(name, email) AS name, email
                        FROM users
                        WHERE org_id=:org AND user_id=:user AND user_id<>:exclude
                    """), {
                        "org": org_id,
                        "user": recipient_user_id,
                        "exclude": user_id,
                    })
                ).mappings().one_or_none()
            person = dict(row) if row else None
            if person is not None:
                _store_person(people_cache, person)
        if person is None:
            return {
                "error": "Recipient is not a signed-in org member. Look them up again."
            }, None, None, None, None
        proposal = ProposedExpertMessage(
            recipient_user_id=str(person["user_id"]),
            recipient_name=str(person["name"]),
            recipient_email=str(person["email"]),
            message=message,
            candidates=_candidates_from_cache(people_cache) or [_as_messageable(person)],
        )
        email = _email_from_person(
            person,
            question=message,
            google_connected=google_connected,
            candidates=proposal.candidates,
            subject=_draft_subject_from_question(message),
            body=message,
        )
        return {
            "status": "proposed",
            "note": "Draft ready. User must approve in the UI before send.",
            "recipient_name": proposal.recipient_name,
            "recipient_email": proposal.recipient_email,
            "message": proposal.message,
        }, proposal, None, None, email

    if name == "propose_email":
        recipient_email = str(arguments.get("recipient_email") or "").strip()
        recipient_name = str(arguments.get("recipient_name") or "").strip()
        recipient_user_id = str(arguments.get("recipient_user_id") or "").strip()
        subject = str(arguments.get("subject") or "").strip()
        body = str(arguments.get("body") or "").strip()
        if not subject or not body:
            return {"error": "subject and body are required."}, None, None, None, None
        person = None
        if recipient_user_id:
            person = people_cache.get(recipient_user_id)
        if person is None and recipient_email:
            person = people_cache.get(f"email:{recipient_email.lower()}")
        if person is None and (recipient_email or recipient_name):
            person = {
                "user_id": recipient_user_id,
                "name": recipient_name or recipient_email,
                "email": recipient_email,
            }
        candidates = _candidates_from_cache(people_cache)
        email = _email_from_person(
            person,
            question=body,
            google_connected=google_connected,
            candidates=candidates,
            subject=subject,
            body=body,
        )
        expert = None
        uid = str((person or {}).get("user_id") or "").strip()
        if uid:
            expert = ProposedExpertMessage(
                recipient_user_id=uid,
                recipient_name=str((person or {}).get("name") or email.recipient_name),
                recipient_email=str((person or {}).get("email") or email.recipient_email),
                message=body,
                candidates=candidates or email.candidates,
            )
        return {
            "status": "proposed",
            "note": "Email draft ready. User must approve in the UI before send.",
            "recipient_email": email.recipient_email,
            "subject": email.subject,
        }, expert, None, None, email

    if name == "github_list_repos":
        from weaver.github_access import (
            CAP_METADATA,
            filter_listed_repos,
            load_github_access,
            _not_connected_error,
        )
        from shuttle.github_client import list_repos

        access = await load_github_access(org_id, user_id)
        if access is None:
            return _not_connected_error(), None, None, None, None
        denied = access.deny(CAP_METADATA)
        if denied:
            return {"error": denied}, None, None, None, None
        owner = arguments.get("owner")
        per_page = arguments.get("per_page", 30)
        try:
            per_page_int = int(per_page) if per_page is not None else 30
        except (TypeError, ValueError):
            per_page_int = 30
        listed = await list_repos(
            token=access.token,
            owner=str(owner).strip() if owner else None,
            per_page=per_page_int,
        )
        if listed.get("error"):
            return listed, None, None, None, None
        repos = filter_listed_repos(listed.get("repositories") or [], access.policy)
        return {
            "count": len(repos),
            "repositories": repos,
            "granted_repositories": list(access.policy.repositories),
            "granted_capabilities": list(access.policy.capabilities),
        }, None, None, None, None

    if name == "github_get_repo":
        from weaver.github_access import CAP_METADATA, load_github_access, _not_connected_error
        from shuttle.github_client import get_repo

        access = await load_github_access(org_id, user_id)
        if access is None:
            return _not_connected_error(), None, None, None, None
        owner, repo = _split_owner_repo(arguments)
        denied = access.deny(CAP_METADATA, owner, repo)
        if denied:
            return {"error": denied}, None, None, None, None
        return await get_repo(access.token, owner, repo), None, None, None, None

    if name == "github_get_file":
        from weaver.github_access import CAP_CONTENTS, load_github_access, _not_connected_error
        from shuttle.github_client import get_file_contents

        access = await load_github_access(org_id, user_id)
        if access is None:
            return _not_connected_error(), None, None, None, None
        owner, repo = _split_owner_repo(arguments)
        denied = access.deny(CAP_CONTENTS, owner, repo)
        if denied:
            return {"error": denied}, None, None, None, None
        path = str(arguments.get("path") or "").strip()
        ref = arguments.get("ref")
        return await get_file_contents(
            access.token,
            owner,
            repo,
            path,
            ref=str(ref).strip() if ref else None,
        ), None, None, None, None

    if name == "propose_github_pr":
        result, pr = await _propose_github_pr(
            arguments, org_id=org_id, user_id=user_id
        )
        return result, None, pr, None, None

    if name == "propose_workspace":
        from loombot.workspace_context import propose_workspace_draft

        member_queries = arguments.get("member_queries") or []
        if not isinstance(member_queries, list):
            member_queries = [str(member_queries)]
        result = await propose_workspace_draft(
            org_id=org_id,
            user_id=user_id,
            name=str(arguments.get("name") or ""),
            purpose=str(arguments.get("purpose") or ""),
            member_queries=[str(q) for q in member_queries],
        )
        if result.get("error") or "draft" not in result:
            return result, None, None, None, None
        draft = result["draft"]
        workspace = ProposedWorkspace(
            name=str(draft["name"]),
            purpose=str(draft["purpose"]),
            context_md=str(draft["context_md"]),
            loombot_mode="context_only",
            members=[
                ProposedWorkspaceMember(
                    user_id=str(m["user_id"]),
                    name=str(m["name"]),
                    email=str(m["email"]),
                    reason=str(m.get("reason") or ""),
                )
                for m in draft.get("members") or []
            ],
            unmatched_people=[
                ProposedWorkspaceUnmatched(
                    name=str(p.get("name") or "Unknown"),
                    email=p.get("email"),
                    reason=str(p.get("reason") or ""),
                )
                for p in draft.get("unmatched_people") or []
            ],
        )
        return result, None, None, workspace, None

    return {"error": f"Unknown tool: {name}"}, None, None, None, None


async def run_ask_agent(
    *,
    question: str,
    retrieval: RetrievalResult,
    history: list[ChatMessage] | None,
    org_id: str,
    user_id: str,
    ephemeral_documents: list[EphemeralDocument] | None = None,
) -> QueryResponse:
    """Answer via RAG, using tools for messaging, GitHub, or workspaces when needed."""

    wants_msg = _wants_messaging(question)
    wants_gh = _wants_github(question)
    wants_ws = _wants_workspace(question)
    if not wants_msg and not wants_gh and not wants_ws:
        return await generate_answer(
            question, retrieval, history, org_id, ephemeral_documents
        )

    # Build a knowledge answer path first so tool turns still get sources
    # when the model mixes intents; tools may replace the final answer.
    base = await generate_answer(
        question, retrieval, history, org_id, ephemeral_documents
    )

    from loombot.answerer import _build_context, _attach_citations

    ephemeral = ephemeral_documents or []
    if retrieval.chunks:
        await _attach_citations(retrieval.chunks, org_id)
    context_string = _build_context(retrieval, ephemeral) if (
        retrieval.chunks or ephemeral
    ) else "(No knowledge-graph context matched this turn.)"

    from weaver.integrations import has_google_workspace_connection

    google_connected = await has_google_workspace_connection(org_id, user_id)
    gmail_status = (
        "available for this user"
        if google_connected
        else "not connected for this user — still draft the email so they can connect or copy it"
    )

    client = await get_llm_client(org_id)
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": _AGENT_SYSTEM.format(
                context_string=context_string,
                gmail_status=gmail_status,
            ),
        },
        *_history_messages(history or []),
        {"role": "user", "content": question},
    ]

    people_cache: dict[str, dict] = {}
    proposal: ProposedExpertMessage | None = None
    email_proposal: ProposedEmail | None = None
    pr_proposal: ProposedPullRequest | None = None
    ws_proposal: ProposedWorkspace | None = None
    used_github = False

    if not client.configuration.supports_tools:
        if wants_msg:
            proposal, email_proposal = await _ensure_proposals(
                question=question, org_id=org_id, user_id=user_id,
                people_cache=people_cache, proposal=None, email_proposal=None,
                google_connected=google_connected,
            )
            return QueryResponse(
                answer=_messaging_answer() or base.answer, sources=[], expert=None,
                confidence="high" if proposal or email_proposal else base.confidence,
                routed=False, routed_reason=None, proposed_message=proposal,
                proposed_email=email_proposal,
            )
        # Knowledge answers already use explicit retrieval then prompting. Actions
        # that require structured arguments fail safely instead of being guessed.
        if wants_gh or wants_ws:
            base.answer += (
                "\n\nThe active local model is configured without structured tool "
                "calling, so I did not perform the requested action."
            )
        return base

    def _messaging_answer() -> str:
        if email_proposal is not None:
            target = email_proposal.recipient_email or email_proposal.recipient_name
            if target:
                return (
                    f"I prepared a draft for {target}. "
                    "Review the address and message, then send as an Expert Message and/or email."
                )
            return (
                "I prepared a draft. Enter or correct the email address, "
                "then send as an Expert Message and/or email."
            )
        if proposal is not None:
            return (
                f"I prepared a message to {proposal.recipient_name} "
                f"({proposal.recipient_email}). Approve it in the card below to send."
            )
        return ""

    for round_idx in range(_MAX_TOOL_ROUNDS):
        create_kwargs: dict[str, Any] = {
            "model": _MODEL,
            "messages": messages,
            "tools": _TOOLS,
            "temperature": 0,
        }
        unique_people = {
            _person_cache_key(person)
            for person in people_cache.values()
            if _person_cache_key(person)
        }
        # Force the first turn to use tools so the model cannot skip them.
        if (
            round_idx == 0
            and proposal is None
            and email_proposal is None
            and pr_proposal is None
            and ws_proposal is None
        ):
            create_kwargs["tool_choice"] = "required"
        elif (
            wants_msg
            and email_proposal is None
            and proposal is None
            and len(unique_people) == 1
        ):
            person = next(
                person
                for person in people_cache.values()
                if _person_cache_key(person) in unique_people
            )
            tool_name = (
                "propose_expert_message"
                if str(person.get("user_id") or "").strip()
                else "propose_email"
            )
            create_kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": tool_name},
            }

        response = await client.chat.completions.create(**create_kwargs)
        choice = response.choices[0].message
        tool_calls = choice.tool_calls or []
        if not tool_calls:
            if wants_msg:
                proposal, email_proposal = await _ensure_proposals(
                    question=question,
                    org_id=org_id,
                    user_id=user_id,
                    people_cache=people_cache,
                    proposal=proposal,
                    email_proposal=email_proposal,
                    google_connected=google_connected,
                )
            answer = _messaging_answer()
            if not answer and pr_proposal is not None:
                answer = (
                    f"I prepared a pull request draft for `{pr_proposal.owner}/"
                    f"{pr_proposal.repo}` — `{pr_proposal.path}`. "
                    "Review the diff and approve to open the PR."
                )
            elif not answer and ws_proposal is not None:
                answer = (
                    f"I prepared a workspace draft for **{ws_proposal.name}**. "
                    "Review the members and CONTEXT.md, then approve to create it."
                )
            elif not answer:
                answer = (choice.content or "").strip() or base.answer
            return QueryResponse(
                answer=answer,
                sources=base.sources if proposal is None and email_proposal is None else [],
                expert=None,
                expert_request_created=False,
                confidence=(
                    "high"
                    if proposal or email_proposal or pr_proposal or ws_proposal or used_github
                    else base.confidence
                ),
                routed=False,
                routed_reason=None,
                proposed_message=proposal,
                proposed_email=email_proposal,
                proposed_pull_request=pr_proposal,
                proposed_workspace=ws_proposal,
            )

        messages.append(
            {
                "role": "assistant",
                "content": choice.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments or "{}",
                        },
                    }
                    for call in tool_calls
                ],
            }
        )
        for call in tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if call.function.name.startswith("github_") or call.function.name == (
                "propose_github_pr"
            ):
                used_github = True
            result, maybe_proposal, maybe_pr, maybe_ws, maybe_email = await _run_tool(
                name=call.function.name,
                arguments=args if isinstance(args, dict) else {},
                org_id=org_id,
                user_id=user_id,
                people_cache=people_cache,
                google_connected=google_connected,
            )
            if maybe_proposal is not None:
                proposal = maybe_proposal
            if maybe_pr is not None:
                pr_proposal = maybe_pr
            if maybe_ws is not None:
                ws_proposal = maybe_ws
            if maybe_email is not None:
                email_proposal = maybe_email
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result),
                }
            )
        if (proposal is not None or email_proposal is not None) and not wants_gh and not wants_ws:
            break
        if pr_proposal is not None and not wants_msg and not wants_ws:
            break
        if ws_proposal is not None and not wants_msg and not wants_gh:
            break

    if wants_msg:
        proposal, email_proposal = await _ensure_proposals(
            question=question,
            org_id=org_id,
            user_id=user_id,
            people_cache=people_cache,
            proposal=proposal,
            email_proposal=email_proposal,
            google_connected=google_connected,
        )
    messaging_answer = _messaging_answer()
    if messaging_answer:
        return QueryResponse(
            answer=messaging_answer,
            sources=[],
            expert=None,
            expert_request_created=False,
            confidence="high",
            routed=False,
            routed_reason=None,
            proposed_message=proposal,
            proposed_email=email_proposal,
            proposed_pull_request=pr_proposal,
            proposed_workspace=ws_proposal,
        )

    if pr_proposal is not None:
        answer = (
            f"I prepared a pull request draft for `{pr_proposal.owner}/"
            f"{pr_proposal.repo}` — `{pr_proposal.path}`. "
            "Review the diff and approve to open the PR."
        )
        return QueryResponse(
            answer=answer,
            sources=base.sources,
            expert=None,
            expert_request_created=False,
            confidence="high",
            routed=False,
            routed_reason=None,
            proposed_message=None,
            proposed_pull_request=pr_proposal,
            proposed_workspace=ws_proposal,
        )

    if ws_proposal is not None:
        answer = (
            f"I prepared a workspace draft for **{ws_proposal.name}**. "
            "Review the members and CONTEXT.md, then approve to create it."
        )
        return QueryResponse(
            answer=answer,
            sources=base.sources,
            expert=None,
            expert_request_created=False,
            confidence="high",
            routed=False,
            routed_reason=None,
            proposed_message=None,
            proposed_pull_request=None,
            proposed_workspace=ws_proposal,
        )

    if used_github or wants_gh:
        # Ask the model for a final prose answer from tool results.
        final = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Respond to the user now using the GitHub tool results. "
                        "Do not call more tools. If a PR draft was proposed, remind "
                        "them to approve the diff in the UI."
                    ),
                },
            ],
            temperature=0,
        )
        answer = (final.choices[0].message.content or "").strip() or base.answer
        return QueryResponse(
            answer=answer,
            sources=base.sources,
            expert=None,
            expert_request_created=False,
            confidence="high",
            routed=False,
            routed_reason=None,
            proposed_message=None,
            proposed_pull_request=None,
            proposed_workspace=None,
        )

    answer = (
        "I couldn't find a single matching signed-in teammate to message. "
        "Try a name, email, or exact title."
    )
    return QueryResponse(
        answer=answer,
        sources=base.sources,
        expert=None,
        expert_request_created=False,
        confidence="medium",
        routed=False,
        routed_reason=None,
        proposed_message=None,
        proposed_pull_request=None,
        proposed_workspace=None,
    )
