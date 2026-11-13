"""The relay: calls a system inside this network for MindLoom, which cannot reach it.

MindLoom's servers park a request (``apps/api/weaver/relay.py``); this PC collects it,
makes the call and posts the answer back. The server decides what to ask. This file
decides what this PC is willing to do, whatever it is asked:

- only the one https address the person approved on this PC;
- only SAP's API paths (S/4HANA OData, its token service, Business One's Service Layer);
- only GET, POST and PATCH; a redirect is reported, never followed;
- never a request the server has stopped waiting for (making it late could create a
  record twice);
- nothing is written to disk or to the log.

Parked requests carry the SAP sign-in, so the server only hands them to one relay per
person: the one whose key it saw first (``relay_key`` in the settings). The short code
made from that key is shown here and typed into MindLoom when connecting, which proves
the paired relay is this one.

It needs nothing from Windows, so ``python -m mindloom_agent --relay <address>`` also runs
it on a server with no screen.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from .api import ApiClient, ApiError

METHODS = ("GET", "POST", "PATCH")
ALLOWED_PREFIXES = (
    "/sap/opu/odata/", "/sap/opu/odata4/", "/b1s/v1/", "/b1s/v2/",
    "/sap/bc/sec/oauth2/", "/sap/public/bc/sec/oauth2/",
)
# Headers the server may set. Anything else (Host, Content-Length, proxy headers) is dropped.
FORWARDED_HEADERS = frozenset({
    "accept", "authorization", "content-type", "cookie", "if-match", "prefer", "x-csrf-token",
})
MAX_REQUEST_BYTES = 200_000
MAX_RESPONSE_BYTES = 1_000_000
CALL_TIMEOUT_SECONDS = 40.0
# A request with less time left than this is not started: its answer would arrive too late.
MIN_SECONDS_LEFT = 3.0
IDLE_POLL_SECONDS = 60.0
ERROR_POLL_SECONDS = 30.0


def new_key() -> str:
    """A random key that identifies this relay to MindLoom."""

    return secrets.token_urlsafe(32)


def code_of(relay_key: str) -> str:
    """The pairing code to type into MindLoom (the server computes the same from the key)."""

    digest = hashlib.sha256(relay_key.strip().encode()).hexdigest()
    return f"{digest[:4]}-{digest[4:8]}".upper()


def origin_of(url: object) -> str | None:
    """``https://host[:port]`` of an https address without a user name, else None."""

    try:
        parts = urlsplit(str(url or "").strip())
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return None
    return f"https://{host}:{port}" if port and port != 443 else f"https://{host}"


def refusal(request: dict[str, Any], approved_origin: str) -> str | None:
    """Why this PC will not make this call, or None."""

    url = str(request.get("url") or "")
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url):
        return "The address has characters that cannot be in an address."
    origin = origin_of(url)
    if origin is None:
        return "Only https addresses are called."
    if not approved_origin:
        return "No address is approved on this PC yet. Approve it there, then try again."
    if origin != origin_of(approved_origin):
        return f"{origin} is not the address approved on this PC."
    if str(request.get("method") or "").upper() not in METHODS:
        return "Only GET, POST and PATCH are made."
    parts = urlsplit(url)
    # Checked as SAP would read it: an encoded "../" must not walk out of the API paths.
    path = unquote(parts.path)
    if (".." in path or "//" in path or "\\" in path or "%" in path or parts.fragment
            or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path)
            or "$batch" in path.lower() or not path.startswith(ALLOWED_PREFIXES)
            or not parts.path.startswith(ALLOWED_PREFIXES)):
        return "Only SAP's API paths are called."
    body = request.get("body_base64")
    if body is not None:
        try:
            if len(base64.b64decode(str(body), validate=True)) > MAX_REQUEST_BYTES:
                return "That is too much data for one call."
        except ValueError:
            return "The request body is unreadable."
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # urllib then raises the 3xx as an HTTPError, which is reported as it is


