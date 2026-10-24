"""Organization-selectable chat-completion provider.

Local servers must expose the OpenAI-compatible ``/v1/models`` and
``/v1/chat/completions`` endpoints. The OpenAI SDK supplies an identical
response and streaming contract for both providers.
"""

from __future__ import annotations

import contextvars
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Iterator, Literal

import httpx
import tiktoken
from openai import AsyncOpenAI, BadRequestError
from sqlalchemy import text

from core.config import get_settings
from core.database import get_session_factory

logger = logging.getLogger(__name__)

Provider = Literal["openai", "local"]
DEFAULT_OPENAI_MODEL = "gpt-6-luna"
_DEFAULT_OPENAI_MODEL = DEFAULT_OPENAI_MODEL  # backwards-compatible alias


class LLMProviderError(RuntimeError):
    """A configured chat provider cannot service a request."""


@dataclass(frozen=True)
class LLMConfiguration:
    provider: Provider = "openai"
    base_url: str | None = None
    model: str = _DEFAULT_OPENAI_MODEL
    context_window: int = 128_000
    supports_tools: bool = True
    reasoning_effort: str | None = "none"
    # Used when ``model`` is rejected as unknown (e.g. a routed tier's model id).
    fallback_model: str | None = None


# A model chosen for the current Ask/workspace turn (see core.model_router). Every
# get_llm_client call made while answering that turn uses it.
_TURN_MODEL: contextvars.ContextVar[tuple[str, str | None] | None] = contextvars.ContextVar(
    "turn_model", default=None
)


@contextmanager
def use_model_for_turn(model: str, reasoning_effort: str | None) -> Iterator[None]:
    """Route this turn's chat calls (on OpenAI) to ``model`` with ``reasoning_effort``."""

    token = _TURN_MODEL.set((model, reasoning_effort))
    try:
        yield
    finally:
        _TURN_MODEL.reset(token)


def default_openai_model() -> str:
    """OpenAI model used when an org has not picked one (env: OPENAI_DEFAULT_MODEL)."""
    try:
        configured = get_settings().openai_default_model
    except Exception:  # noqa: BLE001 - incomplete env (tests, scripts) keeps the built-in default
        configured = ""
    return (configured or "").strip() or DEFAULT_OPENAI_MODEL


async def get_llm_configuration(org_id: str) -> LLMConfiguration:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(text("""
            SELECT provider, base_url, model, context_window, supports_tools
            FROM organization_llm_settings WHERE org_id=:org_id
        """), {"org_id": org_id})).mappings().one_or_none()
    if row is None:
        return LLMConfiguration(model=default_openai_model())
    return LLMConfiguration(
        provider=row["provider"], base_url=row["base_url"],
        model=row["model"] or default_openai_model(),
        context_window=int(row["context_window"]),
        supports_tools=bool(row["supports_tools"]),
    )


def _content_tokens(content: Any) -> int:
    rendered = content if isinstance(content, str) else json.dumps(content, default=str)
    return len(tiktoken.get_encoding("cl100k_base").encode(rendered)) + 8


def _truncate_content(content: Any, tokens: int) -> Any:
    if not isinstance(content, str):
        return content
    encoding = tiktoken.get_encoding("cl100k_base")
    return encoding.decode(encoding.encode(content)[:max(1, tokens)])


