"""Claude (the Anthropic Messages API) behind the chat-completions interface.

Every caller in MindLoom speaks ``client.chat.completions.create(...)`` and reads
``response.choices[0].message``. This adapter keeps that contract for an
organization that connects Claude: it translates the request into a Messages
API call made with the official Anthropic SDK, and the reply back into the
SDK-typed ``ChatCompletion`` the callers already handle.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import anthropic
from anthropic import AsyncAnthropic
from openai.types.chat import ChatCompletion

from core.llm_provider import LLMConfiguration, LLMProviderError, limit_messages

logger = logging.getLogger(__name__)

# Claude thinks before answering and that thinking is spent from max_tokens, so
# a caller's small cap (e.g. 60 for a routing verdict) would cut the answer off.
_MIN_MAX_TOKENS = 16_000
_EFFORT = {"none": "low", "minimal": "low", "low": "low", "medium": "medium", "high": "high"}
_JSON_ONLY = (
    "Reply with a single valid JSON object and nothing else: no prose before or "
    "after it and no code fences."
)
_STOP_REASONS = {"tool_use": "tool_calls", "max_tokens": "length", "refusal": "content_filter"}
# Models that rejected output_config.effort; later calls leave it out.
_NO_EFFORT: set[str] = set()


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return "" if content is None else str(content)


def _image_block(part: dict[str, Any]) -> dict[str, Any] | None:
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    if not isinstance(url, str) or not url:
        return None
    match = re.match(r"data:([^;,]+);base64,(.*)", url, re.S)
    if match:
        return {"type": "image", "source": {
            "type": "base64", "media_type": match.group(1), "data": match.group(2),
        }}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _blocks(content: Any) -> list[dict[str, Any]]:
    """Chat-completions message content as Messages API content blocks."""

    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    blocks: list[dict[str, Any]] = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and str(part.get("text") or "").strip():
            blocks.append({"type": "text", "text": str(part["text"])})
        elif part.get("type") == "image_url":
            image = _image_block(part)
            if image:
                blocks.append(image)
    return blocks


def _tool_input(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split chat-completions messages into a system prompt and Messages API turns."""

    system: list[str] = []
    turns: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        # Tool results for one assistant turn must arrive in a single user turn.
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    for message in messages:
        role = message.get("role")
        if role in ("system", "developer"):
            if _text(message.get("content")).strip():
                system.append(_text(message.get("content")))
        elif role == "tool":
            add("user", [{
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id") or ""),
                "content": _text(message.get("content")) or "(no output)",
            }])
        elif role == "assistant":
            blocks = _blocks(message.get("content"))
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                blocks.append({
                    "type": "tool_use", "id": str(call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "input": _tool_input(function.get("arguments")),
                })
            add("assistant", blocks)
        else:
            add("user", _blocks(message.get("content")))

    # Trimming a long history can leave it starting mid tool round; the API needs
    # a plain user turn first.
    while turns and (
        turns[0]["role"] != "user"
        or any(block["type"] == "tool_result" for block in turns[0]["content"])
    ):
        turns.pop(0)
    if not turns:
        turns = [{"role": "user", "content": [{"type": "text", "text": "Continue."}]}]
    return "\n\n".join(system), turns


def convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    converted = []
    for tool in tools or []:
        function = tool.get("function") or {}
        if not function.get("name"):
            continue
        entry: dict[str, Any] = {
            "name": function["name"],
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
        }
        if function.get("description"):
            entry["description"] = function["description"]
        converted.append(entry)
    return converted


def _tool_choice(choice: Any, parallel: Any) -> tuple[dict[str, Any], str]:
    """The Messages API tool_choice, plus an instruction standing in for a forced call.

    Current Claude models reject a forced tool call (``any`` / a named tool), so
    "required" becomes ``auto`` with the requirement stated in the system prompt.
    """

    if choice == "none":
        return {"type": "none"}, ""
    selected: dict[str, Any] = {"type": "auto"}
    if parallel is False:
        selected["disable_parallel_tool_use"] = True
    if choice == "required":
        return selected, "You must call one of the available tools in this turn."
    if isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name")
        if name:
            return selected, f"You must call the `{name}` tool in this turn."
    return selected, ""


def _json_object(text: str) -> str:
    """The JSON object in a reply that was asked to be JSON only."""

    stripped = text.strip()
    try:
        json.loads(stripped)
        return stripped
    except ValueError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        candidate = stripped[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except ValueError:
            pass
    return text


def to_chat_completion(message: Any, *, json_mode: bool = False) -> ChatCompletion:
    """An Anthropic ``Message`` as the ``ChatCompletion`` MindLoom's callers read."""

    text = "".join(block.text for block in message.content if block.type == "text")
    tool_calls = [
        {
            "id": block.id, "type": "function",
            "function": {"name": block.name, "arguments": json.dumps(block.input or {})},
        }
        for block in message.content if block.type == "tool_use"
    ]
    if message.stop_reason == "refusal" and not text:
        text = "Claude declined to answer this request."
    elif json_mode and text:
        text = _json_object(text)
    usage = message.usage
    prompt_tokens = (
        (usage.input_tokens or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )
    return ChatCompletion.model_validate({
        "id": message.id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": message.model,
        "choices": [{
            "index": 0,
            "finish_reason": _STOP_REASONS.get(message.stop_reason or "", "stop"),
            "message": {
                "role": "assistant",
                "content": text or None,
                **({"tool_calls": tool_calls} if tool_calls else {}),
            },
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": usage.output_tokens or 0,
            "total_tokens": prompt_tokens + (usage.output_tokens or 0),
        },
    })


def _provider_error(exc: Exception, model: str) -> LLMProviderError:
    if isinstance(exc, anthropic.AuthenticationError):
        detail = "Claude rejected the API key. Check the key in your AI provider settings."
    elif isinstance(exc, anthropic.PermissionDeniedError):
        detail = f"This Claude API key is not allowed to use '{model}'."
    elif isinstance(exc, anthropic.NotFoundError):
        detail = f"Claude does not have a model named '{model}'."
    elif isinstance(exc, anthropic.RateLimitError):
        detail = "Claude is rate limiting this API key. Try again shortly."
    elif isinstance(exc, anthropic.BadRequestError):
        detail = f"Claude rejected the request: {getattr(exc, 'message', exc)}"
    elif isinstance(exc, anthropic.APIConnectionError):
        detail = "Could not reach Claude. Try again shortly."
    else:
        detail = "Claude is unavailable right now. Try again shortly."
    return LLMProviderError(detail)


class _AnthropicCompletions:
    def __init__(self, client: AsyncAnthropic, config: LLMConfiguration):
        self._client = client
        self._config = config

    async def create(self, **kwargs: Any) -> ChatCompletion:
        config = self._config
        if kwargs.get("stream"):
            raise LLMProviderError("Streaming replies are not available with Claude yet.")
        system, messages = convert_messages(
            limit_messages(list(kwargs.get("messages") or []), config.context_window)
        )
        json_mode = (kwargs.get("response_format") or {}).get("type") in ("json_object", "json_schema")
        if json_mode:
            system = f"{system}\n\n{_JSON_ONLY}".strip()
        params: dict[str, Any] = {
            "model": config.model,
            "max_tokens": max(
                int(kwargs.get("max_completion_tokens") or kwargs.get("max_tokens") or 0),
                _MIN_MAX_TOKENS,
            ),
            "messages": messages,
            # Tool loops resend a growing prefix; cache it.
            "cache_control": {"type": "ephemeral"},
        }
        tools = convert_tools(kwargs.get("tools"))
        if tools:
            params["tools"] = tools
            params["tool_choice"], must_call = _tool_choice(
                kwargs.get("tool_choice"), kwargs.get("parallel_tool_calls")
            )
            if must_call:
                system = f"{system}\n\n{must_call}".strip()
        if system:
            params["system"] = system
        if config.model not in _NO_EFFORT:
            params["output_config"] = {
                "effort": _EFFORT.get(config.reasoning_effort or "none", "medium")
            }
        # temperature / top_p are not forwarded: current Claude models reject them.
        try:
            try:
                message = await self._send(params)
            except anthropic.BadRequestError as exc:
                if "output_config" not in params or not re.search(r"effort|output_config", str(exc)):
                    raise
                logger.info("Model %s rejected output_config.effort; retrying without it", config.model)
                _NO_EFFORT.add(config.model)
                del params["output_config"]
                message = await self._send(params)
        except anthropic.APIError as exc:
            logger.warning("Claude request failed: %s", exc.__class__.__name__)
            raise _provider_error(exc, config.model) from exc
        return to_chat_completion(message, json_mode=json_mode)

    async def _send(self, params: dict[str, Any]) -> Any:
        # Streamed so a long answer is not cut off by an HTTP timeout.
        async with self._client.messages.stream(**params) as stream:
            return await stream.get_final_message()


class _AnthropicChat:
    def __init__(self, client: AsyncAnthropic, config: LLMConfiguration):
        self.completions = _AnthropicCompletions(client, config)


class AnthropicClientAdapter:
    """Claude with the same surface as ``LLMClientAdapter``."""

    def __init__(self, client: AsyncAnthropic, config: LLMConfiguration):
        self._client = client
        self.chat = _AnthropicChat(client, config)
        self.configuration = config

    def with_model(self, model: str, reasoning_effort: str | None = "none") -> "AnthropicClientAdapter":
        """Model mixing names OpenAI models, so a Claude org stays on its own model."""

        return self


async def check_anthropic_key(api_key: str, model: str) -> None:
    """Confirm the key works and can see ``model`` (free: no tokens are generated)."""

    client = AsyncAnthropic(api_key=api_key, timeout=20.0, max_retries=1)
    try:
        await client.models.retrieve(model)
    except anthropic.APIError as exc:
        raise _provider_error(exc, model) from exc
