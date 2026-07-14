"""Bounded aiogram adapter for private administrator messages."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageEntity,
)

from telegram_assist_bot.application.ports import (
    ApprovalContent,
    ApprovalDeliveryRateLimitError,
    ApprovalDeliveryRejectedError,
    ApprovalDeliveryTransientError,
    ApprovalDeliveryUnavailableError,
    ApprovalMedia,
    ApprovalMediaNetworkError,
    ApprovalMediaPathError,
    ApprovalMediaRejectedError,
    ApprovalMediaUploadTimeoutError,
    BotEditOutcome,
    InlineKeyboard,
)

if TYPE_CHECKING:
    from aiogram import Bot

    from telegram_assist_bot.domain.posts import TelegramEntity


def _keyboard(value: InlineKeyboard | None) -> InlineKeyboardMarkup | None:
    if value is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=button.label, callback_data=button.callback_data
                )
                for button in row
            ]
            for row in value.rows
        ]
    )


def _entities(values: tuple[TelegramEntity, ...]) -> list[MessageEntity]:
    """Map entities without changing their canonical UTF-16 coordinates."""
    return [
        MessageEntity(
            type=value.entity_type,
            offset=value.offset_utf16,
            length=value.length_utf16,
            custom_emoji_id=value.custom_emoji_id,
        )
        for value in values
    ]


class AiogramAdminMessagingGateway:
    """Map aiogram messages and safe error categories at the adapter boundary."""

    def __init__(
        self,
        bot: Bot,
        *,
        timeout_seconds: float,
        media_root: Path = Path(),
        upload_timeout_seconds: float | None = None,
    ) -> None:
        """Store one owned Bot and bounded operation timeout."""
        self._bot = bot
        self._timeout = timeout_seconds
        self._upload_timeout = upload_timeout_seconds or timeout_seconds
        self._media_root = media_root.resolve()
        self._closed = False

    @property
    def bot(self) -> Bot:
        """Expose the owned Bot only to the concrete polling composition root."""
        return self._bot

    async def send_header(
        self,
        chat_id: int,
        text: str,
        keyboard: InlineKeyboard | None = None,
        *,
        reply_to_message_id: int | None = None,
    ) -> int:
        """Send a managerial header with an optional inline keyboard."""
        try:
            async with asyncio.timeout(self._timeout):
                try:
                    message = await self._bot.send_message(
                        chat_id,
                        text,
                        reply_markup=_keyboard(keyboard),
                        reply_to_message_id=reply_to_message_id,
                    )
                except TelegramBadRequest:
                    if reply_to_message_id is None:
                        raise
                    message = await self._bot.send_message(
                        chat_id,
                        text,
                        reply_markup=_keyboard(keyboard),
                    )
        except (
            TelegramForbiddenError,
            TelegramRetryAfter,
            TelegramNetworkError,
            TelegramServerError,
            TelegramBadRequest,
        ) as error:
            raise self._delivery_error(error) from None
        return message.message_id

    async def send_content(
        self, chat_id: int, content: ApprovalContent
    ) -> tuple[int, ...]:
        """Send prepared content without adding managerial metadata."""
        try:
            media = self._approval_media(content)
            if media:
                async with asyncio.timeout(self._upload_timeout):
                    preview = self._preview_member(media)
                    path = self._resolve_media(preview.storage_path)
                    caption_entities = _entities(content.caption_entities)
                    kind, extension = self._preview_kind(preview, path)
                    filename = preview.original_filename or (
                        f"approval-{kind}.{extension}"
                    )
                    upload = FSInputFile(path, filename=filename)
                    if kind == "photo":
                        message = await self._bot.send_photo(
                            chat_id,
                            photo=upload,
                            caption=content.caption,
                            caption_entities=caption_entities,
                        )
                    elif kind == "video":
                        message = await self._bot.send_video(
                            chat_id,
                            video=upload,
                            supports_streaming=True,
                            caption=content.caption,
                            caption_entities=caption_entities,
                        )
                    elif kind == "animation":
                        message = await self._bot.send_animation(
                            chat_id,
                            animation=upload,
                            caption=content.caption,
                            caption_entities=caption_entities,
                        )
                    else:
                        message = await self._bot.send_document(
                            chat_id,
                            document=upload,
                            caption=content.caption,
                            caption_entities=caption_entities,
                        )
                    return (message.message_id,)
            async with asyncio.timeout(self._timeout):
                message = await self._bot.send_message(
                    chat_id,
                    content.text or content.caption or "",
                    entities=_entities(content.text_entities),
                )
                return (message.message_id,)
        except TimeoutError as error:
            if content.media or content.media_paths:
                raise ApprovalMediaUploadTimeoutError(
                    "Approval media upload timed out."
                ) from error
            raise
        except OSError:
            if content.media or content.media_paths:
                raise ApprovalMediaPathError(
                    "Approval media path is unavailable."
                ) from None
            raise ApprovalDeliveryTransientError(
                "Approval delivery temporarily failed."
            ) from None
        except (
            TelegramForbiddenError,
            TelegramRetryAfter,
            TelegramNetworkError,
            TelegramServerError,
            TelegramBadRequest,
        ) as error:
            raise self._delivery_error(
                error, media=bool(content.media or content.media_paths)
            ) from None

    @staticmethod
    def _approval_media(content: ApprovalContent) -> tuple[ApprovalMedia, ...]:
        if content.media:
            return content.media
        return tuple(ApprovalMedia("document", path) for path in content.media_paths)

    @staticmethod
    def _preview_member(media: tuple[ApprovalMedia, ...]) -> ApprovalMedia:
        if len(media) == 1:
            return media[0]
        return next(
            (item for item in media if item.media_type.lower() == "photo"), media[0]
        )

    @classmethod
    def _preview_kind(cls, media: ApprovalMedia, path: Path) -> tuple[str, str]:
        """Recover a safe visual preview kind without changing stored metadata."""
        declared = media.media_type.lower()
        detected = cls._file_signature(path)
        mime_type = (media.mime_type or "").partition(";")[0].strip().lower()
        mime_preview = {
            "image/jpeg": ("photo", "jpg"),
            "image/png": ("photo", "png"),
            "image/gif": ("animation", "gif"),
        }.get(mime_type)
        if declared == "document":
            recovered = mime_preview or detected
            if recovered is not None and recovered[0] in {"photo", "animation"}:
                return recovered
        if mime_preview is not None and mime_preview[0] == declared:
            return mime_preview
        if detected is not None and detected[0] == declared:
            return detected
        extension = {
            "photo": "jpg",
            "video": "mp4",
            "animation": "gif",
            "document": "bin",
        }[declared]
        return declared, extension

    @staticmethod
    def _file_signature(path: Path) -> tuple[str, str] | None:
        """Read a bounded header for legacy records that lack media metadata."""
        with path.open("rb") as media_file:
            header = media_file.read(16)
        if header.startswith(b"\xff\xd8\xff"):
            return "photo", "jpg"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "photo", "png"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "animation", "gif"
        if header.startswith(b"%PDF-"):
            return "document", "pdf"
        if len(header) >= 12 and header[4:8] == b"ftyp":
            return "video", "mp4"
        return None

    def _resolve_media(self, storage_path: str) -> Path:
        candidate = Path(storage_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ApprovalMediaPathError("Approval media path is invalid.")
        current = self._media_root
        for part in candidate.parts:
            current /= part
            if current.is_symlink():
                raise ApprovalMediaPathError("Approval media path is invalid.")
        try:
            resolved = (self._media_root / candidate).resolve(strict=True)
            resolved.relative_to(self._media_root)
        except (OSError, ValueError):
            raise ApprovalMediaPathError("Approval media path is invalid.") from None
        if not resolved.is_file() or resolved.is_symlink():
            raise ApprovalMediaPathError("Approval media path is invalid.")
        return resolved

    @staticmethod
    def _delivery_error(
        error: (
            TelegramForbiddenError
            | TelegramRetryAfter
            | TelegramNetworkError
            | TelegramServerError
            | TelegramBadRequest
        ),
        *,
        media: bool = False,
    ) -> (
        ApprovalDeliveryUnavailableError
        | ApprovalDeliveryRateLimitError
        | ApprovalDeliveryTransientError
        | ApprovalDeliveryRejectedError
    ):
        """Convert Bot SDK failures into safe application-owned errors."""
        if isinstance(error, TelegramForbiddenError):
            return ApprovalDeliveryUnavailableError("Approval delivery is unavailable.")
        if isinstance(error, TelegramRetryAfter):
            return ApprovalDeliveryRateLimitError(error.retry_after)
        if isinstance(error, (TelegramNetworkError, TelegramServerError)):
            if media:
                return ApprovalMediaNetworkError(
                    "Approval media upload temporarily failed."
                )
            return ApprovalDeliveryTransientError(
                "Approval delivery temporarily failed."
            )
        if media:
            return ApprovalMediaRejectedError("Approval media was rejected.")
        return ApprovalDeliveryRejectedError("Approval delivery request was rejected.")

    async def edit_header(
        self, chat_id: int, message_id: int, text: str, keyboard: InlineKeyboard
    ) -> BotEditOutcome:
        """Map edit outcomes without leaking aiogram exceptions."""
        try:
            async with asyncio.timeout(self._timeout):
                await self._bot.edit_message_text(
                    text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=_keyboard(keyboard),
                )
        except TelegramBadRequest as error:
            safe = str(error).lower()
            if "message is not modified" in safe:
                return BotEditOutcome.NOT_MODIFIED
            if "message to edit not found" in safe or "message can't be edited" in safe:
                return BotEditOutcome.DELETED
            raise RuntimeError("Bot rejected the approval edit.") from error
        except (
            TelegramNetworkError,
            TelegramRetryAfter,
            TelegramServerError,
        ) as error:
            raise TimeoutError("Temporary Bot operation failure.") from error
        return BotEditOutcome.UPDATED

    async def answer_callback(self, query_id: str, text: str, *, alert: bool) -> None:
        """Answer one callback with bounded transport time."""
        async with asyncio.timeout(self._timeout):
            await self._bot.answer_callback_query(query_id, text=text, show_alert=alert)

    async def close(self) -> None:
        """Close the Bot session exactly once."""
        if self._closed:
            return
        self._closed = True
        await self._bot.session.close()


__all__ = ("AiogramAdminMessagingGateway",)
