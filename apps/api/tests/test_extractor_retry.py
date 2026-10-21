"""Chunk metadata extraction retries exactly once before falling back to noise."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from brain import extractor
from core import llm_provider
from core.models import Chunk, Message

_VALID = {
    "entities": ["Atlas"],
    "knowledge_type": "decision",
    "ownership": [],
    "confidence": "high",
    "confidence_reason": "Explicit decision.",
    "summary": "The team chose Postgres for Atlas.",
}


def _chunk() -> Chunk:
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    return Chunk(
        chunk_id="chunk-1",
        messages=[Message(speaker="Ada", timestamp=now, body="We picked Postgres for Atlas.")],
        speakers=["Ada"],
        start_time=now,
        end_time=now,
        raw_text="Ada: We picked Postgres for Atlas.",
    )


def _response(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _install(monkeypatch, create: AsyncMock) -> None:
    monkeypatch.setattr(llm_provider, "_REJECTED_PARAMS", {})
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    adapter = llm_provider.LLMClientAdapter(
        sdk, llm_provider.LLMConfiguration(model=llm_provider.DEFAULT_OPENAI_MODEL)
    )
    monkeypatch.setattr(extractor, "get_default_openai_client", lambda: adapter)


async def test_invalid_json_is_retried_once_with_the_error_fed_back(monkeypatch) -> None:
    create = AsyncMock(side_effect=[_response("not json"), _response(json.dumps(_VALID))])
    _install(monkeypatch, create)

    metadata = await extractor.extract_chunk_metadata(_chunk())

    assert metadata.knowledge_type == "decision"
    assert create.await_count == 2
    retry_prompt = create.await_args_list[1].kwargs["messages"][-1]["content"]
    assert "could not be parsed" in retry_prompt


async def test_call_failure_is_retried_once(monkeypatch) -> None:
    create = AsyncMock(side_effect=[RuntimeError("timeout"), _response(json.dumps(_VALID))])
    _install(monkeypatch, create)

    metadata = await extractor.extract_chunk_metadata(_chunk())

    assert metadata.knowledge_type == "decision"
    assert create.await_count == 2


async def test_second_failure_marks_chunk_as_noise_without_a_third_call(monkeypatch) -> None:
    create = AsyncMock(side_effect=[_response("{}"), RuntimeError("still down")])
    _install(monkeypatch, create)

    metadata = await extractor.extract_chunk_metadata(_chunk())

    assert metadata.knowledge_type == "noise"
    assert metadata.confidence == "low"
    assert "still down" in metadata.confidence_reason
    assert create.await_count == 2


async def test_extraction_uses_default_model_and_survives_rejected_temperature(monkeypatch) -> None:
    import httpx
    import openai

    rejection = openai.BadRequestError(
        "Unsupported value: 'temperature' does not support 0 with this model.",
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com")),
        body={"param": "temperature"},
    )
    create = AsyncMock(side_effect=[rejection, _response(json.dumps(_VALID))])
    _install(monkeypatch, create)

    metadata = await extractor.extract_chunk_metadata(_chunk())

    # A parameter rejection is absorbed by the adapter, not spent as the retry.
    assert metadata.knowledge_type == "decision"
    assert create.await_args.kwargs["model"] == "gpt-6-luna"
    assert "temperature" not in create.await_args.kwargs
