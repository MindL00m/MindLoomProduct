import json
import threading
import urllib.error
import urllib.request

import pytest

from mindloom_agent import config
from mindloom_agent.api import ApiClient, ApiError, NotSignedIn
from mindloom_agent.auth import SignInError, SignInSession, origin_of

SITE = "https://app.mindloom.test"
# Straight to the listener: a system proxy lookup for 127.0.0.1 can take seconds per request.
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(session, body, origin=SITE, path="/callback"):
    request = urllib.request.Request(f"http://127.0.0.1:{session.port}{path}", method="POST",
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    if origin:
        request.add_header("Origin", origin)
    try:
        with DIRECT.open(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def session_body(session, **changes):
    return {"state": session.state, "access_token": "jwt-1", "org_id": "org-a", "user_id": "user-a",
            "email": "ada@example.com", **changes}


def start(session):
    opened = []
    session.start(open_browser=opened.append)
    return opened


def test_sign_in_takes_the_session_from_the_page_it_opened():
    session = SignInSession(SITE + "/")
    opened = start(session)
    assert opened == [f"{SITE}/desktop-auth?port={session.port}&state={session.state}"]
    assert "jwt" not in opened[0] and len(session.state) >= 32
    # Wrong origin, wrong state, missing fields and wrong paths are refused and do not end the sign-in.
    assert post(session, session_body(session), origin="https://evil.example") == 403
    assert post(session, session_body(session, state="guess")) == 400
    assert post(session, session_body(session, access_token="")) == 400
    assert post(session, session_body(session, org_id=7)) == 400
    assert post(session, session_body(session), path="/other") == 404
    assert post(session, ["not", "an", "object"]) == 400
    assert not session._done.is_set()
    assert post(session, session_body(session)) == 200
    result = session.wait(timeout=5)
    assert (result.access_token, result.org_id, result.user_id, result.email) == ("jwt-1", "org-a", "user-a", "ada@example.com")


def test_preflight_allows_only_the_mindloom_site():
    session = SignInSession(SITE)
    start(session)
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{session.port}/callback", method="OPTIONS",
                                         headers={"Origin": "https://evil.example"})
        with DIRECT.open(request, timeout=5) as response:
            assert response.status == 204
            assert response.headers["Access-Control-Allow-Origin"] == SITE
            assert response.headers["Access-Control-Allow-Private-Network"] == "true"
    finally:
        session.cancel()
        with pytest.raises(SignInError):
            session.wait(timeout=1)


def test_sign_in_times_out_and_cancels():
    session = SignInSession(SITE)
    start(session)
    with pytest.raises(SignInError, match="timed out"):
        session.wait(timeout=0.05)
    again = SignInSession(SITE)
    start(again)
    threading.Timer(0.05, again.cancel).start()
    with pytest.raises(SignInError, match="cancelled"):
        again.wait(timeout=5)


def test_origins():
    assert origin_of("http://localhost:5500/") == "http://localhost:5500"
    assert origin_of("https://app.example.com/path") == "https://app.example.com"
    for bad in ("", "ftp://x", "app.example.com"):
        with pytest.raises(SignInError):
            origin_of(bad)


# --- Config -----------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLOOM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("MINDLOOM_WEB_BASE", raising=False)
    monkeypatch.delenv("MINDLOOM_API_BASE", raising=False)
    return tmp_path


def test_config_round_trip_and_sign_out(home):
    assert config.load() == config.Config()
    saved = config.Config(access_token="jwt-1", org_id="org-a", user_id="user-a", email="ada@example.com", consented=True)
    config.save(saved)
    assert config.load() == saved and saved.signed_in
    assert "jwt-1" not in (home / "agent.json").read_text() or not __import__("sys").platform == "win32"
    out = saved.signed_out()
    assert not out.signed_in and out.consented and out.email == ""
    (home / "agent.json").write_text("{not json")
    assert config.load() == config.Config()


def test_switching_sites_signs_out(home, monkeypatch):
    config.save(config.Config(access_token="jwt-1", org_id="org-a", user_id="user-a", consented=True))
    (home / "install.json").write_text(json.dumps({"webBase": "https://app.example.com/", "apiBase": "/api"}))
    loaded = config.load()
    assert loaded.web_base == "https://app.example.com" and loaded.api_base == "https://app.example.com/api"
    assert not loaded.signed_in and loaded.consented
    monkeypatch.setenv("MINDLOOM_WEB_BASE", "https://staging.example.com")
    monkeypatch.setenv("MINDLOOM_API_BASE", "https://api.staging.example.com/")
    assert config.load().api_base == "https://api.staging.example.com"
    (home / "install.json").write_text(json.dumps({"webBase": "javascript:alert(1)"}))
    monkeypatch.delenv("MINDLOOM_WEB_BASE")
    assert config.load().web_base == config.DEFAULT_WEB_BASE


def test_api_base_resolution():
    assert config.resolve_api_base("/api", "https://a.example/") == "https://a.example/api"
    assert config.resolve_api_base("api", "https://a.example") == "https://a.example/api"
    assert config.resolve_api_base("", "https://a.example") == "https://a.example"
    assert config.resolve_api_base("https://api.example/", "https://a.example") == "https://api.example"


def test_the_token_only_travels_over_https_or_to_this_pc():
    with pytest.raises(NotSignedIn):
        ApiClient("https://api.example.com", "").list_tasks()
    with pytest.raises(ApiError, match="must be https"):
        ApiClient("http://api.example.com", "jwt").list_tasks()
    with pytest.raises(ApiError) as error:  # allowed, but nothing listens there
        ApiClient("http://127.0.0.1:9", "jwt", timeout=1).list_tasks()
    assert error.value.status == 0 and "must be https" not in str(error.value)
