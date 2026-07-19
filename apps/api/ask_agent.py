"""Ask agent with RAG plus propose-only Expert Messages tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from answerer import generate_answer
from config import get_settings
from models import (
    ChatMessage,
    EphemeralDocument,
    MessageablePerson,
    ProposedExpertMessage,
    QueryResponse,
    RetrievalResult,
)
from review_workflows import lookup_messageable_people

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"
_MAX_TOOL_ROUNDS = 4

_AGENT_SYSTEM = """\
You are Loom, a company knowledge assistant that can also draft Expert Messages.

You have retrieval context below for factual questions. Use it for knowledge answers
and cite sources with [SOURCE: chunk_id] when applicable.

Messaging rules:
- When the user wants to notify, tell, message, or ask a coworker, you MUST use tools.
- Always call lookup_person first with the role/title/name/email from the request
  (e.g. query "CTO" when they say "send a message to the CTO").
- When lookup_person returns exactly one match, you MUST call propose_expert_message.
- Do NOT write the draft email/message in your reply text. The UI shows an approve card
  from propose_expert_message. Your final text should only say a draft is ready for approval.
- If multiple people match, ask which one — do not guess and do not invent a draft.
- propose_expert_message does NOT send anything. Never say a message was sent.
- Do not message people who are not signed-in Loom users.
- For ordinary knowledge questions, answer from context without messaging tools.

