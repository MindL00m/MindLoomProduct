"""Desktop guided walkthroughs: anchors on skills and the guides listing."""

from datetime import datetime, timezone

import pytest

from weaver import capture_service
from core.blob_storage import reset_blob_storage
from core.config import get_settings
from core.models import (
    ActivitySessionCreate,
    ActivityTaskSummary,
    GuideStep,
    SkillFileDraft,
    SkillFileReview,
    StepAnchor,
)

STARTED = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
ENDED = datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc)

SUBMIT = {"bundleId": "com.vendor.app", "action": "click", "role": "AXButton", "title": "Submit"}
AMOUNT = {
    "bundleId": "com.vendor.app",
    "action": "field_focus",
    "role": "AXTextField",
    "title": "Amount",
}


@pytest.fixture(autouse=True)
def _memory(tmp_path, monkeypatch):
    capture_service.use_memory_store(True)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("BLOB_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOB_STORAGE_ROOT", str(tmp_path / "blobs"))
    monkeypatch.setenv("CAPTURE_STORAGE_ROOT", str(tmp_path / "captures"))
    get_settings.cache_clear()
    reset_blob_storage()

    async def fake_publish(_skill):
        return None

    monkeypatch.setattr(capture_service, "publish_skill_to_knowledge_graph", fake_publish)
    yield
    capture_service.use_memory_store(False)
    reset_blob_storage()
    get_settings.cache_clear()


async def _save_session(session_id: str, anchors: list[dict]) -> None:
    await capture_service.save_activity_session(
        ActivitySessionCreate(
            sessionId=session_id,
            startedAt=STARTED,
            endedAt=ENDED,
            tasks=[
                ActivityTaskSummary(
                    taskId="task-1",
                    startedAt=STARTED,
                    endedAt=ENDED,
                    primaryApp="Vendor",
                    apps=["Vendor"],
                    bundleIds=["com.vendor.app"],
                    stepHints=["Focus field Amount [Vendor]", "Click Submit [Vendor]"],
                    anchors=anchors,
                    stats={"eventCount": 3, "activeMs": 1000},
                )
            ],
        ),
        org_id="org-a",
        user_id="user-a",
    )


def test_resolve_guide_steps_maps_ids_and_drops_unknown():
    anchors = [StepAnchor.model_validate(AMOUNT), StepAnchor.model_validate(SUBMIT)]
    steps = ["Enter the amount", "Review totals", "Submit", "Extra"]
    guide = capture_service.resolve_guide_steps(steps, ["a1", None, "[a2]", "a9"], anchors)
    assert [g.text for g in guide] == steps
    assert guide[0].anchor and guide[0].anchor.title == "Amount"
    assert guide[1].anchor is None
    assert guide[2].anchor and guide[2].anchor.title == "Submit"
    assert guide[3].anchor is None


def test_prompt_lists_anchor_ids():
    from core.models import ActivitySessionRecord

    record = ActivitySessionRecord(
        sessionId="s",
        startedAt=STARTED,
        endedAt=ENDED,
        receivedAt=ENDED,
        tasks=[
            ActivityTaskSummary(
                taskId="t",
                startedAt=STARTED,
                endedAt=ENDED,
                anchors=[AMOUNT, SUBMIT, SUBMIT],
            )
        ],
    )
    prompt = capture_service._format_activity_session_for_prompt(record)
    assert '[a1] Fill in "Amount" in com.vendor.app' in prompt
    assert '[a2] Click "Submit" in com.vendor.app' in prompt
    assert "[a3]" not in prompt


@pytest.mark.asyncio
async def test_heuristic_draft_maps_anchors_one_to_one():
    await _save_session("session-guide-1", [AMOUNT, SUBMIT])
    draft = await capture_service.analyze_activity_session("session-guide-1", org_id="org-a")
    assert draft.bundle_ids == ["com.vendor.app"]
    assert [g.text for g in draft.guide_steps] == ['Fill in "Amount"', 'Click "Submit"']
    assert all(g.anchor is not None for g in draft.guide_steps)
    assert draft.steps == [g.text for g in draft.guide_steps]


@pytest.mark.asyncio
async def test_review_edit_keeps_anchors_only_for_unchanged_steps():
    await _save_session("session-guide-2", [AMOUNT, SUBMIT])
    draft = await capture_service.analyze_activity_session("session-guide-2", org_id="org-a")
    approved = await capture_service.review_skill_file(
        draft.skill_id,
        SkillFileReview(
            status="approved",
            steps=['Fill in "Amount"', "Double-check the vendor", 'Click "Submit"'],
        ),
        org_id="org-a",
        actor_user_id="user-a",
    )
    anchors = [g.anchor for g in approved.guide_steps]
    assert anchors[0] is not None and anchors[0].title == "Amount"
    assert anchors[1] is None
    assert anchors[2] is not None and anchors[2].title == "Submit"


@pytest.mark.asyncio
async def test_list_guides_filters_status_visibility_and_bundle():
    await _save_session("session-guide-3", [SUBMIT])
    draft = await capture_service.analyze_activity_session("session-guide-3", org_id="org-a")

    assert await capture_service.list_guides(org_id="org-a", viewer_user_id="user-a") == []

    await capture_service.review_skill_file(
        draft.skill_id,
        SkillFileReview(status="approved"),
        org_id="org-a",
        actor_user_id="user-a",
    )
    mine = await capture_service.list_guides(
        org_id="org-a", viewer_user_id="user-a", bundle_id="com.vendor.app"
    )
    assert [g.skill_id for g in mine] == [draft.skill_id]
    assert mine[0].steps[0].anchor is not None
    assert await capture_service.list_guides(
        org_id="org-a", viewer_user_id="user-a", bundle_id="com.other.app"
    ) == []
    # Private by default: other users in the org can't see it.
    assert await capture_service.list_guides(org_id="org-a", viewer_user_id="user-b") == []
    assert await capture_service.list_guides(org_id="org-b", viewer_user_id="user-a") == []

    with pytest.raises(ValueError):
        await capture_service.get_guide(draft.skill_id, org_id="org-a", viewer_user_id="user-b")
    fetched = await capture_service.get_guide(
        draft.skill_id, org_id="org-a", viewer_user_id="user-a"
    )
    assert fetched.title == draft.title


def test_guide_wire_format_matches_desktop_decoder():
    """FastAPI serializes by alias: snake_case guide keys, camelCase anchors."""

    skill = SkillFileDraft(
        skill_id="s1",
        session_id="session-1",
        title="T",
        purpose="",
        application="Vendor",
        bundle_ids=["com.vendor.app"],
        guide_steps=[GuideStep(text="Submit", anchor=StepAnchor.model_validate(SUBMIT))],
        status="approved",
        created_at=ENDED,
        updated_at=ENDED,
    )
    wire = capture_service.guide_payload(skill).model_dump(mode="json", by_alias=True)
    assert {"skill_id", "bundle_ids", "important_fields", "steps"} <= wire.keys()
    assert wire["steps"][0]["anchor"]["bundleId"] == "com.vendor.app"
    assert "windowTitle" in wire["steps"][0]["anchor"]


def test_legacy_skill_becomes_manual_guide():
    skill = SkillFileDraft(
        skill_id="legacy",
        session_id="expert-request:1",
        title="Close the books",
        purpose="",
        application="Company Brain",
        steps=["Export ledger", "Reconcile"],
        status="approved",
        created_at=ENDED,
        updated_at=ENDED,
    )
    guide = capture_service.guide_payload(skill)
    assert guide.steps == [GuideStep(text="Export ledger"), GuideStep(text="Reconcile")]
    assert guide.bundle_ids == []
