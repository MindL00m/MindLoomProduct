"""Ask agent with RAG plus messaging, GitHub, workspace, and data lookup tools."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from loombot import data_tools
from loombot import files as files_service
from loombot.answerer import generate_answer
from core.config import get_settings
from core.llm_provider import DEFAULT_OPENAI_MODEL, get_llm_client
from core.models import (
    ChatMessage,
    EphemeralDocument,
    FileChange,
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

_MODEL = DEFAULT_OPENAI_MODEL  # informational; the adapter uses the org setting
_MAX_TOOL_ROUNDS = 5

_AGENT_SYSTEM = """\
You are MindLoom, a company knowledge assistant that can draft Expert Messages,
draft emails, work with GitHub repositories when a token is configured,
propose project workspaces with a CONTEXT.md file for Loombot, count mail in the
user's connected mailboxes, report project status from the Status board and
knowledge graph, and create, read, and edit the user's CSV files in the LoomBot
Files tab.

Today is {today} (UTC). Resolve relative dates such as "today", "this week", or
"last month" against this date.

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
- If a GitHub tool says GitHub is not connected or a repository/permission was not
  granted, tell the user to open Import, paste a fine-grained personal access token,
  and choose exactly which repositories and actions MindLoom may use. This applies
  to GitHub only.

Workspace rules:
- When the user asks to create, make, set up, or spin up a workspace for a project
  or topic, you MUST call propose_workspace.
- Use a clear workspace name and a purpose that captures the project/topic.
- When the user names people ("with Amey", "for Priya and Alex"), pass each one
  as its own entry in member_queries. The tool also adds knowledge-graph experts
  for the purpose.
- If the draft lists unmatched_people, tell the user who could not be added and why.
- propose_workspace does NOT create the workspace. The UI shows members + CONTEXT.md
  for approval. Your final text should only say a draft workspace is ready.
- Never claim a workspace was created.

Data rules:
- "How many emails / unread / what's in my inbox" questions: call count_emails.
  Pass start_date and end_date as inclusive YYYY-MM-DD dates when the user names a
  period; omit both for all time. Use folder "all_received" only when the user asks
  about all mail rather than the inbox.
- Report each mailbox's count with its account and the period used. If exact is
  false, say "at least". If a mailbox is unavailable, say why and how to connect it.
  Never estimate a count from the Context excerpts; they are only a sample.
