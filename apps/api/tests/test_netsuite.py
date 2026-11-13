"""NetSuite: certificate sign-in, SuiteQL reads, record writes behind approval."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes import netsuite as netsuite_routes
from core import provider_http, schema, token_crypto
from keeper.auth import require_user_context
from shuttle import skill_automation as sa
from weaver import netsuite as ns

CLIENT_ID = "a" * 64
CERT_ID = "cert-AbC123_xyz"
HOST = "1234567-sb1.suitetalk.api.netsuite.com"
_real_client = httpx.AsyncClient
_KEY = ns.make_certificate(key_bits=2048)  # one small key pair for the whole file


class FakeNetSuite:
    """NetSuite's token endpoint and REST API, with every request recorded."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.tokens = 0
        self.token_status = 200
        self.replies: list[httpx.Response] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/oauth2/v1/token"):
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            self.tokens += 1
            return httpx.Response(200, json={"access_token": f"tok-{self.tokens}", "expires_in": "3600", "token_type": "Bearer"})
        return self.replies.pop(0) if self.replies else httpx.Response(200, json={"items": []})

    @property
    def api_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if "/oauth2/" not in r.url.path]


@pytest.fixture
def fake(monkeypatch):
    rows: dict[tuple[str, str], SimpleNamespace] = {}

    async def get_row(org_id, user_id):
        return rows.get((org_id, user_id))

    async def upsert_row(org_id, user_id, values):
        now = datetime.now(timezone.utc)
        rows[(org_id, user_id)] = SimpleNamespace(org_id=org_id, user_id=user_id, created_at=now, updated_at=now, **values)

    async def update_row(org_id, user_id, **values):
        vars(rows[(org_id, user_id)]).update(values)

    async def delete_row(org_id, user_id):
        rows.pop((org_id, user_id), None)

    async def no_sleep(_seconds):
        return None

    server = FakeNetSuite()
    server.rows = rows
    monkeypatch.setattr(ns, "_get_row", get_row)
    monkeypatch.setattr(ns, "_upsert_row", upsert_row)
    monkeypatch.setattr(ns, "_update_row", update_row)
    monkeypatch.setattr(ns, "_delete_row", delete_row)
    monkeypatch.setattr(ns, "make_certificate", lambda: _KEY)
    from weaver import erp_knowledge

    erp_knowledge.use_memory_store(True)
    from weaver import erp_audit

    erp_audit.use_memory_store(True)
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(token_crypto, "get_settings", lambda: SimpleNamespace(token_encryption_key=key))
    monkeypatch.setattr(provider_http.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(ns.httpx, "AsyncClient",
                        lambda **kw: _real_client(transport=httpx.MockTransport(server.handler), **kw))
    ns._slots.clear()
    return server


async def _connected(fake: FakeNetSuite) -> None:
    await ns.start_setup("org-a", "user-a", "1234567-sb1")
    await ns.finish_setup("org-a", "user-a", CLIENT_ID, CERT_ID)
    fake.requests.clear()


# --- Names and addresses ------------------------------------------------------------------


def test_account_id_becomes_the_only_host() -> None:
    assert ns.normalize_account_id(" 1234567-sb1 ") == "1234567_SB1"
    assert ns.base_url("1234567_SB1") == f"https://{HOST}"
    assert ns.base_url("tstdrv99") == "https://tstdrv99.suitetalk.api.netsuite.com"


@pytest.mark.parametrize("bad", ["", "ab", "evil.com", "1234567.evil.com/", "123 456", "1234567_SB1_X", "a@b", "1234567/../x"])
def test_bad_account_ids_are_refused(bad) -> None:
    with pytest.raises(HTTPException) as error:
        ns.normalize_account_id(bad)
    assert error.value.status_code == 422


@pytest.mark.parametrize("kind,ident", [("../customer", "1"), ("customer/1", "1"), ("customer", "1/../2"),
                                        ("customer", "1?x=1"), ("", "1"), ("customer", "a b")])
def test_record_paths_cannot_leave_the_record_api(kind, ident) -> None:
    with pytest.raises(HTTPException):
        ns._record_path(kind, ident)


def test_record_paths() -> None:
    assert ns._record_path("salesOrder") == "/services/rest/record/v1/salesOrder"
    assert ns._record_path("customer", "eid:CUST-9") == "/services/rest/record/v1/customer/eid:CUST-9"


# --- Certificate and signed request -------------------------------------------------------


def test_signed_request_is_what_netsuite_checks() -> None:
    private_pem, certificate_pem, expires = _KEY
    assert "PRIVATE KEY" in private_pem and "BEGIN CERTIFICATE" in certificate_pem
    assert timedelta(days=720) < expires - datetime.now(timezone.utc) <= timedelta(days=730)
    token = ns.signed_request("1234567_SB1", CLIENT_ID, CERT_ID, private_pem)
    header = jwt.get_unverified_header(token)
    assert header == {"alg": "PS256", "typ": "JWT", "kid": CERT_ID}
    public_key = x509.load_pem_x509_certificate(certificate_pem.encode()).public_key()
    audience = f"https://{HOST}/services/rest/auth/oauth2/v1/token"
    claims = jwt.decode(token, public_key, algorithms=["PS256"], audience=audience)
    assert claims["iss"] == CLIENT_ID and claims["scope"] == ["rest_webservices"]
    assert 0 < claims["exp"] - claims["iat"] < 3600


# --- Setup --------------------------------------------------------------------------------


async def test_setup_then_connect(fake) -> None:
    assert await ns.get_status("org-a", "user-a") == {"status": "not_connected", "connected": False}
    pending = await ns.start_setup("org-a", "user-a", "1234567-sb1")
    assert pending["status"] == "pending" and not pending["connected"]
    assert pending["account_id"] == "1234567_SB1" and "BEGIN CERTIFICATE" in pending["certificate_pem"]
    assert "PRIVATE" not in json.dumps(pending) and "private_key" not in pending
    assert not await ns.is_connected("org-a", "user-a")

    active = await ns.finish_setup("org-a", "user-a", f" {CLIENT_ID} ", CERT_ID)
    assert active["connected"] and active["status"] == "active"
    assert "tok-1" not in json.dumps(active) and "access_token" not in active
    request = fake.requests[0]
    assert request.method == "POST" and request.url.host == HOST
    form = parse_qs(request.content.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_assertion_type"] == ["urn:ietf:params:oauth:client-assertion-type:jwt-bearer"]
    assert jwt.get_unverified_header(form["client_assertion"][0])["kid"] == CERT_ID
    assert await ns.is_connected("org-a", "user-a")
    assert not await ns.is_connected("org-a", "user-b")
    # At rest, the private key and the token are encrypted.
    row = fake.rows[("org-a", "user-a")]
    assert row.private_key.startswith("enc:v1:") and row.access_token.startswith("enc:v1:")


async def test_connect_needs_setup_and_real_ids(fake) -> None:
    with pytest.raises(HTTPException) as error:
        await ns.finish_setup("org-a", "user-a", CLIENT_ID, CERT_ID)
    assert error.value.status_code == 409
    await ns.start_setup("org-a", "user-a", "1234567")
    for client_id, certificate_id in [("short", CERT_ID), (CLIENT_ID, "x"), (CLIENT_ID + "/", CERT_ID)]:
        with pytest.raises(HTTPException) as error:
            await ns.finish_setup("org-a", "user-a", client_id, certificate_id)
        assert error.value.status_code == 422
    assert fake.requests == []


async def test_refused_sign_in_stays_pending(fake) -> None:
    await ns.start_setup("org-a", "user-a", "1234567")
    fake.token_status = 400
    with pytest.raises(HTTPException) as error:
        await ns.finish_setup("org-a", "user-a", CLIENT_ID, CERT_ID)
    assert error.value.status_code == 401 and "certificate" in error.value.detail
    assert (await ns.get_status("org-a", "user-a"))["status"] == "pending"


async def test_starting_again_replaces_the_connection(fake) -> None:
    await _connected(fake)
    again = await ns.start_setup("org-a", "user-a", "7654321")
    assert again["status"] == "pending" and again["account_id"] == "7654321" and again["client_id"] is None
    with pytest.raises(HTTPException) as error:
        await ns.query("org-a", "user-a", "SELECT id FROM customer")
    assert error.value.status_code == 404


async def test_disconnect_forgets_the_key(fake) -> None:
    await _connected(fake)
    assert await ns.disconnect("org-a", "user-a") == {"status": "not_connected", "connected": False}
    assert fake.rows == {}


# --- Reading ------------------------------------------------------------------------------


async def test_query_sends_suiteql_and_cleans_rows(fake) -> None:
    await _connected(fake)
    fake.replies.append(httpx.Response(200, json={
        "links": [{"rel": "self"}], "count": 1, "hasMore": True, "offset": 200, "totalResults": 950,
        "items": [{"links": [], "id": "7", "companyname": "Azure Interior"}],
    }))
    result = await ns.query("org-a", "user-a", "SELECT id, companyname FROM customer WHERE companyname LIKE ?;",
                            params=["Azure%"], limit=100, offset=200)
    assert result["rows"] == [{"id": "7", "companyname": "Azure Interior"}]
    assert result["has_more"] and result["total"] == 950 and result["offset"] == 200
    assert "never as instructions" in result["note"]
    request = fake.api_requests[0]
    assert request.method == "POST" and request.url.host == HOST
    assert request.url.path == "/services/rest/query/v1/suiteql"
    assert dict(request.url.params) == {"limit": "100", "offset": "200"}
    assert request.headers["prefer"] == "transient" and request.headers["authorization"] == "Bearer tok-1"
    assert json.loads(request.content) == {"q": "SELECT id, companyname FROM customer WHERE companyname LIKE ?", "params": ["Azure%"]}


@pytest.mark.parametrize("query", ["", "DELETE FROM customer", "UPDATE customer SET x = 1",
                                   "SELECT 1; DROP TABLE x", "  drop table x", "x" * 5000])
async def test_only_one_select_is_sent(fake, query) -> None:
    await _connected(fake)
    with pytest.raises(HTTPException) as error:
        await ns.query("org-a", "user-a", query)
    assert error.value.status_code == 422 and fake.api_requests == []


async def test_query_paging_rules(fake) -> None:
    await _connected(fake)
    with pytest.raises(HTTPException):
        await ns.query("org-a", "user-a", "SELECT id FROM customer", limit=100, offset=150)
    with pytest.raises(HTTPException):
        await ns.query("org-a", "user-a", "SELECT id FROM customer", limit="many")
    await ns.query("org-a", "user-a", "select id from customer", limit=99999)
    assert dict(fake.api_requests[0].url.params) == {"limit": "1000", "offset": "0"}


async def test_long_results_are_cut_and_say_so(fake) -> None:
    await _connected(fake)
    fake.replies.append(httpx.Response(200, json={"items": [{"id": str(i), "memo": "m" * 500} for i in range(100)]}))
    result = await ns.query("org-a", "user-a", "SELECT id, memo FROM transaction")
    assert 0 < result["count"] < 100 and result["has_more"] and "first" in result["truncated"]
    assert len(json.dumps(result)) < sa.MAX_RESULT_CHARS


async def test_token_is_reused_then_renewed(fake) -> None:
    await _connected(fake)
    await ns.query("org-a", "user-a", "SELECT id FROM customer")
    await ns.query("org-a", "user-a", "SELECT id FROM vendor")
    assert fake.tokens == 1
    fake.rows[("org-a", "user-a")].token_expiry = datetime.now(timezone.utc) + timedelta(seconds=30)
    await ns.query("org-a", "user-a", "SELECT id FROM item")
    assert fake.tokens == 2 and fake.api_requests[-1].headers["authorization"] == "Bearer tok-2"
    # A token NetSuite no longer accepts is replaced once.
    fake.replies += [httpx.Response(401, json={}), httpx.Response(200, json={"items": [{"id": "1"}]})]
    assert (await ns.query("org-a", "user-a", "SELECT id FROM item"))["count"] == 1
    assert fake.tokens == 3


async def test_expired_certificate_asks_to_reconnect(fake) -> None:
    await _connected(fake)
    row = fake.rows[("org-a", "user-a")]
    row.token_expiry = row.certificate_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    with pytest.raises(HTTPException) as error:
        await ns.query("org-a", "user-a", "SELECT id FROM customer")
    assert error.value.status_code == 401 and "run out" in error.value.detail


async def test_busy_and_refused_calls(fake) -> None:
    await _connected(fake)
    fake.replies += [httpx.Response(429, json={})] * 4
    with pytest.raises(HTTPException) as error:
        await ns.query("org-a", "user-a", "SELECT id FROM customer")
    assert error.value.status_code == 429 and len(fake.api_requests) == 4
    fake.replies.append(httpx.Response(400, json={"o:errorDetails": [{"detail": "Invalid search query. Unknown field 'nam'."}]}))
    with pytest.raises(HTTPException) as error:
        await ns.query("org-a", "user-a", "SELECT nam FROM customer")
    assert error.value.status_code == 400 and "Unknown field" in error.value.detail


async def test_not_connected(fake) -> None:
    with pytest.raises(HTTPException) as error:
        await ns.get_record("org-a", "user-a", "customer", "7")
    assert error.value.status_code == 404 and "Apps" in error.value.detail


# --- Records ------------------------------------------------------------------------------


async def test_get_create_update_record(fake) -> None:
    await _connected(fake)
    fake.replies += [
        httpx.Response(200, json={"id": "7", "companyName": "Azure", "links": [], "currency": {"id": "1", "links": []}}),
        httpx.Response(204, headers={"Location": f"https://{HOST}/services/rest/record/v1/customer/88"}),
        httpx.Response(204),
    ]
    got = await ns.get_record("org-a", "user-a", "customer", "7")
    assert got["record"] == {"id": "7", "companyName": "Azure", "currency": {"id": "1"}}
    created = await ns.create_record("org-a", "user-a", "customer", {"companyName": "New Co"})
    assert created == {"created": True, "record_type": "customer", "id": "88"}
    updated = await ns.update_record("org-a", "user-a", "customer", "88", {"memo": "x"})
    assert updated == {"updated": True, "record_type": "customer", "id": "88"}
    get, post, patch = fake.api_requests
    assert (get.method, get.url.path) == ("GET", "/services/rest/record/v1/customer/7")
    assert (post.method, post.url.path, json.loads(post.content)) == ("POST", "/services/rest/record/v1/customer", {"companyName": "New Co"})
    assert (patch.method, patch.url.path) == ("PATCH", "/services/rest/record/v1/customer/88")


async def test_a_create_is_never_sent_twice(fake) -> None:
    await _connected(fake)
    fake.replies += [httpx.Response(502), httpx.Response(204)]
    with pytest.raises(HTTPException) as error:
        await ns.create_record("org-a", "user-a", "invoice", {"memo": "x"})
    assert error.value.status_code == 502 and len(fake.api_requests) == 1
    fake.replies[:] = [httpx.Response(502), httpx.Response(204)]
    await ns.update_record("org-a", "user-a", "invoice", "9", {"memo": "x"})  # an update is safe to repeat
    assert len(fake.api_requests) == 3


async def test_writes_need_fields_and_a_sane_size(fake) -> None:
    await _connected(fake)
    for fields in (None, {}, "memo=x", []):
        with pytest.raises(HTTPException):
            await ns.create_record("org-a", "user-a", "customer", fields)
    with pytest.raises(HTTPException) as error:
        await ns.update_record("org-a", "user-a", "customer", "1", {"memo": "x" * 200_000})
    assert error.value.status_code == 413 and fake.api_requests == []


# --- Automation tools ---------------------------------------------------------------------


def test_tools_are_offered_and_writes_wait_for_approval() -> None:
    names = {t["function"]["name"] for t in sa.ENGINE_TOOLS}
    assert {"netsuite_query", "netsuite_record"} <= names
    assert "netsuite_query" in sa.TOOL_CATALOG and "netsuite_record" in sa._RUN_SYSTEM
    assert not sa.needs_approval("netsuite_query", {"query": "SELECT 1"})
    assert not sa.needs_approval("netsuite_record", {"action": "get", "record_type": "customer", "id": "1"})
    for action in ("create", "update", "delete", "", None, "GET "):
        expected = str(action or "").strip().lower() != "get"
        assert sa.needs_approval("netsuite_record", {"action": action, "record_type": "customer"}) is expected
    enum = next(t for t in sa.ENGINE_TOOLS if t["function"]["name"] == "netsuite_record")
    assert enum["function"]["parameters"]["properties"]["action"]["enum"] == ["get", "fields", "create", "update"]
    assert not sa.needs_approval("netsuite_record", {"action": "fields", "record_type": "customer"})


async def test_tools_call_netsuite_for_the_runs_person(fake) -> None:
    await _connected(fake)
    run = {"org_id": "org-a", "user_id": "user-a"}
    fake.replies += [
        httpx.Response(200, json={"items": [{"id": "7"}]}),
        httpx.Response(200, json={"id": "7"}),
        httpx.Response(204, headers={"Location": "/services/rest/record/v1/vendor/5"}),
        httpx.Response(204),
    ]
    assert (await sa.execute_tool(run, "netsuite_query", {"query": "SELECT id FROM customer", "params": ["a"]}))["count"] == 1
    assert (await sa.execute_tool(run, "netsuite_record", {"action": "get", "record_type": "customer", "id": "7"}))["record"] == {"id": "7"}
    assert (await sa.execute_tool(run, "netsuite_record", {"action": "create", "record_type": "vendor", "fields": {"companyName": "V"}}))["id"] == "5"
    assert (await sa.execute_tool(run, "netsuite_record", {"action": "update", "record_type": "vendor", "id": "5", "fields": {"memo": "m"}}))["updated"]
    assert [r.method for r in fake.api_requests] == ["POST", "GET", "POST", "PATCH"]
    assert "error" in await sa.execute_tool(run, "netsuite_record", {"action": "delete", "record_type": "vendor", "id": "5"})
    assert "error" in await sa.execute_tool(run, "netsuite_record", {"action": "update", "record_type": "vendor", "fields": {"a": 1}})
    assert len(fake.api_requests) == 4
    with pytest.raises(HTTPException):  # someone who has not connected NetSuite
        await sa.execute_tool({"org_id": "org-a", "user_id": "user-b"}, "netsuite_query", {"query": "SELECT 1 FROM customer"})


# --- Routes and schema --------------------------------------------------------------------


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(netsuite_routes.router)
    app.dependency_overrides[require_user_context] = lambda: ("org-a", "user-a")
    return TestClient(app)


def test_routes(client, fake) -> None:
    assert client.get("/integrations/netsuite").json() == {"status": "not_connected", "connected": False}
    assert client.post("/integrations/netsuite/setup", json={"account_id": "x"}).status_code == 422
    assert client.post("/integrations/netsuite/setup", json={"account_id": "evil.com/x"}).status_code == 422
    pending = client.post("/integrations/netsuite/setup", json={"account_id": "1234567_SB1"})
    assert pending.status_code == 200 and pending.json()["status"] == "pending"
    assert client.post("/integrations/netsuite/connect", json={"client_id": "short", "certificate_id": CERT_ID}).status_code == 422
    active = client.post("/integrations/netsuite/connect", json={"client_id": CLIENT_ID, "certificate_id": CERT_ID})
    assert active.status_code == 200 and active.json()["connected"]
    assert "private_key" not in active.text and "tok-1" not in active.text
    assert client.delete("/integrations/netsuite").json()["status"] == "not_connected"


def test_routes_need_a_signed_in_person() -> None:
    app = FastAPI()
    app.include_router(netsuite_routes.router)
    assert TestClient(app).get("/integrations/netsuite").status_code in {401, 403}


def test_schema_has_the_table() -> None:
    sql = "\n".join(schema._DURABLE_INTEGRATIONS_SQL)
    assert "CREATE TABLE IF NOT EXISTS netsuite_connections" in sql
    for column in ns.NetSuiteConnectionRow.__table__.columns:
        assert column.name in sql


# --- Review fixes and field look-up -------------------------------------------------------


def test_record_ids_cannot_climb_out() -> None:
    for ident in ("..", "a..b", "eid:.."):
        with pytest.raises(HTTPException):
            ns._record_path("customer", ident)


async def test_fields_of_a_record_type(fake) -> None:
    await _connected(fake)
    fake.replies.append(httpx.Response(200, json={"type": "object", "properties": {
        "links": {"type": "array"}, "companyName": {"title": "Company Name", "type": "string"},
        "email": {"title": "Email", "type": "string"}, "balance": {"type": "number", "format": "double", "readOnly": True},
        "currency": {"$ref": "/services/rest/record/v1/metadata-catalog/currency"}, "odd": "not a spec",
    }}))
    described = await ns.describe_record("org-a", "user-a", "customer")
    assert described["record_type"] == "customer" and described["fields"] == [
        {"name": "companyName", "type": "string", "label": "Company Name"},
        {"name": "email", "type": "string"},
        {"name": "balance", "type": "double", "read_only": True},
        {"name": "currency", "type": "record"},
    ]
    request = fake.api_requests[0]
    assert request.url.path == "/services/rest/record/v1/metadata-catalog/customer"
    assert request.headers["accept"] == "application/schema+json"
    fake.replies.append(httpx.Response(200, text="not json"))
    assert (await ns.describe_record("org-a", "user-a", "customer"))["fields"] == []
    with pytest.raises(HTTPException):
        await ns.describe_record("org-a", "user-a", "../x")
    # Through the automation tool, without approval.
    fake.replies.append(httpx.Response(200, json={"properties": {"memo": {"type": "string"}}}))
    result = await sa.execute_tool({"org_id": "org-a", "user_id": "user-a"}, "netsuite_record",
                                   {"action": "fields", "record_type": "invoice"})
    assert result["fields"] == [{"name": "memo", "type": "string"}]


async def test_a_create_that_may_have_happened_says_so(fake) -> None:
    await _connected(fake)
    fake.replies.append(httpx.Response(504))
    with pytest.raises(HTTPException) as error:
        await ns.create_record("org-a", "user-a", "invoice", {"memo": "x"})
    assert "may have been created" in error.value.detail
    fake.replies.append(httpx.Response(400, json={"o:errorDetails": [{"detail": "Invalid field"}]}))
    with pytest.raises(HTTPException) as error:
        await ns.create_record("org-a", "user-a", "invoice", {"memo": "x"})
    assert "may have been created" not in error.value.detail


async def test_an_odd_token_answer_is_a_clean_error(fake, monkeypatch) -> None:
    await ns.start_setup("org-a", "user-a", "1234567")
    for body in (httpx.Response(200, text="<html>"), httpx.Response(200, json={"access_token": "t", "expires_in": "soon"}),
                 httpx.Response(200, json=["x"])):
        fake.handler = lambda request, body=body: body
        monkeypatch.setattr(ns.httpx, "AsyncClient", lambda **kw: _real_client(transport=httpx.MockTransport(fake.handler), **kw))
        with pytest.raises(HTTPException) as error:
            await ns.finish_setup("org-a", "user-a", CLIENT_ID, CERT_ID)
        assert error.value.status_code == 502
