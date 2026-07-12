"""Atomic private filesystem media storage."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
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

    from telegram_assist_bot.domain.media import MediaIdentity, StoredMedia


class LocalMediaStorage:
    """Confine streamed media to one private runtime root."""

    def __init__(
        self,
        root: Path,
        *,
        preview_enabled: bool = False,
        preview_root: Path = Path("data/media-preview"),
    ) -> None:
        """Create and protect the private owned storage root."""
        resolved = root.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            resolved.chmod(stat.S_IRWXU)
        self._root = resolved
        self._temporary = resolved / ".tmp"
        self._temporary.mkdir(exist_ok=True)
        self._preview_enabled = preview_enabled
        self._preview_root = preview_root.resolve()

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

    async def ensure_preview(self, media: StoredMedia) -> bool:
        """Create one optional normal preview copy without touching canonical media."""
        if not self._preview_enabled:
            return False
        await self.prepare_preview_directory()
        source = self._safe(media.storage_path)
        if not source.is_file() or source.is_symlink():
            return False
        return await asyncio.to_thread(
            self._create_preview,
            source,
            media.content_hash,
            media.mime_type,
            media.original_filename,
            self._preview_root,
        )

    async def prepare_preview_directory(self) -> bool:
        """Create the optional view-only preview directory when enabled."""
        if not self._preview_enabled:
            return False
        await asyncio.to_thread(self._preview_root.mkdir, parents=True, exist_ok=True)
        return True

    async def backfill_previews(self, media: tuple[StoredMedia, ...]) -> int:
        """Create only missing previews for persisted media records."""
        if not self._preview_enabled:
            return 0
        await self.prepare_preview_directory()
        created = 0
        for item in media:
            if await self.ensure_preview(item):
                created += 1
        return created

    @classmethod
    def _create_preview(
        cls,
        source: Path,
        content_hash: str,
        mime_type: str | None,
        original_filename: str | None,
        preview_root: Path,
    ) -> bool:
        extension = cls._preview_extension(source, mime_type, original_filename)
        return cls._copy_preview(source, preview_root / f"{content_hash}.{extension}")

    @staticmethod
    def _preview_extension(
        source: Path,
        mime_type: str | None,
        original_filename: str | None,
    ) -> str:
        mime_extensions = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
            "video/mp4": "mp4",
            "video/quicktime": "mov",
            "video/webm": "webm",
            "video/x-matroska": "mkv",
            "audio/mpeg": "mp3",
            "audio/mp4": "m4a",
            "audio/ogg": "ogg",
            "application/pdf": "pdf",
            "application/zip": "zip",
        }
        normalized_mime = (
            "" if mime_type is None else mime_type.split(";", 1)[0].strip()
        )
        if normalized_mime.lower() in mime_extensions:
            return mime_extensions[normalized_mime.lower()]
        if original_filename:
            suffix = Path(original_filename).suffix.lower().removeprefix(".")
            if suffix in set(mime_extensions.values()):
                return suffix
        with source.open("rb") as input_file:
            magic = input_file.read(16)
        if magic.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if magic.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if magic.startswith(b"RIFF") and magic[8:12] == b"WEBP":
            return "webp"
        if magic.startswith((b"GIF87a", b"GIF89a")):
            return "gif"
        if magic.startswith(b"%PDF-"):
            return "pdf"
        if magic.startswith(b"PK\x03\x04"):
            return "zip"
        if magic[4:8] == b"ftyp" and magic[8:12] == b"qt  ":
            return "mov"
        if magic[4:8] == b"ftyp" and magic[8:12].lower() in {
            b"m4a ",
            b"m4b ",
            b"m4p ",
        }:
            return "m4a"
        if magic[4:8] == b"ftyp":
            return "mp4"
        if magic.startswith(b"\x1aE\xdf\xa3"):
            return "mkv"
        if magic.startswith(b"ID3"):
            return "mp3"
        if magic.startswith(b"OggS"):
            return "ogg"
        return "bin"

    @staticmethod
    def _copy_preview(source: Path, target: Path) -> bool:
        target.parent.mkdir(parents=True, exist_ok=True)
        if (
            target.is_file()
            and not target.is_symlink()
            and target.stat().st_size == source.stat().st_size
        ):
            return False
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.partial")
        try:
            with source.open("rb") as input_file, temporary.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file, length=64 * 1024)
                output_file.flush()
                os.fsync(output_file.fileno())
            temporary.replace(target)
            return True
        finally:
            temporary.unlink(missing_ok=True)

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
