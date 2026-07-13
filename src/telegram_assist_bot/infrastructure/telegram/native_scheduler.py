"""Telethon adapter for native Scheduled Messages on the shared runtime client."""

from __future__ import annotations

import asyncio
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from telethon import functions, types  # type: ignore[import-untyped]

from telegram_assist_bot.application.ports import (
    NativeScheduledMessage,
    NativeScheduleReceipt,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.infrastructure.telegram.user_publisher import _map_entity

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from telegram_assist_bot.application.ports import PublicationPayload


class TelethonNativeSchedulerClient(Protocol):
    """Describe the small Telethon surface used by native scheduling."""

    def iter_messages(self, entity: int, **kwargs: object) -> AsyncIterator[object]:
        """Iterate messages with the requested Telethon filter."""
        ...

    async def send_message(self, entity: int, message: str, **kwargs: object) -> object:
        """Send or schedule text."""
        ...

    async def send_file(self, entity: int, file: object, **kwargs: object) -> object:
        """Send or schedule media."""
        ...

    async def upload_file(self, file: object, **kwargs: object) -> object:
        """Upload media while retaining its original filename metadata."""
        ...

    async def get_input_entity(self, peer: int) -> object:
        """Resolve a peer for raw deletion requests."""
        ...

    async def __call__(self, request: object) -> object:
        """Execute one raw Telethon request."""
        ...


class TelethonNativeSchedulerGateway:
    """Create and delete native schedules without opening another session."""

    def __init__(
        self, client: TelethonNativeSchedulerClient, *, media_root: Path
    ) -> None:
        """Store the existing client and canonical media root."""
        self._client = client
        self._media_root = media_root.resolve(strict=True)

    async def list_scheduled(
        self, destination_id: int, *, timeout_seconds: float
    ) -> tuple[NativeScheduledMessage, ...]:
        """Read native schedules including entries created outside this app."""
        values: list[NativeScheduledMessage] = []
        async with asyncio.timeout(timeout_seconds):
            async for message in self._client.iter_messages(
                destination_id, scheduled=True
            ):
                identifier = int(getattr(message, "id", 0))
                due_at = getattr(message, "date", None)
                if identifier > 0 and due_at is not None and due_at.tzinfo is not None:
                    values.append(
                        NativeScheduledMessage(identifier, due_at.astimezone(UTC))
                    )
        return tuple(values)

    async def schedule(
        self,
        payload: PublicationPayload,
        *,
        due_at: datetime,
        timeout_seconds: float,
    ) -> NativeScheduleReceipt:
        """Schedule prepared text or media at the exact aware due time."""
        entities = [_map_entity(value) for value in payload.entities]
        async with asyncio.timeout(timeout_seconds):
            if not payload.media:
                result = await self._client.send_message(
                    payload.destination_id,
                    payload.text or "",
                    formatting_entities=entities,
                    parse_mode=None,
                    schedule=due_at,
                )
            else:
                paths = [self._resolve(item.storage_path) for item in payload.media]
                uploads = [
                    await self._client.upload_file(
                        path,
                        file_name=item.original_filename or Path(path).name,
                    )
                    for path, item in zip(paths, payload.media, strict=True)
                ]
                kwargs: dict[str, object] = {
                    "caption": payload.text,
                    "formatting_entities": entities,
                    "parse_mode": None,
                    "schedule": due_at,
                }
                if len(payload.media) == 1:
                    media_type = payload.media[0].media_type
                    kwargs["force_document"] = media_type in {
                        MediaType.DOCUMENT,
                        MediaType.ANIMATION,
                    }
                    if media_type is MediaType.VIDEO:
                        kwargs["supports_streaming"] = True
                    elif media_type is MediaType.ANIMATION:
                        kwargs["attributes"] = [types.DocumentAttributeAnimated()]
                result = await self._client.send_file(
                    payload.destination_id,
                    uploads[0] if len(uploads) == 1 else uploads,
                    **kwargs,
                )
        values = result if isinstance(result, list | tuple) else (result,)
        identifiers = tuple(int(getattr(value, "id", 0)) for value in values)
        if not identifiers or any(value <= 0 for value in identifiers):
            raise RuntimeError("Telegram returned an invalid native schedule receipt.")
        return NativeScheduleReceipt(identifiers, due_at.astimezone(UTC))

    async def cancel(
        self,
        destination_id: int,
        message_ids: tuple[int, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        """Delete native schedules by persisted Telegram identities."""
        if not message_ids:
            return
        async with asyncio.timeout(timeout_seconds):
            peer = await self._client.get_input_entity(destination_id)
            await self._client(
                functions.messages.DeleteScheduledMessagesRequest(
                    peer=peer, id=list(message_ids)
                )
            )

    def _resolve(self, storage_path: str) -> str:
        candidate = Path(storage_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("Native schedule media path is invalid.")
        current = self._media_root
        for part in candidate.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Native schedule media path is invalid.")
        try:
            resolved = (self._media_root / candidate).resolve(strict=True)
            resolved.relative_to(self._media_root)
        except (OSError, ValueError):
            raise ValueError("Native schedule media path is invalid.") from None
        if not resolved.is_file() or resolved.is_symlink():
            raise ValueError("Native schedule media path is invalid.")
        return str(resolved)


__all__ = ("TelethonNativeSchedulerClient", "TelethonNativeSchedulerGateway")