- Status, progress, blockers, open issues, or action items for a project: call
  project_status with the project name without the word "project" (for "project
  tiramisu" pass "tiramisu"). With no project named, call it with no arguments for
  an overview of open work.
- Summarize project status as: current state (work_status and current_status), the
  latest updates with dates, open issues or blockers, and action items with
  assignees. Cite evidence with [SOURCE: chunk_id] using the tool's chunk_id values.
  If found is false, say the project is not on the Status board and answer from
  Context if it has anything relevant.
- Counts of other indexed items (Drive files, meetings, Teams chats, WhatsApp
  chats, uploads, skill files, or "what do you have from X"): call
  knowledge_inventory and say the number covers what MindLoom has indexed.
- If a tool returns an error, explain it plainly. Never invent numbers.

Files rules (CSV files in the user's LoomBot Files tab, not GitHub):
- The user's files: {files_status}
- To view or change a file, call read_csv_file first, then write_csv_file with
  the COMPLETE new CSV (header row first). Keep every row and column the user
  did not ask to change exactly as it was.
- To create a file, call write_csv_file with a new name. Use a short name ending
  in .csv and a header row.
- write_csv_file saves immediately; the user can undo it from the chat or the
  Files tab. Say what changed in one or two sentences. Do not paste the whole
  CSV back unless the user asks to see it; show a small markdown table for
  short previews instead.
- You cannot delete files. Tell the user to delete them in the Files tab.
- "File" questions about a repository, PR, or code are GitHub requests, not Files.

- For ordinary knowledge questions that are not messaging, GitHub, workspace
  creation, files, or data lookups, answer from context without tools.

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
    {
        "type": "function",
        "function": {
            "name": "count_emails",
            "description": (
                "Count messages in the user's own connected Gmail and/or Outlook "
                "mailbox, live from the mail provider, and return the most recent "
                "few with subject, sender, and date."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["auto", "gmail", "outlook"],
                        "description": "Mailbox to read. auto checks every connected one.",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Inclusive first day, YYYY-MM-DD. Omit for all time.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Inclusive last day, YYYY-MM-DD. Omit for up to now.",
                    },
                    "folder": {
                        "type": "string",
                        "enum": ["inbox", "all_received"],
                        "description": (
                            "inbox (default) or all_received (Gmail only: every "
                            "received message, including archived)."
                        ),
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Count only unread messages.",
                    },
                    "search": {
                        "type": "string",
                        "description": (
                            "Optional Gmail search terms, e.g. 'from:priya@acme.com' "
                            "or 'subject:invoice'. Ignored for Outlook."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_inventory",
            "description": (
                "Count documents MindLoom has indexed that the user may see, grouped "
                "by source (gmail, outlook_email, google_drive, google_calendar, "
                "microsoft_teams, sharepoint, zoom, whatsapp_export, slack, skill_file, pdf, "
                "...), with the most recent titles."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": (
                            "Optional source filter such as gmail, email, drive, "
                            "calendar, teams, zoom, whatsapp, skill_file, or pdf. "
                            "Omit for every source."
                        ),
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Inclusive first day, YYYY-MM-DD.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Inclusive last day, YYYY-MM-DD.",
                    },
                    "title_contains": {
                        "type": "string",
                        "description": "Optional case-insensitive title filter.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_status",
            "description": (
                "Current status of a project from the Status board (open work, "
                "issues, blockers, action items) and the knowledge graph (latest "
                "mentions, including closed projects). Omit project for an overview "
                "of all open work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name, e.g. 'tiramisu'.",
                    },
                },
            },
        },
    },
]

_FILE_TOOLS_SPEC: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_csv_files",
            "description": (
                "List the user's CSV files in the LoomBot Files tab with their "
                "row and column counts."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_csv_file",
            "description": "Read the full contents of one of the user's CSV files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "File name, e.g. 'budget.csv'.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_csv_file",
            "description": (
                "Create a CSV file, or replace an existing file's contents. Saves "
                "immediately and can be undone by the user. Read an existing file "
                "with read_csv_file before replacing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "File name ending in .csv.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The COMPLETE CSV text, header row first.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One short sentence describing the change.",
                    },
                },
                "required": ["name", "content"],
            },
        },
    },
]
_TOOLS.extend(_FILE_TOOLS_SPEC)

_DATA_TOOLS = {"count_emails", "knowledge_inventory", "project_status"}
_FILE_TOOLS = {"list_csv_files", "read_csv_file", "write_csv_file"}


@dataclass
class _FileToolState:
    """Per-turn bookkeeping so Ask only overwrites files it has read."""

    changes: list[FileChange] = field(default_factory=list)
    read_ids: set[str] = field(default_factory=set)


_FILE_TERMS = re.compile(
    r"\b(?:csvs?|spreadsheets?|files tab|my files?|loombot files)\b"
)
_FILE_PARTS = re.compile(
    r"\b(?:file|sheet|table|rows?|columns?|cells?|headers?)\b"
)
_FILE_EDIT_VERBS = re.compile(
    r"\b(?:add|append|insert|remove|delete|drop|rename|change|update|edit|modify|"
    r"set|fill|sort|replace|fix|put|create|make|open|show|read)\b"
)


def _mentions_file(text_lower: str, file_names: list[str]) -> bool:
    """True when the text names one of the user's files (with or without .csv)."""

    for name in file_names:
        lowered = name.lower()
        if lowered in text_lower:
            return True
        stem = lowered[:-4] if lowered.endswith(".csv") else lowered
        if len(stem) >= 3 and re.search(rf"\b{re.escape(stem)}\b", text_lower) and (
            _FILE_PARTS.search(text_lower) or _FILE_EDIT_VERBS.search(text_lower)
        ):
            return True
    return False


