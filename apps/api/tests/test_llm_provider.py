"""Provider switching and local chat adapter behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from core import llm_provider


@pytest.fixture(autouse=True)
def _fresh_rejected_params(monkeypatch) -> None:
    monkeypatch.setattr(llm_provider, "_REJECTED_PARAMS", {})
    monkeypatch.setattr(llm_provider, "_EFFORT_NONE_ONLY", set())
    monkeypatch.setattr(llm_provider, "_UNKNOWN_MODELS", set())


TOOLS = [{"type": "function", "function": {"name": "lookup"}}]
TOOLS_WITH_EFFORT = (
    "Error code: 400 - Function tools with reasoning_effort are not supported for gpt-6-luna "
    "in /v1/chat/completions. To use function tools, use /v1/responses or set "
    "reasoning_effort to 'none'."
)


def _adapter(create, model="gpt-6-luna", effort="medium"):
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    config = llm_provider.LLMConfiguration(model=model, context_window=128_000, reasoning_effort=effort)
    return llm_provider.LLMClientAdapter(sdk, config)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-6-sol", "gpt-6-astra", "gpt-5.4-mini"])
async def test_tool_requests_never_send_a_reasoning_effort_on_any_model(model) -> None:
    create = AsyncMock(return_value="ok")
    client = _adapter(create, model=model, effort="high")
    await client.chat.completions.create(model="", messages=[], tools=TOOLS, tool_choice="required")
    await client.chat.completions.create(model="", messages=[], tools=TOOLS, tool_choice="none")
    assert [c.kwargs["reasoning_effort"] for c in create.await_args_list] == ["none", "none"]
    # Calls without tools keep the routed effort.
    await client.chat.completions.create(model="", messages=[])
    assert create.await_args.kwargs["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_rejected_effort_is_retried_as_none_not_dropped() -> None:
    create = AsyncMock(side_effect=[_bad_request(TOOLS_WITH_EFFORT, param="reasoning_effort"), "ok", "next"])
    client = _adapter(create, effort="low")
    assert await client.chat.completions.create(model="", messages=[]) == "ok"
    assert [c.kwargs["reasoning_effort"] for c in create.await_args_list] == ["low", "none"]
    # Remembered: this model only takes "none", and the field is still sent.
    assert await client.chat.completions.create(model="", messages=[]) == "next"
    assert create.await_args.kwargs["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_routed_tier_with_tools_works_end_to_end() -> None:
    """The reported failure: a moderate/complex tier turn that calls tools."""

    async def strict(**kwargs):
        if kwargs.get("tools") and kwargs.get("reasoning_effort") not in (None, "none"):
            raise _bad_request(TOOLS_WITH_EFFORT, param="reasoning_effort")
        return "ok"

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=strict)))
    base = llm_provider.LLMClientAdapter(sdk, llm_provider.LLMConfiguration(model="gpt-6-luna"))
    for model, effort in [("gpt-6-luna", "low"), ("gpt-6-sol", "medium"), ("gpt-6-astra", "high")]:
        routed = base.with_model(model, effort)
        assert await routed.chat.completions.create(model="", messages=[], tools=TOOLS) == "ok"


def _bad_request(message: str, param: str | None = None) -> openai.BadRequestError:
    return openai.BadRequestError(
        message,
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com")),
        body={"message": message, "param": param} if param else None,
    )


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
async def test_adapter_drops_reasoning_effort_for_models_that_reject_it() -> None:
    rejection = _bad_request(
        "Error code: 400 - Unrecognized request argument supplied: reasoning_effort"
    )
    create = AsyncMock(side_effect=[rejection, "answer", "second answer"])
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    config = llm_provider.LLMConfiguration(model="gpt-4o-mini", context_window=128_000)
    client = llm_provider.LLMClientAdapter(sdk, config)
    messages = [{"role": "user", "content": "hello"}]

    assert await client.chat.completions.create(model="ignored", messages=messages) == "answer"
    assert "reasoning_effort" in create.await_args_list[0].kwargs
    assert "reasoning_effort" not in create.await_args_list[1].kwargs

    assert await client.chat.completions.create(model="ignored", messages=messages) == "second answer"
    assert create.await_count == 3
    assert "reasoning_effort" not in create.await_args_list[2].kwargs


@pytest.mark.asyncio
async def test_adapter_does_not_retry_other_bad_requests() -> None:
    create = AsyncMock(side_effect=_bad_request("Error code: 400 - Invalid 'messages'"))
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client = llm_provider.LLMClientAdapter(sdk, llm_provider.LLMConfiguration())
    with pytest.raises(openai.BadRequestError):
        await client.chat.completions.create(model="ignored", messages=[])
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_adapter_retries_without_each_unsupported_sampling_param() -> None:
    create = AsyncMock(side_effect=[
        _bad_request(
            "Unsupported value: 'temperature' does not support 0 with this model.",
            param="temperature",
        ),
        _bad_request(
            "Unsupported parameter: 'max_tokens' is not supported with this model. "
            "Use 'max_completion_tokens' instead.",
            param="max_tokens",
        ),
        "answer",
    ])
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client = llm_provider.LLMClientAdapter(
        sdk, llm_provider.LLMConfiguration(model="o-series", context_window=128_000)
    )
    answer = await client.chat.completions.create(
        model="ignored",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0,
        max_tokens=300,
    )
    assert answer == "answer"
    final = create.await_args_list[-1].kwargs
    assert "temperature" not in final
    assert "max_tokens" not in final
    assert final["max_completion_tokens"] == 300


def test_context_limit_drops_tool_results_whose_call_was_trimmed() -> None:
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "x " * 3000, "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
        {"role": "user", "content": "follow up"},
    ]
    limited = llm_provider.limit_messages(messages, 1024)
    assert [message["role"] for message in limited] == ["system", "user"]
    assert limited[-1]["content"] == "follow up"


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


def test_default_openai_model_is_gpt_6_luna() -> None:
    assert llm_provider.DEFAULT_OPENAI_MODEL == "gpt-6-luna"
    assert llm_provider.LLMConfiguration().model == "gpt-6-luna"


@pytest.mark.asyncio
async def test_org_without_settings_uses_default_model(monkeypatch) -> None:
    monkeypatch.setattr(llm_provider, "get_session_factory", lambda: _factory_for(None))
    config = await llm_provider.get_llm_configuration("org")
    assert (config.provider, config.model) == ("openai", "gpt-6-luna")


@pytest.mark.asyncio
async def test_adapter_drops_rejected_response_format() -> None:
    create = AsyncMock(side_effect=[
        _bad_request("Invalid parameter: 'response_format' is not supported.", param="response_format"),
        "ok",
    ])
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client = llm_provider.LLMClientAdapter(sdk, llm_provider.LLMConfiguration())
    result = await client.chat.completions.create(
        model="ignored", messages=[], response_format={"type": "json_object"}
    )
    assert result == "ok"
    assert "response_format" not in create.await_args.kwargs
