"""Workflows created from Ask: intent, drafting, and saving the Skill File."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.models import QueryResponse, RetrievalResult, SkillFileCreate
from loombot import ask_agent
from weaver import capture_service

INVOICE_PROMPT = (
    "Create a workflow to read invoices from ameyagarwal@mindloomai.org from my inbox, add a row "
    "for each to Invoices.csv in Files, including item, date, vendor name and amount, and send an "
    'email to Amey with subject "Invoice update!" and body "Your invoice email has been processed"'
)


@pytest.mark.parametrize("question", [
    INVOICE_PROMPT,
    "Set up an automation that emails Priya the sales numbers every Monday",
    "make a workflow for logging receipts",
    "Can you build a skill file for onboarding new suppliers?",
    "Automate adding new leads to Leads.csv",
])
def test_workflow_requests_are_detected(question: str) -> None:
    assert ask_agent._wants_workflow(question)


@pytest.mark.parametrize("question", [
    "How do I create a workflow in MindLoom?",
    "What workflows do we have for onboarding?",
    "Send an email to priya@acme.com about the launch",
    "Add a row to Invoices.csv for the Acme invoice",
])
def test_questions_and_one_off_tasks_are_not_workflows(question: str) -> None:
    assert not ask_agent._wants_workflow(question)


def _invoice_steps() -> list[str]:
    return [
        "Search the inbox for emails from ameyagarwal@mindloomai.org with invoices",
        "Read each invoice and note the item, date, vendor name and amount",
        "Read Invoices.csv and skip invoices it already lists",
        "Add one row per new invoice to Invoices.csv with columns Item, Date, Vendor, Amount",
        "Send an email to ameyagarwal@mindloomai.org with subject 'Invoice update!' and body "
        "'Your invoice email has been processed'",
    ]


async def test_ask_drafts_a_workflow_without_doing_the_task(monkeypatch) -> None:
    async def base_answer(*args, **kwargs):
        return QueryResponse(answer="base", sources=[], confidence="low", routed=False)

    async def connected(*args):
        return True

    async def no_files(org_id, user_id):
        return []

    def call(call_id, name, arguments):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))

    sent: list[dict] = []

    async def create(**kwargs):
        sent.append(kwargs)
        message = SimpleNamespace(content=None, tool_calls=[call("c1", "propose_workflow", {
            "title": "Log Amey's invoices", "purpose": "Record Amey's invoices and confirm.",
            "steps": _invoice_steps(),
            "decision_guidance": ["Send the email only when at least one new invoice was added."],
        })])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(
        configuration=SimpleNamespace(supports_tools=True),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "generate_answer", base_answer)
    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    monkeypatch.setattr(ask_agent, "_user_files", no_files)
    monkeypatch.setattr("weaver.integrations.has_google_workspace_connection", connected)

    response = await ask_agent.run_ask_agent(
        question=INVOICE_PROMPT,
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u",
    )

    offered = {t["function"]["name"] for t in sent[0]["tools"]}
    assert offered == ask_agent._WORKFLOW_TOOLS
    assert len(sent) == 1  # the draft ends the turn; no extra model round
    draft = response.proposed_workflow
    assert draft is not None and draft.title == "Log Amey's invoices"
    assert len(draft.steps) == 5 and draft.sends_email
    assert draft.interval_value is None and draft.interval_unit is None
    assert response.proposed_email is None and response.proposed_recurring_email is None
    assert "Create workflow" in response.answer


async def test_workflow_schedule_is_validated() -> None:
    outputs = ask_agent._TurnOutputs()
    result, *_ = await ask_agent._safe_run_tool(
        name="propose_workflow",
        arguments={"title": "x", "steps": ["a"], "interval_value": 5, "interval_unit": "minutes"},
        org_id="o", user_id="u", people_cache={}, outputs=outputs,
    )
    assert "15 minutes" in result["error"] and not outputs.workflows

    result, *_ = await ask_agent._safe_run_tool(
        name="propose_workflow",
        arguments={"title": "Daily", "steps": ["Read the file"], "interval_value": 1, "interval_unit": "days"},
        org_id="o", user_id="u", people_cache={}, outputs=outputs,
    )
    assert result["repeats"] == "every day"
    assert (outputs.workflows[0].interval_value, outputs.workflows[0].interval_unit) == (1, "days")
    assert not outputs.workflows[0].sends_email


async def test_ask_workflow_is_saved_approved_and_private(monkeypatch) -> None:
    published: list[str] = []

    async def publish(skill):
        published.append(skill.skill_id)

    capture_service.use_memory_store(True)
    monkeypatch.setattr(capture_service, "publish_skill_to_knowledge_graph", publish)
    try:
        skill = await capture_service.create_skill_file_from_ask(
            SkillFileCreate(title="  Log   invoices ", steps=["", *_invoice_steps()]),
            org_id="org-a", user_id="user-a",
        )
        assert (skill.source, skill.status, skill.visibility) == ("ask", "approved", "private")
        assert skill.title == "Log invoices" and len(skill.steps) == 5
        assert skill.created_by == "user-a" and skill.session_id.startswith("ask:")
        assert published == [skill.skill_id]
        visible = await capture_service.get_visible_skill(skill.skill_id, org_id="org-a", viewer_user_id="user-a")
        assert visible is not None
        assert await capture_service.get_visible_skill(skill.skill_id, org_id="org-a", viewer_user_id="user-b") is None
    finally:
        capture_service.use_memory_store(False)