def _wants_files(
    question: str,
    file_names: list[str] | None = None,
    history: list[ChatMessage] | None = None,
) -> bool:
    """CSV/Files-tab requests, including follow-ups about a file just discussed."""

    lowered = " ".join(question.lower().split())
    names = file_names or []
    if _FILE_TERMS.search(lowered) or re.search(r"\.csv\b", lowered):
        return True
    if _mentions_file(lowered, names):
        return True
    if history and _FILE_EDIT_VERBS.search(lowered):
        last_answer = next(
            (turn.content.lower() for turn in reversed(history) if turn.role == "assistant"),
            "",
        )
        if any(name.lower() in last_answer for name in names):
            return True
    return False


async def _user_files(org_id: str, user_id: str) -> list[dict[str, Any]]:
    try:
        return await files_service.list_files(org_id, user_id)
    except Exception:  # noqa: BLE001 - Ask must still answer if Files is unavailable
        logger.warning("Could not list LoomBot files for Ask", exc_info=True)
        return []


def _files_status(files: list[dict[str, Any]]) -> str:
    if not files:
        return "none yet."
    shown = files[:25]
    listing = "; ".join(
        f"{f['name']} ({f['row_count']} rows x {f['column_count']} columns)" for f in shown
    )
    if len(files) > len(shown):
        listing += f"; and {len(files) - len(shown)} more (call list_csv_files)"
    return listing + "."


async def _run_file_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    org_id: str,
    user_id: str,
    state: _FileToolState,
) -> dict[str, Any]:
    if name == "list_csv_files":
        files = await files_service.list_files(org_id, user_id)
        return {
            "files": [
                {
                    "name": f["name"],
                    "rows": f["row_count"],
                    "columns": f["column_count"],
                    "updated_at": f["updated_at"],
                }
                for f in files
            ]
        }

    file_name = str(arguments.get("name") or "").strip()
    if not file_name:
        return {"error": "name is required."}

    if name == "read_csv_file":
        found = await files_service.find_file_by_name(org_id, user_id, file_name)
        if found is None:
            return {"error": f"No file named {file_name}. Call list_csv_files to see names."}
        content = found["content"]
        if len(content) > files_service.ASK_MAX_FILE_CHARS:
            return {
                "name": found["name"],
                "rows": found["row_count"],
                "columns": found["column_count"],
                "truncated": True,
                "preview": content[:4000],
                "note": (
                    "This file is too large for Ask to edit safely. You may "
                    "describe it from the preview; edits must be made in the Files tab."
                ),
            }
        state.read_ids.add(found["file_id"])
        return {
            "name": found["name"],
            "rows": found["row_count"],
            "columns": found["column_count"],
            "content": content,
        }

    if name == "write_csv_file":
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            return {"error": "content must be the complete CSV text."}
        summary = str(arguments.get("summary") or "").strip()[:300]
        existing = await files_service.find_file_by_name(org_id, user_id, file_name)
        if existing is None:
            saved = await files_service.create_file(
                org_id, user_id, file_name, content, edited_by="loombot"
            )
            action = "created"
        else:
            if len(existing["content"]) > files_service.ASK_MAX_FILE_CHARS:
                return {
                    "error": (
                        f"{existing['name']} is too large for Ask to rewrite. "
                        "Ask the user to edit it in the Files tab."
                    )
                }
            if existing["file_id"] not in state.read_ids:
                return {
                    "error": (
                        f"Call read_csv_file for {existing['name']} before replacing "
                        "it, so no existing rows are lost."
                    )
                }
            saved = await files_service.update_file(
                org_id, user_id, existing["file_id"], content=content, edited_by="loombot"
            )
            action = "updated"
        state.read_ids.add(saved["file_id"])
        change = FileChange(
            file_id=saved["file_id"],
            name=saved["name"],
            action=action,
            summary=summary,
            row_count=saved["row_count"],
            column_count=saved["column_count"],
        )
        # Several writes to one file in a turn collapse to one card; a file
        # created this turn stays "created".
        prior = next((c for c in state.changes if c.file_id == change.file_id), None)
        if prior is not None:
            change.action = prior.action
            state.changes.remove(prior)
        state.changes.append(change)
        return {
            "saved": True,
            "action": action,
            "name": saved["name"],
            "rows": saved["row_count"],
            "columns": saved["column_count"],
        }

    return {"error": f"Unknown tool: {name}"}


