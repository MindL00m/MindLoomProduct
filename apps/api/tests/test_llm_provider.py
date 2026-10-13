"""Provider switching and local chat adapter behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from core import llm_provider


class _Mappings:
    def __init__(self, row): self.row = row
    def mappings(self): return self
    def one_or_none(self): return self.row


def _factory_for(row):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Mappings(row))

    @asynccontextmanager
    async def factory():
        yield session
    return factory


@pytest.mark.asyncio
async def test_provider_switches_from_default_openai_to_local(monkeypatch) -> None:
    monkeypatch.setattr(llm_provider, "get_session_factory", lambda: _factory_for(None))
    assert (await llm_provider.get_llm_configuration("org")).provider == "openai"

    row = {"provider": "local", "base_url": "http://llm/v1", "model": "qwen",
           "context_window": 8192, "supports_tools": False}
    monkeypatch.setattr(llm_provider, "get_session_factory", lambda: _factory_for(row))
    config = await llm_provider.get_llm_configuration("org")
    assert (config.provider, config.model, config.context_window) == ("local", "qwen", 8192)


@pytest.mark.asyncio
async def test_local_adapter_preserves_streaming(monkeypatch) -> None:
    async def chunks():
        yield "first"
        yield "second"

    create = AsyncMock(return_value=chunks())
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    config = llm_provider.LLMConfiguration(
        provider="local", base_url="http://llm/v1", model="local-model", context_window=4096
    )
    client = llm_provider.LLMClientAdapter(sdk, config)
    stream = await client.chat.completions.create(
        model="ignored", messages=[{"role": "user", "content": "hello"}], stream=True
    )
    assert [part async for part in stream] == ["first", "second"]
    assert create.await_args.kwargs["stream"] is True
    assert create.await_args.kwargs["model"] == "local-model"
    assert create.await_args.kwargs["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_adapter_always_sets_reasoning_effort_none() -> None:
    create = AsyncMock()
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    config = llm_provider.LLMConfiguration(model="gpt-6-luna", context_window=128_000)
    client = llm_provider.LLMClientAdapter(sdk, config)
    await client.chat.completions.create(
        model="ignored",
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "lookup"}}],
        tool_choice="required",
        reasoning_effort="high",
    )
    assert create.await_args.kwargs["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_unreachable_local_endpoint_has_clear_error(monkeypatch) -> None:
    async def fail(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", fail)
    monkeypatch.setattr(
        llm_provider,
        "get_settings",
        lambda: SimpleNamespace(
            local_llm_api_key="local", local_llm_timeout_seconds=1.0
        ),
    )
    with pytest.raises(llm_provider.LLMProviderError, match="Cannot reach.*local LLM"):
        await llm_provider.check_local_endpoint("http://offline/v1")


def test_context_limit_keeps_system_and_latest_message() -> None:
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "old " * 3000},
        {"role": "user", "content": "latest"},
    ]
    limited = llm_provider.limit_messages(messages, 1024)
    assert [message["content"] for message in limited] == ["rules", "latest"]
