"""Tests for the browser capture functionality merged into the MindLoom API."""

from datetime import datetime, timezone

import pytest

from weaver import capture_service
from core.blob_storage import LocalBlobStorage, reset_blob_storage
from core.config import get_settings
from core.models import ActivitySessionCreate, ActivityTaskSummary, CaptureCreate

PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def _memory_and_blob(tmp_path, monkeypatch):
    capture_service.use_memory_store(True)
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
    reset_blob_storage()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_save_and_list_capture():
    record = await capture_service.save_capture(
        CaptureCreate(
            id="capture-1",
            timestamp=123,
            dataUrl=PNG_DATA_URL,
            url="https://example.com",
            tabTitle="Example",
            windowId=7,
            orgId="spoof-org",
            userId="spoof-user",
        ),
        org_id="org-a",
        user_id="user-a",
    )

    assert record.id == "capture-1"
    assert record.tab_title == "Example"
    assert record.org_id == "org-a"
    assert record.user_id == "user-a"
    assert record.filepath.startswith("file://")
    assert len(await capture_service.list_captures(org_id="org-a")) == 1
    assert await capture_service.list_captures(org_id="org-b") == []

    # Image readable from blob backend
    storage = LocalBlobStorage(get_settings().blob_storage_root)
    data = await storage.get(record.filepath)
    assert data.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_rejects_invalid_capture_data():
    with pytest.raises(ValueError, match="Invalid base64"):
        await capture_service.save_capture(
            CaptureCreate(id="bad", timestamp=123, dataUrl="not-an-image"),
            org_id="org-a",
            user_id="user-a",
        )


@pytest.mark.asyncio
async def test_save_and_list_activity_session():
    started = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc)
    record = await capture_service.save_activity_session(
        ActivitySessionCreate(
            sessionId="session-desktop-1",
            orgId="spoof-org",
            userId="spoof-user",
            startedAt=started,
            endedAt=ended,
            tasks=[
                ActivityTaskSummary(
                    taskId="task-1",
                    startedAt=started,
                    endedAt=ended,
                    primaryApp="Notes",
                    apps=["Notes"],
                    stepHints=["Focus Notes", "Click New Note"],
                    fieldInteractions=[
                        {"role": "AXTextArea", "label": "Note body", "durationMs": 1200}
                    ],
                    stats={"eventCount": 4, "activeMs": 300000},
                )
            ],
            note="Create a note",
        ),
        org_id="org-a",
        user_id="user-a",
    )

    assert record.session_id == "session-desktop-1"
    assert record.org_id == "org-a"
    assert record.user_id == "user-a"
    assert record.source == "desktop_ax"
    assert len(record.tasks) == 1
    listed = await capture_service.list_activity_sessions(org_id="org-a")
    assert len(listed) == 1
    assert listed[0]["sessionId"] == "session-desktop-1"
    assert await capture_service.list_activity_sessions(org_id="org-b") == []


