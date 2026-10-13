"""Inbound API rate limiting via slowapi + Redis."""

from __future__ import annotations

import logging
import os
import types
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _client_key(request: Request) -> str:
    """Prefer authenticated org:user from Bearer JWT; fall back to client IP."""

    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        try:
            from keeper.session_tokens import decode_access_token

            claims = decode_access_token(token.strip())
            return f"user:{claims.org_id}:{claims.user_id}"
        except Exception:  # noqa: BLE001 - invalid token still rate-limits by IP
            pass
    return f"ip:{get_remote_address(request)}"


def _storage_uri() -> str | None:
    uri = os.environ.get("REDIS_URL", "").strip()
    return uri or None


def _build_limiter() -> Limiter:
    storage_uri = _storage_uri()
    app_env = os.environ.get("APP_ENV", "development").strip().lower()
    try:
        return Limiter(
            key_func=_client_key,
            storage_uri=storage_uri,
            default_limits=[],
            headers_enabled=False,
        )
    except Exception as exc:  # noqa: BLE001
        if app_env == "production":
            raise RuntimeError(
                f"Rate limiter could not connect to Redis at {storage_uri!r}: {exc}"
            ) from exc
        logger.warning(
            "Rate limiter falling back to in-memory storage (Redis unavailable: %s)",
            exc,
        )
        return Limiter(
            key_func=_client_key,
            default_limits=[],
            headers_enabled=False,
        )


limiter = _build_limiter()


def _rebind_globals(wrapper: F, donor: Callable[..., Any]) -> F:
    """Rebuild ``wrapper`` so FastAPI can resolve annotations AND SlowAPI can run.

    SlowAPI's ``@limiter.limit`` wrapper keeps ``slowapi.extension`` as
    ``__globals__``. With ``from __future__ import annotations``, FastAPI then
    fails to resolve Pydantic body models and treats them as query params
    (``Field required`` on ``body``).

    Rebinding to the donor module alone breaks SlowAPI (``Response`` is missing).
    Merge both namespaces: SlowAPI symbols stay, donor models/annotations win.
    """

    merged_globals = {**wrapper.__globals__, **donor.__globals__}
    rebound = types.FunctionType(
        wrapper.__code__,
        merged_globals,
        name=wrapper.__name__,
        argdefs=wrapper.__defaults__,
        closure=wrapper.__closure__,
    )
    rebound.__module__ = donor.__module__
    rebound.__doc__ = wrapper.__doc__
    rebound.__annotations__ = dict(getattr(donor, "__annotations__", {}))
    rebound.__kwdefaults__ = wrapper.__kwdefaults__
    rebound.__dict__.update(wrapper.__dict__)
    rebound.__wrapped__ = getattr(wrapper, "__wrapped__", donor)
    return rebound  # type: ignore[return-value]


def limit(limit_value: Any, *args: Any, **kwargs: Any) -> Callable[[F], F]:
    """Like ``limiter.limit``, but preserves the endpoint module globals."""

    apply = limiter.limit(limit_value, *args, **kwargs)

    def decorator(func: F) -> F:
        return _rebind_globals(apply(func), func)

    return decorator


def auth_limit() -> str:
    return os.environ.get("RATE_LIMIT_AUTH", "20/minute").strip() or "20/minute"


def query_limit() -> str:
    return os.environ.get("RATE_LIMIT_QUERY", "30/minute").strip() or "30/minute"


def captures_limit() -> str:
    return os.environ.get("RATE_LIMIT_CAPTURES", "30/minute").strip() or "30/minute"


def ingest_limit() -> str:
    return os.environ.get("RATE_LIMIT_INGEST", "20/minute").strip() or "20/minute"