Context:
{context_string}
"""

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_person",
            "description": (
                "Find signed-in org members by name, email, or directory title/role "
                "(e.g. CTO, engineering manager) who can receive Expert Messages."
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
    )
    return any(token in lowered for token in triggers)


def _extract_recipient_query(question: str) -> str | None:
    """Best-effort recipient hint from a messaging ask (title/name)."""

    import re

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
    import re

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


def _proposal_from_person(person: dict, message: str) -> ProposedExpertMessage:
    return ProposedExpertMessage(
        recipient_user_id=str(person["user_id"]),
        recipient_name=str(person["name"]),
        recipient_email=str(person["email"]),
        message=message,
        candidates=[
            MessageablePerson(
                user_id=str(person["user_id"]),
                name=str(person["name"]),
                email=str(person["email"]),
                title=person.get("title"),
                department=person.get("department"),
            )
        ],
    )


async def _ensure_proposal(
    *,
    question: str,
    org_id: str,
    user_id: str,
    people_cache: dict[str, dict],
    proposal: ProposedExpertMessage | None,
) -> ProposedExpertMessage | None:
    """If the model forgot propose_expert_message, build one for a clear recipient."""

    if proposal is not None:
        return proposal

    if len(people_cache) == 1:
        person = next(iter(people_cache.values()))
        return _proposal_from_person(person, _draft_message_from_question(question))

    hint = _extract_recipient_query(question)
    if not hint:
        return None
    people = await lookup_messageable_people(
        org_id, hint, exclude_user_id=user_id, limit=5
    )
    for person in people:
        people_cache[str(person["user_id"])] = person
    if len(people) == 1:
        return _proposal_from_person(people[0], _draft_message_from_question(question))
    return None


async def _run_tool(
    *,
    name: str,
    arguments: dict[str, Any],
    org_id: str,
    user_id: str,
    people_cache: dict[str, dict],
) -> tuple[Any, ProposedExpertMessage | None]:
    if name == "lookup_person":
        query = str(arguments.get("query") or "").strip()
        people = await lookup_messageable_people(
            org_id, query, exclude_user_id=user_id
        )
        for person in people:
            people_cache[str(person["user_id"])] = person
        return {
            "matches": [
                {
                    "user_id": p["user_id"],
                    "name": p["name"],
                    "email": p["email"],
                    "title": p.get("title"),
                    "department": p.get("department"),
                }
                for p in people
            ]
        }, None

    if name == "propose_expert_message":
        recipient_user_id = str(arguments.get("recipient_user_id") or "").strip()
        message = str(arguments.get("message") or "").strip()
        if not recipient_user_id or not message:
            return {"error": "recipient_user_id and message are required."}, None
        person = people_cache.get(recipient_user_id)
        if person is None:
            from database import get_session_factory
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
                people_cache[recipient_user_id] = person
        if person is None:
            return {
                "error": "Recipient is not a signed-in org member. Look them up again."
            }, None
        proposal = ProposedExpertMessage(
            recipient_user_id=str(person["user_id"]),
            recipient_name=str(person["name"]),
            recipient_email=str(person["email"]),
            message=message,
            candidates=[
                MessageablePerson(
                    user_id=str(p["user_id"]),
                    name=str(p["name"]),
                    email=str(p["email"]),
                    title=p.get("title"),
                    department=p.get("department"),
                )
                for p in people_cache.values()
            ],
        )
        return {
            "status": "proposed",
            "note": "Draft ready. User must approve in the UI before send.",
            "recipient_name": proposal.recipient_name,
            "recipient_email": proposal.recipient_email,
            "message": proposal.message,
        }, proposal

    return {"error": f"Unknown tool: {name}"}, None


async def run_ask_agent(
    *,
    question: str,
    retrieval: RetrievalResult,
    history: list[ChatMessage] | None,
    org_id: str,
    user_id: str,
    ephemeral_documents: list[EphemeralDocument] | None = None,
) -> QueryResponse:
    """Answer via RAG, using messaging tools when the user wants to notify someone."""

    if not _wants_messaging(question):
        return await generate_answer(
            question, retrieval, history, org_id, ephemeral_documents
        )

    # Build a knowledge answer path first so messaging turns still get sources
    # when the model mixes both intents; tools may replace the final answer.
    base = await generate_answer(
        question, retrieval, history, org_id, ephemeral_documents
    )

    from answerer import _build_context, _attach_citations

    ephemeral = ephemeral_documents or []
    if retrieval.chunks:
        await _attach_citations(retrieval.chunks, org_id)
    context_string = _build_context(retrieval, ephemeral) if (
        retrieval.chunks or ephemeral
    ) else "(No knowledge-graph context matched this turn.)"

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": _AGENT_SYSTEM.format(context_string=context_string),
        },
        *_history_messages(history or []),
        {"role": "user", "content": question},
    ]

    people_cache: dict[str, dict] = {}
    proposal: ProposedExpertMessage | None = None

    for round_idx in range(_MAX_TOOL_ROUNDS):
        create_kwargs: dict[str, Any] = {
            "model": _MODEL,
            "messages": messages,
            "tools": _TOOLS,
            "temperature": 0,
        }
        # Force the first turn to use tools so the model cannot skip to a prose draft.
        if round_idx == 0 and proposal is None:
            create_kwargs["tool_choice"] = "required"
        elif proposal is None and people_cache and len(people_cache) == 1:
            create_kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": "propose_expert_message"},
            }

        response = await client.chat.completions.create(**create_kwargs)
        choice = response.choices[0].message
        tool_calls = choice.tool_calls or []
        if not tool_calls:
            proposal = await _ensure_proposal(
                question=question,
                org_id=org_id,
                user_id=user_id,
                people_cache=people_cache,
                proposal=proposal,
            )
            if proposal is not None:
                answer = (
                    f"I prepared a message to {proposal.recipient_name} "
                    f"({proposal.recipient_email}). Approve it in the card below to send."
                )
            else:
                answer = (choice.content or "").strip() or base.answer
            return QueryResponse(
                answer=answer,
                sources=base.sources if proposal is None else [],
                expert=None,
                expert_request_created=False,
                confidence="high" if proposal else base.confidence,
                routed=False,
                routed_reason=None,
                proposed_message=proposal,
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
            result, maybe_proposal = await _run_tool(
                name=call.function.name,
                arguments=args if isinstance(args, dict) else {},
                org_id=org_id,
                user_id=user_id,
                people_cache=people_cache,
            )
            if maybe_proposal is not None:
                proposal = maybe_proposal
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result),
                }
            )
        if proposal is not None:
            break

    proposal = await _ensure_proposal(
        question=question,
        org_id=org_id,
        user_id=user_id,
        people_cache=people_cache,
        proposal=proposal,
    )
    if proposal is not None:
        answer = (
            f"I prepared a message to {proposal.recipient_name} "
            f"({proposal.recipient_email}). Approve it in the card below to send."
        )
    else:
        answer = (
            "I couldn't find a single matching signed-in teammate to message. "
            "Try a name, email, or exact title."
        )
    return QueryResponse(
        answer=answer,
        sources=[] if proposal is not None else base.sources,
        expert=None,
        expert_request_created=False,
        confidence="high" if proposal else "medium",
        routed=False,
        routed_reason=None,
        proposed_message=proposal,
    )
