"""Tests for the browser capture functionality merged into the Loom API."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import capture_service
from models import ActivitySessionCreate, ActivityTaskSummary, CaptureCreate

PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_save_and_list_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(
        capture_service,
        "get_settings",
        lambda: SimpleNamespace(capture_storage_root=str(tmp_path)),
    )
    record = capture_service.save_capture(
        CaptureCreate(
            id="capture-1",
            timestamp=123,
            dataUrl=PNG_DATA_URL,
            url="https://example.com",
            tabTitle="Example",
            windowId=7,
        )
    )

    assert record.id == "capture-1"
    assert record.tab_title == "Example"
    assert len(capture_service.list_captures()) == 1


def test_rejects_invalid_capture_data(tmp_path, monkeypatch):
    monkeypatch.setattr(
        capture_service,
        "get_settings",
        lambda: SimpleNamespace(capture_storage_root=str(tmp_path)),
    )
    with pytest.raises(ValueError, match="Invalid base64"):
        capture_service.save_capture(
            CaptureCreate(id="bad", timestamp=123, dataUrl="not-an-image")
        )


def test_save_and_list_activity_session(tmp_path, monkeypatch):
    monkeypatch.setattr(
        capture_service,
        "get_settings",
        lambda: SimpleNamespace(capture_storage_root=str(tmp_path)),
    )
    started = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc)
    record = capture_service.save_activity_session(
        ActivitySessionCreate(
            sessionId="session-desktop-1",
            orgId="default",
            userId="desktop-user",
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
        )
    )

    assert record.session_id == "session-desktop-1"
    assert record.source == "desktop_ax"
    assert len(record.tasks) == 1
    listed = capture_service.list_activity_sessions()
    assert len(listed) == 1
    assert listed[0]["sessionId"] == "session-desktop-1"


def test_rejects_empty_activity_session(tmp_path, monkeypatch):
    monkeypatch.setattr(
        capture_service,
        "get_settings",
        lambda: SimpleNamespace(capture_storage_root=str(tmp_path)),
    )
    started = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="At least one task"):
        capture_service.save_activity_session(
            ActivitySessionCreate(
                sessionId="empty",
                startedAt=started,
                endedAt=started,
                tasks=[],
            )
        )
