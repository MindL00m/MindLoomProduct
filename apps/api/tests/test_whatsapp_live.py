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


@pytest.mark.parametrize(("raw", "expected"), [
    ("+919401264052", "+919401264052"),
    ("919401264052@s.whatsapp.net", "+919401264052"),
    ("919401264052:12@s.whatsapp.net", "+919401264052"),
    ("whatsapp:+44 7700 900123", "+447700900123"),
    ("+1 (415) 555-2671", "+14155552671"),
    ("4915112345678@c.us", "+4915112345678"),
    ("971501234567", "+971501234567"),
    ("120363001@G.US", "120363001@g.us"),
    ("98765432109876@lid", "98765432109876@lid"),
    ("", ""),
])
def test_numbers_from_any_country_normalise_without_assuming_one(raw, expected) -> None:
    from weaver.whatsapp_live import normalize_whatsapp_id

    assert normalize_whatsapp_id(raw) == expected


def test_the_same_person_is_one_chat_however_their_number_arrives() -> None:
    a = normalize_message(LiveMessage(text="hi", sender_id="447700900123@s.whatsapp.net"), NOW)
    b = normalize_message(LiveMessage(text="yo", sender_id="+44 7700 900123", sender_name="Tom"), NOW)
    assert a["chat_id"] == b["chat_id"] == "+447700900123"
    # No saved contact name: the number stands in for it.
    assert a["sender_name"] == "+447700900123"


def test_a_group_is_its_own_chat_and_is_not_named_after_a_member() -> None:
    row = normalize_message(LiveMessage(
        text="Docs are signed.", chat_id="120363001234@g.us", is_group=True,
        sender_id="+5511987654321", sender_name="Ana",
    ), NOW)
    assert (row["chat_id"], row["chat_name"]) == ("120363001234@g.us", "WhatsApp group 1234")
    assert (row["sender_id"], row["sender_name"]) == ("+5511987654321", "Ana")
    named = normalize_message(LiveMessage(
        text="x", chat_id="120363001234@g.us", chat_name="Ops team", sender_id="+5511987654321",
    ), NOW)
    assert named["chat_name"] == "Ops team"


def test_status_updates_and_channels_are_not_chats() -> None:
    for chat in ("status@broadcast", "12036300@newsletter"):
        assert normalize_message(LiveMessage(text="x", chat_id=chat, sender_id="+65"), NOW) is None
