"""Organization-selectable chat-completion provider.

Local servers must expose the OpenAI-compatible ``/v1/models`` and
``/v1/chat/completions`` endpoints. The OpenAI SDK supplies an identical
response and streaming contract for both providers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import tiktoken
from openai import AsyncOpenAI
from sqlalchemy import text

from core.config import get_settings
from core.database import get_session_factory

Provider = Literal["openai", "local"]
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


class LLMProviderError(RuntimeError):
    """A configured chat provider cannot service a request."""


@dataclass(frozen=True)
class LLMConfiguration:
    provider: Provider = "openai"
    base_url: str | None = None
    model: str = _DEFAULT_OPENAI_MODEL
    context_window: int = 128_000
    supports_tools: bool = True


async def get_llm_configuration(org_id: str) -> LLMConfiguration:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(text("""
            SELECT provider, base_url, model, context_window, supports_tools
            FROM organization_llm_settings WHERE org_id=:org_id
        """), {"org_id": org_id})).mappings().one_or_none()
    if row is None:
        return LLMConfiguration()
    return LLMConfiguration(
        provider=row["provider"], base_url=row["base_url"],
        model=row["model"] or _DEFAULT_OPENAI_MODEL,
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
    remaining = max(64, budget - used)
    if system and _content_tokens(system[0].get("content")) > remaining:
        system = [{
            **system[0],
            "content": _truncate_content(system[0].get("content"), remaining - 8),
        }]
    return [*system, *reversed(kept)]


class _CompletionsAdapter:
    def __init__(self, completions: Any, config: LLMConfiguration):
        self._completions = completions
        self._config = config

    async def create(self, **kwargs: Any) -> Any:
        kwargs["model"] = self._config.model
        kwargs["messages"] = limit_messages(
            list(kwargs.get("messages") or []), self._config.context_window
        )
        kwargs["reasoning_effort"] = "none"
        try:
            return await self._completions.create(**kwargs)
        except Exception as exc:
            if self._config.provider == "local":
                raise LLMProviderError(
                    f"Local LLM '{self._config.model}' at {self._config.base_url} failed: {exc}"
                ) from exc
            raise


class _ChatAdapter:
    def __init__(self, chat: Any, config: LLMConfiguration):
        self.completions = _CompletionsAdapter(chat.completions, config)


class LLMClientAdapter:
    """Expose the SDK's ``chat.completions.create`` interface unchanged."""
    def __init__(self, client: AsyncOpenAI, config: LLMConfiguration):
        self.chat = _ChatAdapter(client.chat, config)
        self.configuration = config


async def get_llm_client(org_id: str) -> LLMClientAdapter:
    config = await get_llm_configuration(org_id)
    settings = get_settings()
    if config.provider == "local" and not config.base_url:
        raise LLMProviderError("Local LLM is selected but no base URL is configured.")
    client = AsyncOpenAI(
        api_key=(settings.local_llm_api_key if config.provider == "local" else settings.openai_api_key),
        base_url=config.base_url if config.provider == "local" else None,
        timeout=(settings.local_llm_timeout_seconds if config.provider == "local" else settings.openai_request_timeout_seconds),
    )
    return LLMClientAdapter(client, config)


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
