"""Atomic private filesystem media storage."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from telegram_assist_bot.application.ports import (
    MediaPermanentError,
    MediaTooLargeError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from telegram_assist_bot.domain.media import MediaIdentity


class LocalMediaStorage:
    """Confine streamed media to one private runtime root."""

    def __init__(self, root: Path) -> None:
        """Create and protect the private owned storage root."""
        resolved = root.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            resolved.chmod(stat.S_IRWXU)
        self._root = resolved
        self._temporary = resolved / ".tmp"
        self._temporary.mkdir(exist_ok=True)

    def _safe(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise MediaPermanentError("Media path is outside the storage root.")
        resolved = (self._root / candidate).resolve()
        if not resolved.is_relative_to(self._root):
            raise MediaPermanentError("Media path is outside the storage root.")
        current = self._root
        for part in candidate.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise MediaPermanentError("Media path contains a symbolic link.")
        return resolved

    async def store(
        self,
        identity: MediaIdentity,
        stream: AsyncIterator[bytes],
        *,
        maximum_bytes: int,
    ) -> tuple[str, int, str]:
        """Stream to a temporary file and atomically commit by content hash."""
        if maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be positive")
        temporary = self._temporary / f"{identity.key}.{uuid4().hex}.partial"
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as output:
                with suppress(OSError):
                    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
                async for chunk in stream:
                    if not chunk:
                        raise MediaPermanentError(
                            "Media stream yielded an invalid chunk."
                        )
                    size += len(chunk)
                    if size > maximum_bytes:
                        raise MediaTooLargeError(
                            "Media exceeds the configured size limit."
                        )
                    digest.update(chunk)
                    await asyncio.to_thread(output.write, chunk)
                await asyncio.to_thread(output.flush)
                await asyncio.to_thread(os.fsync, output.fileno())
            content_hash = digest.hexdigest()
            relative = f"sha256/{content_hash[:2]}/{content_hash}"
            final = self._safe(relative)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                temporary.unlink(missing_ok=True)
                if final.stat().st_size != size:
                    raise MediaPermanentError(
                        "Existing canonical media is inconsistent."
                    )
            else:
                temporary.replace(final)
                with suppress(OSError):
                    final.chmod(stat.S_IRUSR | stat.S_IWUSR)
            return relative, size, content_hash
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    async def exists(self, storage_path: str) -> bool:
        """Return whether a confined regular file exists."""
        path = self._safe(storage_path)
        return path.is_file() and not path.is_symlink()

    async def delete(self, storage_path: str) -> bool:
        """Delete a confined regular file idempotently."""
        path = self._safe(storage_path)
        if not path.exists():
            return False
        if path.is_symlink() or not path.is_file():
            raise MediaPermanentError("Only regular media files may be deleted.")
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    async def delete_stale_temporary_files(
        self, *, older_than: datetime, limit: int
    ) -> int:
        """Delete bounded old partial files from the owned temp directory."""
        if older_than.tzinfo is None or limit <= 0:
            raise ValueError("Cleanup boundary is invalid.")
        removed = 0
        for path in sorted(self._temporary.glob("*.partial")):
            if removed >= limit:
                break
            if path.is_symlink() or not path.is_file():
                continue
            modified = datetime.fromtimestamp(
                path.stat().st_mtime, tz=older_than.tzinfo
            )
            if modified <= older_than:
                path.unlink(missing_ok=True)
                removed += 1
        return removed
