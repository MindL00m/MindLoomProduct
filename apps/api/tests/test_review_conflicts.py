"""Conflict reviews are queued only for real contradictions; bulk resolve; model routing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core import model_router
from core.models import Chunk, ChunkMetadata
from keeper import review_workflows as reviews


@pytest.mark.parametrize("new, old", [
    ("The total amount paid was $21.40 on October 21, 2026.", "The receipt number is 2290-2314-6727."),
    ("The project owner is Priya.", "The project deadline is 30 November."),
    ("The order total was $12.00.", "The order total was $12.00."),
    ("Invoice 41 was paid on 3 October.", "The office opens at 9am."),
])
def test_different_facts_are_not_candidates(new: str, old: str) -> None:
    assert not reviews.is_conflict_candidate(new, old)


@pytest.mark.parametrize("new, old", [
    ("The Q3 marketing budget is $45,000.", "The Q3 marketing budget is $40,000."),
    ("Priya does not own the data pipeline.", "Priya owns the data pipeline."),
    ("The launch date moved to November 10.", "The launch date is November 3."),
])
def test_same_attribute_disagreements_are_candidates(new: str, old: str) -> None:
    assert reviews.is_conflict_candidate(new, old)


class _Session:
    def __init__(self, prior):
        self.prior = prior
        self.inserted: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM knowledge_claims" in sql:
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: self.prior))
        if "INSERT INTO knowledge_claims" in sql:
            self.inserted.append(params["claim"])
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def commit(self):
        return None


def _chunk() -> Chunk:
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    return Chunk(chunk_id="c-new", messages=[], speakers=[], start_time=now, end_time=now, raw_text="x")


def _metadata(claims: list[str]) -> ChunkMetadata:
    return ChunkMetadata.model_construct(factual_claims=claims, entities=["Acme"])


async def _run(monkeypatch, claims, prior, verdict):
    session = _Session(prior)
    created: list[dict] = []
    judged: list[tuple[str, str]] = []

    async def judge(new, old, **kwargs):
        judged.append((new, old))
        return verdict

    async def create(**kwargs):
        created.append(kwargs)
        return "r1"

    monkeypatch.setattr(reviews, "get_session_factory", lambda: (lambda: session))
    monkeypatch.setattr(reviews, "judge_conflict", judge)
    monkeypatch.setattr(reviews, "create_review", create)
    await reviews.record_claims_and_detect_conflicts("org", _chunk(), _metadata(claims))
    return created, judged, session


async def test_receipt_total_and_number_never_reach_the_queue(monkeypatch) -> None:
    prior = [{"claim_id": "1", "chunk_id": "c-old", "claim_text": "The receipt number is 2290-2314-6727.",
              "created_at": None}]
    created, judged, session = await _run(
        monkeypatch, ["The total amount paid was $21.40 on October 21, 2026."], prior,
        ("contradiction", "x"),
    )
    assert judged == [] and created == []
    assert session.inserted == ["The total amount paid was $21.40 on October 21, 2026."]


async def test_model_confirmed_contradictions_are_queued_with_the_reason(monkeypatch) -> None:
    prior = [{"claim_id": "1", "chunk_id": "c-old", "claim_text": "The Q3 marketing budget is $40,000.",
              "created_at": None}]
    created, judged, _ = await _run(
        monkeypatch, ["The Q3 marketing budget is $45,000."], prior,
        ("contradiction", "Both state the current Q3 marketing budget."),
    )
    assert len(judged) == 1
    assert created[0]["source_ids"] == ["c-new", "c-old"]
    assert "Why they conflict: Both state the current Q3 marketing budget." in created[0]["description"]


@pytest.mark.parametrize("verdict", [("compatible", "Two different receipts."), ("update", "Newer value."), None])
async def test_compatible_updates_and_unchecked_pairs_are_not_queued(monkeypatch, verdict) -> None:
    prior = [{"claim_id": "1", "chunk_id": "c-old", "claim_text": "The receipt total was $9.99.",
              "created_at": None}]
    created, judged, _ = await _run(monkeypatch, ["The receipt total was $21.40."], prior, verdict)
    assert len(judged) == 1 and created == []


async def test_resolve_all_only_touches_conflicts_and_verifications(monkeypatch) -> None:
    seen: dict = {}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def begin(self):
            return self

        async def execute(self, statement, params):
            seen.update(sql=str(statement), params=params)
            return SimpleNamespace(rowcount=7)

    monkeypatch.setattr(reviews, "get_session_factory", lambda: (lambda: Session()))
    result = await reviews.resolve_all_reviews("org", actor_user_id="admin")
    assert result == {"resolved": 7, "review_types": ["conflict", "verification"]}
    assert seen["params"]["types"] == ["conflict", "verification"]
    assert "status='open'" in seen["sql"]
    with pytest.raises(HTTPException):
        await reviews.resolve_all_reviews("org", actor_user_id="admin", review_types=["proposal"])


# --- Model routing --------------------------------------------------------------


def _settings(**overrides):
    values = dict(
        model_routing_enabled=True, model_router_model="gpt-6-luna",
        model_simple="gpt-6-luna", model_simple_reasoning="none",
        model_moderate="gpt-6-luna", model_moderate_reasoning="low",
        model_complex="gpt-6-sol", model_complex_reasoning="medium",
        openai_default_model="gpt-6-luna",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(reply: str, provider: str = "openai", model: str = "gpt-6-luna"):
    calls: list[dict] = []

    class Client:
        configuration = SimpleNamespace(provider=provider, model=model)

        def with_model(self, name, effort):
            calls.append({"router_model": name, "effort": effort})
            return self

        @property
        def chat(self):
            async def create(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])

            return SimpleNamespace(completions=SimpleNamespace(create=create))

    return Client(), calls


async def test_hard_questions_go_to_the_complex_tier(monkeypatch) -> None:
    monkeypatch.setattr("core.config.get_settings", lambda: _settings())
    client, calls = _client(json.dumps({"tier": "complex", "reason": "Strategy across many sources."}))
    choice = await model_router.choose_model(client, "Compare our three pricing options and recommend one")
    assert (choice.tier, choice.model, choice.reasoning_effort) == ("complex", "gpt-6-sol", "medium")
    assert calls[0] == {"router_model": "gpt-6-luna", "effort": "none"}


async def test_small_talk_skips_the_router_and_defaults_to_luna(monkeypatch) -> None:
    monkeypatch.setattr("core.config.get_settings", lambda: _settings())
    client, calls = _client("{}")
    choice = await model_router.choose_model(client, "thanks!")
    assert (choice.tier, choice.model) == ("simple", "gpt-6-luna") and calls == []


@pytest.mark.parametrize("client_args, settings", [
    (("{}", "local", "llama3"), {}),
    (("{}", "openai", "gpt-6-astra"), {}),  # an admin pinned a model
    (("{}", "openai", "gpt-6-luna"), {"model_routing_enabled": False}),
])
async def test_routing_respects_pinned_models_and_the_switch(monkeypatch, client_args, settings) -> None:
    monkeypatch.setattr("core.config.get_settings", lambda: _settings(**settings))
    client, calls = _client(*client_args)
    assert await model_router.choose_model(client, "Compare options") is None and calls == []


async def test_unreadable_router_reply_keeps_the_default(monkeypatch) -> None:
    monkeypatch.setattr("core.config.get_settings", lambda: _settings())
    client, _ = _client("not json")
    assert await model_router.choose_model(client, "Compare options") is None


async def test_unknown_tier_model_falls_back_to_the_default(monkeypatch) -> None:
    from core import llm_provider

    calls: list[str] = []

    class NotFound(Exception):
        status_code = 404

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "gpt-6-nope":
            raise NotFound("The model `gpt-6-nope` does not exist")
        return "ok"

    raw = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    base = llm_provider.LLMClientAdapter(raw, llm_provider.LLMConfiguration(model="gpt-6-luna"))
    routed = base.with_model("gpt-6-nope", "medium")
    assert await routed.chat.completions.create(model="", messages=[]) == "ok"
    assert await routed.chat.completions.create(model="", messages=[]) == "ok"
    assert calls == ["gpt-6-nope", "gpt-6-luna", "gpt-6-luna"]  # remembered after the first miss


async def test_ask_answers_on_the_routed_model(monkeypatch) -> None:
    from core import llm_provider
    from core.models import QueryResponse, RetrievalResult
    from loombot import ask_agent

    seen: list = []

    async def fake_get_client(org_id):
        return SimpleNamespace(configuration=SimpleNamespace(provider="openai", model="gpt-6-luna"))

    async def fake_choose(client, question, history, **kwargs):
        return model_router.ModelChoice(tier="complex", model="gpt-6-sol", reasoning_effort="medium")

    async def fake_turn(**kwargs):
        seen.append(llm_provider._TURN_MODEL.get())
        return QueryResponse(answer="a", sources=[], confidence="high", routed=False)

    monkeypatch.setattr(ask_agent, "get_llm_client", fake_get_client)
    monkeypatch.setattr(model_router, "choose_model", fake_choose)
    monkeypatch.setattr(ask_agent, "_answer_turn", fake_turn)
    response = await ask_agent.run_ask_agent(
        question="q", retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u",
    )
    assert seen == [("gpt-6-sol", "medium")]
    assert llm_provider._TURN_MODEL.get() is None  # reset after the turn
    assert (response.model_used, response.model_tier) == ("gpt-6-sol", "complex")
