"""Zoom connector policy and webhook tests."""

import hashlib
import hmac
from types import SimpleNamespace

import zoom_workspace


def test_zoom_webhook_signature(monkeypatch):
    secret = "zoom-secret"
    monkeypatch.setattr(
        zoom_workspace,
        "get_settings",
        lambda: SimpleNamespace(zoom_webhook_secret_token=secret),
    )
    body = b'{"event":"recording.completed"}'
    timestamp = "1784280000"
    signature = "v0=" + hmac.new(
        secret.encode(),
        f"v0:{timestamp}:{body.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()

    assert zoom_workspace.validate_zoom_webhook(body, timestamp, signature)
    assert not zoom_workspace.validate_zoom_webhook(body, timestamp, "v0=bad")


def test_zoom_selected_access_policy():
    policy = SimpleNamespace(
        access_mode="selected",
        allowed_user_ids='["expert-1"]',
        allowed_departments='["Operations"]',
    )
    assert zoom_workspace._visible_to("org-1", "admin-1", policy) == [
        "user:expert-1",
        "department:Operations",
    ]


def test_zoom_source_permissions_are_conservative():
    policy = SimpleNamespace(access_mode="respect_source_permissions")
    assert zoom_workspace._visible_to("org-1", "host-1", policy) == ["user:host-1"]
