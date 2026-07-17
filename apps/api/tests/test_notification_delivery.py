"""Outbound expert notification delivery behavior."""

import pytest

import notification_delivery


@pytest.mark.asyncio
async def test_delivery_attempts_every_channel_when_one_fails(monkeypatch):
    recorded: list[tuple[str, str]] = []

    async def gmail(_org, _recipient, _question):
        return "gmail-id"

    async def outlook(_org, _recipient, _question):
        raise RuntimeError("outlook unavailable")

    async def teams(_org, _recipient, _question):
        return "teams-id"

    async def record(**kwargs):
        recorded.append((kwargs["channel"], kwargs["status"]))

    monkeypatch.setattr(notification_delivery, "_send_gmail", gmail)
    monkeypatch.setattr(notification_delivery, "_send_outlook", outlook)
    monkeypatch.setattr(notification_delivery, "_send_teams", teams)
    monkeypatch.setattr(notification_delivery, "_record", record)

    outcomes = await notification_delivery.deliver_expert_request(
        org_id="org", review_id="review", recipient="expert@example.com",
        question="What is the escalation policy?",
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
