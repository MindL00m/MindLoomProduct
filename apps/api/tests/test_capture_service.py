"""Tests for the browser capture functionality merged into the Loom API."""

from types import SimpleNamespace

import pytest

import capture_service
from models import CaptureCreate

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
