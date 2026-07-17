"""Blob storage for raw uploaded source files.

The rest of the pipeline depends only on the :class:`BlobStorage` interface, so
the concrete backend can be swapped (local filesystem today; S3/GCS later)
without touching callers. Backends are content-agnostic key/value stores:

* :meth:`BlobStorage.put` writes bytes under a caller-chosen ``key`` and returns
  an opaque ``storage_path`` URI that the *same* backend understands.
* :meth:`BlobStorage.get` / :meth:`exists` / :meth:`delete` accept that URI.

Keys are expected to be content-addressed (see :mod:`documents`), so writing the
same bytes twice is naturally idempotent.

All methods are ``async`` even though the local backend is blocking — filesystem
I/O is dispatched to a worker thread so it never stalls the event loop, and the
async signature matches what network backends (S3/GCS) will need.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from config import get_settings

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


@lru_cache(maxsize=1)
def get_blob_storage() -> BlobStorage:
    """Return the process-wide blob storage backend selected by configuration."""

    settings = get_settings()
    if settings.blob_storage_backend == "local":
        return LocalBlobStorage(settings.blob_storage_root)
    raise ValueError(f"Unsupported blob storage backend: {settings.blob_storage_backend}")
