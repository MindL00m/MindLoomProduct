"""Google ID-token verification and MindLoom access JWT issue/decode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from core.config import get_settings


@dataclass(frozen=True)
class GoogleIdentity:
    """Verified claims from a Google ID token."""

    sub: str
    email: str
    name: str | None = None
    picture: str | None = None


@dataclass(frozen=True)
class AccessTokenClaims:
    """Claims embedded in a MindLoom access JWT."""

    user_id: str
    org_id: str
    role: str
    email: str


def verify_google_id_token(raw_token: str) -> GoogleIdentity:
    """Verify a Google GIS ID token and return stable identity claims."""

    settings = get_settings()
    client_id = settings.google_client_id.strip()
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured (GOOGLE_CLIENT_ID missing).",
        )
    token = raw_token.strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing Google ID token.")
    try:
        payload: dict[str, Any] = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=client_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Google ID token.") from exc

    email = str(payload.get("email") or "").strip().lower()
    sub = str(payload.get("sub") or "").strip()
    if not email or not sub:
        raise HTTPException(
            status_code=401,
            detail="Google ID token is missing email or subject.",
        )
    if payload.get("email_verified") is False:
        raise HTTPException(status_code=401, detail="Google email is not verified.")

    name = payload.get("name")
    picture = payload.get("picture")
    return GoogleIdentity(
        sub=sub,
        email=email,
        name=str(name) if name else None,
        picture=str(picture) if picture else None,
    )


def issue_access_token(*, org_id: str, user_id: str, role: str, email: str) -> str:
    """Issue a signed MindLoom access JWT for API Authorization headers."""

    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "org_id": org_id,
        "role": role,
        "email": email.strip().lower(),
        "iat": now,
        "exp": now + timedelta(hours=settings.session_ttl_hours),
    }
    return jwt.encode(payload, settings.resolved_session_secret, algorithm="HS256")


def decode_access_token(raw_token: str) -> AccessTokenClaims:
    """Decode and validate a MindLoom access JWT."""

    token = raw_token.strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing access token.")
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.resolved_session_secret,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Access token expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid access token.") from exc

    user_id = str(payload.get("sub") or "").strip()
    org_id = str(payload.get("org_id") or "").strip()
    role = str(payload.get("role") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    if not user_id or not org_id or not role:
        raise HTTPException(status_code=401, detail="Access token is missing required claims.")
    return AccessTokenClaims(user_id=user_id, org_id=org_id, role=role, email=email)