def limit_messages(messages: list[dict[str, Any]], context_window: int) -> list[dict[str, Any]]:
    """Keep system and latest turns within the configured input budget."""
    budget = max(256, context_window - min(2048, context_window // 4))
    if not messages:
        return messages
    system = messages[:1] if messages[0].get("role") == "system" else []
    used = 0
    kept: list[dict[str, Any]] = []
    for message in reversed(messages[len(system):]):
        cost = _content_tokens(message.get("content"))
        if not kept and cost > budget // 2:
            message = {
                **message,
                "content": _truncate_content(message.get("content"), budget // 2 - 8),
            }
            cost = _content_tokens(message.get("content"))
        if kept and used + cost > budget:
            break
        kept.append(message)
        used += cost
    # A tool result whose assistant tool_calls turn was trimmed is a 400 error.
    while kept and kept[-1].get("role") == "tool":
        kept.pop()
    remaining = max(64, budget - used)
    if system and _content_tokens(system[0].get("content")) > remaining:
        system = [{
            **system[0],
            "content": _truncate_content(system[0].get("content"), remaining - 8),
        }]
    return [*system, *reversed(kept)]


# Optional request fields that some models reject (gpt-4o-mini has no
# reasoning_effort; reasoning models refuse temperature=0 and max_tokens; some
# local servers lack tool_choice or response_format). A rejected field is dropped, or renamed for
# max_tokens, and remembered per model so later calls skip it.
_REJECTABLE_PARAMS = (
    "reasoning_effort",
    "temperature",
    "top_p",
    "max_tokens",
    "parallel_tool_calls",
    "tool_choice",
    "response_format",
)
_REJECTED_PARAMS: dict[tuple[str | None, str], set[str]] = {}


def _rejected_param(exc: BaseException, kwargs: dict[str, Any]) -> str | None:
    if not isinstance(exc, BadRequestError):
        return None
    named = getattr(exc, "param", None)
    if named in _REJECTABLE_PARAMS and named in kwargs:
        return named
    message = str(exc)
    for param in _REJECTABLE_PARAMS:
        if param in kwargs and param in message:
            return param
    return None


def _drop_params(kwargs: dict[str, Any], params: set[str]) -> None:
    for param in params:
        if param == "max_tokens" and "max_tokens" in kwargs:
            kwargs.setdefault("max_completion_tokens", kwargs.pop("max_tokens"))
        else:
            kwargs.pop(param, None)


# Model ids the provider said do not exist; later calls go straight to the fallback.
_UNKNOWN_MODELS: set[tuple[str | None, str]] = set()
# Models that rejected a reasoning effort other than "none" even without tools.
_EFFORT_NONE_ONLY: set[tuple[str | None, str]] = set()


def _reasoning_effort(config: LLMConfiguration, kwargs: dict[str, Any]) -> str:
    """The effort to send. Chat Completions rejects function tools combined with any
    effort but "none" (GPT-6 models and later), so every request carrying tools,
    on any model, uses "none"; other requests keep the configured effort."""

    effort = config.reasoning_effort or "none"
    if effort != "none" and (
        kwargs.get("tools") or (config.base_url, config.model) in _EFFORT_NONE_ONLY
    ):
        return "none"
    return effort


def _effort_must_be_none(exc: BaseException, kwargs: dict[str, Any]) -> bool:
    """A rejected non-"none" reasoning effort, which should be retried as "none"."""

    if not isinstance(exc, BadRequestError) or kwargs.get("reasoning_effort") in (None, "none"):
        return False
    return getattr(exc, "param", None) == "reasoning_effort" or "reasoning_effort" in str(exc)


def _unknown_model(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    message = str(exc).lower()
    return "model" in message and (
        status == 404 or "model_not_found" in message or "does not exist" in message
    )


class _CompletionsAdapter:
    def __init__(self, completions: Any, config: LLMConfiguration):
        self._completions = completions
        self._config = config

    async def create(self, **kwargs: Any) -> Any:
        config = self._config
        if config.fallback_model and (config.base_url, config.model) in _UNKNOWN_MODELS:
            config = replace(config, model=config.fallback_model, fallback_model=None)
        kwargs["model"] = config.model
        kwargs["messages"] = limit_messages(
            list(kwargs.get("messages") or []), config.context_window
        )
        rejected = _REJECTED_PARAMS.setdefault(
            (config.base_url, config.model), set()
        )
        kwargs["reasoning_effort"] = _reasoning_effort(config, kwargs)
        _drop_params(kwargs, rejected)
        try:
            while True:
                try:
                    return await self._completions.create(**kwargs)
                except Exception as exc:
                    if (
                        config.fallback_model
                        and config.fallback_model != config.model
                        and _unknown_model(exc)
                    ):
                        logger.warning(
                            "Model %s is not available; using %s instead",
                            config.model, config.fallback_model,
                        )
                        _UNKNOWN_MODELS.add((config.base_url, config.model))
                        config = replace(config, model=config.fallback_model, fallback_model=None)
                        kwargs["model"] = config.model
                        kwargs["reasoning_effort"] = _reasoning_effort(config, kwargs)
                        rejected = _REJECTED_PARAMS.setdefault((config.base_url, config.model), set())
                        _drop_params(kwargs, rejected)
                        continue
                    if _effort_must_be_none(exc, kwargs):
                        # Retry as "none" rather than dropping the field: without it
                        # the model's default effort is rejected the same way.
                        logger.info(
                            "Model %s rejected reasoning_effort=%s; retrying with none",
                            config.model, kwargs["reasoning_effort"],
                        )
                        if not kwargs.get("tools"):
                            _EFFORT_NONE_ONLY.add((config.base_url, config.model))
                        kwargs["reasoning_effort"] = "none"
                        continue
                    param = _rejected_param(exc, kwargs)
                    if param is None:
                        raise
                    logger.info(
                        "Model %s rejected %s; retrying without it",
                        config.model, param,
                    )
                    rejected.add(param)
                    _drop_params(kwargs, {param})
        except Exception as exc:
            if config.provider == "local":
                raise LLMProviderError(
                    f"Local LLM '{config.model}' at {config.base_url} failed: {exc}"
                ) from exc
            raise


class _ChatAdapter:
    def __init__(self, chat: Any, config: LLMConfiguration):
        self.completions = _CompletionsAdapter(chat.completions, config)


class LLMClientAdapter:
    """Expose the SDK's ``chat.completions.create`` interface unchanged."""
    def __init__(self, client: AsyncOpenAI, config: LLMConfiguration):
        self._client = client
        self.chat = _ChatAdapter(client.chat, config)
        self.configuration = config

    def with_model(self, model: str, reasoning_effort: str | None = "none") -> "LLMClientAdapter":
        """The same connection on another model; falls back to this one if it is unknown."""

        return LLMClientAdapter(self._client, replace(
            self.configuration,
            model=model,
            reasoning_effort=reasoning_effort,
            fallback_model=self.configuration.model if model != self.configuration.model else None,
        ))


async def get_llm_client(org_id: str) -> LLMClientAdapter:
    config = await get_llm_configuration(org_id)
    turn = _TURN_MODEL.get()
    if turn is not None and config.provider == "openai":
        model, effort = turn
        config = replace(
            config, model=model, reasoning_effort=effort,
            fallback_model=config.model if model != config.model else None,
        )
    settings = get_settings()
    if config.provider == "local" and not config.base_url:
        raise LLMProviderError("Local LLM is selected but no base URL is configured.")
    client = AsyncOpenAI(
        api_key=(settings.local_llm_api_key if config.provider == "local" else settings.openai_api_key),
        base_url=config.base_url if config.provider == "local" else None,
        timeout=(settings.local_llm_timeout_seconds if config.provider == "local" else settings.openai_request_timeout_seconds),
    )
    return LLMClientAdapter(client, config)


def get_default_openai_client() -> LLMClientAdapter:
    """OpenAI client on the default model for org-independent background calls.

    Ingestion extraction, query condensation and CONTEXT.md synthesis use this
    instead of a bare ``AsyncOpenAI`` so they share the adapter's handling of
    parameters a model rejects (e.g. ``temperature=0`` on reasoning models).
    """
    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )
    return LLMClientAdapter(client, LLMConfiguration(model=default_openai_model()))


async def check_local_endpoint(base_url: str) -> None:
    url = f"{base_url.rstrip('/')}/models"
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.local_llm_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=settings.local_llm_timeout_seconds) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except Exception as exc:
        raise LLMProviderError(f"Cannot reach an OpenAI-compatible local LLM at {url}: {exc}") from exc
