"""The relay only calls the approved address, only SAP's API paths, and never follows a redirect."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mindloom_agent import __main__ as app
from mindloom_agent import config, relay
from mindloom_agent.api import ApiError

SAP = "https://sap.corp.example:44300"
BP = "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner"


def request(url=SAP + BP, method="GET", **more):
    return {"id": "r1", "method": method, "url": url, "headers": {"Accept": "application/json"}, **more}


def test_origin_of() -> None:
    assert relay.origin_of("https://SAP.corp.example:44300/x?y=1") == SAP
    assert relay.origin_of("https://sap.corp.example:443/") == "https://sap.corp.example"
    for bad in ("http://sap.corp.example", "https://user:pw@sap.corp.example", "ftp://x", "", "https://", "https://x:99999999"):
        assert relay.origin_of(bad) is None


def test_allowed_calls() -> None:
    assert relay.refusal(request(), SAP) is None
    assert relay.refusal(request(SAP + "/b1s/v2/Login", "POST", body_base64=base64.b64encode(b"{}").decode()), SAP) is None
    assert relay.refusal(request(SAP + "/sap/bc/sec/oauth2/token", "POST"), SAP + "/") is None
    assert relay.refusal(request(SAP + "/sap/opu/odata4/sap/x/srvd_a2x/sap/x/0001/Product?$top=1", "PATCH"), SAP) is None


@pytest.mark.parametrize("bad", [
    request("https://other.corp.example:44300" + BP),             # another host
    request("https://sap.corp.example" + BP),                     # another port
    request("http://sap.corp.example:44300" + BP),                # not https
    request("https://sap.corp.example:44300@evil.example" + BP),  # the approved host as a user name
    request(SAP + "/sap/bc/adt/programs"),                        # not a data service
    request(SAP + "/sap/opu/odata/../bc/gui"),
    request(SAP + "/sap/opu/odata//x"),
    request(SAP + "/"),
    request(method="DELETE"), request(method="PUT"), request(method=""),
    request(body_base64="not base64!"),
    request(body_base64=base64.b64encode(b"x" * 300_000).decode()),
])
def test_refused_calls(bad) -> None:
    assert relay.refusal(bad, SAP)


def test_nothing_is_called_without_an_approved_address() -> None:
    assert relay.refusal(request(), "")
    assert relay.refusal(request(), "http://sap.corp.example:44300")


# --- Making the call ----------------------------------------------------------------------


class _Sap(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                          "body": self.rfile.read(length)})
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
            return
        if self.path.startswith("/missing"):
            body, status = b'{"error": {"message": {"value": "Not found"}}}', 404
        elif self.path.startswith("/big"):
            body, status = b"x" * (relay.MAX_RESPONSE_BYTES + 10), 200
        else:
            body, status = b'{"d": {"results": []}}', 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "SAP_SESSIONID=abc; path=/")
        self.send_header("x-csrf-token", "tok")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = _reply

    def log_message(self, *args):
        pass


@pytest.fixture
def sap_server():
    class Quick(HTTPServer):
        def server_bind(self):  # skip the slow name lookup
            self.socket.bind(self.server_address)
            self.server_address = self.socket.getsockname()
            self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    _Sap.seen = []
    server = Quick(("127.0.0.1", 0), _Sap)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _call(base, path="/ok", method="GET", **more):
    # perform() is only reached after refusal() passed; the test server speaks plain http.
    return relay.perform({"method": method, "url": base + path, **more})


def test_perform_returns_status_headers_and_body(sap_server) -> None:
    result = _call(sap_server, method="POST", body_base64=base64.b64encode(b'{"Name": "A"}').decode(), headers={
        "Authorization": "Basic abc", "x-csrf-token": "Fetch", "Cookie": "a=b", "Host": "evil.example",
        "X-Forwarded-For": "1.2.3.4", "Content-Type": "application/json"})
    assert result["status"] == 200 and json.loads(base64.b64decode(result["body_base64"])) == {"d": {"results": []}}
    names = {k.lower(): v for k, v in result["headers"]}
    assert names["x-csrf-token"] == "tok" and names["set-cookie"].startswith("SAP_SESSIONID=abc")
    sent = _Sap.seen[0]
    assert sent["method"] == "POST" and sent["body"] == b'{"Name": "A"}'
    assert sent["headers"]["Authorization"] == "Basic abc" and sent["headers"]["Cookie"] == "a=b"
    assert sent["headers"]["Host"].startswith("127.0.0.1") and "X-Forwarded-For" not in sent["headers"]


def test_errors_and_redirects_are_answers_not_followed(sap_server) -> None:
    assert _call(sap_server, "/missing")["status"] == 404
    redirected = _call(sap_server, "/redirect")
    assert redirected["status"] == 302 and len(_Sap.seen) == 2
    assert "too large" in _call(sap_server, "/big")["error"]
    assert "Could not reach" in _call("http://127.0.0.1:1")["error"]


# --- The loop -----------------------------------------------------------------------------


class FakeApi:
    def __init__(self, *polls):
        self.polls = list(polls)
        self.results: list[tuple[str, dict]] = []

    def relay_requests(self):
        poll = self.polls.pop(0)
        if isinstance(poll, Exception):
            raise poll
        return poll

    def relay_result(self, request_id, result):
        self.results.append((request_id, result))
        return {"ok": True}


def test_poll_answers_requests_and_keeps_the_servers_pace(monkeypatch) -> None:
    made = []
    monkeypatch.setattr(relay, "perform", lambda req, opener=None, timeout=0: made.append(req["url"]) or {"status": 200, "headers": [], "body_base64": ""})
    api = FakeApi({"requests": [request(), {**request("https://other.example" + BP), "id": "r2"}], "poll_seconds": 2},
                  {"requests": [], "poll_seconds": 60}, ApiError(0, "offline"), ApiError(404, "old server"), {})
    worker = relay.Relay(api, approved=lambda: SAP, approve=lambda origin: pytest.fail("already approved"))
    assert worker.poll_once() == 2
    assert made == [SAP + BP]
    assert api.results[0] == ("r1", {"status": 200, "headers": [], "body_base64": ""})
    assert api.results[1][0] == "r2" and "not the address approved" in api.results[1][1]["error"]
    assert worker.poll_once() == 60
    assert worker.poll_once() == relay.ERROR_POLL_SECONDS
    assert worker.poll_once() == relay.IDLE_POLL_SECONDS
    assert worker.poll_once() == relay.IDLE_POLL_SECONDS


def test_first_address_is_asked_once(monkeypatch) -> None:
    monkeypatch.setattr(relay, "perform", lambda req, opener=None, timeout=0: {"status": 200, "headers": [], "body_base64": ""})
    state = {"approved": "", "answers": [None, True], "asked": []}

    def approve(origin):
        state["asked"].append(origin)
        answer = state["answers"].pop(0)
        if answer:
            state["approved"] = origin
        return answer

    api = FakeApi(*[{"requests": [request()], "poll_seconds": 2}] * 3)
    worker = relay.Relay(api, approved=lambda: state["approved"], approve=approve)
    worker.poll_once()  # nobody could be asked: refused, ask again next time
    assert "refused" in api.results[0][1]["error"]
    worker.poll_once()  # approved
    assert api.results[1][1]["status"] == 200
    worker.poll_once()
    assert state["asked"] == [SAP, SAP] and api.results[2][1]["status"] == 200


def test_a_no_is_not_asked_again(monkeypatch) -> None:
    asked = []
    api = FakeApi(*[{"requests": [request()], "poll_seconds": 2}] * 2)
    worker = relay.Relay(api, approved=lambda: "", approve=lambda origin: asked.append(origin) or False)
    worker.poll_once()
    worker.poll_once()
    assert asked == [SAP] and all("refused" in result["error"] for _id, result in api.results)


# --- Settings and the headless relay ------------------------------------------------------


def test_relay_address_is_saved_and_forgotten_on_sign_out(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MINDLOOM_CONFIG_DIR", str(tmp_path))
    for name in ("MINDLOOM_WEB_BASE", "MINDLOOM_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    config.save(config.Config(access_token="jwt-1", relay_origin=SAP))
    assert config.load().relay_origin == SAP
    assert config.load().signed_out().relay_origin == ""


def test_headless_relay_needs_an_https_address_and_a_sign_in(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MINDLOOM_CONFIG_DIR", str(tmp_path))
    for name in ("MINDLOOM_WEB_BASE", "MINDLOOM_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    assert app.main(["--relay", "http://sap.corp.example"]) == 2
    assert app.main(["--relay"]) == 2
    assert app.main(["--relay", SAP]) == 1 and "--sign-in" in capsys.readouterr().out

    config.save(config.Config(access_token="jwt-1"))
    polled = []
    stop = threading.Event()

    class OneRound(relay.Relay):
        def poll_once(self):
            polled.append(self._approved())
            assert self._approve("https://other.example") is False  # the command line is the only approval
            assert len(self.api.relay_key) >= 32
            stop.set()
            return 0.0

    monkeypatch.setattr(app, "Relay", OneRound)
    assert app.run_relay(SAP + "/ignored/path", stop) == 0
    assert polled == [SAP]
    out = capsys.readouterr().out
    saved = config.load()
    assert relay.code_of(saved.relay_key) in out and saved.relay_key  # the code to type into MindLoom
    assert saved.signed_out().relay_key == saved.relay_key  # the key names the PC, not the person


# --- Review fixes: odd addresses, late requests, pairing ----------------------------------


@pytest.mark.parametrize("url", [
    SAP + "/sap/opu/odata/%2e%2e/%2e%2e/bc/soap/rfc",       # encoded "../"
    SAP + "/sap/opu/odata/sap/X/%252e%252e/y",              # double-encoded
    SAP + "/sap/opu/odata/sap/X%2f%2fy",                    # encoded "//"
    SAP + "/sap/opu/odata/sap/X/$batch",
    SAP + "/sap/opu/odata/sap/X/%24batch",
    SAP + BP + "('A B')",                                   # a raw space
    SAP + BP + "\r\nHost: evil.example",
    SAP + "/sap/opu/odata/sap/X%00",
])
def test_odd_addresses_are_refused(url) -> None:
    assert relay.refusal(request(url), SAP)


def test_encoded_record_keys_are_fine_and_never_crash_the_relay() -> None:
    encoded = SAP + BP + "('ACME%20Corp')?%24filter=Name+eq+%27A+B%27"
    assert relay.refusal(request(encoded), SAP) is None
    # Whatever reaches perform(), it answers instead of raising.
    for bad in ({"method": "GET", "url": "https://sap.corp.example:44300/x y"}, {"method": "GET"}, {},
                {"method": "GET", "url": "https://[bad"}, {"method": "POST", "url": SAP + BP, "body_base64": "!!"}):
        assert "error" in relay.perform(bad)


def test_a_request_the_server_gave_up_on_is_not_made(monkeypatch) -> None:
    made = []
    monkeypatch.setattr(relay, "perform", lambda req, opener=None, timeout=0: made.append((req["id"], timeout)) or {"status": 200, "headers": [], "body_base64": ""})
    now = [100.0]

    def slow_approve(origin):  # the person takes a minute to answer the prompt
        now[0] += 60
        state["approved"] = origin
        return True

    state = {"approved": ""}
    api = FakeApi({"requests": [{**request(), "id": "late", "seconds_left": 44.0}], "poll_seconds": 2},
                  {"requests": [{**request(), "id": "ok", "seconds_left": 30.0},
                                {**request(), "id": "stale", "seconds_left": 1.0},
                                {**request(), "id": "odd", "seconds_left": "soon"}], "poll_seconds": 2})
    worker = relay.Relay(api, approved=lambda: state["approved"], approve=slow_approve, clock=lambda: now[0])
    worker.poll_once()
    assert made == [] and "too late" in api.results[0][1]["error"]
    worker.poll_once()
    assert made == [("ok", 29.0)]  # and it is given no longer than the server will wait
    assert [r[0] for r in api.results] == ["late", "ok", "stale", "odd"]
    assert all("too late" in r[1]["error"] for r in api.results[2:])


def test_one_bad_request_does_not_stop_the_others(monkeypatch) -> None:
    def perform(req, opener=None, timeout=0):
        if req["id"] == "boom":
            raise RuntimeError("unexpected")
        return {"status": 200, "headers": [], "body_base64": ""}

    monkeypatch.setattr(relay, "perform", perform)
    api = FakeApi({"requests": ["nonsense", {**request(), "id": "boom"}, {**request(), "id": "fine"}], "poll_seconds": 2},
                  ApiError(403, "another relay is paired"))
    worker = relay.Relay(api, approved=lambda: SAP, approve=lambda origin: None)
    assert worker.poll_once() == 2
    assert "RuntimeError" in api.results[0][1]["error"] and api.results[1] == ("fine", {"status": 200, "headers": [], "body_base64": ""})
    assert worker.poll_once() == relay.IDLE_POLL_SECONDS  # not paired: ask rarely, do not hammer


def test_relay_key_and_code(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MINDLOOM_CONFIG_DIR", str(tmp_path))
    for name in ("MINDLOOM_WEB_BASE", "MINDLOOM_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    first = config.ensure_relay_key(config.load())
    assert len(first.relay_key) >= 32 and config.load().relay_key == first.relay_key
    assert config.ensure_relay_key(config.load()).relay_key == first.relay_key  # made once
    code = relay.code_of(first.relay_key)
    assert len(code) == 9 and code[4] == "-" and code == code.upper() and code == relay.code_of(f" {first.relay_key} ")
    assert relay.code_of(relay.new_key()) != code
    raw = (tmp_path / "agent.json").read_text()
    assert ("plain:" + first.relay_key) in raw or "dpapi:" in raw


def test_the_key_goes_only_on_relay_calls(monkeypatch) -> None:
    from mindloom_agent.api import ApiClient
    import urllib.request as request_module

    seen = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b"{}"

    monkeypatch.setattr(request_module, "urlopen", lambda req, data=None, timeout=0: seen.append((req.full_url, dict(req.header_items()))) or Answer())
    client = ApiClient("https://api.mindloom.test", "jwt-1", relay_key="k" * 43)
    client.relay_requests()
    client.relay_result("r1", {"status": 200})
    client.list_tasks()
    assert [("X-mindloom-relay-key" in headers) for _url, headers in seen] == [True, True, False]