def _history_messages(history: list[ChatMessage]) -> list[dict[str, str]]:
    recent = history[-12:]
    return [{"role": turn.role, "content": turn.content} for turn in recent]


_MESSAGE_VERBS = r"(?:message|tell|notify|ping|ask|dm|text|e-?mail|remind|send)"
# Words after a messaging verb that mean the user is asking a question
# ("tell me about…", "ask whether…", "email policy") rather than messaging someone.
_NOT_A_RECIPIENT = (
    r"(?!(?:me|us|about|if|whether|how|what|why|when|which|who|for|of|from|in|on|is|"
    r"was|are|policy|policies|address|addresses|settings|server|thread|threads|"
    r"template|templates|signature|list|history)\b)"
)
_MESSAGING_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rf"^(?:(?:hey|hi|ok|okay)[,!\s]+)?(?:please\s+|pls\s+|can you\s+|could you\s+|"
        rf"would you\s+|go ahead and\s+)?{_MESSAGE_VERBS}\s+{_NOT_A_RECIPIENT}\S",
        rf"\b(?:you to|please|can you|could you|would you|then|and also)\s+"
        rf"{_MESSAGE_VERBS}\s+{_NOT_A_RECIPIENT}\S",
        r"\bsend\s+(?:a|an|the)?\s*(?:quick\s+|short\s+)?"
        r"(?:message|e-?mail|note|dm|text|reminder)\b",
        r"\b(?:write|draft|compose)\s+(?:a|an|the)?\s*(?:quick\s+|short\s+)?"
        r"(?:message|e-?mail|note|reply)\b",
        r"\blet\s+(?!me\b|us\b)[\w.@'-]+(?:\s+[\w.@'-]+){0,3}\s+know\b",
        r"\breach out to\b",
    )
)


def _wants_messaging(question: str) -> bool:
    lowered = " ".join(question.lower().split())
    return any(pattern.search(lowered) for pattern in _MESSAGING_PATTERNS)


_COUNT_WORDS = re.compile(r"\b(?:how many|number of|count(?: of)?|total)\b")
_COUNTABLE = re.compile(
    r"\b(?:e-?mails?|mails?|messages?|inbox|meetings?|events?|calls?|documents?|"
    r"docs?|files?|chats?|threads?|uploads?|sources?|projects?|issues?|"
    r"action items?|tasks?|skill files?|recordings?)\b"
)
_DATA_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bunread\b",
        r"\b(?:my|the) inbox\b",
        r"\b(?:e-?mails?|mail)\b.{0,40}\b(?:did|have) i\s+(?:get|got|gotten|receive|received)\b",
        r"\b(?:did|have) i\s+(?:get|got|gotten|receive|received)\b.{0,40}\be-?mails?\b",
        r"\bstatus\b",
        r"\bprogress\b",
        r"\bupdates? (?:on|for|about)\b",
        r"\bhow(?:'s| is| are)\b.{0,60}\bgoing\b",
        r"\bwhere (?:are|do) we stand\b",
        r"\bon track\b",
        r"\bblock(?:ed|er|ers|ing)\b",
        r"\bopen (?:issues?|projects?|action items?|items?|work|questions?)\b",
        r"\baction items?\b",
        r"\bwhat(?:'s| is| are)\s+(?:left|outstanding|pending)\b",
    )
)


