"""Live check of the Claude connection, through the same adapter MindLoom uses.

Run from apps/api with a real key (a few cents of usage):

    ANTHROPIC_API_KEY=sk-ant-... python scripts/check_ai_connection.py [model]

It verifies the key, then asks for a plain answer, a JSON answer, and a tool
round trip, which together cover every way MindLoom calls its model.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import AsyncAnthropic  # noqa: E402

from core.anthropic_adapter import AnthropicClientAdapter, check_anthropic_key  # noqa: E402
from core.llm_provider import (  # noqa: E402
    ANTHROPIC_CONTEXT_WINDOW,
    DEFAULT_ANTHROPIC_MODEL,
    LLMConfiguration,
    LLMProviderError,
)

TOOLS = [{"type": "function", "function": {
    "name": "find_owner",
    "description": "Look up who owns a project at the company.",
    "parameters": {
        "type": "object", "properties": {"project": {"type": "string"}}, "required": ["project"],
    },
}}]


async def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("Set ANTHROPIC_API_KEY to a Claude API key from console.anthropic.com.")
        return 2
    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ANTHROPIC_MODEL
    config = LLMConfiguration(
        provider="anthropic", model=model, context_window=ANTHROPIC_CONTEXT_WINDOW,
        reasoning_effort="medium",
    )
    client = AnthropicClientAdapter(AsyncAnthropic(api_key=key), config)
    create = client.chat.completions.create
    try:
        await check_anthropic_key(key, model)
        print(f"1. key accepted, {model} is available")

        reply = await create(model="", temperature=0, max_tokens=60, messages=[
            {"role": "system", "content": "You are MindLoom's company assistant. Answer in one sentence."},
            {"role": "user", "content": "Say hello and name the model family you are."},
        ])
        print(f"2. plain answer: {reply.choices[0].message.content!r}  (served by {reply.model})")

        reply = await create(model="", response_format={"type": "json_object"}, messages=[
            {"role": "user", "content": 'Rate "who owns billing?" as JSON: {"tier": "simple"|"complex"}'},
        ])
        print(f"3. JSON answer: {json.loads(reply.choices[0].message.content)}")

        messages = [{"role": "user", "content": "Who owns the Atlas project? Use the tool."}]
        reply = await create(model="", messages=messages, tools=TOOLS, tool_choice="required")
        calls = reply.choices[0].message.tool_calls or []
        if not calls:
            print("4. FAILED: Claude did not call the tool")
            return 1
        print(f"4. tool call: {calls[0].function.name}({calls[0].function.arguments})")
        messages += [
            {"role": "assistant", "content": reply.choices[0].message.content, "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in calls
            ]},
            *[{"role": "tool", "tool_call_id": c.id, "content": json.dumps({"owner": "Priya Raman"})}
              for c in calls],
        ]
        reply = await create(model="", messages=messages, tools=TOOLS, tool_choice="auto")
        answer = reply.choices[0].message.content or ""
        print(f"5. answer from the tool result: {answer!r}")
        if "Priya" not in answer:
            print("   FAILED: the tool result was not used")
            return 1
    except LLMProviderError as exc:
        print(f"FAILED: {exc}")
        return 1
    print("Claude connection works.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
