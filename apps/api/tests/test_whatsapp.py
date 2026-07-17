"""WhatsApp export connector tests."""

import pytest

from whatsapp import decode_export, parse_timezone, preview_export


EXPORT = """\
17/07/2026, 09:15 - Alice: Morning team
17/07/2026, 09:16 - Bob: The launch is approved.
This is the second line.
17/07/2026, 09:17 - Alice: <Media omitted>
17/07/2026, 09:18 - Alice added Charlie
"""


def test_preview_reports_only_searchable_messages():
    preview = preview_export(EXPORT.encode(), "Asia/Singapore")

    assert preview["message_count"] == 2
    assert preview["participants"] == ["Alice", "Bob"]
    assert preview["first_message_at"] == "2026-07-17T09:15:00+08:00"
    assert preview["sample_messages"][1]["text"].endswith("second line.")


def test_preview_rejects_export_without_messages():
    with pytest.raises(ValueError, match="No WhatsApp messages"):
        preview_export(b"17/07/2026, 09:18 - Alice added Bob", "UTC")


def test_utf16_export_and_invalid_timezone():
    assert "Alice" in decode_export(EXPORT.encode("utf-16"))
    with pytest.raises(ValueError, match="Unknown timezone"):
        parse_timezone("Not/A_Timezone")
