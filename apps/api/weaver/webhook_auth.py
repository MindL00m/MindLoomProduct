"""Verification helpers for inbound provider webhooks."""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from core.config import get_settings

logger = logging.getLogger(__name__)


def verify_google_pubsub_oidc(request: Request) -> None:
    """Require a Google-signed OIDC Bearer token for Pub/Sub push.

    In development, verification is skipped when ``GOOGLE_PUBSUB_PUSH_AUDIENCE``
    is empty. Production always requires a valid token (startup also requires
    the audience to be configured).
    """

    settings = get_settings()
    audience = settings.google_pubsub_push_audience.strip()
    if not audience:
        if settings.app_env == "development":
            logger.warning(
                "Skipping Pub/Sub OIDC verification "
                "(GOOGLE_PUBSUB_PUSH_AUDIENCE unset in development)."
            )
            return
        raise HTTPException(
            status_code=503,
            detail="Gmail Pub/Sub push audience is not configured.",
        )

    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Missing Pub/Sub OIDC bearer token.")

    try:
        claims = google_id_token.verify_oauth2_token(
            token.strip(),
            google_requests.Request(),
            audience=audience,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Pub/Sub OIDC token.") from exc

    email = str(claims.get("email") or "")
    # Google Pub/Sub push tokens are issued for the service account pushing.
    if claims.get("email_verified") is False:
        raise HTTPException(status_code=401, detail="Pub/Sub OIDC email is not verified.")
    logger.debug("Accepted Pub/Sub OIDC token for %s", email or claims.get("sub"))
