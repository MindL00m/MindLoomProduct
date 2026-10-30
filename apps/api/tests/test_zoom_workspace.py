"""Zoom connector policy and webhook tests."""

import hashlib
import hmac
from types import SimpleNamespace

from weaver import zoom_workspace


def test_zoom_webhook_signature(monkeypatch):
    secret = "zoom-secret"
    monkeypatch.setattr(
        zoom_workspace,
        "get_settings",
        lambda: SimpleNamespace(zoom_webhook_secret_token=secret),
    )
    body = b'{"event":"recording.completed"}'

    def sign(timestamp: str) -> str:
        return "v0=" + hmac.new(
            secret.encode(), f"v0:{timestamp}:{body.decode()}".encode(), hashlib.sha256,
        ).hexdigest()

    import time

    now = str(int(time.time()))
    assert zoom_workspace.validate_zoom_webhook(body, now, sign(now))
    assert not zoom_workspace.validate_zoom_webhook(body, now, "v0=bad")
    # A correctly signed but old request is a replay.
    old = str(int(time.time()) - 3600)
    assert not zoom_workspace.validate_zoom_webhook(body, old, sign(old))


def test_zoom_selected_access_policy():
    policy = SimpleNamespace(
        access_mode="selected",
        allowed_user_ids='["expert-1"]',
        allowed_departments='["Operations"]',
    )
    assert zoom_workspace._visible_to("org-1", "admin-1", policy) == [
        "user:admin-1",
        "user:expert-1",
        "department:Operations",
    ]


def test_zoom_source_permissions_are_conservative():
    policy = SimpleNamespace(access_mode="respect_source_permissions")
    assert zoom_workspace._visible_to("org-1", "host-1", policy) == ["user:host-1"]
