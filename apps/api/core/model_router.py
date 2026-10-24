"""Model mixing: pick the chat model for an Ask or workspace question by difficulty.

A very cheap model (``MODEL_ROUTER_MODEL``, default gpt-6-luna with no reasoning)
rates the question as simple, moderate, or complex; each tier maps to a model
and reasoning effort from settings. Routing applies only to organizations on
OpenAI that use the default model. An admin who pins another model, or a local
model, keeps it. Any routing failure answers with the default model.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from core.models import ChatMessage

logger = logging.getLogger(__name__)

Tier = Literal["simple", "moderate", "complex"]
TIERS: tuple[Tier, ...] = ("simple", "moderate", "complex")

_ROUTER_SYSTEM = """\
You rate how hard a request to a company assistant is, so it can be sent to the
cheapest model that will handle it well. Reply with JSON only:
{"tier": "simple" | "moderate" | "complex", "reason": "<at most 12 words>"}

simple: greetings and small talk; one fact or lookup ("who owns X", "latest email
from Y", "how many unread emails"); a short email or message draft; creating or
editing a small file with clear instructions; starting a status tracker.
moderate: questions with several parts; summarizing or comparing a few sources;
multi-step work across tools (search email, then update a file); scheduling a
meeting; drafting a longer document such as a memo or agenda.
complex: deep analysis or reasoning across many sources; resolving conflicting
evidence; strategy, plans, or recommendations with trade-offs; non-trivial
calculations; long reports or proposals; code changes and pull requests;
ambiguous requests that need careful judgment.
When unsure between two tiers, pick the lower one.
"""

_TRIVIAL = re.compile(
    r"^\s*(?:hi|hello|hey|thanks|thank you|ok|okay|yes|no|sure|cool|great)[\s!.?]*$", re.I
)


@dataclass(frozen=True)
class ModelChoice:
    tier: Tier
    model: str
    reasoning_effort: str
    reason: str = ""
    routed: bool = True


def _settings() -> Any | None:
    try:
        from core.config import get_settings

        return get_settings()
    except Exception:  # noqa: BLE001 - no configuration means no routing
        return None


def tier_choice(tier: Tier, reason: str = "") -> ModelChoice | None:
    settings = _settings()
    if settings is None:
        return None
    model = str(getattr(settings, f"model_{tier}") or "").strip()
    effort = str(getattr(settings, f"model_{tier}_reasoning") or "none").strip() or "none"
    if not model:
        return None
    return ModelChoice(tier=tier, model=model, reasoning_effort=effort, reason=reason)


def routing_enabled_for(configuration: Any) -> bool:
    """True for orgs on OpenAI with the default model, when routing is switched on."""

    from core.llm_provider import default_openai_model

    settings = _settings()
    if settings is None or not getattr(settings, "model_routing_enabled", False):
        return False
    return (
        getattr(configuration, "provider", None) == "openai"
        and getattr(configuration, "model", None) == default_openai_model()
    )


def _parse_tier(content: str) -> tuple[Tier | None, str]:
    match = re.search(r"\{.*\}", content or "", re.S)
    try:
        parsed = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        return None, ""
    tier = str(parsed.get("tier") or "").strip().lower()
    reason = " ".join(str(parsed.get("reason") or "").split())[:120]
    return (tier if tier in TIERS else None), reason  # type: ignore[return-value]


async def choose_model(
    client: Any,
    question: str,
    history: list[ChatMessage] | None = None,
    *,
    attachments: int = 0,
    in_workspace: bool = False,
) -> ModelChoice | None:
    """The model for this question, or None to keep the organization's model."""

    if not routing_enabled_for(getattr(client, "configuration", None)):
        return None
    if _TRIVIAL.match(question or ""):
        return tier_choice("simple", "Small talk.")
    settings = _settings()
    recent = "\n".join(
        f"{turn.role}: {' '.join(turn.content.split())[:200]}" for turn in (history or [])[-4:]
    )
    prompt = (
        f"Where: {'a team workspace' if in_workspace else 'Ask'}\n"
        f"Files attached: {attachments}\n"
        f"Recent conversation:\n{recent or '(none)'}\n\n"
        f"Request: {' '.join((question or '').split())[:2000]}"
    )
    try:
        router = client.with_model(str(settings.model_router_model), "none")
        response = await router.chat.completions.create(
            model="",
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=60,
            response_format={"type": "json_object"},
        )
        tier, reason = _parse_tier(response.choices[0].message.content or "")
    except Exception:  # noqa: BLE001 - routing never blocks an answer
        logger.warning("Model routing failed; using the default model", exc_info=True)
        return None
    if tier is None:
        return None
    choice = tier_choice(tier, reason)
    if choice is not None:
        logger.info("Model routing: %s -> %s (%s)", tier, choice.model, reason)
    return choice
