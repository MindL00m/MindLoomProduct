"""Generate and refresh workspace CONTEXT.md from company knowledge."""

from __future__ import annotations

import logging
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import text

from config import get_settings
from database import get_session_factory
from models import ExpertResult
from review_workflows import lookup_messageable_people

logger = logging.getLogger(__name__)

_CONTEXT_MODEL = "gpt-4o-mini"
_MAX_CONTEXT_CHARS = 8000
_MAX_CHUNKS = 8

_SYNTH_SYSTEM = """\
You write a concise CONTEXT.md for a Loom project workspace.
Use ONLY the retrieval snippets and people list provided.
Do not invent facts. If something is unknown, say so briefly.

Output markdown with these sections when possible:
# Purpose
# People
# Key facts
# Decisions
# Open questions
# Sources

Keep the whole document under ~7000 characters. Prefer short bullets.
Under Sources, list chunk_id values that support the facts.
"""


async def resolve_members_from_experts(
    org_id: str,
    experts: list[ExpertResult] | list[dict[str, Any]],
    *,
    exclude_user_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map expert hits to signed-in users; return (members, unmatched)."""

    members: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    seen_user_ids: set[str] = set()

    for expert in experts:
        if isinstance(expert, ExpertResult):
            name = expert.name
            email = (expert.email or "").strip().lower()
            reason = expert.reason
        else:
            name = str(expert.get("name") or "").strip()
            email = str(expert.get("email") or "").strip().lower()
            reason = str(expert.get("reason") or "")
        if not name and not email:
            continue

        match: dict[str, Any] | None = None
        if email:
            factory = get_session_factory()
            async with factory() as session:
                params: dict[str, Any] = {"org": org_id, "email": email}
                exclude_clause = ""
                if exclude_user_id:
                    exclude_clause = "AND user_id <> :exclude"
                    params["exclude"] = exclude_user_id
                row = (
                    await session.execute(text(f"""
                        SELECT user_id, coalesce(name, email) AS name, email
                        FROM users
                        WHERE org_id=:org AND lower(email)=lower(:email)
                          {exclude_clause}
                        LIMIT 1
                    """), params)
                ).mappings().one_or_none()
            if row is not None:
                match = dict(row)

        if match is None and name:
            hits = await lookup_messageable_people(
                org_id, name, exclude_user_id=exclude_user_id, limit=3
            )
            if len(hits) == 1:
                match = hits[0]
            elif email:
                for hit in hits:
                    if str(hit.get("email") or "").lower() == email:
                        match = hit
                        break

        if match is None:
            unmatched.append({"name": name, "email": email or None, "reason": reason})
            continue

        user_id = str(match["user_id"])
        if user_id in seen_user_ids:
            continue
        if exclude_user_id and user_id == exclude_user_id:
            continue
        seen_user_ids.add(user_id)
        members.append({
            "user_id": user_id,
            "name": str(match["name"]),
            "email": str(match["email"]),
            "reason": reason,
        })

    return members, unmatched


async def resolve_members_from_queries(
    org_id: str,
    queries: list[str],
    *,
    exclude_user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve optional name/email/title queries to signed-in users."""

    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in queries:
        query = " ".join(str(raw or "").split())
        if not query:
            continue
        hits = await lookup_messageable_people(
            org_id, query, exclude_user_id=exclude_user_id, limit=3
        )
        if len(hits) != 1:
            continue
        person = hits[0]
        user_id = str(person["user_id"])
        if user_id in seen:
            continue
        seen.add(user_id)
        members.append({
            "user_id": user_id,
            "name": str(person["name"]),
            "email": str(person["email"]),
            "reason": f"Matched query '{query}'.",
        })
    return members


async def generate_workspace_context(
    *,
    org_id: str,
    user_id: str,
    purpose: str,
    member_labels: list[str] | None = None,
) -> str:
    """Scrape org knowledge for ``purpose`` and synthesize CONTEXT.md."""

    from auth import get_user_access_tokens
    from retrieval import retrieve

    cleaned_purpose = " ".join(purpose.strip().split())
    if not cleaned_purpose:
        return "# Purpose\n\n(No purpose provided.)\n"

    access_tokens = await get_user_access_tokens(org_id, user_id)
    retrieval = await retrieve(cleaned_purpose, [], org_id, access_tokens)

    chunk_blocks: list[str] = []
    for chunk in retrieval.chunks[:_MAX_CHUNKS]:
        text_body = (chunk.summary or chunk.raw_text or "").strip()
        if len(text_body) > 900:
            text_body = text_body[:900] + "…"
        chunk_blocks.append(
            f"- chunk_id={chunk.chunk_id}\n  {text_body}"
        )

    expert_lines = [
        f"- {e.name}"
        + (f" <{e.email}>" if e.email else "")
        + (f" — {e.reason}" if e.reason else "")
        for e in retrieval.experts[:12]
    ]
    member_block = "\n".join(f"- {label}" for label in (member_labels or []) if label)
    entities = ", ".join(retrieval.entities_found) if retrieval.entities_found else "(none)"

    user_prompt = (
        f"Workspace purpose: {cleaned_purpose}\n"
        f"Entities found: {entities}\n\n"
        f"Signed-in members to include:\n{member_block or '(none resolved yet)'}\n\n"
        f"Experts from the knowledge graph:\n"
        f"{chr(10).join(expert_lines) or '(none)'}\n\n"
        f"Retrieved snippets:\n"
        f"{chr(10).join(chunk_blocks) or '(no snippets)'}\n"
    )

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    try:
        response = await client.chat.completions.create(
            model=_CONTEXT_MODEL,
            temperature=0.2,
            messages=[
                {"role": "system", "content": _SYNTH_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("CONTEXT.md synthesis failed for purpose=%s", cleaned_purpose)
        content = ""

    if not content:
        content = _fallback_context(
            purpose=cleaned_purpose,
            member_labels=member_labels or [],
            expert_lines=expert_lines,
            chunk_blocks=chunk_blocks,
        )

    if len(content) > _MAX_CONTEXT_CHARS:
        content = content[: _MAX_CONTEXT_CHARS - 20].rstrip() + "\n\n…"
    return content


def _fallback_context(
    *,
    purpose: str,
    member_labels: list[str],
    expert_lines: list[str],
    chunk_blocks: list[str],
) -> str:
    people = member_labels or [line.lstrip("- ").split(" —")[0] for line in expert_lines]
    facts = chunk_blocks[:5] or ["- (No retrieved snippets.)"]
    return (
        f"# Purpose\n\n{purpose}\n\n"
        f"# People\n\n"
        + ("\n".join(f"- {p}" for p in people) or "- (None identified)")
        + "\n\n# Key facts\n\n"
        + "\n".join(facts)
        + "\n"
    )


async def propose_workspace_draft(
    *,
    org_id: str,
    user_id: str,
    name: str,
    purpose: str,
    member_queries: list[str] | None = None,
) -> dict[str, Any]:
    """Build a propose-only workspace draft with members and CONTEXT.md."""

    from auth import get_user_access_tokens
    from retrieval import retrieve

    cleaned_name = " ".join(name.strip().split())
    cleaned_purpose = " ".join(purpose.strip().split()) or cleaned_name
    if not cleaned_name:
        return {"error": "name is required."}

    access_tokens = await get_user_access_tokens(org_id, user_id)
    retrieval = await retrieve(cleaned_purpose, [], org_id, access_tokens)

    members, unmatched = await resolve_members_from_experts(
        org_id, retrieval.experts, exclude_user_id=user_id
    )
    query_members = await resolve_members_from_queries(
        org_id, member_queries or [], exclude_user_id=user_id
    )
    seen = {m["user_id"] for m in members}
    for person in query_members:
        if person["user_id"] not in seen:
            members.append(person)
            seen.add(person["user_id"])

    member_labels = [
        f"{m['name']} <{m['email']}>" + (f" — {m['reason']}" if m.get("reason") else "")
        for m in members
    ]
    for person in unmatched:
        label = person["name"] or person.get("email") or "Unknown"
        if person.get("email"):
            label = f"{label} <{person['email']}>"
        member_labels.append(f"{label} (not a signed-in Loom user)")

    context_md = await generate_workspace_context(
        org_id=org_id,
        user_id=user_id,
        purpose=cleaned_purpose,
        member_labels=member_labels,
    )

    return {
        "status": "proposed",
        "note": (
            "Draft workspace ready. User must approve in the UI before creation. "
            "Loombot in this workspace will answer only from CONTEXT.md."
        ),
        "name": cleaned_name,
        "purpose": cleaned_purpose,
        "member_count": len(members),
        "unmatched_count": len(unmatched),
        "draft": {
            "name": cleaned_name,
            "purpose": cleaned_purpose,
            "context_md": context_md,
            "loombot_mode": "context_only",
            "members": members,
            "unmatched_people": unmatched,
        },
    }
