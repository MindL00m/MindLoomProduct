"""Tests for local and S3 blob storage backends."""

from __future__ import annotations

import pytest

from core.blob_storage import LocalBlobStorage, S3BlobStorage, get_blob_storage, reset_blob_storage
from core.config import get_settings


@pytest.mark.asyncio
async def test_local_blob_put_get(tmp_path) -> None:
    storage = LocalBlobStorage(tmp_path)
    uri = await storage.put("docs/a.txt", b"hello")
    assert uri.startswith("file://")
    assert await storage.get(uri) == b"hello"
    assert await storage.exists(uri)
    await storage.delete(uri)
    assert not await storage.exists(uri)


@pytest.mark.asyncio
async def test_s3_blob_put_get_with_stub() -> None:
    objects: dict[tuple[str, str], bytes] = {}

    class FakeBody:
        def __init__(self, data: bytes):
            self._data = data

        def read(self) -> bytes:
            return self._data

    class FakeClient:
        class exceptions:
            class ClientError(Exception):
                def __init__(self, response):
                    self.response = response

        def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
            objects[(Bucket, Key)] = Body

        def get_object(self, *, Bucket: str, Key: str):
            data = objects[(Bucket, Key)]
            return {"Body": FakeBody(data)}

        def head_object(self, *, Bucket: str, Key: str) -> dict:
            if (Bucket, Key) not in objects:
                raise self.exceptions.ClientError({"Error": {"Code": "404"}})
            return {}

        def delete_object(self, *, Bucket: str, Key: str) -> None:
            objects.pop((Bucket, Key), None)

    storage = S3BlobStorage("test-bucket", region="us-east-1", client=FakeClient())
    uri = await storage.put("captures/org/1.png", b"png-bytes")
    assert uri == "s3://test-bucket/captures/org/1.png"
    assert await storage.get(uri) == b"png-bytes"
    assert await storage.exists(uri)
    await storage.delete(uri)
    assert not await storage.exists(uri)


def test_get_blob_storage_selects_local(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("BLOB_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOB_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    reset_blob_storage()
    storage = get_blob_storage()
    assert isinstance(storage, LocalBlobStorage)
    reset_blob_storage()
    get_settings.cache_clear()
