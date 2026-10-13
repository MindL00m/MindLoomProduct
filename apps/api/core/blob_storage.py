"""Blob storage for raw uploaded source files and capture images.

The rest of the pipeline depends only on the :class:`BlobStorage` interface, so
the concrete backend can be swapped (local filesystem or S3) without touching
callers. Backends are content-agnostic key/value stores:

* :meth:`BlobStorage.put` writes bytes under a caller-chosen ``key`` and returns
  an opaque ``storage_path`` URI that the *same* backend understands.
* :meth:`BlobStorage.get` / :meth:`exists` / :meth:`delete` accept that URI.

Keys are expected to be content-addressed (see :mod:`documents`), so writing the
same bytes twice is naturally idempotent.

All methods are ``async`` even though the local backend is blocking — filesystem
I/O is dispatched to a worker thread so it never stalls the event loop, and the
async signature matches what network backends (S3) need.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.config import get_settings

logger = logging.getLogger(__name__)


class BlobStorage(ABC):
    """Abstract content-agnostic blob store. Implement once per backend."""

    @abstractmethod
    async def put(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key`` and return its ``storage_path`` URI.

        Implementations must be idempotent: storing identical bytes under the
        same key twice is a no-op that returns the same URI.
        """

    @abstractmethod
    async def get(self, storage_path: str) -> bytes:
        """Return the bytes previously stored at ``storage_path``."""

    @abstractmethod
    async def exists(self, storage_path: str) -> bool:
        """Return whether an object exists at ``storage_path``."""

    @abstractmethod
    async def delete(self, storage_path: str) -> None:
        """Delete the object at ``storage_path`` (no error if already absent)."""


class LocalBlobStorage(BlobStorage):
    """Filesystem-backed blob store rooted at a configurable directory.

    The returned ``storage_path`` is a ``file://<key>`` URI where ``<key>`` is
    relative to ``root``. Storing the key (not an absolute path) keeps records
    portable across machines and mirrors how object stores address objects by
    bucket + key.
    """

    _SCHEME = "file://"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    def _key_of(self, storage_path: str) -> str:
        if storage_path.startswith(self._SCHEME):
            return storage_path[len(self._SCHEME) :]
        return storage_path

    def _path_of(self, storage_path: str) -> Path:
        # Resolve and confirm the target stays within root (defends against
        # keys containing ``..`` traversal).
        target = (self._root / self._key_of(storage_path)).resolve()
        if self._root not in target.parents and target != self._root:
            raise ValueError(f"storage path escapes blob root: {storage_path!r}")
        return target

    async def put(self, key: str, data: bytes) -> str:
        path = self._path_of(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Content-addressed keys mean identical content yields an identical
            # path; rewriting the same bytes is harmless and keeps this idempotent.
            path.write_bytes(data)

        await asyncio.to_thread(_write)
        logger.info("Stored blob (%d bytes) at key %s", len(data), key)
        return f"{self._SCHEME}{key}"

    async def get(self, storage_path: str) -> bytes:
        path = self._path_of(storage_path)
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, storage_path: str) -> bool:
        path = self._path_of(storage_path)
        return await asyncio.to_thread(path.is_file)

    async def delete(self, storage_path: str) -> None:
        path = self._path_of(storage_path)

        def _unlink() -> None:
            path.unlink(missing_ok=True)

        await asyncio.to_thread(_unlink)


class S3BlobStorage(BlobStorage):
    """S3-compatible object store (AWS, MinIO, R2 via endpoint_url)."""

    _SCHEME = "s3://"

    def __init__(
        self,
        bucket: str,
        *,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3 bucket name is required")
        self._bucket = bucket.strip()
        self._region = region
        self._endpoint_url = (endpoint_url or "").strip() or None
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        import boto3

        kwargs: dict[str, Any] = {"region_name": self._region}
        if self._endpoint_url:
            kwargs["endpoint_url"] = self._endpoint_url
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def _key_of(self, storage_path: str) -> str:
        prefix = f"{self._SCHEME}{self._bucket}/"
        if storage_path.startswith(prefix):
            return storage_path[len(prefix) :]
        if storage_path.startswith(self._SCHEME):
            # s3://other-bucket/key — reject mismatched bucket
            rest = storage_path[len(self._SCHEME) :]
            bucket, _, key = rest.partition("/")
            if bucket != self._bucket:
                raise ValueError(f"storage path bucket mismatch: {storage_path!r}")
            return key
        return storage_path

    def _uri(self, key: str) -> str:
        return f"{self._SCHEME}{self._bucket}/{key}"

    async def put(self, key: str, data: bytes) -> str:
        client = self._get_client()

        def _put() -> None:
            client.put_object(Bucket=self._bucket, Key=key, Body=data)

        await asyncio.to_thread(_put)
        logger.info("Stored S3 blob (%d bytes) at s3://%s/%s", len(data), self._bucket, key)
        return self._uri(key)

    async def get(self, storage_path: str) -> bytes:
        client = self._get_client()
        key = self._key_of(storage_path)

        def _get() -> bytes:
            response = client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()

        return await asyncio.to_thread(_get)

    async def exists(self, storage_path: str) -> bool:
        client = self._get_client()
        key = self._key_of(storage_path)

        def _head() -> bool:
            try:
                client.head_object(Bucket=self._bucket, Key=key)
                return True
            except client.exceptions.ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"404", "NoSuchKey", "NotFound"}:
                    return False
                raise

        try:
            return await asyncio.to_thread(_head)
        except Exception:  # noqa: BLE001 — botocore error shapes vary by stub
            # Fallback for stub clients without exceptions.ClientError
            try:
                await self.get(storage_path)
                return True
            except Exception:  # noqa: BLE001
                return False

    async def delete(self, storage_path: str) -> None:
        client = self._get_client()
        key = self._key_of(storage_path)

        def _delete() -> None:
            client.delete_object(Bucket=self._bucket, Key=key)

        await asyncio.to_thread(_delete)


@lru_cache(maxsize=1)
def get_blob_storage() -> BlobStorage:
    """Return the process-wide blob storage backend selected by configuration."""

    settings = get_settings()
    if settings.blob_storage_backend == "local":
        return LocalBlobStorage(settings.blob_storage_root)
    if settings.blob_storage_backend == "s3":
        if not settings.s3_bucket.strip():
            raise ValueError("S3_BUCKET is required when BLOB_STORAGE_BACKEND=s3")
        return S3BlobStorage(
            settings.s3_bucket,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url or None,
        )
    raise ValueError(f"Unsupported blob storage backend: {settings.blob_storage_backend}")


def reset_blob_storage() -> None:
    """Clear the cached backend (tests / settings reload)."""

    get_blob_storage.cache_clear()
