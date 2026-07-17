"""HTTP retry and pagination helpers for rate-limited provider APIs."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
from fastapi import HTTPException

RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


async def request_with_backoff(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    attempts: int = 6,
    **kwargs: Any,
) -> httpx.Response:
    for attempt in range(attempts):
        try:
            response = await client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == attempts - 1:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUSES:
                return response
            if attempt == attempts - 1:
                return response
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = min(float(retry_after), 120.0)
                except ValueError:
                    delay = 0.0
            else:
                delay = 0.0
        await asyncio.sleep(delay or min(2**attempt + random.random(), 30.0))
    raise RuntimeError("Provider request retry loop ended unexpectedly.")


async def graph_get_all(
    client: httpx.AsyncClient,
    access_token: str,
    path_or_url: str,
    params: dict[str, str] | None = None,
    *,
    item_limit: int = 10_000,
) -> list[dict[str, Any]]:
    url = (
        path_or_url
        if path_or_url.startswith("https://")
        else f"https://graph.microsoft.com/v1.0{path_or_url}"
    )
    items: list[dict[str, Any]] = []
    next_params = params
    while url and len(items) < item_limit:
        response = await request_with_backoff(
            client,
            "GET",
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params=next_params,
        )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Microsoft Graph request failed ({response.status_code}).",
            )
        payload = response.json()
        items.extend(payload.get("value") or [])
        url = str(payload.get("@odata.nextLink") or "")
        next_params = None
    return items[:item_limit]
