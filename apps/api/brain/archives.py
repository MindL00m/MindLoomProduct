"""Reading ZIP archives safely.

ZIP files are opened in memory with limits that stop archive bombs: at most
``MAX_ENTRIES`` files, ``MAX_ENTRY_BYTES`` per file, ``MAX_TOTAL_BYTES`` in all,
and a compression ratio no higher than ``MAX_RATIO``. Folders, macOS resource
forks, hidden files and nested paths are handled here so callers only see
(path, bytes) pairs. Encrypted entries are skipped.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

MAX_ENTRIES = 200
MAX_ENTRY_BYTES = 15_000_000
MAX_TOTAL_BYTES = 100_000_000
MAX_RATIO = 100
MAX_ARCHIVE_BYTES = 50_000_000


class ArchiveError(ValueError):
    """The upload is not a readable ZIP archive, or it is too large to open."""


@dataclass
class ArchiveEntry:
    path: str
    name: str
    data: bytes


@dataclass
class ArchiveContents:
    entries: list[ArchiveEntry] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def is_zip(filename: str, data: bytes = b"") -> bool:
    return (filename or "").lower().endswith(".zip") or data[:4] == b"PK\x03\x04" and not (
        (filename or "").lower().endswith((".docx", ".xlsx", ".pptx"))
    )


def _ignored(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        not parts
        or parts[0] == "__MACOSX"
        or any(part.startswith(".") for part in parts)
        or parts[-1].lower() in {"thumbs.db", "desktop.ini"}
    )


def list_archive(data: bytes) -> list[dict[str, int | str]]:
    """Name and size of each file in the archive, without extracting anything."""

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return [
                {"path": info.filename, "size": info.file_size}
                for info in archive.infolist()
                if not info.is_dir() and not _ignored(info.filename)
            ][:MAX_ENTRIES * 5]
    except zipfile.BadZipFile as exc:
        raise ArchiveError("That file is not a valid ZIP archive.") from exc


def read_archive(data: bytes, *, accept=None) -> ArchiveContents:
    """Extract files (optionally only those ``accept(path)`` allows) within the limits."""

    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("ZIP files are limited to 50 MB.")
    contents = ArchiveContents()
    total = 0
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("That file is not a valid ZIP archive.") from exc
    with archive:
        for info in archive.infolist():
            path = info.filename
            if info.is_dir() or _ignored(path):
                continue
            name = PurePosixPath(path).name
            if accept is not None and not accept(path):
                contents.skipped.append({"path": path, "reason": "This file type is not supported."})
                continue
            if len(contents.entries) >= MAX_ENTRIES:
                contents.skipped.append({"path": path, "reason": f"More than {MAX_ENTRIES} files."})
                continue
            if info.flag_bits & 0x1:
                contents.skipped.append({"path": path, "reason": "Password-protected."})
                continue
            if info.file_size > MAX_ENTRY_BYTES:
                contents.skipped.append({"path": path, "reason": "Larger than 15 MB."})
                continue
            if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                contents.skipped.append({"path": path, "reason": "Suspiciously compressed."})
                continue
            if total + info.file_size > MAX_TOTAL_BYTES:
                contents.skipped.append({"path": path, "reason": "The archive's files exceed 100 MB."})
                continue
            with archive.open(info) as handle:
                # Read at most the declared size + 1 so a lying header cannot inflate memory.
                blob = handle.read(info.file_size + 1)
            if len(blob) > info.file_size:
                contents.skipped.append({"path": path, "reason": "Size does not match the archive."})
                continue
            total += len(blob)
            contents.entries.append(ArchiveEntry(path=path, name=name, data=blob))
    return contents


# Office files (DOCX, XLSX, PPTX) are ZIPs too. A 15 MB file whose XML inflates a
# thousand times would exhaust memory in any parser, so check before parsing.
MAX_OOXML_PART_BYTES = 100_000_000
MAX_OOXML_TOTAL_BYTES = 300_000_000
MAX_OOXML_RATIO = 200


def check_ooxml(data: bytes) -> None:
    """Raise :class:`ArchiveError` for an Office file that would decompress too far."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("That is not a valid Office file.") from exc
    with archive:
        total = 0
        for info in archive.infolist():
            total += info.file_size
            ratio = info.file_size / max(info.compress_size, 1)
            if (
                info.file_size > MAX_OOXML_PART_BYTES
                or total > MAX_OOXML_TOTAL_BYTES
                or (info.file_size > 1_000_000 and ratio > MAX_OOXML_RATIO)
            ):
                raise ArchiveError("That Office file expands to an unsafe size and was not opened.")
