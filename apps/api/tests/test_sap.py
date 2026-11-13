"""SAP S/4HANA: OData reads, and writes behind approval with a write token and a version check."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes import sap as sap_routes
from core import provider_http, schema, token_crypto
from keeper.auth import require_user_context
from shuttle import skill_automation as sa
from weaver import sap

BASE = "https://my123456-api.s4hana.cloud.sap"
ADDRESS = "203.0.113.10"
BP = "/sap/opu/odata/sap/API_BUSINESS_PARTNER"
_real_client = httpx.AsyncClient


class FakeSap:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.replies: list[httpx.Response] = []
        self.password = "s3cret"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        expected = "Basic " + base64.b64encode(f"MINDLOOM:{self.password}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401)
        if self.replies:
            return self.replies.pop(0)
        if request.headers.get("x-csrf-token") == "Fetch":
            return httpx.Response(200, json={"d": {"__metadata": {"etag": "W/\"v7\""}, "Name": "A"}}, headers=[
                ("x-csrf-token", "tok-9"), ("set-cookie", "SAP_SESSIONID_X=abc%3d; path=/; secure; HttpOnly"),
                ("set-cookie", "sap-usercontext=sap-client=100; Domain=my123456-api.s4hana.cloud.sap; path=/"),
            ])
        return httpx.Response(200, json={"d": {"results": []}})


@pytest.fixture
def fake(monkeypatch):
    rows: dict[tuple[str, str], SimpleNamespace] = {}

    async def get_row(org_id, user_id):
        return rows.get((org_id, user_id))

    async def upsert_row(org_id, user_id, values):
        now = datetime.now(timezone.utc)
        rows[(org_id, user_id)] = SimpleNamespace(org_id=org_id, user_id=user_id, created_at=now, updated_at=now, **values)

    async def delete_row(org_id, user_id):
        rows.pop((org_id, user_id), None)

    async def resolve(url):
        return url, ADDRESS

    async def no_sleep(_seconds):
        return None

    server = FakeSap()
    server.rows = rows
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(token_crypto, "get_settings", lambda: SimpleNamespace(token_encryption_key=key))
    monkeypatch.setattr(sap, "_get_row", get_row)
    monkeypatch.setattr(sap, "_upsert_row", upsert_row)
    monkeypatch.setattr(sap, "_delete_row", delete_row)
    monkeypatch.setattr(sap, "resolve_public_url", resolve)
    monkeypatch.setattr(provider_http.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(sap.httpx, "AsyncClient",
                        lambda **kw: _real_client(transport=httpx.MockTransport(server.handler), **kw))
    return server


async def _connected(fake: FakeSap, client: str | None = None) -> None:
    await sap.connect("org-a", "user-a", base_url=BASE, username="MINDLOOM", password="s3cret",
                      test_service="API_BUSINESS_PARTNER", sap_client=client)
    fake.requests.clear()


# --- Addresses ----------------------------------------------------------------------------


def test_base_url_is_https_host_only() -> None:
    assert sap.normalize_base_url(" my123456-api.s4hana.cloud.sap ") == BASE
    assert sap.normalize_base_url("https://SAP.Example.com/") == "https://sap.example.com"
    assert sap.normalize_base_url("https://sap.example.com:44300") == "https://sap.example.com:44300"
    assert sap.normalize_base_url("https://sap.example.com:443") == "https://sap.example.com"


@pytest.mark.parametrize("bad", ["", "http://sap.example.com", "ftp://sap.example.com", "https://user:pw@sap.example.com",
                                 "https://sap.example.com/sap/opu", "https://sap.example.com?x=1", "https://sap.example.com#f",
                                 "https://sap.example.com:99999", "https://sap_example.com", "https://"])
def test_bad_base_urls_are_refused(bad) -> None:
    with pytest.raises(HTTPException) as error:
        sap.normalize_base_url(bad)
    assert error.value.status_code == 422


def test_paths_stay_inside_the_odata_roots() -> None:
    assert sap.normalize_path("API_BUSINESS_PARTNER") == BP
    assert sap.normalize_path(f"{BP}/A_BusinessPartner('1000')") == f"{BP}/A_BusinessPartner('1000')"
    v4 = "/sap/opu/odata4/sap/api_product/srvd_a2x/sap/product/0002/Product"
    assert sap.normalize_path(v4.lstrip("/")) == v4
    assert sap.normalize_path(f"{BP};v=0002/A_Item(SalesOrder='1',Item='10')").endswith("Item='10')")


@pytest.mark.parametrize("bad", ["", "/", "/sap/bc/adt/programs", "/sap/opu/odata/../bc/x", "/sap/opu/odata/sap//x",
                                 f"{BP}/A_BusinessPartner?$top=1", f"{BP}/$batch", f"{BP}/A_Doc('1')/$value",
                                 "https://evil.example/sap/opu/odata/sap/X", f"{BP}/A#x", "/sap/opu/odatax/sap/X",
                                 f"{BP}/A&sap-client=000"])
def test_bad_paths_are_refused(bad) -> None:
    with pytest.raises(HTTPException) as error:
        sap.normalize_path(bad)
    assert error.value.status_code == 422


def test_only_odata_options_reach_the_address() -> None:
    params = sap._query({"$Filter": "Name eq 'A'", "$top": 5}, f"{BP}/A_BusinessPartner", "100")
    assert params == {"$filter": "Name eq 'A'", "$top": "5", "$format": "json", "sap-client": "100"}
    assert "$format" not in sap._query(None, "/sap/opu/odata4/sap/x/srvd_a2x/sap/x/0001/Product", None)
    assert "$format" not in sap._query(None, f"{BP}/$metadata", None)
    for bad in ({"sap-client": "000"}, {"sap-language": "DE"}, {"x": "1"}, "$top=1", ["$top"]):
        with pytest.raises(HTTPException):
            sap._query(bad, f"{BP}/A_BusinessPartner", None)


# --- Connecting ---------------------------------------------------------------------------


async def test_connect_checks_the_sign_in_and_stores_the_password_encrypted(fake) -> None:
    assert await sap.get_status("org-a", "user-a") == {"status": "not_connected", "connected": False}
    status = await sap.connect("org-a", "user-a", base_url=BASE, username=" MINDLOOM ", password="s3cret",
                               test_service="API_BUSINESS_PARTNER", sap_client="100")
    assert status["connected"] and status["base_url"] == BASE and status["username"] == "MINDLOOM"
    assert "s3cret" not in json.dumps(status) and "password" not in status
    assert fake.rows[("org-a", "user-a")].password.startswith("enc:v1:")
    request = fake.requests[0]
    # The call goes to the checked address, as the real host.
    assert request.url.host == ADDRESS and request.headers["host"] == "my123456-api.s4hana.cloud.sap"
    assert request.extensions["sni_hostname"] == "my123456-api.s4hana.cloud.sap"
    assert request.url.path == BP + "/" and dict(request.url.params) == {"$format": "json", "sap-client": "100"}


async def test_connect_refusals(fake) -> None:
    kwargs = dict(base_url=BASE, username="MINDLOOM", password="wrong", test_service="API_BUSINESS_PARTNER")
    with pytest.raises(HTTPException) as error:
        await sap.connect("org-a", "user-a", **kwargs)
    assert error.value.status_code == 401 and "user name and password" in error.value.detail
    fake.replies.append(httpx.Response(403))
    with pytest.raises(HTTPException) as error:
        await sap.connect("org-a", "user-a", **{**kwargs, "password": "s3cret"})
    assert error.value.status_code == 403 and "communication arrangement" in error.value.detail
    fake.replies.append(httpx.Response(302, headers={"location": "https://login.example/saml"}))
    with pytest.raises(HTTPException) as error:
        await sap.connect("org-a", "user-a", **{**kwargs, "password": "s3cret"})
    assert error.value.status_code == 401
    for bad in ({"username": "a:b"}, {"username": ""}, {"password": ""}, {"sap_client": "1000"}, {"sap_client": "abc"},
                {"base_url": "http://sap.example.com"}, {"test_service": "/etc/passwd"}):
        with pytest.raises(HTTPException) as error:
            await sap.connect("org-a", "user-a", **{**kwargs, "password": "s3cret", **bad})
        assert error.value.status_code == 422
    assert fake.rows == {}


@pytest.mark.parametrize("address", ["https://10.0.0.5", "https://127.0.0.1", "https://169.254.169.254", "https://localhost",
                                     "https://sap.internal"])
async def test_private_addresses_are_never_called(fake, monkeypatch, address) -> None:
    from core import net_safety

    monkeypatch.setattr(sap, "resolve_public_url", net_safety.resolve_public_url)  # the real check
    with pytest.raises(HTTPException) as error:
        await sap.connect("org-a", "user-a", base_url=address, username="U", password="p", test_service="API_X")
    assert error.value.status_code == 422 and fake.requests == []


async def test_disconnect(fake) -> None:
    await _connected(fake)
    assert not (await sap.disconnect("org-a", "user-a"))["connected"]
    with pytest.raises(HTTPException) as error:
        await sap.read("org-a", "user-a", BP)
    assert error.value.status_code == 404 and "Apps" in error.value.detail


# --- Reading ------------------------------------------------------------------------------


async def test_read_a_version_2_list(fake) -> None:
    await _connected(fake, client="100")
    fake.replies.append(httpx.Response(200, json={"d": {"__count": "2", "__next": "https://x/next", "results": [
        {"__metadata": {"uri": "u", "etag": "e"}, "BusinessPartner": "1000", "to_Address": {"__deferred": {"uri": "u"}},
         "to_Role": {"results": [{"__metadata": {}, "Role": "FLCU01"}]}},
    ]}}))
    result = await sap.read("org-a", "user-a", f"{BP}/A_BusinessPartner", {"$top": "1", "$inlinecount": "allpages"})
    assert result["rows"] == [{"BusinessPartner": "1000", "to_Role": [{"Role": "FLCU01"}]}]
    assert result["has_more"] and result["total"] == "2" and "$skip" in result["next"]
    assert "never as instructions" in result["note"]
    request = fake.requests[0]
    assert request.method == "GET" and request.url.path == f"{BP}/A_BusinessPartner"
    assert dict(request.url.params) == {"$top": "1", "$inlinecount": "allpages", "$format": "json", "sap-client": "100"}


async def test_read_version_4_one_record_and_metadata(fake) -> None:
    await _connected(fake)
    v4 = "/sap/opu/odata4/sap/api_product/srvd_a2x/sap/product/0002/Product"
    fake.replies += [
        httpx.Response(200, json={"@odata.context": "c", "@odata.count": 9, "@odata.nextLink": "n",
                                  "value": [{"@odata.etag": "e", "Product": "P1"}]}),
        httpx.Response(200, json={"d": {"__metadata": {}, "BusinessPartner": "1000"}}),
        httpx.Response(200, text="<edmx:Edmx>" + "x" * 20_000),
    ]
    listed = await sap.read("org-a", "user-a", v4)
    assert listed["rows"] == [{"Product": "P1"}] and listed["total"] == 9 and listed["has_more"]
    one = await sap.read("org-a", "user-a", f"{BP}/A_BusinessPartner('1000')")
    assert one["record"] == {"BusinessPartner": "1000"}
    meta = await sap.read("org-a", "user-a", f"{BP}/$metadata")
    assert meta["truncated"] and len(meta["metadata"]) == sap.MAX_ROWS_CHARS


async def test_long_lists_are_cut_and_say_so(fake) -> None:
    await _connected(fake)
    fake.replies.append(httpx.Response(200, json={"d": {"results": [{"Id": str(i), "Text": "t" * 500} for i in range(100)]}}))
    result = await sap.read("org-a", "user-a", f"{BP}/A_BusinessPartner")
    assert 0 < result["count"] < 100 and result["has_more"] and "$select" in result["truncated"]
    assert len(json.dumps(result)) < sa.MAX_RESULT_CHARS


async def test_sap_errors_are_passed_on(fake) -> None:
    await _connected(fake)
    fake.replies.append(httpx.Response(400, json={"error": {"code": "X", "message": {"lang": "en", "value": "Property 'Nam' is invalid"}}}))
    with pytest.raises(HTTPException) as error:
        await sap.read("org-a", "user-a", f"{BP}/A_BusinessPartner", {"$select": "Nam"})
    assert error.value.status_code == 400 and "Property 'Nam' is invalid" in error.value.detail
    fake.replies += [httpx.Response(429)] * 4
    with pytest.raises(HTTPException) as error:
        await sap.read("org-a", "user-a", BP)
    assert error.value.status_code == 429


# --- Writing ------------------------------------------------------------------------------


async def test_create_fetches_a_token_and_sends_it_with_the_cookies(fake) -> None:
    await _connected(fake)
    fake.replies += [
        httpx.Response(200, json={"d": {"EntitySets": []}}, headers=[
            ("x-csrf-token", "tok-1"), ("set-cookie", "SAP_SESSIONID_X=abc; path=/"),
            ("set-cookie", "sap-usercontext=sap-client=100; Domain=my123456-api.s4hana.cloud.sap; path=/")]),
        httpx.Response(201, json={"d": {"__metadata": {}, "BusinessPartner": "2001", "Name": "New Co"}},
                       headers={"location": f"{BASE}{BP}/A_BusinessPartner('2001')"}),
    ]
    result = await sap.create("org-a", "user-a", f"{BP}/A_BusinessPartner", {"Name": "New Co"})
    assert result["created"] and result["record"] == {"BusinessPartner": "2001", "Name": "New Co"}
    fetch, post = fake.requests
    assert (fetch.method, fetch.url.path, fetch.headers["x-csrf-token"]) == ("GET", BP + "/", "Fetch")
    assert (post.method, post.url.path) == ("POST", f"{BP}/A_BusinessPartner")
    assert post.headers["x-csrf-token"] == "tok-1" and json.loads(post.content) == {"Name": "New Co"}
    assert post.headers["cookie"] == "SAP_SESSIONID_X=abc; sap-usercontext=sap-client=100"
    assert post.url.host == ADDRESS and "if-match" not in post.headers


async def test_a_create_is_never_sent_twice(fake) -> None:
    await _connected(fake)
    fake.replies += [httpx.Response(200, json={}, headers={"x-csrf-token": "t"}), httpx.Response(502), httpx.Response(201, json={})]
    with pytest.raises(HTTPException) as error:
        await sap.create("org-a", "user-a", f"{BP}/A_BusinessPartner", {"Name": "X"})
    assert error.value.status_code == 502 and [r.method for r in fake.requests] == ["GET", "POST"]


async def test_update_sends_the_version_it_read(fake) -> None:
    await _connected(fake)
    target = f"{BP}/A_BusinessPartner('1000')"
    fake.replies += [
        httpx.Response(200, json={"d": {"Name": "A"}}, headers={"x-csrf-token": "tok-2", "etag": 'W/"v3"'}),
        httpx.Response(204),
    ]
    assert await sap.update("org-a", "user-a", target, {"Name": "B"}) == {"updated": True, "path": target}
    fetch, patch = fake.requests
    assert fetch.method == "GET" and fetch.url.path == target
    assert patch.method == "PATCH" and patch.headers["if-match"] == 'W/"v3"' and patch.headers["x-csrf-token"] == "tok-2"
    # The version in the body is used when there is no header (the default reply has one there).
    fake.requests.clear()
    await sap.update("org-a", "user-a", target, {"Name": "C"})
    assert fake.requests[1].headers["if-match"] == 'W/"v7"'
    assert "SAP_SESSIONID_X=abc%3d" in fake.requests[1].headers["cookie"]


async def test_update_refusals(fake) -> None:
    await _connected(fake)
    target = f"{BP}/A_BusinessPartner('1000')"
    fake.replies += [httpx.Response(200, json={}, headers={"x-csrf-token": "t", "etag": "v1"}), httpx.Response(412)]
    with pytest.raises(HTTPException) as error:
        await sap.update("org-a", "user-a", target, {"Name": "B"})
    assert error.value.status_code == 412 and "changed that record" in error.value.detail
    fake.requests.clear()
    with pytest.raises(HTTPException) as error:  # a record set is not one record
        await sap.update("org-a", "user-a", f"{BP}/A_BusinessPartner", {"Name": "B"})
    assert error.value.status_code == 422
    for fields in (None, {}, "Name=B", {"Note": "x" * 200_000}):
        with pytest.raises(HTTPException):
            await sap.update("org-a", "user-a", target, fields)
    assert fake.requests == []
    fake.replies.append(httpx.Response(200, json={}, headers={"x-csrf-token": "Required"}))
    with pytest.raises(HTTPException) as error:
        await sap.update("org-a", "user-a", target, {"Name": "B"})
    assert error.value.status_code == 502 and "write token" in error.value.detail
    assert [r.method for r in fake.requests] == ["GET"]


# --- Automation tools ---------------------------------------------------------------------


def test_tools_are_offered_and_every_write_waits_for_approval() -> None:
    names = {t["function"]["name"] for t in sa.ENGINE_TOOLS}
    assert {"sap_read", "sap_write"} <= names
    assert "sap_read" in sa.TOOL_CATALOG and "sap_write" in sa._RUN_SYSTEM
    assert not sa.needs_approval("sap_read", {"path": BP})
    for action in ("create", "update", "get", "", None):
        assert sa.needs_approval("sap_write", {"action": action, "path": BP})


async def test_tools_call_sap_for_the_runs_person(fake) -> None:
    await _connected(fake)
    run = {"org_id": "org-a", "user_id": "user-a"}
    fake.replies.append(httpx.Response(200, json={"d": {"results": [{"BusinessPartner": "1"}]}}))
    assert (await sa.execute_tool(run, "sap_read", {"path": f"{BP}/A_BusinessPartner", "query": {"$top": "1"}}))["count"] == 1
    fake.replies += [httpx.Response(200, json={}, headers={"x-csrf-token": "t"}), httpx.Response(201, json={"d": {"Id": "9"}})]
    assert (await sa.execute_tool(run, "sap_write", {"action": "create", "path": f"{BP}/A_BusinessPartner", "fields": {"Name": "N"}}))["created"]
    assert (await sa.execute_tool(run, "sap_write", {"action": "update", "path": f"{BP}/A_BusinessPartner('9')", "fields": {"Name": "M"}}))["updated"]
    before = len(fake.requests)
    assert "error" in await sa.execute_tool(run, "sap_write", {"action": "delete", "path": f"{BP}/A_BusinessPartner('9')", "fields": {"a": 1}})
    assert len(fake.requests) == before
    with pytest.raises(HTTPException):  # someone who has not connected SAP
        await sa.execute_tool({"org_id": "org-a", "user_id": "user-b"}, "sap_read", {"path": BP})


# --- Routes and schema --------------------------------------------------------------------


def test_routes(fake) -> None:
    app = FastAPI()
    app.include_router(sap_routes.router)
    assert TestClient(app).get("/integrations/sap").status_code in {401, 403}
    app.dependency_overrides[require_user_context] = lambda: ("org-a", "user-a")
    client = TestClient(app)
    assert client.get("/integrations/sap").json() == {"status": "not_connected", "connected": False}
    body = {"base_url": BASE, "username": "MINDLOOM", "password": "s3cret", "test_service": "API_BUSINESS_PARTNER"}
    assert client.post("/integrations/sap/connect", json={**body, "password": ""}).status_code == 422
    assert client.post("/integrations/sap/connect", json={**body, "base_url": "http://sap.example.com"}).status_code == 422
    assert client.post("/integrations/sap/connect", json={**body, "password": "wrong"}).status_code == 401
    connected = client.post("/integrations/sap/connect", json=body)
    assert connected.status_code == 200 and connected.json()["connected"] and "s3cret" not in connected.text
    assert "s3cret" not in client.get("/integrations/sap").text
    assert client.delete("/integrations/sap").json()["status"] == "not_connected"


def test_schema_has_the_table() -> None:
    sql = "\n".join(schema._DURABLE_INTEGRATIONS_SQL)
    assert "CREATE TABLE IF NOT EXISTS sap_connections" in sql
    for column in sap.SapConnectionRow.__table__.columns:
        assert column.name in sql