def default_opener() -> urllib.request.OpenerDirector:
    # Straight to the address: it is inside this network, not behind the internet proxy. The
    # default TLS setup uses the system's certificates, so a company CA installed on this PC
    # is trusted.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )


def perform(request: dict[str, Any], opener: urllib.request.OpenerDirector | None = None,
            timeout: float = CALL_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Make one checked call. Returns what to post back: a status, headers and body, or an
    error. Never raises: one odd request must not stop the relay."""

    try:
        body = request.get("body_base64")
        data = base64.b64decode(str(body)) if body is not None else None
        call = urllib.request.Request(str(request["url"]), data=data, method=str(request["method"]).upper())
        for name, value in (request.get("headers") or {}).items():
            if str(name).lower() in FORWARDED_HEADERS:
                call.add_header(str(name), str(value))
        try:
            response = (opener or default_opener()).open(call, timeout=max(1.0, timeout))
        except urllib.error.HTTPError as error:
            response = error  # 3xx, 4xx and 5xx are answers too
        with response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                return {"error": "The answer was too large. Ask for fewer rows or fields."}
            return {
                "status": int(response.status if hasattr(response, "status") else response.code),
                "headers": [[str(k), str(v)[:8000]] for k, v in response.headers.items()],
                "body_base64": base64.b64encode(content).decode("ascii"),
            }
    except Exception as error:  # noqa: BLE001 - reported to the server, which tells the person
        reason = getattr(error, "reason", None) or error.__class__.__name__
        return {"error": f"Could not reach the address from this PC: {str(reason)[:200]}"}


class Relay:
    """Collects waiting requests and answers them.

    ``approved`` returns the address approved on this PC ("" when none); ``approve`` is
    asked for an address when none is approved yet and returns True (yes), False (no: do
    not ask about that address again) or None (could not ask now: ask next time).
    """

    def __init__(self, api: ApiClient, *, approved: Callable[[], str], approve: Callable[[str], bool | None],
                 opener: urllib.request.OpenerDirector | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.api = api
        self._approved = approved
        self._approve = approve
        self._opener = opener
        self._clock = clock
        self._declined: set[str] = set()

    def _answer(self, request: dict[str, Any], received: float) -> dict[str, Any]:
        origin = origin_of(request.get("url"))
        if origin and not self._approved() and origin not in self._declined:
            # Nothing approved yet: ask the person once whether this PC may call that address.
            if self._approve(origin) is False:
                self._declined.add(origin)
        reason = refusal(request, self._approved())
        if reason:
            return {"error": f"The relay refused: {reason}"}
        try:
            left = float(request.get("seconds_left", CALL_TIMEOUT_SECONDS)) - (self._clock() - received)
        except (TypeError, ValueError):
            left = 0.0
        if left < MIN_SECONDS_LEFT:
            # Asking the person, or an earlier slow call, used up the time. The server has
            # given up (or is about to), so the call is not made at all.
            return {"error": "The relay was too late to make this call. Try again."}
        return perform(request, self._opener, timeout=min(CALL_TIMEOUT_SECONDS, left - 1.0))

    def poll_once(self) -> float:
        """Answer what is waiting. Returns how many seconds to wait before asking again."""

        try:
            waiting = self.api.relay_requests()
        except ApiError as error:
            return IDLE_POLL_SECONDS if error.status in {400, 403, 404} else ERROR_POLL_SECONDS
        received = self._clock()
        for request in waiting.get("requests") or []:
            request_id = str(request.get("id") or "") if isinstance(request, dict) else ""
            if not request_id:
                continue
            try:
                answer = self._answer(request, received)
            except Exception as error:  # noqa: BLE001 - answer it as failed and carry on
                answer = {"error": f"The relay could not handle this call ({error.__class__.__name__})."}
            try:
                self.api.relay_result(request_id, answer)
            except ApiError:
                continue  # the server stopped waiting, or the connection dropped
        try:
            return max(1.0, min(float(waiting.get("poll_seconds") or IDLE_POLL_SECONDS), 300.0))
        except (TypeError, ValueError):
            return IDLE_POLL_SECONDS
