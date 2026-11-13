"""SAP beyond a user and password: OAuth, client certificates, Business One, and the relay
for systems inside a company network."""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes import relay as relay_routes
from core import schema, token_crypto
from keeper.auth import require_user_context
from weaver import netsuite, relay, sap

BASE = "https://my123456-api.s4hana.cloud.sap"
B1 = "https://b1.example.com:50000"
INSIDE = "https://sap.corp.internal:44300"
BP = "/sap/opu/odata/sap/API_BUSINESS_PARTNER"
TOKEN_URL = BASE + "/sap/bc/sec/oauth2/token"
KEY = "k" * 43  # this person's relay
CODE = relay.code_of(__import__("hashlib").sha256(KEY.encode()).hexdigest())
_real_client = httpx.AsyncClient
_KEY, _CERT, _ = netsuite.make_certificate(key_bits=2048)


def _basic(name: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{name}:{secret}".encode()).decode()


class Fake:
    """Whatever SAP system the test scripts: ``handler(request) -> httpx.Response``."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.clients: list[dict] = []
        self.handler = lambda request: httpx.Response(200, json={"d": {"results": []}})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def paths(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.requests]


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

    async def resolve(url):
        return url, "203.0.113.10"

    server = Fake()
    server.rows = rows

    def client(**kwargs):
        server.clients.append(dict(kwargs))
        kwargs.pop("verify", None)
        return _real_client(transport=httpx.MockTransport(server.handle), **kwargs)

    from weaver import erp_knowledge

    erp_knowledge.use_memory_store(True)
    from weaver import erp_audit

    erp_audit.use_memory_store(True)
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(token_crypto, "get_settings", lambda: SimpleNamespace(token_encryption_key=key))
    for name, value in (("_get_row", get_row), ("_upsert_row", upsert_row), ("_update_row", update_row),
                        ("_delete_row", delete_row), ("resolve_public_url", resolve)):
        monkeypatch.setattr(sap, name, value)
    monkeypatch.setattr(sap.httpx, "AsyncClient", client)
    relay.use_memory_store(True)
    yield server
    relay.use_memory_store(False)


def _row(fake):
    return fake.rows[("org-a", "user-a")]


# --- OAuth --------------------------------------------------------------------------------


def _oauth_server(fake, *, header_ok=True, tokens=None):
    issued = tokens if tokens is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token") or request.url.path == "/oauth/token":
            form = parse_qs(request.content.decode())
            by_header = request.headers.get("authorization") == _basic("CLIENT", "s3cret")
            by_body = form.get("client_id") == ["CLIENT"] and form.get("client_secret") == ["s3cret"]
            if form.get("grant_type") != ["client_credentials"] or not ((by_header and header_ok) or by_body):
                return httpx.Response(401, json={"error": "invalid_client"})
            issued.append(f"tok-{len(issued) + 1}")
            return httpx.Response(200, json={"access_token": issued[-1], "expires_in": 3600})
        if request.headers.get("authorization") != f"Bearer {issued[-1] if issued else ''}":
            return httpx.Response(401)
        if request.method == "GET":
            return httpx.Response(200, json={"d": {"results": [{"Id": "1"}]}})
        return httpx.Response(201 if request.method == "POST" else 204, json={"d": {"Id": "2"}})

    fake.handler = handler
    return issued


async def _connect_oauth(fake, **more):
    return await sap.connect("org-a", "user-a", **{**dict(
        base_url=BASE, auth="oauth", client_id="CLIENT", client_secret="s3cret", token_url=TOKEN_URL,
        test_service="API_BUSINESS_PARTNER"), **more})


async def test_oauth_connects_caches_the_token_and_renews_it(fake) -> None:
    issued = _oauth_server(fake)
    status = await _connect_oauth(fake)
    assert status["auth"] == "oauth" and status["token_url"] == TOKEN_URL and status["username"] == "CLIENT"
    assert "s3cret" not in json.dumps(status) and "tok-1" not in json.dumps(status)
    assert fake.paths() == [("POST", "/sap/bc/sec/oauth2/token"), ("GET", BP + "/")]
    assert _row(fake).password.startswith("enc:v1:") and _row(fake).access_token.startswith("enc:v1:")

    fake.requests.clear()
    assert (await sap.read("org-a", "user-a", f"{BP}/A_BusinessPartner"))["count"] == 1
    assert fake.paths() == [("GET", f"{BP}/A_BusinessPartner")] and issued == ["tok-1"]  # the saved token

    issued.append("tok-revoked-elsewhere")  # SAP no longer accepts the saved one
    fake.requests.clear()
    assert (await sap.read("org-a", "user-a", f"{BP}/A_BusinessPartner"))["count"] == 1
    assert [m for m, _ in fake.paths()] == ["GET", "POST", "GET"]

    _row(fake).token_expiry = datetime.now(timezone.utc) + timedelta(seconds=30)  # about to run out
    fake.requests.clear()
    await sap.read("org-a", "user-a", f"{BP}/A_BusinessPartner")
    assert [m for m, _ in fake.paths()] == ["POST", "GET"]


async def test_oauth_client_in_the_body_and_a_token_service_elsewhere(fake) -> None:
    _oauth_server(fake, header_ok=False)
    xsuaa = "https://tenant.authentication.eu10.hana.ondemand.com/oauth/token"
    status = await _connect_oauth(fake, token_url=xsuaa)
    assert status["connected"]
    first, second, _service = fake.requests
    assert first.headers["host"] == "tenant.authentication.eu10.hana.ondemand.com" == second.headers["host"]
    assert "client_secret" not in first.content.decode() and "client_secret=s3cret" in second.content.decode()
    # SAP's cookies are not sent to the other host, and the other host's are not kept.
    assert "cookie" not in second.headers


async def test_oauth_writes_work_without_a_write_token(fake) -> None:
    _oauth_server(fake)
    await _connect_oauth(fake)
    fake.requests.clear()
    assert (await sap.create("org-a", "user-a", f"{BP}/A_BusinessPartner", {"Name": "N"}))["created"]
    assert "x-csrf-token" not in fake.requests[-1].headers and fake.requests[-1].method == "POST"


async def test_oauth_refusals(fake) -> None:
    _oauth_server(fake)
    with pytest.raises(HTTPException) as error:
        await _connect_oauth(fake, client_secret="wrong")
    assert error.value.status_code == 401 and "client ID and secret" in error.value.detail
    for bad in ({"token_url": "http://x.example/token"}, {"token_url": BASE}, {"token_url": BASE + "/a/../b"},
                {"token_url": None}, {"client_id": ""}, {"client_secret": ""}):
        with pytest.raises(HTTPException) as error:
            await _connect_oauth(fake, **bad)
        assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:  # through the relay the token service must be on the SAP host
        await _connect_oauth(fake, via="relay", token_url="https://other.example/oauth/token")
    assert error.value.status_code == 422
    assert fake.rows == {}


# --- Client certificate -------------------------------------------------------------------


async def test_certificate_sign_in_presents_the_certificate_and_sends_no_password(fake) -> None:
    status = await sap.connect("org-a", "user-a", base_url=BASE, auth="certificate", certificate_pem=_CERT,
                               private_key_pem=_KEY, test_service="API_BUSINESS_PARTNER")
    assert status["auth"] == "certificate" and "PRIVATE" not in json.dumps(status)
    assert isinstance(fake.clients[0]["verify"], ssl.SSLContext)
    assert "authorization" not in fake.requests[0].headers
    assert _row(fake).password.startswith("enc:v1:") and "BEGIN CERTIFICATE" in _row(fake).certificate_pem
    fake.clients.clear()
    await sap.read("org-a", "user-a", BP)
    assert isinstance(fake.clients[0]["verify"], ssl.SSLContext)


async def test_certificate_refusals(fake) -> None:
    other_key, _other_cert, _ = netsuite.make_certificate(key_bits=2048)
    _old_key, old_cert, _ = netsuite.make_certificate(now=datetime.now(timezone.utc) - timedelta(days=1000), key_bits=2048)
    cases = [
        dict(certificate_pem=_CERT, private_key_pem=other_key),          # not this certificate's key
        dict(certificate_pem="nonsense", private_key_pem=_KEY),
        dict(certificate_pem=_CERT, private_key_pem=""),
        dict(certificate_pem=_CERT, private_key_pem=_KEY.replace("PRIVATE KEY", "ENCRYPTED PRIVATE KEY")),
        dict(certificate_pem=old_cert, private_key_pem=_old_key),        # run out
        dict(certificate_pem=_CERT, private_key_pem=_KEY, via="relay"),  # the relay cannot present it
    ]
    for case in cases:
        with pytest.raises(HTTPException) as error:
            await sap.connect("org-a", "user-a", base_url=BASE, auth="certificate", test_service="API_X", **case)
        assert error.value.status_code == 422
    assert fake.requests == [] and fake.rows == {}


# --- Business One -------------------------------------------------------------------------


def _b1_server(fake, *, root="/b1s/v1", logins=None):
    count = logins if logins is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/Login"):
            if not path.startswith(root):
                return httpx.Response(404)
            body = json.loads(request.content)
            if body != {"CompanyDB": "SBODemoUS", "UserName": "manager", "Password": "s3cret"}:
                return httpx.Response(401, json={"error": {"message": {"value": "Login failed"}}})
            count.append(1)
            return httpx.Response(200, json={"SessionId": f"s{len(count)}", "SessionTimeout": 30}, headers=[
                ("set-cookie", f"B1SESSION=s{len(count)}; HttpOnly"), ("set-cookie", "ROUTEID=.node1; path=/b1s")])
        if f"B1SESSION=s{len(count)}" not in request.headers.get("cookie", ""):
            return httpx.Response(401, json={"error": {"message": {"value": "Invalid session"}}})
        if request.method == "GET" and path.endswith(")"):
            return httpx.Response(200, json={"odata.metadata": "m", "CardCode": "C001", "CardName": "Azure"})
        if request.method == "GET":
            return httpx.Response(200, json={"odata.metadata": "m", "odata.nextLink": "BusinessPartners?$skip=2",
                                             "value": [{"odata.etag": "e", "CardCode": "C001"}, {"CardCode": "C002"}]})
        if request.method == "POST":
            return httpx.Response(201, json={"CardCode": "C003", "CardName": "New Co"})
        return httpx.Response(204)

    fake.handler = handler
    return count


async def _connect_b1(fake, **more):
    return await sap.connect("org-a", "user-a", **{**dict(base_url=B1, system="business_one", username="manager",
                             password="s3cret", company_db="SBODemoUS"), **more})


async def test_business_one_signs_in_and_finds_its_api_version(fake) -> None:
    _b1_server(fake)
    status = await _connect_b1(fake)
    assert status["system"] == "business_one" and status["company_db"] == "SBODemoUS" and status["base_url"] == B1
    assert "s3cret" not in json.dumps(status)
    assert fake.paths() == [("POST", "/b1s/v2/Login"), ("POST", "/b1s/v1/Login")]
    assert _row(fake).api_root == "/b1s/v1" and _row(fake).access_token.startswith("enc:v1:")
    assert fake.requests[0].headers["host"] == "b1.example.com:50000"


async def test_business_one_reads_and_writes_with_its_session(fake) -> None:
    logins = _b1_server(fake)
    await _connect_b1(fake)
    fake.requests.clear()
    listed = await sap.read("org-a", "user-a", "BusinessPartners", {"$select": "CardCode", "$top": 2})
    assert listed["rows"] == [{"CardCode": "C001"}, {"CardCode": "C002"}] and listed["has_more"]
    request = fake.requests[0]
    assert (request.method, request.url.path) == ("GET", "/b1s/v1/BusinessPartners")
    assert dict(request.url.params) == {"$select": "CardCode", "$top": "2"}  # no $format, no sap-client
    assert request.headers["prefer"] == "odata.maxpagesize=100"
    assert request.headers["cookie"] == "B1SESSION=s1; ROUTEID=.node1" and "authorization" not in request.headers
    assert logins == [1]  # the saved session, no new sign-in

    one = await sap.read("org-a", "user-a", "/b1s/v1/BusinessPartners('C001')")
    assert one["record"] == {"CardCode": "C001", "CardName": "Azure"}
    fake.requests.clear()
    created = await sap.create("org-a", "user-a", "BusinessPartners", {"CardName": "New Co"})
    assert created["record"]["CardCode"] == "C003" and fake.paths() == [("POST", "/b1s/v1/BusinessPartners")]
    fake.requests.clear()
    assert (await sap.update("org-a", "user-a", "BusinessPartners('C003')", {"CardName": "Newer"}))["updated"]
    assert fake.paths() == [("PATCH", "/b1s/v1/BusinessPartners('C003')")]  # no token fetch, no version read
    assert "x-csrf-token" not in fake.requests[0].headers


async def test_business_one_signs_in_again_when_the_session_ran_out(fake) -> None:
    logins = _b1_server(fake)
    await _connect_b1(fake)
    logins.append(1)  # the server forgot session s1
    fake.requests.clear()
    assert (await sap.read("org-a", "user-a", "Items"))["count"] == 2
    assert fake.paths() == [("GET", "/b1s/v1/Items"), ("POST", "/b1s/v1/Login"), ("GET", "/b1s/v1/Items")]
    _row(fake).token_expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    fake.requests.clear()
    await sap.read("org-a", "user-a", "Items")
    assert fake.paths() == [("POST", "/b1s/v1/Login"), ("GET", "/b1s/v1/Items")]


async def test_business_one_refusals(fake) -> None:
    _b1_server(fake)
    with pytest.raises(HTTPException) as error:
        await _connect_b1(fake, password="wrong")
    assert error.value.status_code == 401 and "company" in error.value.detail
    for bad in ({"company_db": ""}, {"company_db": "a b"}, {"username": ""}, {"sap_client": "100"}):
        with pytest.raises(HTTPException) as error:
            await _connect_b1(fake, **bad)
        assert error.value.status_code == 422
    await _connect_b1(fake)
    fake.requests.clear()
    for path in ("Login", "/b1s/v1/Logout", "Login()", "/b1s/v1/Logout()", "/sap/opu/odata/sap/API_X", "/b1s/v3/Items",
                 "Items/../Login", "$batch"):
        with pytest.raises(HTTPException) as error:
            await sap.read("org-a", "user-a", path)
        assert error.value.status_code == 422
    assert fake.requests == []


def test_business_one_paths() -> None:
    assert sap.normalize_path("Orders(12)", "business_one", "/b1s/v1") == "/b1s/v1/Orders(12)"
    assert sap.normalize_path("Items", "business_one") == "/b1s/v2/Items"
    with pytest.raises(HTTPException):
        sap.normalize_path("/b1s/v2/Items", "s4hana")


# --- The relay ----------------------------------------------------------------------------


class RelayPc:
    """Stands in for MindLoom for Windows: collects requests and answers them from ``fake``."""

    def __init__(self, fake, who=("org-a", "user-a"), key=None):
        self.fake, self.who, self.collected, self.task = fake, who, [], None
        self.key = key or KEY

    async def poll(self):
        claimed = await relay.claim(*self.who, self.key)
        for item in claimed["requests"]:
            self.collected.append(item)
            content = base64.b64decode(item["body_base64"]) if item["body_base64"] else None
            answer = self.fake.handle(httpx.Request(item["method"], item["url"], headers=item["headers"], content=content))
            await relay.complete(*self.who, item["id"], self.key, status_code=answer.status_code,
                                 headers=[[k, v] for k, v in answer.headers.multi_items()],
                                 body_base64=base64.b64encode(answer.content).decode(), error=None)
        return claimed

    async def __aenter__(self):
        await relay.claim(*self.who, self.key)  # seen: online, and paired

        async def loop():
            while True:
                await self.poll()
                await asyncio.sleep(0.01)

        self.task = asyncio.create_task(loop())
        return self

    async def __aexit__(self, *_):
        self.task.cancel()


@pytest.fixture
def quick_relay(monkeypatch):
    monkeypatch.setattr(relay, "WAIT_STEP_SECONDS", 0.01)
    monkeypatch.setattr(relay, "WAIT_SECONDS", 1.0)


def _s4_server(fake):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != _basic("MINDLOOM", "s3cret"):
            return httpx.Response(401)
        if request.headers.get("x-csrf-token") == "Fetch":
            return httpx.Response(200, json={"d": {"Name": "A"}}, headers=[
                ("x-csrf-token", "tok-9"), ("etag", 'W/"v3"'), ("set-cookie", "SAP_SESSIONID=abc; path=/")])
        if request.method == "GET":
            return httpx.Response(200, json={"d": {"results": [{"BusinessPartner": "1000"}]}})
        return httpx.Response(204)

    fake.handler = handler


async def _connect_inside(fake):
    return await sap.connect("org-a", "user-a", base_url=INSIDE, username="MINDLOOM", password="s3cret",
                             test_service="API_BUSINESS_PARTNER", via="relay", sap_client="100", relay_code=CODE)


async def test_a_system_inside_the_network_is_reached_through_the_relay(fake, quick_relay, monkeypatch) -> None:
    async def never(url):
        raise AssertionError("the server must not look up or call an inside address itself")

    monkeypatch.setattr(sap, "resolve_public_url", never)
    _s4_server(fake)
    async with RelayPc(fake) as pc:
        status = await _connect_inside(fake)
        assert status["via"] == "relay" and status["base_url"] == INSIDE
        listed = await sap.read("org-a", "user-a", f"{BP}/A_BusinessPartner", {"$top": "1"})
        assert listed["rows"] == [{"BusinessPartner": "1000"}]
        await sap.update("org-a", "user-a", f"{BP}/A_BusinessPartner('1000')", {"Name": "B"})
    assert fake.clients == []  # no connection was made from the server
    read = pc.collected[1]
    assert read["method"] == "GET" and read["url"].startswith(f"{INSIDE}{BP}/A_BusinessPartner?")
    assert "%24top=1" in read["url"] and "sap-client=100" in read["url"]
    patch = pc.collected[-1]
    assert patch["method"] == "PATCH" and json.loads(base64.b64decode(patch["body_base64"])) == {"Name": "B"}
    assert patch["headers"]["x-csrf-token"] == "tok-9" and patch["headers"]["If-Match"] == 'W/"v3"'
    assert patch["headers"]["Cookie"] == "SAP_SESSIONID=abc"
    assert relay._memory == {}  # nothing is left parked


async def test_business_one_through_the_relay(fake, quick_relay) -> None:
    _b1_server(fake, root="/b1s/v2")
    async with RelayPc(fake) as pc:
        await _connect_b1(fake, base_url=INSIDE, via="relay", relay_code=CODE)
        assert (await sap.read("org-a", "user-a", "Items"))["count"] == 2
    login = pc.collected[0]
    assert login["url"] == INSIDE + "/b1s/v2/Login" and login["headers"]["Content-Type"] == "application/json"


async def test_relay_that_is_not_running_or_not_answering(fake, quick_relay) -> None:
    _s4_server(fake)
    with pytest.raises(HTTPException) as error:  # no relay has ever called
        await _connect_inside(fake)
    assert error.value.status_code == 503 and "MindLoom for Windows" in error.value.detail
    await relay.claim("org-a", "user-a", KEY)  # seen once, then silent
    with pytest.raises(HTTPException) as error:
        await _connect_inside(fake)
    assert error.value.status_code == 504 and relay._memory == {}
    relay._memory_clients[("org-a", "user-a")]["last_seen_at"] = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert not (await relay.status("org-a", "user-a"))["online"]
    with pytest.raises(HTTPException) as error:
        await _connect_inside(fake)
    assert error.value.status_code == 503


async def test_requests_are_encrypted_while_parked_and_only_their_owner_gets_them(fake, quick_relay) -> None:
    await relay.claim("org-a", "user-a", KEY)
    waiting = asyncio.create_task(relay.forward("org-a", "user-a", method="POST", url=INSIDE + "/b1s/v2/Login",
                                                headers={"Authorization": "Basic c2VjcmV0"}, body=b'{"Password": "s3cret"}'))
    await asyncio.sleep(0.05)
    (parked,) = relay._memory.values()
    assert parked["headers"].startswith("enc:v1:") and parked["body"].startswith("enc:v1:")
    assert "s3cret" not in json.dumps(parked, default=str) and "c2VjcmV0" not in json.dumps(parked, default=str)
    assert parked["url"].startswith("enc:v1:")
    assert (await relay.claim("org-a", "user-b", "b" * 43))["requests"] == []
    assert (await relay.claim("org-b", "user-a", "c" * 43))["requests"] == []
    (mine,) = (await relay.claim("org-a", "user-a", KEY))["requests"]
    assert mine["url"] == INSIDE + "/b1s/v2/Login" and 0 < mine["seconds_left"] <= relay.WAIT_SECONDS
    assert mine["headers"] == {"Authorization": "Basic c2VjcmV0"} and base64.b64decode(mine["body_base64"]) == b'{"Password": "s3cret"}'
    assert (await relay.claim("org-a", "user-a", KEY))["requests"] == []  # handed out once
    with pytest.raises(HTTPException) as error:  # someone else cannot answer it
        await relay.complete("org-a", "user-b", mine["id"], "b" * 43, status_code=200, headers=[], body_base64="", error=None)
    assert error.value.status_code == 404
    await relay.complete("org-a", "user-a", mine["id"], KEY, status_code=200, headers=[
        ["Set-Cookie", "a=b"], ["Content-Length", "999"], ["X-Bad\r\nInjected", "1"], ["X-Evil", "a\r\nb"], ["lonely"], "junk"],
                         body_base64=base64.b64encode(b'{"ok": true}').decode(), error=None)
    response = await waiting
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert response.headers.get_list("set-cookie") == ["a=b"]
    # Only well-formed headers survive, and the length is the real one, not the claimed 999.
    assert sorted(response.headers) == ["content-length", "set-cookie"] and response.headers["content-length"] == "12"


async def test_relay_errors_and_limits(fake, quick_relay) -> None:
    await relay.claim("org-a", "user-a", KEY)
    for bad in (dict(method="DELETE", url=INSIDE + "/x"), dict(method="GET", url="http://sap.corp.internal/x")):
        with pytest.raises(HTTPException) as error:
            await relay.forward("org-a", "user-a", headers={}, **bad)
        assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        await relay.forward("org-a", "user-a", method="POST", url=INSIDE + "/x", headers={}, body=b"x" * 300_000)
    assert error.value.status_code == 413

    async def answer(**result):
        task = asyncio.create_task(relay.forward("org-a", "user-a", method="GET", url=INSIDE + BP, headers={}))
        await asyncio.sleep(0.03)
        (item,) = (await relay.claim("org-a", "user-a", KEY))["requests"]
        await relay.complete("org-a", "user-a", item["id"], KEY, **{**dict(status_code=None, headers=None, body_base64=None, error=None), **result})
        return await task

    with pytest.raises(HTTPException) as error:
        await answer(error="The relay refused: not the address approved on this PC.")
    assert error.value.status_code == 502 and "not the address approved" in error.value.detail
    with pytest.raises(HTTPException) as error:
        await answer(status_code=200, body_base64="not base64!")
    assert error.value.status_code == 502


async def test_the_relay_is_asked_often_only_when_a_connection_uses_it(fake, quick_relay) -> None:
    assert (await relay.claim("org-a", "user-a", KEY))["poll_seconds"] == relay.IDLE_POLL_SECONDS
    _s4_server(fake)
    async with RelayPc(fake):
        await _connect_inside(fake)
    assert (await relay.claim("org-a", "user-a", KEY))["poll_seconds"] == relay.ACTIVE_POLL_SECONDS
    assert (await relay.claim("org-a", "user-b", "b" * 43))["poll_seconds"] == relay.IDLE_POLL_SECONDS


def test_relay_routes(fake) -> None:
    app = FastAPI()
    app.include_router(relay_routes.router)
    assert TestClient(app).get("/relay/requests").status_code in {401, 403}
    app.dependency_overrides[require_user_context] = lambda: ("org-a", "user-a")
    client = TestClient(app)
    key = {"X-MindLoom-Relay-Key": KEY}
    assert client.get("/relay/status").json() == {"online": False, "last_seen_at": None, "paired": False}
    assert client.get("/relay/requests").status_code == 400  # a relay without a key is too old
    assert client.get("/relay/requests", headers={"X-MindLoom-Relay-Key": "short"}).status_code == 400
    assert client.get("/relay/requests", headers=key).json() == {
        "requests": [], "poll_seconds": relay.IDLE_POLL_SECONDS, "code": CODE}
    status = client.get("/relay/status").json()
    assert status["online"] and status["paired"] and "code" not in status and "key" not in json.dumps(status)
    assert client.get("/relay/requests", headers={"X-MindLoom-Relay-Key": "x" * 43}).status_code == 403
    assert client.post("/relay/requests/nope/result", json={"status": 200}, headers=key).status_code == 404
    assert client.post("/relay/requests/nope/result", json={"status": 200}).status_code == 400
    assert client.post("/relay/requests/nope/result", json={"status": 99}, headers=key).status_code == 422


# --- Pairing: only the person's own relay gets the sign-in ---------------------------------


async def test_only_the_paired_relay_collects_requests(fake, quick_relay) -> None:
    await relay.claim("org-a", "user-a", KEY)
    waiting = asyncio.create_task(relay.forward("org-a", "user-a", method="GET", url=INSIDE + BP,
                                                headers={"Authorization": "Basic c2VjcmV0"}))
    await asyncio.sleep(0.03)
    # A stolen web session can call the endpoint, but without this PC's key it gets nothing.
    for stolen in ("", "short", "x" * 43):
        with pytest.raises(HTTPException) as error:
            await relay.claim("org-a", "user-a", stolen)
        assert error.value.status_code in {400, 403}
    (item,) = (await relay.claim("org-a", "user-a", KEY))["requests"]
    with pytest.raises(HTTPException) as error:
        await relay.complete("org-a", "user-a", item["id"], "x" * 43, status_code=200, headers=[], body_base64="", error=None)
    assert error.value.status_code == 403
    await relay.complete("org-a", "user-a", item["id"], KEY, status_code=200, headers=[], body_base64="", error=None)
    assert (await waiting).status_code == 200


async def test_connecting_through_the_relay_needs_the_code_that_pc_shows(fake, quick_relay) -> None:
    _s4_server(fake)
    someone_elses = "z" * 43  # a relay that paired first, e.g. with a stolen session
    async with RelayPc(fake, key=someone_elses) as pc:
        for wrong in (None, "", "0000-0000", CODE):  # CODE is the code of the person's own PC
            with pytest.raises(HTTPException) as error:
                await sap.connect("org-a", "user-a", base_url=INSIDE, username="MINDLOOM", password="s3cret",
                                  test_service="API_BUSINESS_PARTNER", via="relay", relay_code=wrong)
            assert error.value.status_code == 422 and "relay code" in error.value.detail
        assert pc.collected == [] and fake.rows == {}  # the sign-in never left the server
    relay.use_memory_store(True)
    async with RelayPc(fake):
        lower = CODE.lower().replace("-", " ")  # typed loosely
        assert (await sap.connect("org-a", "user-a", base_url=INSIDE, username="MINDLOOM", password="s3cret",
                                  test_service="API_BUSINESS_PARTNER", via="relay", relay_code=lower))["connected"]


async def test_disconnecting_a_relay_connection_starts_pairing_over(fake, quick_relay) -> None:
    _s4_server(fake)
    async with RelayPc(fake):
        await _connect_inside(fake)
    assert (await relay.status("org-a", "user-a"))["paired"]
    await sap.disconnect("org-a", "user-a")
    assert not (await relay.status("org-a", "user-a"))["paired"]
    assert (await relay.claim("org-a", "user-a", "n" * 43))["code"] != CODE  # a new PC can pair now


async def test_a_direct_connection_leaves_pairing_alone(fake, quick_relay) -> None:
    _s4_server(fake)
    await relay.claim("org-a", "user-a", KEY)
    await sap.connect("org-a", "user-a", base_url=BASE, username="MINDLOOM", password="s3cret", test_service="API_X")
    await sap.disconnect("org-a", "user-a")
    assert (await relay.status("org-a", "user-a"))["paired"]


# --- A request nobody waits for is never made ---------------------------------------------


async def test_stale_requests_are_dropped_not_handed_out(fake, quick_relay) -> None:
    await relay.claim("org-a", "user-a", KEY)
    old = datetime.now(timezone.utc) - timedelta(seconds=relay.WAIT_SECONDS + 5)
    relay._memory["dead"] = {"request_id": "dead", "org_id": "org-a", "user_id": "user-a", "method": "POST",
                             "url": INSIDE + BP, "headers": "{}", "body": None, "status": "pending", "created_at": old}
    assert (await relay.claim("org-a", "user-a", KEY))["requests"] == [] and "dead" not in relay._memory


async def test_relay_addresses_are_encoded(fake, quick_relay) -> None:
    _s4_server(fake)
    async with RelayPc(fake) as pc:
        await _connect_inside(fake)
        await sap.read("org-a", "user-a", f"{BP}/A_BusinessPartner('ACME Corp')")
    url = pc.collected[-1]["url"]
    assert "A_BusinessPartner('ACME%20Corp')?" in url and " " not in url


async def test_oauth_through_the_relay_only_uses_saps_own_token_service(fake, quick_relay) -> None:
    await relay.claim("org-a", "user-a", KEY)
    for bad in (INSIDE + "/custom/token", INSIDE + "/sap/opu/odata/sap/X/token"):
        with pytest.raises(HTTPException) as error:
            await sap.connect("org-a", "user-a", base_url=INSIDE, auth="oauth", client_id="CLIENT", client_secret="s3cret",
                              token_url=bad, test_service="API_X", via="relay", relay_code=CODE)
        assert error.value.status_code == 422


def test_schema_has_the_new_columns_and_tables() -> None:
    sql = "\n".join(schema._DURABLE_INTEGRATIONS_SQL)
    for column in sap.SapConnectionRow.__table__.columns:
        assert column.name in sql
    for table in ("relay_requests", "relay_clients"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
