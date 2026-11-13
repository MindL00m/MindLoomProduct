"""The relay: reaching a system inside a company network through MindLoom for Windows.

MindLoom's servers cannot call an address that only exists inside a customer's network.
Instead the request is parked here, MindLoom for Windows (signed in as the same person,
on a PC inside that network) collects it, makes the call, and posts the answer back.

The server decides what to ask; the relay decides what it is willing to do. On the PC the
relay only calls the one https address its owner approved there, only SAP's API paths, and
never follows a redirect (``apps/windows-agent/mindloom_agent/relay.py``).

Requests wait for seconds, not minutes. Headers and bodies are encrypted while parked
(they carry sign-in details) and the row is removed as soon as the answer is read.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException

from core.database import get_session_factory
from core.token_crypto import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

WAIT_SECONDS = 45.0
WAIT_STEP_SECONDS = 0.5
ONLINE_SECONDS = 90
STALE = timedelta(minutes=10)
MAX_REQUEST_BYTES = 200_000
MAX_RESPONSE_BYTES = 1_000_000
MAX_CLAIM = 5
ACTIVE_POLL_SECONDS = 2
# Short enough that the first connection through a relay does not time out waiting for it.
IDLE_POLL_SECONDS = 15
MAX_HEADER_CHARS = 8000
METHODS = ("GET", "POST", "PATCH")

# Tests swap the database for these.
_memory: dict[str, dict[str, Any]] | None = None
_memory_clients: dict[tuple[str, str], dict[str, Any]] = {}


def use_memory_store(enabled: bool = True) -> None:
    global _memory
    _memory = {} if enabled else None
    _memory_clients.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Storage --------------------------------------------------------------------------


async def _execute(sql: str, **params: Any) -> list[dict[str, Any]]:
    from sqlalchemy import text

    async with get_session_factory()() as session:
        async with session.begin():
            result = await session.execute(text(sql), params)
            return [dict(row._mapping) for row in result] if result.returns_rows else []


async def _insert(row: dict[str, Any]) -> None:
    if _memory is not None:
        _memory[row["request_id"]] = dict(row)
        return
    await _execute(
        "INSERT INTO relay_requests (request_id, org_id, user_id, method, url, headers, body, status, created_at) "
        "VALUES (:request_id, :org_id, :user_id, :method, :url, :headers, :body, 'pending', :created_at)", **row)


async def _claim(org_id: str, user_id: str, limit: int) -> list[dict[str, Any]]:
    now = _now()
    # A request nobody is waiting for any more must never be made: the caller has already
    # been told it failed, and making it late could create a record twice.
    fresh = now - timedelta(seconds=WAIT_SECONDS)
    if _memory is not None:
        for key in [k for k, r in _memory.items()
                    if r["created_at"] < now - STALE or (r["status"] == "pending" and r["created_at"] <= fresh)]:
            del _memory[key]
        waiting = sorted((r for r in _memory.values() if r["org_id"] == org_id and r["user_id"] == user_id
                          and r["status"] == "pending"), key=lambda r: r["created_at"])[:limit]
        for row in waiting:
            row["status"] = "claimed"
        return [dict(r) for r in waiting]
    await _execute("DELETE FROM relay_requests WHERE created_at < :cutoff "
                   "OR (status = 'pending' AND created_at <= :fresh)", cutoff=now - STALE, fresh=fresh)
    return await _execute(
        "UPDATE relay_requests SET status = 'claimed', claimed_at = :now WHERE request_id IN ("
        "  SELECT request_id FROM relay_requests WHERE org_id = :org_id AND user_id = :user_id "
        "  AND status = 'pending' AND created_at > :fresh ORDER BY created_at LIMIT :limit FOR UPDATE SKIP LOCKED) "
        "RETURNING request_id, method, url, headers, body, created_at",
        now=now, fresh=fresh, org_id=org_id, user_id=user_id, limit=limit)


async def _finish(org_id: str, user_id: str, request_id: str, values: dict[str, Any]) -> bool:
    if _memory is not None:
        row = _memory.get(request_id)
        if not row or (row["org_id"], row["user_id"]) != (org_id, user_id) or row["status"] != "claimed":
            return False
        row.update(values, status="done")
        return True
    done = await _execute(
        "UPDATE relay_requests SET status = 'done', response_status = :response_status, "
        "response_headers = :response_headers, response_body = :response_body, error = :error, done_at = :now "
        "WHERE request_id = :request_id AND org_id = :org_id AND user_id = :user_id AND status = 'claimed' "
        "RETURNING request_id", now=_now(), request_id=request_id, org_id=org_id, user_id=user_id, **values)
    return bool(done)


async def _fetch_done(request_id: str) -> dict[str, Any] | None:
    if _memory is not None:
        row = _memory.get(request_id)
        return dict(row) if row and row["status"] == "done" else None
    rows = await _execute(
        "SELECT response_status, response_headers, response_body, error FROM relay_requests "
        "WHERE request_id = :request_id AND status = 'done'", request_id=request_id)
    return rows[0] if rows else None


async def _remove(request_id: str) -> None:
    if _memory is not None:
        _memory.pop(request_id, None)
        return
    await _execute("DELETE FROM relay_requests WHERE request_id = :request_id", request_id=request_id)


async def _client(org_id: str, user_id: str) -> dict[str, Any] | None:
    if _memory is not None:
        return _memory_clients.get((org_id, user_id))
    rows = await _execute("SELECT last_seen_at, key_hash FROM relay_clients WHERE org_id = :org_id AND user_id = :user_id",
                          org_id=org_id, user_id=user_id)
    return rows[0] if rows else None


async def _touch(org_id: str, user_id: str, key_hash: str) -> None:
    if _memory is not None:
        _memory_clients[(org_id, user_id)] = {"last_seen_at": _now(), "key_hash": key_hash}
        return
    await _execute(
        "INSERT INTO relay_clients (org_id, user_id, last_seen_at, key_hash) VALUES (:org_id, :user_id, :now, :key_hash) "
        "ON CONFLICT (org_id, user_id) DO UPDATE SET last_seen_at = :now, key_hash = :key_hash",
        org_id=org_id, user_id=user_id, now=_now(), key_hash=key_hash)


async def last_seen(org_id: str, user_id: str) -> datetime | None:
    client = await _client(org_id, user_id)
    return client["last_seen_at"] if client else None


async def unpair(org_id: str, user_id: str) -> None:
    """Forget the paired relay. The next relay to ask becomes the paired one, so this is
    only done together with removing the connection whose sign-in it carried."""

    if _memory is not None:
        _memory_clients.pop((org_id, user_id), None)
        for key in [k for k, r in _memory.items() if (r["org_id"], r["user_id"]) == (org_id, user_id)]:
            del _memory[key]
        return
    await _execute("DELETE FROM relay_clients WHERE org_id = :org_id AND user_id = :user_id", org_id=org_id, user_id=user_id)
    await _execute("DELETE FROM relay_requests WHERE org_id = :org_id AND user_id = :user_id", org_id=org_id, user_id=user_id)


# --- Pairing --------------------------------------------------------------------------
#
# Parked requests carry the system's sign-in, so "signed in as this person" is not enough
# to collect them: a stolen web session would be. Each relay makes a random key of its
# own and sends it with every call. The first relay to call becomes the person's paired
# relay; after that only calls with that key are served. To connect a system through the
# relay, the person types the short code their relay shows (taken from the key), which
# proves the paired relay is the one in front of them and not someone else's.


def _key_hash(relay_key: object) -> str:
    key = str(relay_key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{32,128}", key):
        raise HTTPException(status_code=400, detail="This relay is too old. Update MindLoom for Windows.")
    return hashlib.sha256(key.encode()).hexdigest()


def code_of(key_hash: str) -> str:
    """The pairing code people compare: eight characters of the key's fingerprint."""

    return f"{key_hash[:4]}-{key_hash[4:8]}".upper()


