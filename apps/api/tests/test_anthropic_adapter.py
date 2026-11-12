"""Claude behind the chat-completions interface: request and reply translation."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from core import anthropic_adapter
from core.anthropic_adapter import AnthropicClientAdapter, convert_messages, convert_tools
from core.llm_provider import LLMConfiguration, LLMProviderError

TOOLS = [{"type": "function", "function": {
    "name": "lookup", "description": "Find a record.",
    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
}}]


def _message(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(
        id="msg_1", model="claude-opus-5-5", content=list(blocks), stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5, cache_read_input_tokens=3, cache_creation_input_tokens=2
        ),
    )


def _text(text):
    return SimpleNamespace(type="text", text=text)


class _FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def stream(self, **params):
        self.calls.append(params)
        reply = self.replies.pop(0)

        @asynccontextmanager
        async def manager():
            if isinstance(reply, Exception):
                raise reply

            async def final():
                return reply

            yield SimpleNamespace(get_final_message=final)

        return manager()


def _client(*replies, effort="medium"):
    messages = _FakeMessages(replies)
    config = LLMConfiguration(
        provider="anthropic", model="claude-opus-5-5", context_window=200_000, reasoning_effort=effort
    )
    return AnthropicClientAdapter(SimpleNamespace(messages=messages), config), messages


def _api_error(cls, status, message):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(message, response=response, body=None)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch) -> None:
    monkeypatch.setattr(anthropic_adapter, "_NO_EFFORT", set())


def test_messages_become_a_system_prompt_and_alternating_turns() -> None:
    system, turns = convert_messages([
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Who owns billing?"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "lookup", "arguments": '{"q": "billing"}'}},
            {"id": "t2", "type": "function", "function": {"name": "lookup", "arguments": "not json"}},
        ]},
        {"role": "tool", "tool_call_id": "t1", "content": "Priya"},
        {"role": "tool", "tool_call_id": "t2", "content": ""},
    ])
    assert system == "Be brief."
    assert [turn["role"] for turn in turns] == ["user", "assistant", "user"]
    assert turns[1]["content"] == [
        {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "billing"}},
        {"type": "tool_use", "id": "t2", "name": "lookup", "input": {}},
    ]
    # Both results travel in one user turn, and an empty result is still valid.
    assert turns[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": "Priya"},
        {"type": "tool_result", "tool_use_id": "t2", "content": "(no output)"},
    ]


def test_images_are_translated_and_a_trimmed_history_starts_on_a_user_turn() -> None:
    _, turns = convert_messages([
        {"role": "tool", "tool_call_id": "gone", "content": "orphan"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD", "detail": "high"}},
        ]},
    ])
    assert turns == [{"role": "user", "content": [
        {"type": "text", "text": "What is this?"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
    ]}]


def test_tools_use_input_schema() -> None:
    assert convert_tools(TOOLS + [{"type": "function", "function": {"name": "ping"}}]) == [
        {"name": "lookup", "description": "Find a record.", "input_schema": TOOLS[0]["function"]["parameters"]},
        {"name": "ping", "input_schema": {"type": "object", "properties": {}}},
    ]


async def test_a_text_reply_reads_like_a_chat_completion() -> None:
    client, sent = _client(_message(SimpleNamespace(type="thinking", thinking=""), _text("Priya owns it.")))
    response = await client.chat.completions.create(
        model="", messages=[{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Who?"}],
        temperature=0, max_tokens=60,
    )
    assert response.choices[0].message.content == "Priya owns it."
    assert response.choices[0].message.tool_calls is None
    assert response.choices[0].finish_reason == "stop"
    assert response.usage.prompt_tokens == 15
    params = sent.calls[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["system"] == "Be brief."
    assert params["output_config"] == {"effort": "medium"}
    # Thinking is spent from max_tokens, so a small caller cap is raised.
    assert params["max_tokens"] == 16_000
    assert "temperature" not in params and "tools" not in params


async def test_tool_calls_come_back_in_the_openai_shape() -> None:
    reply = _message(
        _text("Looking."),
        SimpleNamespace(type="tool_use", id="toolu_1", name="lookup", input={"q": "billing"}),
        stop_reason="tool_use",
    )
    client, sent = _client(reply)
    response = await client.chat.completions.create(
        model="", messages=[{"role": "user", "content": "Who?"}], tools=TOOLS, tool_choice="required",
    )
    call = response.choices[0].message.tool_calls[0]
    assert (call.id, call.function.name, json.loads(call.function.arguments)) == (
        "toolu_1", "lookup", {"q": "billing"}
    )
    assert response.choices[0].finish_reason == "tool_calls"
    # A forced call is not accepted by current models: auto, with the rule in the prompt.
    assert sent.calls[0]["tool_choice"] == {"type": "auto"}
    assert "must call one of the available tools" in sent.calls[0]["system"]


async def test_tool_choice_none_is_kept() -> None:
    client, sent = _client(_message(_text("Done.")))
    await client.chat.completions.create(
        model="", messages=[{"role": "user", "content": "Summarize."}], tools=TOOLS, tool_choice="none",
    )
    assert sent.calls[0]["tool_choice"] == {"type": "none"}


async def test_json_mode_asks_for_json_and_strips_fences() -> None:
    client, sent = _client(_message(_text('```json\n{"tier": "simple"}\n```')))
    response = await client.chat.completions.create(
        model="", messages=[{"role": "user", "content": "Rate it."}],
        response_format={"type": "json_object"},
    )
    assert json.loads(response.choices[0].message.content) == {"tier": "simple"}
    assert "single valid JSON object" in sent.calls[0]["system"]


async def test_a_model_without_effort_is_retried_and_remembered() -> None:
    rejected = _api_error(anthropic.BadRequestError, 400, "output_config.effort is not supported")
    client, sent = _client(rejected, _message(_text("ok")), _message(_text("again")))
    assert (await client.chat.completions.create(model="", messages=[])).choices[0].message.content == "ok"
    await client.chat.completions.create(model="", messages=[])
    assert ["output_config" in call for call in sent.calls] == [True, False, False]


async def test_a_refusal_is_reported_not_returned_empty() -> None:
    client, _ = _client(_message(stop_reason="refusal"))
    response = await client.chat.completions.create(model="", messages=[{"role": "user", "content": "x"}])
    assert response.choices[0].finish_reason == "content_filter"
    assert "declined" in response.choices[0].message.content


async def test_a_bad_key_is_a_clear_provider_error() -> None:
    client, _ = _client(_api_error(anthropic.AuthenticationError, 401, "invalid x-api-key"))
    with pytest.raises(LLMProviderError, match="rejected the API key"):
        await client.chat.completions.create(model="", messages=[{"role": "user", "content": "x"}])
