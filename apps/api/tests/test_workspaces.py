"""Workspace helpers and Loombot mention parsing."""

from __future__ import annotations

from workspaces import extract_loombot_question


def test_extract_loombot_question_detects_mention() -> None:
    assert extract_loombot_question("@Loombot what is our PTO policy?") == "what is our PTO policy?"
    assert extract_loombot_question("Hey @loombot, who owns payroll?") == "Hey, who owns payroll?"


def test_extract_loombot_question_ignores_plain_text() -> None:
    assert extract_loombot_question("Loombot should review this") is None
    assert extract_loombot_question("email loombot@example.com") is None


def test_extract_loombot_question_alone_uses_default() -> None:
    assert extract_loombot_question("@Loombot") == "What should the team know right now?"
