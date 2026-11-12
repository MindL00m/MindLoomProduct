"""Live WhatsApp messages forwarded by OpenClaw: normalising and rendering."""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from weaver.whatsapp import preview_export
from weaver.whatsapp_live import (
    LiveMessage,
    normalize_message,
    parse_timestamp,
    render_chat_day,
    require_link,
)

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)


def test_timestamps_accept_seconds_milliseconds_and_iso() -> None:
    expected = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
    assert parse_timestamp(expected.timestamp(), NOW) == expected
    assert parse_timestamp(expected.timestamp() * 1000, NOW) == expected
    assert parse_timestamp(str(int(expected.timestamp() * 1000)), NOW) == expected
    assert parse_timestamp("2026-11-12T16:00:00+08:00", NOW) == expected
    assert parse_timestamp("2026-11-12T08:00:00Z", NOW) == expected
    for missing in (None, "", "soon", -5):
        assert parse_timestamp(missing, NOW) == NOW


def test_a_direct_message_is_its_own_chat() -> None:
    row = normalize_message(
        LiveMessage(text="  Leaving Friday.  ", sender_id="+6591234567", sender_name="Priya"), NOW
    )
    assert (row["chat_id"], row["chat_name"], row["body"]) == ("+6591234567", "Priya", "Leaving Friday.")
    # Without an id from WhatsApp, the same message always gets the same id (no duplicates).
    again = normalize_message(
        LiveMessage(text="Leaving Friday.", sender_id="+6591234567", sender_name="Priya"), NOW
    )
    assert row["message_id"] == again["message_id"]


def test_messages_without_text_are_skipped() -> None:
    assert normalize_message(LiveMessage(text="   ", sender_id="+65"), NOW) is None


def test_a_chat_day_reads_as_a_whatsapp_export() -> None:
    rows = [
        {"sent_at": datetime(2026, 11, 12, 9, 15, tzinfo=timezone.utc), "sender_name": "Priya", "body": "Morning"},
        {"sent_at": datetime(2026, 11, 12, 9, 16, tzinfo=timezone.utc), "sender_name": "Sam", "body": "Launch approved.\nSecond line."},
    ]
    data = render_chat_day(rows)
    assert data.decode().splitlines()[0] == "12/11/2026, 09:15 - Priya: Morning"
    # The existing export parser understands it, multi-line bodies included.
    preview = preview_export(data, "UTC")
    assert preview["message_count"] == 2
    assert preview["participants"] == ["Priya", "Sam"]
    assert preview["sample_messages"][1]["text"].endswith("Second line.")


@pytest.mark.parametrize("header", [None, "Bearer", "Basic abc", "Bearer a-session-jwt"])
async def test_only_a_link_token_reaches_the_ingest_endpoint(header) -> None:
    headers = [(b"authorization", header.encode())] if header else []
    with pytest.raises(HTTPException) as exc:
        await require_link(Request({"type": "http", "headers": headers}))
    assert exc.value.status_code == 401