async def _paired(org_id: str, user_id: str, relay_key: object) -> str:
    """The caller's key fingerprint, when it is this person's paired relay (or becomes it)."""

    key_hash = _key_hash(relay_key)
    client = await _client(org_id, user_id)
    if client and client.get("key_hash") and not hmac.compare_digest(str(client["key_hash"]), key_hash):
        raise HTTPException(status_code=403, detail=(
            "Another relay is paired with this account. To use this one, disconnect the system that "
            "goes through the relay in MindLoom, then connect it again."
        ))
    return key_hash


async def check_code(org_id: str, user_id: str, code: object) -> None:
    """Raise unless ``code`` is the one this person's paired relay shows."""

    client = await _client(org_id, user_id)
    if not client or not client.get("key_hash"):
        raise HTTPException(status_code=503, detail=(
            "No relay is running. Open MindLoom for Windows, signed in as you, on a PC inside your network."
        ))
    typed = re.sub(r"[^A-Za-z0-9]", "", str(code or "")).upper()
    if not typed or not hmac.compare_digest(typed, code_of(str(client["key_hash"])).replace("-", "")):
        raise HTTPException(status_code=422, detail="That is not the relay code MindLoom for Windows shows on that PC.")


# --- For the server's own calls -------------------------------------------------------


async def status(org_id: str, user_id: str) -> dict[str, Any]:
    seen = await last_seen(org_id, user_id)
    online = bool(seen and seen > _now() - timedelta(seconds=ONLINE_SECONDS))
    return {"online": online, "last_seen_at": seen.isoformat() if seen else None, "paired": seen is not None}


