"""Shared type-preserving Telethon media upload serialization."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from telethon import types, utils  # type: ignore[import-untyped]

from telegram_assist_bot.domain.media import MediaType

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import PublicationMedia


class TelethonMediaUploadClient(Protocol):
    """Describe the shared Telethon upload call used by publication paths."""

    async def upload_file(self, file: object, **kwargs: object) -> object:
        """Upload one confined file with an explicit destination filename."""
        ...


class TelethonMediaSerializationError(RuntimeError):
    """Represent a safe deterministic media serialization failure."""


class TelethonMediaSerializer:
    """Upload and map application media without exposing hashed storage names."""

    def __init__(self, client: TelethonMediaUploadClient, *, media_root: Path) -> None:
        """Store the shared client and canonical private media root."""
        try:
            self._media_root = media_root.resolve(strict=True)
        except OSError:
            raise TelethonMediaSerializationError(
                "Publication media root is unavailable."
            ) from None
        self._client = client

    async def serialize(
        self, media: tuple[PublicationMedia, ...]
    ) -> tuple[object, ...]:
        """Return ordered Telethon input media with explicit safe filenames."""
        values: list[object] = []
        for item in media:
            path = self._resolve(item.storage_path)
            filename = self._filename(item)
            uploaded = await self._client.upload_file(str(path), file_name=filename)
            values.append(self._input_media(item, uploaded, filename))
        return tuple(values)

    def _resolve(self, storage_path: str) -> Path:
        candidate = Path(storage_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise TelethonMediaSerializationError("Publication media path is invalid.")
        current = self._media_root
        for part in candidate.parts:
            current /= part
            if current.is_symlink():
                raise TelethonMediaSerializationError(
                    "Publication media path is invalid."
                )
        try:
            resolved = (self._media_root / candidate).resolve(strict=True)
            resolved.relative_to(self._media_root)
        except (OSError, ValueError):
            raise TelethonMediaSerializationError(
                "Publication media path is invalid."
            ) from None
        if not resolved.is_file() or resolved.is_symlink():
            raise TelethonMediaSerializationError("Publication media path is invalid.")
        return resolved

    @staticmethod
    def _filename(item: PublicationMedia) -> str:
        if item.original_filename:
            filename = Path(item.original_filename).name
            if filename not in {"", ".", ".."}:
                return filename
        suffix = {
            MediaType.PHOTO: "jpg",
            MediaType.VIDEO: "mp4",
            MediaType.ANIMATION: ("gif" if item.mime_type == "image/gif" else "mp4"),
            MediaType.DOCUMENT: "bin",
            MediaType.AUDIO: "bin",
            MediaType.VOICE: "ogg",
            MediaType.STICKER: "webp",
            MediaType.VIDEO_NOTE: "mp4",
        }[item.media_type]
        return f"publication-{item.media_type.value.lower()}.{suffix}"

    @staticmethod
    def _input_media(item: PublicationMedia, uploaded: object, filename: str) -> object:
        if item.media_type is MediaType.PHOTO:
            return types.InputMediaUploadedPhoto(file=uploaded)
        force_document = item.media_type not in {
            MediaType.VIDEO,
            MediaType.ANIMATION,
        }
        attributes, mime_type = utils.get_attributes(
            uploaded,
            mime_type=item.mime_type,
            force_document=force_document,
            supports_streaming=item.media_type is MediaType.VIDEO,
        )
        attributes = [
            value
            for value in attributes
            if not isinstance(value, types.DocumentAttributeFilename)
        ]
        attributes.insert(0, types.DocumentAttributeFilename(filename))
        if item.media_type is MediaType.ANIMATION and not any(
            isinstance(value, types.DocumentAttributeAnimated) for value in attributes
        ):
            attributes.append(types.DocumentAttributeAnimated())
        return types.InputMediaUploadedDocument(
            file=uploaded,
            mime_type=mime_type,
            attributes=attributes,
            force_file=force_document,
            nosound_video=item.media_type is MediaType.VIDEO,
        )


__all__ = (
    "TelethonMediaSerializationError",
    "TelethonMediaSerializer",
    "TelethonMediaUploadClient",
)
