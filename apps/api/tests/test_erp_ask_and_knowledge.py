"""NetSuite and SAP in Ask (reads run, changes become a card) and as knowledge imports."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes import erp as erp_routes
from core import schema
from core.models import ProposedErpChange, QueryResponse
from keeper.auth import require_user_context
from loombot import ask_agent, erp_tools
from weaver import erp_knowledge as ek
from weaver import netsuite, sap

WHO = ("org-a", "user-a")
BP = "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner"


@pytest.fixture
def erp(monkeypatch):
    """Both systems connected, with every call recorded instead of made."""

    calls: list[tuple] = []
    state = SimpleNamespace(calls=calls, netsuite=True, sap=True, rows=[{"id": "1", "companyname": "Azure"}],
                            sap_row=SimpleNamespace(system="s4hana", api_root=None, via="direct"))

    def record(name, result):
        async def call(org_id, user_id, *args, **kwargs):
            calls.append((name, org_id, user_id, *args, kwargs))
            return result(*args, **kwargs) if callable(result) else result
        return call

    async def ns_connected(org_id, user_id):
        return state.netsuite and (org_id, user_id) == WHO

    async def sap_connected(org_id, user_id):
        return state.sap and (org_id, user_id) == WHO

    async def sap_row(org_id, user_id):
        return state.sap_row if await sap_connected(org_id, user_id) else None

    monkeypatch.setattr(netsuite, "is_connected", ns_connected)
    monkeypatch.setattr(sap, "is_connected", sap_connected)
    monkeypatch.setattr(sap, "_get_row", sap_row)
    monkeypatch.setattr(netsuite, "query", record("ns.query", {"rows": [{"id": "7"}], "count": 1}))
    monkeypatch.setattr(netsuite, "get_record", record("ns.get", {"record": {"id": "7"}}))
    monkeypatch.setattr(netsuite, "create_record", record("ns.create", {"created": True, "id": "88"}))
    monkeypatch.setattr(netsuite, "update_record", record("ns.update", {"updated": True}))
    monkeypatch.setattr(netsuite, "query_all", record("ns.all", lambda *a, **k: list(state.rows)))
    monkeypatch.setattr(sap, "read", record("sap.read", {"rows": [{"BusinessPartner": "1"}], "count": 1}))
    monkeypatch.setattr(sap, "create", record("sap.create", {"created": True}))
    monkeypatch.setattr(sap, "update", record("sap.update", {"updated": True}))
    monkeypatch.setattr(sap, "read_all", record("sap.all", lambda *a, **k: list(state.rows)))
    from weaver import erp_audit

    erp_audit.use_memory_store(True)
    ek.use_memory_store(True)
    yield state
    ek.use_memory_store(False)


# --- Ask ----------------------------------------------------------------------------------


async def test_ask_offers_tools_only_for_connected_systems(erp) -> None:
    names = [t["function"]["name"] for t in await erp_tools.ask_specs(*WHO)]
    assert names == ["netsuite_query", "netsuite_record", "sap_read", "sap_write"]
    erp.sap = False
    assert [t["function"]["name"] for t in await erp_tools.ask_specs(*WHO)] == ["netsuite_query", "netsuite_record"]
    assert await erp_tools.ask_specs("org-a", "user-b") == []


async def test_ask_survives_a_failing_connection_lookup(monkeypatch) -> None:
    async def broken(org_id, user_id):
        raise RuntimeError("database is down")

    monkeypatch.setattr(netsuite, "is_connected", broken)
    monkeypatch.setattr(sap, "is_connected", broken)
    assert await erp_tools.ask_specs(*WHO) == []


async def test_ask_write_tools_say_they_only_draft(erp) -> None:
    specs = {t["function"]["name"]: t["function"]["description"] for t in await erp_tools.ask_specs(*WHO)}
    for name in ("netsuite_record", "sap_write"):
        assert "does NOT change anything" in specs[name] and "pause" not in specs[name].lower()
    assert "does NOT" not in specs["netsuite_query"]
    # The automation's own descriptions are untouched.
    from shuttle import skill_automation as sa

    assert all("does NOT change anything" not in t["function"]["description"] for t in sa.ENGINE_TOOLS)


async def test_ask_reads_run_and_writes_only_draft(erp) -> None:
    outputs = ask_agent._TurnOutputs()

    async def run(name, **arguments):
        result, *rest = await ask_agent._run_tool(name=name, arguments=arguments, org_id="org-a", user_id="user-a",
                                                  people_cache={}, outputs=outputs)
        assert rest == [None, None, None, None]
        return result

    assert (await run("netsuite_query", query="SELECT id FROM customer", params=["a"], limit=5))["count"] == 1
    assert (await run("sap_read", path=BP, query={"$top": "1"}))["count"] == 1
    assert (await run("netsuite_record", action="get", record_type="customer", id="7"))["record"] == {"id": "7"}
    assert [c[0] for c in erp.calls] == ["ns.query", "sap.read", "ns.get"] and outputs.erp_changes == []

    drafted = await run("netsuite_record", action="create", record_type="customer", fields={"companyName": "New Co"})
    assert drafted["drafted"] and "Nothing was changed" in drafted["note"]
    assert [c[0] for c in erp.calls] == ["ns.query", "sap.read", "ns.get"]  # no write reached either system
    (first,) = outputs.erp_changes
    assert first == ProposedErpChange(system="netsuite", action="create", target="customer", fields={"companyName": "New Co"})
    outputs.erp_changes.clear()
    await run("sap_write", action="update", path=f"{BP}('1000')", fields={"Name": "B"})
    (second,) = outputs.erp_changes
    assert (second.system, second.action, second.target) == ("sap", "update", f"{BP}('1000')")
    # One card per reply: a second draft in the same turn is refused, not silently lost.
    again = await run("netsuite_record", action="create", record_type="vendor", fields={"companyName": "V"})
    assert "One change at a time" in again["error"] and len(outputs.erp_changes) == 1
    outputs.erp_changes.clear()
    assert "error" in await run("sap_write", action="create", path="x" * 500, fields={"a": 1})  # too long to be a path
    outputs.erp_changes.append(first)
    outputs.erp_changes.append(second)

    for bad in (dict(action="delete", record_type="customer", id="1", fields={"a": 1}),
                dict(action="update", record_type="customer", fields={"a": 1}),
                dict(action="create", record_type="customer"),
                dict(action="create", record_type="", fields={"a": 1})):
        assert "error" in await run("netsuite_record", **bad)
    assert "error" in await run("sap_write", action="create", path="", fields={"a": 1})
    assert len(outputs.erp_changes) == 2
    assert "error" in await run("netsuite_record", action="get", record_type="customer")


async def test_ask_turns_a_refusal_into_a_tool_error(erp, monkeypatch) -> None:
    async def refuse(*args, **kwargs):
        raise HTTPException(status_code=400, detail="NetSuite refused the request (400): Unknown field 'nam'.")

    monkeypatch.setattr(netsuite, "query", refuse)
    result = await erp_tools.run_for_ask("netsuite_query", {"query": "SELECT nam FROM customer"},
                                         org_id="org-a", user_id="user-a", changes=[])
    assert "Unknown field" in result["error"]


def test_ask_response_carries_the_draft_and_prompts_know_the_tools() -> None:
    change = ProposedErpChange(system="sap", action="create", target=BP, fields={"Name": "N"})
    response = QueryResponse(answer="Draft ready.", sources=[], confidence="high", routed=False, proposed_erp_change=change)
    assert response.model_dump()["proposed_erp_change"]["target"] == BP
    assert "{erp_rules}" in ask_agent._AGENT_SYSTEM and "sap_read" in ask_agent._PLANNER_SYSTEM
    assert "{" not in erp_tools.RULES  # it is put into a str.format template
    assert {"netsuite_query", "sap_write"} <= ask_agent._DATA_TOOLS
    with pytest.raises(ValueError):
        ProposedErpChange(system="oracle", action="create", target="x", fields={})
    with pytest.raises(ValueError):
        ProposedErpChange(system="sap", action="delete", target="x", fields={})


async def test_approving_a_draft_makes_the_change_as_that_person(erp) -> None:
    await erp_tools.apply_change(*WHO, ProposedErpChange(system="netsuite", action="create", target="customer", fields={"a": 1}))
    await erp_tools.apply_change(*WHO, ProposedErpChange(system="netsuite", action="update", target="customer", record_id="9", fields={"a": 2}))
    await erp_tools.apply_change(*WHO, ProposedErpChange(system="sap", action="create", target=BP, fields={"b": 1}))
    await erp_tools.apply_change(*WHO, ProposedErpChange(system="sap", action="update", target=f"{BP}('1')", fields={"b": 2}))
    assert [(c[0], c[1], c[2]) for c in erp.calls] == [
        ("ns.create", *WHO), ("ns.update", *WHO), ("sap.create", *WHO), ("sap.update", *WHO)]
    assert erp.calls[1][3:6] == ("customer", "9", {"a": 2})
    with pytest.raises(HTTPException):
        await erp_tools.apply_change(*WHO, ProposedErpChange(system="netsuite", action="update", target="customer", fields={"a": 1}))


# --- Knowledge imports --------------------------------------------------------------------


def test_pages_are_stable_and_readable() -> None:
    rows = [{"id": str(i), "name": f"Customer {i}", "note": None, "tags": ["a", "b"], "blank": ""} for i in range(1, 121)]
    pages = ek.render_pages("Customers", "netsuite", rows)
    assert len(pages) == 3 and pages == ek.render_pages("Customers", "netsuite", rows)
    assert pages[0].startswith("Customers (NetSuite), rows 1 to 50 of 120\n")
    assert '- id: 1; name: Customer 1; tags: ["a", "b"]' in pages[0] and "note" not in pages[0] and "blank" not in pages[0]
    assert pages[2].startswith("Customers (NetSuite), rows 101 to 120 of 120")
    assert ek.render_pages("Empty", "sap", []) == []
    long = ek.render_pages("Long", "sap", [{"text": "x" * 5000 + "\n\n y"}])[0]
    assert len(long) < 500


async def test_creating_an_import_checks_the_read(erp) -> None:
    made = await ek.create_import(*WHO, provider="netsuite", name="  Customers  ", query="SELECT id FROM customer;")
    assert made["name"] == "Customers" and made["query"] == "SELECT id FROM customer" and made["max_rows"] == 500
    assert made["visibility"] == "me" and made["last_run_at"] is None
    sap_made = await ek.create_import(*WHO, provider="sap", name="Partners", query="API_BUSINESS_PARTNER",
                                      options={"$select": "BusinessPartner", "$top": "5"}, max_rows=99999, visibility="org")
    assert sap_made["query"] == "/sap/opu/odata/sap/API_BUSINESS_PARTNER" and sap_made["max_rows"] == ek.MAX_ROWS
    assert sap_made["options"] == {"$select": "BusinessPartner"}  # paging is MindLoom's own
    assert [i["name"] for i in await ek.list_imports(*WHO)] == ["Customers", "Partners"]
    assert await ek.list_imports("org-a", "user-b") == []

    for bad in (dict(provider="netsuite", name="x", query="DELETE FROM customer"),
                dict(provider="oracle", name="x", query="SELECT 1"),
                dict(provider="netsuite", name="", query="SELECT id FROM customer"),
                dict(provider="netsuite", name="x", query="SELECT id FROM customer", visibility="everyone"),
                dict(provider="sap", name="x", query="/etc/passwd"),
                dict(provider="sap", name="x", query="API_X", options={"sap-client": "000"})):
        with pytest.raises(HTTPException) as error:
            await ek.create_import(*WHO, **bad)
        assert error.value.status_code == 422
    erp.netsuite = False
    with pytest.raises(HTTPException) as error:
        await ek.create_import(*WHO, provider="netsuite", name="x", query="SELECT id FROM customer")
    assert error.value.status_code == 409


async def test_import_limit(erp, monkeypatch) -> None:
    monkeypatch.setattr(ek, "MAX_IMPORTS", 2)
    for name in ("a", "b"):
        await ek.create_import(*WHO, provider="netsuite", name=name, query="SELECT id FROM customer")
    with pytest.raises(HTTPException) as error:
        await ek.create_import(*WHO, provider="netsuite", name="c", query="SELECT id FROM customer")
    assert error.value.status_code == 409


@pytest.fixture
def knowledge(monkeypatch):
    """Stands in for the knowledge base: remembers each page by its external id."""

    from brain import source_registry

    store: dict[str, dict] = {}
    deleted: list[str] = []

    async def ingest(*, org_id, provider, external_id, version, conversation, document):
        if store.get(external_id, {}).get("data") == document.data:
            return None  # unchanged
        store[external_id] = {"data": document.data, "visible_to": document.visible_to, "title": document.title,
                              "provider": provider, "org_id": org_id, "source": conversation.source}
        return object()

    async def forget(org_id, provider, external_id):
        deleted.append(external_id)
        return store.pop(external_id, None) is not None

    monkeypatch.setattr(source_registry, "ingest_external_source", ingest)
    monkeypatch.setattr(source_registry, "mark_external_source_deleted", forget)
    return SimpleNamespace(store=store, deleted=deleted)


async def test_running_an_import_fills_refreshes_and_trims_knowledge(erp, knowledge) -> None:
    erp.rows = [{"id": str(i), "companyname": f"C{i}"} for i in range(120)]
    made = await ek.create_import(*WHO, provider="netsuite", name="Customers", query="SELECT id, companyname FROM customer",
                                  max_rows=300)
    key = made["import_id"]
    assert await ek.run_import(*WHO, key) == {"ok": True, "rows": 120, "pages": 3, "pages_changed": 3}
    assert erp.calls[-1][0] == "ns.all" and erp.calls[-1][-1] == {"max_rows": 300}
    assert sorted(knowledge.store) == [f"erp:{key}:0", f"erp:{key}:1", f"erp:{key}:2"]
    page = knowledge.store[f"erp:{key}:0"]
    assert page["visible_to"] == ["user:user-a"] and page["title"] == "Customers (1 of 3)" and page["provider"] == "netsuite"
    listed = (await ek.list_imports(*WHO))[0]
    assert listed["last_status"] == "ok" and listed["last_rows"] == 120 and listed["last_run_at"]

    assert (await ek.run_import(*WHO, key))["pages_changed"] == 0  # nothing changed: nothing re-ingested
    erp.rows[0]["companyname"] = "Renamed"
    assert (await ek.run_import(*WHO, key))["pages_changed"] == 1  # only the page that changed

    erp.rows = erp.rows[:40]  # fewer rows now: the extra pages go
    outcome = await ek.run_import(*WHO, key)
    assert outcome["pages"] == 1 and sorted(knowledge.store) == [f"erp:{key}:0"]
    assert knowledge.deleted == [f"erp:{key}:1", f"erp:{key}:2"]
    assert knowledge.store[f"erp:{key}:0"]["title"] == "Customers"

    assert await ek.delete_import(*WHO, key) == {"deleted": True}
    assert knowledge.store == {} and await ek.list_imports(*WHO) == []
    with pytest.raises(HTTPException) as error:
        await ek.run_import(*WHO, key)
    assert error.value.status_code == 404


async def test_org_visibility_and_sap_reads(erp, knowledge) -> None:
    made = await ek.create_import(*WHO, provider="sap", name="Partners", query=BP, options={"$select": "BusinessPartner"},
                                  visibility="org")
    assert (await ek.run_import(*WHO, made["import_id"]))["ok"]
    name, _org, _user, path, options, kwargs = erp.calls[-1]
    assert (name, path, options, kwargs) == ("sap.all", BP, {"$select": "BusinessPartner"}, {"max_rows": 500})
    (page,) = knowledge.store.values()
    assert page["visible_to"] == ["org:org-a"] and page["source"] == "sap"
    assert page["data"].decode().startswith("Partners (SAP), rows 1 to 1 of 1")


async def test_a_failing_import_is_recorded_and_others_still_run(erp, knowledge, monkeypatch) -> None:
    good = await ek.create_import(*WHO, provider="netsuite", name="Good", query="SELECT id FROM customer")
    bad = await ek.create_import(*WHO, provider="sap", name="Bad", query=BP)

    async def refuse(*args, **kwargs):
        raise HTTPException(status_code=403, detail="SAP refused the request (403).")

    monkeypatch.setattr(sap, "read_all", refuse)
    assert await ek.run_due_imports() == 2
    by_name = {i["name"]: i for i in await ek.list_imports(*WHO)}
    assert by_name["Good"]["last_status"] == "ok"
    assert by_name["Bad"]["last_status"] == "error" and "403" in by_name["Bad"]["last_error"]
    assert await ek.run_due_imports() == 0  # both ran within the last day
    ek._memory[good["import_id"]]["last_run_at"] = datetime.now(timezone.utc) - timedelta(hours=25)
    assert await ek.run_due_imports() == 1
    assert bad["import_id"] in ek._memory


async def test_disconnecting_removes_that_systems_imports(erp, knowledge) -> None:
    ns_import = await ek.create_import(*WHO, provider="netsuite", name="Customers", query="SELECT id FROM customer")
    sap_import = await ek.create_import(*WHO, provider="sap", name="Partners", query=BP)
    await ek.run_import(*WHO, ns_import["import_id"])
    await ek.run_import(*WHO, sap_import["import_id"])
    await ek.delete_for(*WHO, "netsuite")
    assert [i["provider"] for i in await ek.list_imports(*WHO)] == ["sap"]
    assert list(knowledge.store) == [f"erp:{sap_import['import_id']}:0"]


# --- Paging through every row -------------------------------------------------------------


async def test_netsuite_query_all_pages_until_done(monkeypatch) -> None:
    offsets = []

    async def call(org_id, user_id, method, path, *, params=None, body=None, headers=None):
        offsets.append(params["offset"])
        rows = [{"id": str(params["offset"] + i), "links": []} for i in range(1000 if params["offset"] < 2000 else 300)]
        return httpx.Response(200, json={"items": rows, "hasMore": params["offset"] < 2000})

    monkeypatch.setattr(netsuite, "_call", call)
    rows = await netsuite.query_all(*WHO, "SELECT id FROM customer", max_rows=5000)
    assert len(rows) == 2300 and offsets == [0, 1000, 2000] and rows[0] == {"id": "0"}
    offsets.clear()
    assert len(await netsuite.query_all(*WHO, "SELECT id FROM customer", max_rows=1500)) == 1500 and offsets == [0, 1000]
    with pytest.raises(HTTPException):
        await netsuite.query_all(*WHO, "DROP TABLE customer")


async def test_sap_read_all_pages_with_top_and_skip(monkeypatch) -> None:
    asked = []

    class Session:
        def __init__(self, system):
            self.conn = SimpleNamespace(system=system, api_root="/b1s/v1")

        async def get(self, path, *, options=None, headers=None):
            asked.append((path, dict(options)))
            left = 250 - options["$skip"]
            return httpx.Response(200, json={"value": [{"Id": i, "@odata.etag": "e"} for i in range(min(options["$top"], left))]})

        async def close(self):
            asked.append("closed")

    async def s4(org_id, user_id):
        return Session("s4hana")

    monkeypatch.setattr(sap, "_session", s4)
    rows = await sap.read_all(*WHO, BP, {"$select": "Id", "$top": "5", "$skip": "9"}, max_rows=1000)
    assert len(rows) == 250 and rows[0] == {"Id": 0}
    assert asked == [(BP, {"$select": "Id", "$top": 200, "$skip": 0}), (BP, {"$select": "Id", "$top": 200, "$skip": 200}), "closed"]

    async def b1(org_id, user_id):
        return Session("business_one")

    asked.clear()
    monkeypatch.setattr(sap, "_session", b1)
    assert len(await sap.read_all(*WHO, "Items", max_rows=150)) == 150
    assert [a[1]["$top"] for a in asked[:-1]] == [100, 50]  # Business One's own page size


# --- Routes and schema --------------------------------------------------------------------


def test_routes(erp, knowledge, monkeypatch) -> None:
    queued = []

    async def enqueue(kind, *, org_id, conversation_id, payload):
        queued.append((kind, org_id, payload))
        return "job-1"

    monkeypatch.setattr(erp_routes, "enqueue", enqueue)
    app = FastAPI()
    app.include_router(erp_routes.router)
    assert TestClient(app).get("/integrations/erp/imports").status_code in {401, 403}
    assert TestClient(app).post("/integrations/erp/apply", json={}).status_code in {401, 403}
    app.dependency_overrides[require_user_context] = lambda: WHO
    client = TestClient(app)

    change = {"system": "sap", "action": "create", "target": BP, "fields": {"Name": "N"}}
    assert client.post("/integrations/erp/apply", json=change).json() == {"created": True}
    assert erp.calls[-1][:4] == ("sap.create", *WHO, BP)
    for bad in ({**change, "action": "delete"}, {**change, "system": "x"}, {**change, "target": ""}, {"system": "sap"}):
        assert client.post("/integrations/erp/apply", json=bad).status_code == 422

    made = client.post("/integrations/erp/imports", json={"provider": "netsuite", "name": "Customers",
                                                          "query": "SELECT id FROM customer"})
    assert made.status_code == 200
    key = made.json()["import_id"]
    assert queued == [("erp_import", "org-a", {"user_id": "user-a", "import_id": key})]
    assert client.post("/integrations/erp/imports", json={"provider": "netsuite", "name": "x", "query": "DROP x"}).status_code == 422
    assert client.post("/integrations/erp/imports", json={"provider": "netsuite", "name": "x", "query": "SELECT 1", "max_rows": 0}).status_code == 422
    assert [i["name"] for i in client.get("/integrations/erp/imports").json()] == ["Customers"]
    assert client.post(f"/integrations/erp/imports/{key}/run").json() == {"job_id": "job-1", "status": "queued"}
    assert client.post("/integrations/erp/imports/nope/run").status_code == 404
    assert client.delete(f"/integrations/erp/imports/{key}").json() == {"deleted": True}
    assert client.delete(f"/integrations/erp/imports/{key}").status_code == 404


def test_schema_has_the_imports_table() -> None:
    sql = "\n".join(schema._DURABLE_INTEGRATIONS_SQL)
    assert "CREATE TABLE IF NOT EXISTS erp_imports" in sql
    for column in ek._COLUMNS:
        assert column in sql
    assert json.loads("{}") == {}


async def test_a_run_that_fails_halfway_leaves_nothing_behind(erp, knowledge, monkeypatch) -> None:
    from brain import source_registry

    erp.rows = [{"id": str(i)} for i in range(260)]  # six pages
    made = await ek.create_import(*WHO, provider="netsuite", name="Customers", query="SELECT id FROM customer")
    key = made["import_id"]
    real = source_registry.ingest_external_source

    async def fails_on_page_four(**kwargs):
        if kwargs["external_id"].endswith(":3"):
            raise RuntimeError("knowledge base is down")
        return await real(**kwargs)

    monkeypatch.setattr(source_registry, "ingest_external_source", fails_on_page_four)
    outcome = await ek.run_import(*WHO, key)
    assert outcome["ok"] is False and len(knowledge.store) == 3  # pages 0 to 2 made it in
    assert (await ek.list_imports(*WHO))[0]["last_status"] == "error"
    await ek.delete_import(*WHO, key)
    assert knowledge.store == {}  # and deleting the import still removes them