async def forward(org_id: str, user_id: str, *, method: str, url: str, headers: dict[str, str],
                  body: bytes | None = None) -> httpx.Response:
    """Have the person's relay make one call and return its answer."""

    if method not in METHODS or not url.startswith("https://"):
        raise HTTPException(status_code=422, detail="The relay only makes GET, POST and PATCH calls to https addresses.")
    if body is not None and len(body) > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="That is too much data for one call through the relay.")
    if not (await status(org_id, user_id))["online"]:
        raise HTTPException(status_code=503, detail=(
            "The MindLoom relay is not running. Open MindLoom for Windows, signed in as you, on a PC "
            "inside your network that can reach the system."
        ))
    request_id = uuid4().hex
    await _insert({
        "request_id": request_id, "org_id": org_id, "user_id": user_id, "method": method,
        "url": encrypt_token(url),
        "headers": encrypt_token(json.dumps(headers)),
        "body": encrypt_token(base64.b64encode(body).decode()) if body is not None else None,
        "status": "pending", "created_at": _now(),
    })
    try:
        waited = 0.0
        while waited < WAIT_SECONDS:
            done = await _fetch_done(request_id)
            if done is not None:
                return _response(done)
            await asyncio.sleep(WAIT_STEP_SECONDS)
            waited += WAIT_STEP_SECONDS
    finally:
        await _remove(request_id)
    raise HTTPException(status_code=504, detail="The MindLoom relay did not answer in time. Check the PC it runs on.")


def _response(done: dict[str, Any]) -> httpx.Response:
    if done.get("error"):
        raise HTTPException(status_code=502, detail=f"The MindLoom relay could not make the call: {str(done['error'])[:300]}")
    try:
        pairs = json.loads(decrypt_token(done.get("response_headers")) or "[]")
    except ValueError:
        pairs = []
    # The relay sends the body already decoded, so the length and encoding headers no longer apply.
    skipped = {"content-length", "content-encoding", "transfer-encoding", "connection"}
    headers = []
    for pair in pairs if isinstance(pairs, list) else []:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            continue
        name, value = str(pair[0]), str(pair[1])
        # Only well-formed headers: the answer comes from outside, and is read as SAP's.
        if (re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}", name) and name.lower() not in skipped
                and len(value) <= MAX_HEADER_CHARS and not re.search(r"[\x00-\x1f\x7f]", value)):
            headers.append((name, value))
    try:
        content = base64.b64decode(decrypt_token(done.get("response_body")) or "", validate=True)
        code = int(done.get("response_status") or 0)
    except (ValueError, TypeError):
        raise HTTPException(status_code=502, detail="The MindLoom relay sent an unreadable answer.") from None
    if not 100 <= code <= 599:
        raise HTTPException(status_code=502, detail="The MindLoom relay sent an unreadable answer.")
    return httpx.Response(code, headers=headers, content=content)


# --- For the relay --------------------------------------------------------------------


async def poll_seconds(org_id: str, user_id: str) -> int:
    """How soon the relay should ask again: often when this person has a connection that
    goes through it, less often otherwise."""

    from weaver import sap

    row = await sap._get_row(org_id, user_id)
    return ACTIVE_POLL_SECONDS if row is not None and getattr(row, "via", "direct") == "relay" else IDLE_POLL_SECONDS


async def claim(org_id: str, user_id: str, relay_key: object) -> dict[str, Any]:
    """The paired relay's poll: marks it as running and hands it the waiting requests, each
    with the seconds left before the server stops waiting for its answer."""

    key_hash = await _paired(org_id, user_id, relay_key)
    await _touch(org_id, user_id, key_hash)
    now = _now()
    requests = []
    for row in await _claim(org_id, user_id, MAX_CLAIM):
        left = WAIT_SECONDS - (now - row["created_at"]).total_seconds()
        requests.append({
            "id": row["request_id"], "method": row["method"], "url": decrypt_token(row["url"]),
            "headers": json.loads(decrypt_token(row.get("headers")) or "{}"),
            "body_base64": decrypt_token(row.get("body")), "seconds_left": round(max(left, 0.0), 1),
        })
    return {"requests": requests, "poll_seconds": await poll_seconds(org_id, user_id), "code": code_of(key_hash)}


async def complete(org_id: str, user_id: str, request_id: str, relay_key: object, *, status_code: int | None,
                   headers: list[list[str]] | None, body_base64: str | None, error: str | None) -> dict[str, bool]:
    """The paired relay's answer to one request it claimed."""

    await _paired(org_id, user_id, relay_key)
    raw = body_base64 or ""
    if len(raw) > MAX_RESPONSE_BYTES * 4 // 3 + 4:
        raw, error = "", "The answer was too large to send back."
    try:
        base64.b64decode(raw, validate=True)
    except ValueError:
        raw, error = "", "The relay sent an unreadable answer."
    ok = await _finish(org_id, user_id, request_id, {
        "response_status": status_code, "response_headers": encrypt_token(json.dumps((headers or [])[:100])),
        "response_body": encrypt_token(raw), "error": (error or None) and str(error)[:500],
    })
    if not ok:
        raise HTTPException(status_code=404, detail="That request is no longer waiting.")
    return {"ok": True}
