"""Thin Redis helpers shared by OAuth state (and eventually jobs / rate limits)."""

from __future__ import annotations

import logging
from functools import lru_cache

import redis

from core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_redis_sync() -> redis.Redis:
    """Return a process-wide sync Redis client (decode_responses=True)."""

    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def reset_redis_client() -> None:
    """Clear the cached client (tests / settings reload)."""

    get_redis_sync.cache_clear()
