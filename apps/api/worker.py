"""Run the durable Redis ingestion worker."""

from __future__ import annotations

import asyncio
import json
import logging

from redis.asyncio import Redis

from config import get_settings
from database import close_pools
from durable_jobs import PROCESSING_QUEUE_NAME, QUEUE_NAME, execute
from schema import ensure_schema
from connection_setup import run_periodic_connection_checks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    await ensure_schema()
    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    scheduler = asyncio.create_task(run_periodic_connection_checks())
    try:
        # Recover work reserved by this single worker before a process crash.
        while True:
            stranded = await redis.rpoplpush(PROCESSING_QUEUE_NAME, QUEUE_NAME)
            if stranded is None:
                break
        while True:
            item = await redis.brpoplpush(
                QUEUE_NAME, PROCESSING_QUEUE_NAME, timeout=30
            )
            if item:
                await execute(json.loads(item))
                await redis.lrem(PROCESSING_QUEUE_NAME, 1, item)
    finally:
        scheduler.cancel()
        await redis.aclose()
        await close_pools()


if __name__ == "__main__":
    asyncio.run(main())
