"""Skill automation: tools, approvals, pauses and URL safety."""

from __future__ import annotations

import json

import pytest

from core import net_safety
from shuttle import skill_automation as sa


def _call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _run() -> dict:
    return {"run_id": "r", "org_id": "o", "user_id": "u", "skill_id": "s", "status": "running",
            "messages": json.dumps([{"role": "system", "content": "x"}]), "log": "[]", "pending": None}


def test_calculate_is_exact_and_safe() -> None:
    assert sa.calculate("20.000 * 520.50 + 3,000") == "13410"
    assert sa.calculate("0.1 + 0.2") == "0.3"
    for bad in ["__import__('os')", "2 ** 999999", "open('x')", "a + 1"]:
        with pytest.raises(ValueError):
            sa.calculate(bad)


def test_what_needs_approval() -> None:
    assert sa.needs_approval("send_email", {})
    assert sa.needs_approval("create_meeting", {})
    assert sa.needs_approval("http_request", {"method": "POST"})
    allowed = sa.hosts_named_in("Check stock at https://api.supplier.com/v1/stock")
    assert not sa.needs_approval("http_request", {"method": "GET", "url": "https://api.supplier.com/x"}, allowed)
    # A GET anywhere the skill does not name could carry data out in its URL.
    assert sa.needs_approval("http_request", {"method": "GET", "url": "https://evil.example/?d=secret"}, allowed)
    assert not sa.needs_approval("read_file", {})


async def test_outbound_tool_pauses_until_approved(monkeypatch) -> None:
    ran: list = []

    async def fake_execute(run, name, args):
        ran.append(name)
        return {"ok": True}

    monkeypatch.setattr(sa, "execute_tool", fake_execute)
    run = _run()
    calls = [_call("read_file", {"name": "BL.pdf"}, "c1"),
             _call("send_email", {"to": "a@x.com", "subject": "S", "body": "B"}, "c2"),
             _call("calculate", {"expression": "1+1"}, "c3")]
    assert await sa._drain(run, calls, auto_approve=False) is False
    assert ran == ["read_file"] and run["status"] == "needs_approval"
    pending = json.loads(run["pending"])
    assert [c["id"] for c in pending["calls"]] == ["c2", "c3"]
    assert pending["arguments"]["to"] == "a@x.com"
    # the read_file result is in the conversation; the email is not sent
    assert json.loads(run["messages"])[-1]["tool_call_id"] == "c1"


async def test_auto_approve_runs_outbound_tools(monkeypatch) -> None:
    ran: list = []

    async def fake_execute(run, name, args):
        ran.append(name)
        return {"ok": True}

    monkeypatch.setattr(sa, "execute_tool", fake_execute)
    run = _run()
    assert await sa._drain(run, [_call("send_email", {"to": "a@x.com"})], auto_approve=True)
    assert ran == ["send_email"]


async def test_ask_user_and_finish(monkeypatch) -> None:
    run = _run()
    assert await sa._drain(run, [_call("ask_user", {"question": "Which supplier?"})], False) is False
    assert run["status"] == "needs_input" and json.loads(run["pending"])["question"] == "Which supplier?"
    run = _run()
    assert await sa._drain(run, [_call("finish", {"summary": "Sent 3 offer requests."})], False) is False
    assert run["status"] == "completed" and run["summary"] == "Sent 3 offer requests."


async def test_tool_errors_are_returned_to_the_model(monkeypatch) -> None:
    async def broken(run, name, args):
        raise RuntimeError("boom")

    monkeypatch.setattr(sa, "execute_tool", broken)
    run = _run()
    assert await sa._drain(run, [_call("read_file", {"name": "x"})], False)
    assert "boom" in json.loads(run["messages"])[-1]["content"]
    assert json.loads(run["log"])[-1]["status"] == "error"


def test_toolbox_has_engine_and_ask_tools() -> None:
    names = {t["function"]["name"] for t in sa.tool_specs()}
    assert {"send_email", "http_request", "update_spreadsheet", "calculate", "ask_user",
            "hand_off_to_device", "finish", "read_file", "fill_template",
            "create_shipment_documents", "search_emails"} <= names
    assert not any(n.startswith("propose_") for n in names)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "http://localhost:8000", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5", "http://[::1]/", "file:///etc/passwd", "http://postgres:5432",
    "http://user:pw@example.com", "http://metadata.google.internal/",
])
async def test_private_and_internal_urls_are_refused(url) -> None:
    with pytest.raises(net_safety.UnsafeUrl):
        await net_safety.check_public_url(url)


async def test_public_ip_literal_is_allowed() -> None:
    assert await net_safety.check_public_url("https://8.8.8.8/x") == "https://8.8.8.8/x"


def test_pinned_request_keeps_host_for_tls() -> None:
    url, headers, ext = net_safety.pinned_request_args("https://api.example.com:8443/v1?q=1", "93.184.216.34")
    assert url == "https://93.184.216.34:8443/v1?q=1"
    assert headers == {"Host": "api.example.com:8443"} and ext == {"sni_hostname": "api.example.com"}


@pytest.mark.parametrize("url", ["http://[64:ff9b::a00:1]/", "http://[2002:a00:1::]/"])
async def test_nat64_and_6to4_are_refused(url) -> None:
    with pytest.raises(net_safety.UnsafeUrl):
        await net_safety.check_public_url(url)