def _wants_data(question: str) -> bool:
    """Counts of mail or indexed items, or project/work status questions."""

    lowered = " ".join(question.lower().split())
    if _COUNT_WORDS.search(lowered) and _COUNTABLE.search(lowered):
        return True
    return any(pattern.search(lowered) for pattern in _DATA_PATTERNS)


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
    file_state: _FileToolState | None = None,
) -> tuple[
    Any,
    ProposedExpertMessage | None,
    ProposedPullRequest | None,
    ProposedWorkspace | None,
    ProposedEmail | None,
]:
    if name in _FILE_TOOLS:
        return await _run_file_tool(
            name,
            arguments,
            org_id=org_id,
            user_id=user_id,
            state=file_state if file_state is not None else _FileToolState(),
        ), None, None, None, None

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

    if name == "count_emails":
        return await data_tools.count_emails(
            org_id,
            user_id,
            provider=str(arguments.get("provider") or "auto"),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
            folder=str(arguments.get("folder") or "inbox"),
            unread_only=bool(arguments.get("unread_only")),
            search=arguments.get("search"),
        ), None, None, None, None

    if name == "knowledge_inventory":
        return await data_tools.knowledge_inventory(
            org_id,
            user_id,
            source=arguments.get("source"),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
            title_contains=arguments.get("title_contains"),
        ), None, None, None, None

    if name == "project_status":
        return await data_tools.project_status(
            org_id, user_id, project=arguments.get("project")
        ), None, None, None, None

    return {"error": f"Unknown tool: {name}"}, None, None, None, None


async def _safe_run_tool(**kwargs: Any) -> tuple[
    Any,
    ProposedExpertMessage | None,
    ProposedPullRequest | None,
    ProposedWorkspace | None,
    ProposedEmail | None,
]:
    """Run a tool, turning failures into a tool result instead of a 500."""

    try:
        return await _run_tool(**kwargs)
    except HTTPException as exc:
        return {"error": str(exc.detail)}, None, None, None, None
    except Exception as exc:  # noqa: BLE001 - the model explains tool failures
        logger.exception("Ask tool %s failed", kwargs.get("name"))
        return (
            {"error": f"{kwargs.get('name')} failed ({exc.__class__.__name__}). Try again shortly."},
            None, None, None, None,
        )


def _workspace_answer(workspace: ProposedWorkspace) -> str:
    answer = (
        f"I prepared a workspace draft for **{workspace.name}**. "
        "Review the members and CONTEXT.md, then approve to create it."
    )
    if workspace.unmatched_people:
        missing = "; ".join(
            f"{person.name}: {person.reason}" if person.reason else person.name
            for person in workspace.unmatched_people
        )
        answer += f"\n\nI couldn't add everyone. {missing}"
    return answer


_CITED_CHUNK_RE = re.compile(r"\[SOURCE:\s*([^\]\s]+)\s*\]", re.IGNORECASE)


async def _with_cited_sources(
    answer: str, sources: list[Any], org_id: str, user_id: str
) -> list[Any]:
    """Add chunks the answer cites from tool results so citations render."""

    known = {source.chunk_id for source in sources}
    missing = [
        chunk_id
        for chunk_id in dict.fromkeys(_CITED_CHUNK_RE.findall(answer or ""))
        if chunk_id not in known and not chunk_id.startswith(("directory:", "issue:"))
    ]
    if not missing:
        return sources
    try:
        from brain.retrieval import _load_chunks_by_ids
        from keeper.auth import get_user_access_tokens
        from loombot.answerer import _attach_citations

        extra = await _load_chunks_by_ids(
            missing[:12], org_id, await get_user_access_tokens(org_id, user_id)
        )
        await _attach_citations(extra, org_id)
    except Exception:  # noqa: BLE001 - citations are non-critical
        logger.warning("Could not load chunks cited by tool results", exc_info=True)
        return sources
    return [*sources, *extra]


def _only_cited(answer: str, sources: list[Any]) -> list[Any]:
    """Drop retrieved-but-uncited sources from answers about the user's files."""

    cited = set(_CITED_CHUNK_RE.findall(answer or ""))
    return [source for source in sources if source.chunk_id in cited]


