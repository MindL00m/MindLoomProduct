"""Skill-file execution: browser anchors on captures and the skill run record."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.routes import captures as capture_routes
from core.blob_storage import reset_blob_storage
from core.config import get_settings
from core.models import BrowserAnchor, CaptureCreate, GuideStep, SkillFileDraft, StepAnchor
from keeper.auth import require_user_context
from weaver import capture_service, skill_runs

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
CLICK = StepAnchor(bundleId="com.vendor.app", action="click", role="AXButton", title="Submit")
SAVE = {
    "url": "https://app.example.com/invoices/new?draft=1#top",
    "role": "button",
    "name": "Save",
    "selector": "#save",
}


@pytest.fixture(autouse=True)
def _memory(tmp_path, monkeypatch):
    capture_service.use_memory_store(True)
    skill_runs.use_memory_store(True)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("BLOB_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOB_STORAGE_ROOT", str(tmp_path / "blobs"))
    monkeypatch.setenv("CAPTURE_STORAGE_ROOT", str(tmp_path / "captures"))
    get_settings.cache_clear()
    reset_blob_storage()
    yield
    capture_service.use_memory_store(False)
    skill_runs.use_memory_store(False)
    reset_blob_storage()
    get_settings.cache_clear()


@pytest.fixture
def caller():
    """Mutable (org_id, user_id) the test client authenticates as."""

    return {"ctx": ("org-a", "user-a")}


@pytest.fixture
def client(caller):
    app = FastAPI()
    app.include_router(capture_routes.router)

    async def as_caller() -> tuple[str, str]:
        return caller["ctx"]

    app.dependency_overrides[require_user_context] = as_caller
    return TestClient(app)


async def _skill(
    skill_id: str,
    *,
    status: str = "approved",
    created_by: str = "user-a",
    visibility: str = "private",
    guide_steps: list[GuideStep] | None = None,
) -> None:
    steps = guide_steps or [
        GuideStep(text="Click Submit", anchor=CLICK),
        GuideStep(text="Check the total"),
    ]
    await capture_service._persist_skill(
        SkillFileDraft(
            skill_id=skill_id,
            session_id=f"session-{skill_id}",
            title="Submit invoice",
            purpose="",
            application="Vendor",
            steps=[step.text for step in steps],
            guide_steps=steps,
            status=status,
            visibility=visibility,
            created_at=NOW,
            updated_at=NOW,
            org_id="org-a",
            created_by=created_by,
        )
    )


def _start(client: TestClient, skill_id: str) -> dict:
    response = client.post("/captures/skill-runs", json={"skill_id": skill_id})
    assert response.status_code == 201, response.text
    return response.json()


# --- Browser anchors -----------------------------------------------------------


def test_browser_anchor_keeps_origin_and_path_only():
    anchor = BrowserAnchor.model_validate(
        {**SAVE, "href": "https://app.example.com/invoices/42?tab=lines#x", "needsValue": True}
    )
    assert anchor.url == "https://app.example.com/invoices/new"
    assert anchor.href == "/invoices/42"
    assert anchor.needs_value is True


def test_guide_step_holds_at_most_one_anchor():
    with pytest.raises(ValidationError):
        GuideStep(text="Both", anchor=CLICK, browser_anchor=BrowserAnchor.model_validate(SAVE))


def test_browser_guide_wire_format():
    skill = SkillFileDraft(
        skill_id="s1",
        session_id="session-1",
        title="T",
        purpose="",
        application="Web",
        guide_steps=[
            GuideStep(
                text="Fill in Amount",
                browser_anchor=BrowserAnchor(url="https://a.test/x", role="textbox", name="Amount", needs_value=True),
            )
        ],
        status="approved",
        created_at=NOW,
        updated_at=NOW,
    )
    wire = capture_service.guide_payload(skill).model_dump(mode="json", by_alias=True)
    step = wire["steps"][0]
    assert step["anchor"] is None
    assert step["browser_anchor"]["needsValue"] is True
    assert step["browser_anchor"]["name"] == "Amount"


async def test_capture_stores_browser_anchor():
    await capture_service.save_capture(
        CaptureCreate.model_validate(
            {"id": "c1", "timestamp": 1, "dataUrl": PNG_DATA_URL, "browserAnchor": SAVE}
        ),
        org_id="org-a",
        user_id="user-a",
    )
    [row] = await capture_service.list_captures(org_id="org-a")
    assert row["browser_anchor"]["url"] == "https://app.example.com/invoices/new"
    assert row["browser_anchor"]["selector"] == "#save"


def _fake_openai(monkeypatch, payload: dict) -> list[dict]:
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps(payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class Client:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr(capture_service, "AsyncOpenAI", Client)
    return calls


async def test_browser_analyze_attaches_only_recorded_anchors(monkeypatch):
    for index, anchor in enumerate([None, SAVE], 1):
        body = {"id": f"c{index}", "timestamp": index, "dataUrl": PNG_DATA_URL, "sessionId": "s"}
        if anchor:
            body["browserAnchor"] = anchor
        await capture_service.save_capture(
            CaptureCreate.model_validate(body), org_id="org-a", user_id="user-a"
        )
    calls = _fake_openai(
        monkeypatch,
        {
            "title": "Save invoice",
            "steps": ["Open invoices", "Click Save", "Email the customer"],
            "step_anchor_ids": [None, "b1", "b7"],
        },
    )

    draft = await capture_service.analyze_capture_session("s", org_id="org-a")

    prompt_text = " ".join(
        part["text"] for part in calls[0]["messages"][1]["content"] if part["type"] == "text"
    )
    assert '[b1]: Click button "Save" on https://app.example.com/invoices/new' in prompt_text
    assert draft.steps == ["Open invoices", "Click Save", "Email the customer"]
    assert [step.browser_anchor is not None for step in draft.guide_steps] == [False, True, False]
    assert draft.guide_steps[1].browser_anchor.selector == "#save"
    assert all(step.anchor is None for step in draft.guide_steps)


def test_review_edit_keeps_browser_anchor_for_unchanged_step():
    save = BrowserAnchor.model_validate(SAVE)
    guide = capture_service.realign_guide_steps(
        [GuideStep(text="Open invoices"), GuideStep(text="Click Save", browser_anchor=save)],
        ["Open the invoices page", "Click Save"],
    )
    assert guide[0].browser_anchor is None
    assert guide[1].browser_anchor == save


# --- Run record ----------------------------------------------------------------


async def test_create_rejects_proposed_skill(client):
    await _skill("proposed", status="proposed")
    response = client.post("/captures/skill-runs", json={"skill_id": "proposed"})
    assert response.status_code == 400
    assert client.get("/captures/skill-runs").json() == []


async def test_create_rejects_skill_caller_cannot_view(client, caller):
    await _skill("private", created_by="user-b")
    response = client.post("/captures/skill-runs", json={"skill_id": "private"})
    assert response.status_code == 404
    assert client.post("/captures/skill-runs", json={"skill_id": "missing"}).status_code == 404

    caller["ctx"] = ("org-b", "user-b")
    assert client.post("/captures/skill-runs", json={"skill_id": "private"}).status_code == 404


async def test_org_skill_runs_as_the_caller(client, caller):
    await _skill("shared", created_by="user-b", visibility="organization")
    run = _start(client, "shared")
    assert (run["status"], run["step_index"], run["user_id"]) == ("pending", 0, "user-a")

    caller["ctx"] = ("org-a", "user-b")
    assert client.get(f"/captures/skill-runs/{run['run_id']}").status_code == 404
    assert client.get("/captures/skill-runs?status=pending").json() == []
    assert client.post(f"/captures/skill-runs/{run['run_id']}/claim").status_code == 404


async def test_pending_poll_claim_and_detail(client):
    await _skill("sk")
    run = _start(client, "sk")
    pending = client.get("/captures/skill-runs?status=pending").json()
    assert [item["run_id"] for item in pending] == [run["run_id"]]

    claimed = client.post(f"/captures/skill-runs/{run['run_id']}/claim")
    assert claimed.status_code == 200 and claimed.json()["status"] == "running"
    assert client.post(f"/captures/skill-runs/{run['run_id']}/claim").status_code == 409
    assert client.get("/captures/skill-runs?status=pending").json() == []

    detail = client.get(f"/captures/skill-runs/{run['run_id']}").json()
    assert detail["guide"]["steps"][0]["anchor"]["bundleId"] == "com.vendor.app"
    assert detail["guide"]["steps"][1]["anchor"] is None


async def test_advance_rejects_index_that_is_not_current(client):
    await _skill("sk")
    run_id = _start(client, "sk")["run_id"]
    advance = f"/captures/skill-runs/{run_id}/advance"

    assert client.post(advance, json={"step_index": 0, "outcome": "done"}).status_code == 409
    client.post(f"/captures/skill-runs/{run_id}/claim")
    assert client.post(advance, json={"step_index": 1, "outcome": "done"}).status_code == 409
    assert client.post(advance, json={"step_index": 0, "outcome": "done"}).status_code == 200
    # A repeat report for the step that just finished is stale.
    assert client.post(advance, json={"step_index": 0, "outcome": "done"}).status_code == 409


async def test_waiting_then_done_on_last_step_completes(client):
    await _skill("sk")
    run_id = _start(client, "sk")["run_id"]
    advance = f"/captures/skill-runs/{run_id}/advance"
    client.post(f"/captures/skill-runs/{run_id}/claim")

    assert client.post(advance, json={"step_index": 0, "outcome": "done"}).json()["status"] == "running"
    waiting = client.post(
        advance, json={"step_index": 1, "outcome": "waiting", "wait_reason": "no_anchor"}
    ).json()
    assert (waiting["status"], waiting["wait_reason"]) == ("waiting", "no_anchor")

    done = client.post(advance, json={"step_index": 1, "outcome": "done"}).json()
    assert (done["status"], done["step_index"], done["wait_reason"]) == ("completed", 2, None)
    assert client.post(advance, json={"step_index": 2, "outcome": "done"}).status_code == 409


async def test_advance_validates_outcome_fields(client):
    await _skill("sk")
    run_id = _start(client, "sk")["run_id"]
    client.post(f"/captures/skill-runs/{run_id}/claim")
    advance = f"/captures/skill-runs/{run_id}/advance"
    assert client.post(advance, json={"step_index": 0, "outcome": "waiting"}).status_code == 422
    assert client.post(advance, json={"step_index": 0, "outcome": "failed"}).status_code == 422
    assert (
        client.post(
            advance, json={"step_index": 0, "outcome": "waiting", "wait_reason": "typing"}
        ).status_code
        == 422
    )
    failed = client.post(
        advance, json={"step_index": 0, "outcome": "failed", "error": "Guide did not load"}
    ).json()
    assert (failed["status"], failed["error"]) == ("failed", "Guide did not load")


@pytest.mark.parametrize("wait", [False, True])
async def test_stop_from_running_and_waiting(client, wait):
    await _skill("sk")
    run_id = _start(client, "sk")["run_id"]
    client.post(f"/captures/skill-runs/{run_id}/claim")
    if wait:
        client.post(
            f"/captures/skill-runs/{run_id}/advance",
            json={"step_index": 0, "outcome": "waiting", "wait_reason": "action_failed"},
        )

    stopped = client.post(f"/captures/skill-runs/{run_id}/stop")
    assert stopped.status_code == 200
    assert (stopped.json()["status"], stopped.json()["wait_reason"]) == ("stopped", None)
    # Nothing moves a stopped run again.
    assert client.post(f"/captures/skill-runs/{run_id}/stop").json()["status"] == "stopped"
    assert (
        client.post(
            f"/captures/skill-runs/{run_id}/advance", json={"step_index": 0, "outcome": "done"}
        ).status_code
        == 409
    )
    assert client.post(f"/captures/skill-runs/{run_id}/claim").status_code == 409


async def test_stop_after_completion_conflicts(client):
    await _skill("one", guide_steps=[GuideStep(text="Only step")])
    run_id = _start(client, "one")["run_id"]
    client.post(f"/captures/skill-runs/{run_id}/claim")
    client.post(f"/captures/skill-runs/{run_id}/advance", json={"step_index": 0, "outcome": "done"})
    assert client.post(f"/captures/skill-runs/{run_id}/stop").status_code == 409


async def test_run_fails_when_skill_is_no_longer_approved(client):
    await _skill("sk")
    run_id = _start(client, "sk")["run_id"]
    client.post(f"/captures/skill-runs/{run_id}/claim")
    await _skill("sk", status="rejected")

    detail = client.get(f"/captures/skill-runs/{run_id}").json()
    assert detail["guide"] is None
    response = client.post(
        f"/captures/skill-runs/{run_id}/advance", json={"step_index": 0, "outcome": "done"}
    )
    assert response.status_code == 409
    assert client.get(f"/captures/skill-runs/{run_id}").json()["status"] == "failed"
