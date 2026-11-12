"""Sign in through the browser, the same way the Mac app does.

The app listens on 127.0.0.1 on a random port, opens ``<site>/desktop-auth?port=…&state=…``
and waits for that page to POST the session to ``/callback``. Only the page this app
opened can complete it: the request must come from the MindLoom site's origin and echo
the one-time ``state``. The token never travels in a URL.
"""

from __future__ import annotations

import hmac
import json
import secrets
import socketserver
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import urlencode, urlsplit

MAX_BODY = 64 * 1024


class SignInError(Exception):
    pass


@dataclass(frozen=True)
class SignInResult:
    access_token: str
    org_id: str
    user_id: str
    email: str


def origin_of(web_base: str) -> str:
    split = urlsplit(web_base)
    if split.scheme not in {"http", "https"} or not split.hostname:
        raise SignInError("The MindLoom site address is not valid.")
    return f"{split.scheme}://{split.hostname}" + (f":{split.port}" if split.port else "")


class _Listener(HTTPServer):
    def server_bind(self) -> None:
        # HTTPServer looks up this machine's full name here, which can take half a minute
        # on some networks. The listener is only ever 127.0.0.1.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


class SignInSession:
    """One sign-in attempt. ``start`` returns the page to open; ``wait`` returns the session."""

    def __init__(self, web_base: str) -> None:
        self.web_base = web_base.rstrip("/")
        self.allowed_origin = origin_of(self.web_base)
        self.state = secrets.token_urlsafe(32)
        self._result: SignInResult | None = None
        self._done = threading.Event()
        self._cancelled = False
        session = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 8  # a browser that connects and never finishes must not hold the listener

            def log_message(self, *args: object) -> None:  # no request logging: bodies carry the token
                pass

            def _answer(self, status: int, body: str = "", content_type: str = "text/plain; charset=utf-8") -> None:
                origin = self.headers.get("Origin")
                data = body.encode("utf-8")
                self.send_response(status)
                # A public https page calling 127.0.0.1 is a private-network request: Chrome
                # drops it unless the preflight opts in.
                self.send_header("Access-Control-Allow-Origin",
                                 origin if origin == session.allowed_origin else session.allowed_origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Access-Control-Max-Age", "600")
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self._answer(204)

            def do_GET(self) -> None:  # noqa: N802
                self._answer(405, "Method not allowed")

            def do_POST(self) -> None:  # noqa: N802
                if self.path.split("?", 1)[0] != "/callback":
                    return self._answer(404, "Not found")
                origin = self.headers.get("Origin")
                if origin is not None and origin != session.allowed_origin:
                    return self._answer(403, "Forbidden origin")
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= MAX_BODY:
                        raise ValueError
                    fields = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(fields, dict):
                        raise ValueError
                except (ValueError, OSError):
                    return self._answer(400, "Invalid sign-in")
                result = session.accept(fields)
                # Wrong or missing values get a 400 but do not end the sign-in, so a stray
                # request cannot cancel it either.
                if result is None:
                    return self._answer(400, "Invalid sign-in")
                self._answer(200, "<!doctype html><html><body style='font-family:system-ui;padding:40px;"
                                  "text-align:center'><h2>Signed in to MindLoom for Windows</h2>"
                                  "<p>You can close this tab.</p></body></html>", "text/html; charset=utf-8")
                session.finish(result)

        self._server = _Listener(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, name="mindloom-sign-in", daemon=True)

    @property
    def url(self) -> str:
        return f"{self.web_base}/desktop-auth?" + urlencode({"port": self.port, "state": self.state})

    def accept(self, fields: dict) -> SignInResult | None:
        """The session a callback carries, or None when it is not the one this app asked for."""

        def text(key: str) -> str:
            value = fields.get(key)
            return value.strip() if isinstance(value, str) else ""

        if not hmac.compare_digest(text("state").encode(), self.state.encode()):
            return None
        if not (text("access_token") and text("org_id") and text("user_id")):
            return None
        return SignInResult(text("access_token"), text("org_id"), text("user_id"), text("email"))

    def finish(self, result: SignInResult) -> None:
        if not self._done.is_set():
            self._result = result
            self._done.set()

    def start(self, open_browser: Callable[[str], object] = webbrowser.open) -> str:
        self._thread.start()
        open_browser(self.url)
        return self.url

    def cancel(self) -> None:
        self._cancelled = True
        self._done.set()

    def wait(self, timeout: float = 180.0) -> SignInResult:
        try:
            finished = self._done.wait(timeout)
        finally:
            self.close()
        if self._cancelled:
            raise SignInError("Sign-in was cancelled.")
        if not finished or self._result is None:
            raise SignInError("Sign-in timed out. Try again.")
        return self._result

    def close(self) -> None:
        def shut() -> None:
            self._server.shutdown()
            self._server.server_close()

        threading.Thread(target=shut, daemon=True).start()
