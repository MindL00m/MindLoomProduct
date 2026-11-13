"""Every NetSuite or SAP change is recorded, and unattended runs can only make so many."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes import erp as erp_routes
from core import schema
from core.models import ProposedErpChange
from keeper.auth import require_user_context
from loombot import erp_tools
from shuttle import skill_automation as sa
from weaver import erp_audit, netsuite, sap

WHO = ("org-a", "user-a")
BP = "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner"


@pytest.fixture
def systems(monkeypatch):
    made: list[tuple] = []
    state = {"fail": None}

    def write(name, result):
        async def call(org_id, user_id, *args):
            if state["fail"]:
                raise state["fail"]
            made.append((name, org_id, user_id, *args))
            return dict(result)
        return call

    monkeypatch.setattr(netsuite, "create_record", write("ns.create", {"created": True, "id": "88"}))
    monkeypatch.setattr(netsuite, "update_record", write("ns.update", {"updated": True}))
    monkeypatch.setattr(sap, "create", write("sap.create", {"created": True}))
    monkeypatch.setattr(sap, "update", write("sap.update", {"updated": True}))
    erp_audit.use_memory_store(True)
    yield type("Systems", (), {"made": made, "state": state})
    erp_audit.use_memory_store(False)


def _run(run_id="run-1"):
    return {"run_id": run_id, "org_id": "org-a", "user_id": "user-a", "messages": "[]", "log": "[]",
            "status": "running", "pending": None}


def _call(name, args, call_id):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_which_calls_are_changes() -> None:
    assert sa.is_erp_write("sap_write", {"action": "create"}) and sa.is_erp_write("sap_write", {})
    assert sa.is_erp_write("netsuite_record", {"action": "update"}) and sa.is_erp_write("netsuite_record", {"action": " Create "})
    assert sa.is_erp_write("netsuite_record", {})  # unknown is treated as a change
    assert not sa.is_erp_write("netsuite_record", {"action": "GET"})
    assert not sa.is_erp_write("netsuite_query", {}) and not sa.is_erp_write("sap_read", {})
    assert not sa.is_erp_write("send_email", {})
    for name, args in (("netsuite_record", {"action": "create"}), ("netsuite_record", {}), ("sap_write", {})):
        assert sa.needs_approval(name, args)  # every change needs approval


async def test_automation_changes_are_recorded_with_field_names_only(systems) -> None:
    run = _run()
    await sa.execute_tool(run, "netsuite_record", {"action": "create", "record_type": "customer",
                                                   "fields": {"companyName": "Secret Co", "email": "a@b.example"}})
    await sa.execute_tool(run, "netsuite_record", {"action": "update", "record_type": "customer", "id": "9", "fields": {"memo": "m"}})
    await sa.execute_tool(run, "sap_write", {"action": "update", "path": f"{BP}('1')", "fields": {"Name": "B"}})
    assert [m[0] for m in systems.made] == ["ns.create", "ns.update", "sap.update"]
    newest, middle, oldest = await erp_audit.list_changes(*WHO)
    assert (oldest["system"], oldest["action"], oldest["target"], oldest["record_id"]) == ("netsuite", "create", "customer", "88")
    assert oldest["field_names"] == "companyName, email" and oldest["source"] == "automation" and oldest["run_id"] == "run-1"
    assert oldest["ok"] is True and middle["record_id"] == "9" and newest["target"] == f"{BP}('1')"
    assert "Secret Co" not in json.dumps(await erp_audit.list_changes(*WHO))
    assert await erp_audit.list_changes("org-a", "user-b") == [] and await erp_audit.list_changes("org-b", "user-a") == []
    assert "org_id" not in newest and "user_id" not in newest


async def test_failed_and_invalid_changes(systems) -> None:
    run = _run()
    systems.state["fail"] = HTTPException(status_code=400, detail="NetSuite refused the request (400): Invalid field.")
    with pytest.raises(HTTPException):
        await sa.execute_tool(run, "netsuite_record", {"action": "create", "record_type": "invoice", "fields": {"x": 1}})
    systems.state["fail"] = RuntimeError("secret connection string")
    with pytest.raises(RuntimeError):
        await sa.execute_tool(run, "sap_write", {"action": "create", "path": BP, "fields": {"x": 1}})
    second, first = await erp_audit.list_changes(*WHO)
    assert first["ok"] is False and "Invalid field" in first["detail"]
    assert second["ok"] is False and second["detail"] == "RuntimeError"  # never the message of an unknown error
    # Calls that are not valid changes reach neither system nor the record.
    systems.state["fail"] = None
    for name, args in (("netsuite_record", {"action": "delete", "record_type": "customer", "id": "1"}),
                       ("netsuite_record", {"action": "update", "record_type": "customer", "fields": {"a": 1}}),
                       ("sap_write", {"action": "remove", "path": BP, "fields": {"a": 1}})):
        assert "error" in await sa.execute_tool(run, name, args)
    assert systems.made == [] and len(await erp_audit.list_changes(*WHO)) == 2


async def test_ask_approvals_are_recorded_too(systems) -> None:
    await erp_tools.apply_change(*WHO, ProposedErpChange(system="sap", action="create", target=BP, fields={"Name": "N"}))
    systems.state["fail"] = HTTPException(status_code=412, detail="Someone changed that record in SAP just now.")
    with pytest.raises(HTTPException):
        await erp_tools.apply_change(*WHO, ProposedErpChange(system="sap", action="update", target=f"{BP}('1')", fields={"Name": "B"}))
    with pytest.raises(HTTPException):  # an update with no id is refused and recorded
        systems.state["fail"] = None
        await erp_tools.apply_change(*WHO, ProposedErpChange(system="netsuite", action="update", target="customer", fields={"a": 1}))
    changes = await erp_audit.list_changes(*WHO)
    assert [c["ok"] for c in changes] == [False, False, True] and {c["source"] for c in changes} == {"ask"}
    assert all(c["run_id"] is None for c in changes)


async def test_recording_never_breaks_the_change(systems, monkeypatch) -> None:
    erp_audit.use_memory_store(False)  # no database in tests: the insert fails

    def broken():
        raise RuntimeError("database is down")

    monkeypatch.setattr(erp_audit, "get_session_factory", broken)
    result = await sa.execute_tool(_run(), "sap_write", {"action": "create", "path": BP, "fields": {"Name": "N"}})
    assert result == {"created": True} and len(systems.made) == 1


async def test_changes_wait_for_approval_unless_automatic(systems) -> None:
    run = _run()
    calls = [_call("sap_write", {"action": "create", "path": BP, "fields": {"Name": "N"}}, "c1")]
    assert await sa._drain(run, calls, False) is False
    assert run["status"] == "needs_approval" and systems.made == []
    assert json.loads(run["pending"])["tool"] == "sap_write"


async def test_automatic_approval_stops_after_a_handful_of_changes(systems, monkeypatch) -> None:
    monkeypatch.setattr(sa, "MAX_AUTO_ERP_WRITES", 3)
    run = _run()
    calls = [_call("netsuite_record", {"action": "create", "record_type": "customer", "fields": {"n": i}}, f"c{i}")
             for i in range(5)]
    calls.insert(1, _call("netsuite_query", {"query": "SELECT id FROM customer"}, "read"))

    async def query(*args, **kwargs):
        return {"rows": [], "count": 0}

    monkeypatch.setattr(netsuite, "query", query)
    assert await sa._drain(run, calls, True) is False
    assert len(systems.made) == 3 and run["status"] == "needs_approval"  # reads do not count
    pending = json.loads(run["pending"])
    assert pending["tool"] == "netsuite_record" and pending["arguments"]["fields"] == {"n": 3} and len(pending["calls"]) == 2
    assert sum(1 for entry in json.loads(run["log"]) if entry.get("erp_write")) == 3
    assert len(await erp_audit.list_changes(*WHO)) == 3


def test_route_and_schema(systems) -> None:
    app = FastAPI()
    app.include_router(erp_routes.router)
    assert TestClient(app).get("/integrations/erp/changes").status_code in {401, 403}
    app.dependency_overrides[require_user_context] = lambda: WHO
    client = TestClient(app)
    assert client.get("/integrations/erp/changes").json() == []
    client.post("/integrations/erp/apply", json={"system": "sap", "action": "create", "target": BP, "fields": {"Name": "N"}})
    (change,) = client.get("/integrations/erp/changes?limit=5").json()
    assert change["source"] == "ask" and change["field_names"] == "Name" and change["ok"] is True
    sql = "\n".join(schema._DURABLE_INTEGRATIONS_SQL)
    assert "CREATE TABLE IF NOT EXISTS erp_changes" in sql
    for column in erp_audit._COLUMNS:
        assert column in sql