@pytest.mark.asyncio
async def test_activity_analyze_falls_back_without_openai(monkeypatch):
    started = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc)
    await capture_service.save_activity_session(
        ActivitySessionCreate(
            sessionId="session-desktop-fallback",
            startedAt=started,
            endedAt=ended,
            tasks=[
                ActivityTaskSummary(
                    taskId="task-1",
                    startedAt=started,
                    endedAt=ended,
                    primaryApp="Notes",
                    apps=["Notes"],
                    stepHints=["Focus Notes", "Click New Note"],
                    fieldInteractions=[
                        {"role": "AXTextArea", "label": "Note body", "durationMs": 1200}
                    ],
                    stats={"eventCount": 4, "activeMs": 300000},
                )
            ],
        ),
        org_id="org-a",
        user_id="user-a",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()

    draft = await capture_service.analyze_activity_session(
        "session-desktop-fallback", org_id="org-a"
    )
    assert draft.source == "desktop_ax"
    assert draft.status == "proposed"
    assert draft.application == "Notes"
    assert "Focus Notes" in draft.steps
    skills = await capture_service.list_skill_files(
        org_id="org-a", viewer_user_id="user-a"
    )
    assert any(row["session_id"] == "session-desktop-fallback" for row in skills)

    # Idempotent: second call returns the existing skill.
    again = await capture_service.analyze_activity_session(
        "session-desktop-fallback", org_id="org-a"
    )
    assert again.skill_id == draft.skill_id


@pytest.mark.asyncio
async def test_approve_skill_publishes_to_knowledge_graph(monkeypatch):
    started = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc)
    await capture_service.save_activity_session(
        ActivitySessionCreate(
            sessionId="session-desktop-publish",
            startedAt=started,
            endedAt=ended,
            tasks=[
                ActivityTaskSummary(
                    taskId="task-1",
                    startedAt=started,
                    endedAt=ended,
                    primaryApp="Notes",
                    apps=["Notes"],
                    stepHints=["Focus Notes", "Click New Note"],
                    stats={"eventCount": 2, "activeMs": 1000},
                )
            ],
        ),
        org_id="org-a",
        user_id="user-a",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    draft = await capture_service.analyze_activity_session(
        "session-desktop-publish", org_id="org-a"
    )

    published: dict[str, object] = {}

    async def fake_publish(skill):
        published["skill_id"] = skill.skill_id
        published["title"] = skill.title
        published["body"] = capture_service.format_skill_for_knowledge_graph(skill)

    monkeypatch.setattr(
        capture_service, "publish_skill_to_knowledge_graph", fake_publish
    )

    from core.models import SkillFileReview

    approved = await capture_service.review_skill_file(
        draft.skill_id,
        SkillFileReview(status="approved", title=draft.title),
        org_id="org-a",
        actor_user_id="user-a",
    )
    assert approved.status == "approved"
    assert published["skill_id"] == draft.skill_id
    body = str(published["body"])
    assert "Workflow:" in body
    assert "Focus Notes" in body
    assert "Desktop capture" in body


@pytest.mark.asyncio
async def test_approve_skill_keeps_proposed_when_graph_publish_fails(monkeypatch):
    started = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc)
    await capture_service.save_activity_session(
        ActivitySessionCreate(
            sessionId="session-desktop-fail-publish",
            startedAt=started,
            endedAt=ended,
            tasks=[
                ActivityTaskSummary(
                    taskId="task-1",
                    startedAt=started,
                    endedAt=ended,
                    primaryApp="Notes",
                    apps=["Notes"],
                    stepHints=["Focus Notes"],
                    stats={"eventCount": 1, "activeMs": 500},
                )
            ],
        ),
        org_id="org-a",
        user_id="user-a",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    draft = await capture_service.analyze_activity_session(
        "session-desktop-fail-publish", org_id="org-a"
    )

    async def boom(_skill):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(capture_service, "publish_skill_to_knowledge_graph", boom)

    from core.models import SkillFileReview

    with pytest.raises(ValueError, match="knowledge graph"):
        await capture_service.review_skill_file(
            draft.skill_id,
            SkillFileReview(status="approved"),
            org_id="org-a",
            actor_user_id="user-a",
        )
    still = next(
        row
        for row in await capture_service.list_skill_files(
            org_id="org-a", viewer_user_id="user-a"
        )
        if row["skill_id"] == draft.skill_id
    )
    assert still["status"] == "proposed"


@pytest.mark.asyncio
async def test_skill_visibility_list_and_acl(monkeypatch):
    started = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc)
    await capture_service.save_activity_session(
        ActivitySessionCreate(
            sessionId="session-visibility",
            startedAt=started,
            endedAt=ended,
            tasks=[
                ActivityTaskSummary(
                    taskId="task-1",
                    startedAt=started,
                    endedAt=ended,
                    primaryApp="Notes",
                    apps=["Notes"],
                    stepHints=["Focus Notes"],
                    stats={"eventCount": 1, "activeMs": 500},
                )
            ],
        ),
        org_id="org-a",
        user_id="user-a",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    draft = await capture_service.analyze_activity_session(
        "session-visibility", org_id="org-a"
    )
    assert draft.visibility == "private"
    assert draft.created_by == "user-a"

    mine = await capture_service.list_skill_files(
        org_id="org-a", viewer_user_id="user-a"
    )
    other = await capture_service.list_skill_files(
        org_id="org-a", viewer_user_id="user-b"
    )
    assert any(row["skill_id"] == draft.skill_id for row in mine)
    assert not any(row["skill_id"] == draft.skill_id for row in other)

    from core.models import SkillFileReview, SkillFileUpdate

    # Private skills are hidden from other users (404, not a visibility leak).
    with pytest.raises(ValueError, match="not found"):
        await capture_service.update_skill_file(
            draft.skill_id,
            SkillFileUpdate(visibility="organization"),
            org_id="org-a",
            actor_user_id="user-b",
        )

    shared = await capture_service.update_skill_file(
        draft.skill_id,
        SkillFileUpdate(visibility="organization"),
        org_id="org-a",
        actor_user_id="user-a",
    )
    assert shared.visibility == "organization"
    other_after = await capture_service.list_skill_files(
        org_id="org-a", viewer_user_id="user-b"
    )
    assert any(row["skill_id"] == draft.skill_id for row in other_after)

    # Org-visible skills are listed to peers, but only the creator can change visibility.
    with pytest.raises(capture_service.SkillAccessError, match="visibility"):
        await capture_service.update_skill_file(
            draft.skill_id,
            SkillFileUpdate(visibility="private"),
            org_id="org-a",
            actor_user_id="user-b",
        )

    published: dict[str, object] = {}

    async def fake_publish(skill):
        from keeper.visibility_acl import skill_acl_tokens

        published["visibility"] = skill.visibility
        published["tokens"] = skill_acl_tokens(skill)

    monkeypatch.setattr(
        capture_service, "publish_skill_to_knowledge_graph", fake_publish
    )

    approved = await capture_service.review_skill_file(
        draft.skill_id,
        SkillFileReview(status="approved"),
        org_id="org-a",
        actor_user_id="user-b",
    )
    assert approved.status == "approved"
    assert published["tokens"] == ["org:org-a"]

    # Creator can switch approved skill back to private and re-publish ACL.
    published.clear()
    private_again = await capture_service.update_skill_file(
        draft.skill_id,
        SkillFileUpdate(visibility="private"),
        org_id="org-a",
        actor_user_id="user-a",
    )
    assert private_again.visibility == "private"
    assert published["tokens"] == ["user:user-a"]


@pytest.mark.asyncio
async def test_legacy_skill_without_visibility_is_organization():
    from core.models import SkillFileDraft

    now = datetime.now(timezone.utc)
    draft = SkillFileDraft(
        skill_id="legacy-1",
        session_id="legacy-session",
        title="Legacy",
        purpose="old",
        application="Notes",
        created_at=now,
        updated_at=now,
        org_id="org-a",
        created_by="user-a",
    )
    payload = draft.model_dump(mode="json")
    del payload["visibility"]
    capture_service._memory["skills"][draft.skill_id] = payload  # type: ignore[index]

    rows = await capture_service.list_skill_files(
        org_id="org-a", viewer_user_id="user-b"
    )
    assert any(row["skill_id"] == "legacy-1" for row in rows)
    legacy = next(row for row in rows if row["skill_id"] == "legacy-1")
    assert legacy["visibility"] == "organization"


@pytest.mark.asyncio
async def test_rejects_empty_activity_session():
    started = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="At least one task"):
        await capture_service.save_activity_session(
            ActivitySessionCreate(
                sessionId="empty",
                startedAt=started,
                endedAt=started,
                tasks=[],
            ),
            org_id="org-a",
            user_id="user-a",
        )
