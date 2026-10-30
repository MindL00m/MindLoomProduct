"""Outbound expert notification delivery behavior."""

import pytest

from shuttle import notification_delivery


@pytest.mark.asyncio
async def test_delivery_attempts_every_channel_when_one_fails(monkeypatch):
    recorded: list[tuple[str, str]] = []

    async def gmail(_org, _recipient, _question, sender):
        assert sender == "asker"
        return "gmail-id"

    async def outlook(_org, _recipient, _question, sender):
        raise RuntimeError("outlook unavailable")

    async def teams(_org, _recipient, _question, sender):
        return "teams-id"

    async def record(**kwargs):
        recorded.append((kwargs["channel"], kwargs["status"]))

    monkeypatch.setattr(notification_delivery, "_send_gmail", gmail)
    monkeypatch.setattr(notification_delivery, "_send_outlook", outlook)
    monkeypatch.setattr(notification_delivery, "_send_teams", teams)
    monkeypatch.setattr(notification_delivery, "_record", record)

    outcomes = await notification_delivery.deliver_expert_request(
        org_id="org", review_id="review", recipient="expert@example.com",
        question="What is the escalation policy?", sender_user_id="asker",
    )

    assert outcomes == {
        "gmail": "delivered",
        "outlook": "failed",
        "teams": "delivered",
    }
    assert recorded == [
        ("gmail", "delivered"),
        ("outlook", "failed"),
        ("teams", "delivered"),
    ]


@pytest.mark.asyncio
async def test_without_the_askers_connection_nothing_is_sent_from_others(monkeypatch):
    async def record(**kwargs):
        return None

    monkeypatch.setattr(notification_delivery, "_record", record)
    outcomes = await notification_delivery.deliver_expert_request(
        org_id="org", review_id="r", recipient="expert@example.com", question="Q",
        sender_user_id=None,
    )
    assert outcomes == {"gmail": "failed", "outlook": "failed", "teams": "failed"}
