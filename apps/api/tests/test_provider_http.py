import httpx
import pytest

import provider_http


@pytest.mark.asyncio
async def test_request_with_backoff_retries_throttling(monkeypatch):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429 if calls == 1 else 200, request=request)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(provider_http.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await provider_http.request_with_backoff(
            client, "GET", "https://provider.test/items"
        )

    assert response.status_code == 200
    assert calls == 2


@pytest.mark.asyncio
async def test_graph_get_all_follows_next_link():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"value": [{"id": "2"}]}, request=request)
        return httpx.Response(
            200,
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/items?page=2",
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await provider_http.graph_get_all(client, "token", "/items")

    assert [item["id"] for item in items] == ["1", "2"]