async def run_ask_agent(
    *,
    question: str,
    retrieval: RetrievalResult,
    history: list[ChatMessage] | None,
    org_id: str,
    user_id: str,
    ephemeral_documents: list[EphemeralDocument] | None = None,
) -> QueryResponse:
    """Answer via RAG, using tools for messaging, GitHub, workspaces, or data."""

    wants_msg = _wants_messaging(question)
    wants_gh = _wants_github(question)
    wants_ws = _wants_workspace(question)
    wants_data = _wants_data(question)
    user_files = await _user_files(org_id, user_id)
    wants_files = _wants_files(question, [f["name"] for f in user_files], history)
    if not wants_msg and not wants_gh and not wants_ws and not wants_data and not wants_files:
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
                files_status=_files_status(user_files),
                today=datetime.now(timezone.utc).strftime("%A, %d %B %Y"),
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
    used_data = False
    used_files = False
    file_state = _FileToolState()

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
        if wants_gh or wants_ws or wants_data or wants_files:
            base.answer += (
                "\n\nThe active local model is configured without structured tool "
                "calling, so I could not use connectors, Files, or the Status board "
                "for this."
            )
        return base

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
                answer = _workspace_answer(ws_proposal)
            elif not answer:
                answer = (choice.content or "").strip() or base.answer
            sources = (
                await _with_cited_sources(answer, base.sources, org_id, user_id)
                if proposal is None and email_proposal is None
                else []
            )
            if used_files and not (used_github or used_data):
                sources = _only_cited(answer, sources)
            return QueryResponse(
                file_changes=list(file_state.changes),
                answer=answer,
                sources=sources,
                expert=None,
                expert_request_created=False,
                confidence=(
                    "high"
                    if proposal or email_proposal or pr_proposal or ws_proposal
                    or used_github or used_data or used_files
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
            if call.function.name in _DATA_TOOLS:
                used_data = True
            if call.function.name in _FILE_TOOLS:
                used_files = True
            result, maybe_proposal, maybe_pr, maybe_ws, maybe_email = await _safe_run_tool(
                name=call.function.name,
                arguments=args if isinstance(args, dict) else {},
                org_id=org_id,
                user_id=user_id,
                people_cache=people_cache,
                google_connected=google_connected,
                file_state=file_state,
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
                    "content": json.dumps(data_tools.jsonable(result), default=str),
                }
            )
        if (
            (proposal is not None or email_proposal is not None)
            and not wants_gh and not wants_ws and not wants_files
        ):
            break
        if pr_proposal is not None and not wants_msg and not wants_ws and not wants_files:
            break
        if ws_proposal is not None and not wants_msg and not wants_gh and not wants_files:
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
            file_changes=list(file_state.changes),
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
            file_changes=list(file_state.changes),
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
        return QueryResponse(
            file_changes=list(file_state.changes),
            answer=_workspace_answer(ws_proposal),
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

    # Tool rounds ran out (or stopped) with results the model has not answered yet.
    final = await client.chat.completions.create(
        model=_MODEL,
        messages=[
            *messages,
            {
                "role": "user",
                "content": (
                    "Respond to the user now using the tool results above. Do not "
                    "call more tools. If a draft was proposed, remind them to "
                    "approve it in the UI."
                ),
            },
        ],
        tools=_TOOLS,
        tool_choice="none",
        temperature=0,
    )
    answer = (final.choices[0].message.content or "").strip() or base.answer
    return QueryResponse(
        file_changes=list(file_state.changes),
        answer=answer,
        sources=await _with_cited_sources(answer, base.sources, org_id, user_id),
        expert=None,
        expert_request_created=False,
        confidence="high" if used_github or used_data or used_files else base.confidence,
        routed=False,
        routed_reason=None,
        proposed_message=None,
        proposed_pull_request=None,
        proposed_workspace=None,
    )
