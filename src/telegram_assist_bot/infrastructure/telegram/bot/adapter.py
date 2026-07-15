"""Bounded aiogram adapter for private administrator messages."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Final

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
    ApprovalMediaRejectionReason,
    ApprovalMediaUploadTimeoutError,
    BotEditOutcome,
    InlineKeyboard,
)

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

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


_MAXIMUM_CAPTION_UTF16_UNITS: Final = 1024
_MAXIMUM_BOT_UPLOAD_BYTES: Final = 50 * 1024 * 1024
_ENTITY_TYPE_MAP: Final = {
    "bold": "bold",
    "italic": "italic",
    "underline": "underline",
    "strike": "strikethrough",
    "strikethrough": "strikethrough",
    "spoiler": "spoiler",
    "code": "code",
    "pre": "pre",
    "blockquote": "blockquote",
    "expandable_blockquote": "expandable_blockquote",
    "mention": "mention",
    "hashtag": "hashtag",
    "cashtag": "cashtag",
    "bot_command": "bot_command",
    "url": "url",
    "email": "email",
    "phone": "phone_number",
    "phone_number": "phone_number",
}


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _entities(
    text: str, values: tuple[TelegramEntity, ...]
) -> list[MessageEntity]:
    """Build safe preview entities while retaining valid UTF-16 coordinates."""
    text_length = _utf16_length(text)
    result: list[MessageEntity] = []
    for value in values:
        if (
            value.offset_utf16 < 0
            or value.length_utf16 <= 0
            or value.offset_utf16 + value.length_utf16 > text_length
        ):
            raise ApprovalMediaRejectedError(
                ApprovalMediaRejectionReason.ENTITY_BOUNDS_INVALID
            )
        mapped_type = _ENTITY_TYPE_MAP.get(value.entity_type)
        if mapped_type is None:
            # The stored contract has no URL/user metadata for TextUrl/TextMention;
            # Custom Emoji is also unavailable to a non-premium Bot preview. Keep
            # the visible text and omit only the unsupported preview formatting.
            continue
        result.append(
            MessageEntity(
                type=mapped_type,
                offset=value.offset_utf16,
                length=value.length_utf16,
            )
        )
    return result


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
                    caption = content.caption or ""
                    if _utf16_length(caption) > _MAXIMUM_CAPTION_UTF16_UNITS:
                        raise ApprovalMediaRejectedError(
                            ApprovalMediaRejectionReason.CAPTION_TOO_LONG
                        )
                    caption_entities = _entities(caption, content.caption_entities)
                    kind, extension = self._preview_kind(preview, path)
                    filename = preview.original_filename or (
                        f"approval-{kind}.{extension}"
                    )
                    try:
                        message = await self._send_preview_media(
                            chat_id,
                            path=path,
                            filename=filename,
                            kind=kind,
                            caption=content.caption,
                            caption_entities=caption_entities,
                        )
                    except TelegramBadRequest as error:
                        reason = self._bad_request_reason(error)
                        if (
                            kind == "document"
                            and caption_entities
                            and reason
                            in {
                                ApprovalMediaRejectionReason.ENTITY_PARSE_FAILED,
                                ApprovalMediaRejectionReason.CUSTOM_EMOJI_REJECTED,
                            }
                        ):
                            try:
                                message = await self._send_preview_media(
                                    chat_id,
                                    path=path,
                                    filename=filename,
                                    kind=kind,
                                    caption=content.caption,
                                    caption_entities=[],
                                )
                            except TelegramBadRequest as fallback_error:
                                raise ApprovalMediaRejectedError(
                                    self._bad_request_reason(fallback_error)
                                ) from None
                        else:
                            raise ApprovalMediaRejectedError(reason) from None
                    return (message.message_id,)
            async with asyncio.timeout(self._timeout):
                message = await self._bot.send_message(
                    chat_id,
                    content.text or content.caption or "",
                    entities=_entities(content.text or "", content.text_entities),
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
                    ApprovalMediaRejectionReason.FILE_UNREADABLE
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

    async def _send_preview_media(
        self,
        chat_id: int,
        *,
        path: Path,
        filename: str,
        kind: str,
        caption: str | None,
        caption_entities: list[MessageEntity],
    ) -> Message:
        """Send one preview with a fresh upload object for each bounded attempt."""
        upload = FSInputFile(path, filename=filename)
        if kind == "photo":
            return await self._bot.send_photo(
                chat_id,
                photo=upload,
                caption=caption,
                caption_entities=caption_entities,
            )
        if kind == "video":
            return await self._bot.send_video(
                chat_id,
                video=upload,
                supports_streaming=True,
                caption=caption,
                caption_entities=caption_entities,
            )
        if kind == "animation":
            return await self._bot.send_animation(
                chat_id,
                animation=upload,
                caption=caption,
                caption_entities=caption_entities,
            )
        return await self._bot.send_document(
            chat_id,
            document=upload,
            caption=caption,
            caption_entities=caption_entities,
        )

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
            raise ApprovalMediaPathError(
                ApprovalMediaRejectionReason.FILE_UNREADABLE
            )
        current = self._media_root
        for part in candidate.parts:
            current /= part
            if current.is_symlink():
                raise ApprovalMediaPathError(
                    ApprovalMediaRejectionReason.FILE_UNREADABLE
                )
        try:
            resolved = (self._media_root / candidate).resolve(strict=True)
            resolved.relative_to(self._media_root)
        except FileNotFoundError:
            raise ApprovalMediaPathError(
                ApprovalMediaRejectionReason.FILE_MISSING
            ) from None
        except (OSError, ValueError):
            raise ApprovalMediaPathError(
                ApprovalMediaRejectionReason.FILE_UNREADABLE
            ) from None
        if not resolved.is_file() or resolved.is_symlink():
            raise ApprovalMediaPathError(
                ApprovalMediaRejectionReason.FILE_UNREADABLE
            )
        try:
            size = resolved.stat().st_size
        except OSError:
            raise ApprovalMediaPathError(
                ApprovalMediaRejectionReason.FILE_UNREADABLE
            ) from None
        if size == 0:
            raise ApprovalMediaPathError(
                ApprovalMediaRejectionReason.FILE_EMPTY
            )
        if size > _MAXIMUM_BOT_UPLOAD_BYTES:
            raise ApprovalMediaRejectedError(
                ApprovalMediaRejectionReason.FILE_TOO_LARGE
            )
        return resolved

    @staticmethod
    def _bad_request_reason(
        error: TelegramBadRequest,
    ) -> ApprovalMediaRejectionReason:
        """Classify an allowlisted provider reason without retaining raw details."""
        detail = str(error).casefold()
        if "custom emoji" in detail or "custom_emoji" in detail:
            return ApprovalMediaRejectionReason.CUSTOM_EMOJI_REJECTED
        if "caption is too long" in detail or "caption too long" in detail:
            return ApprovalMediaRejectionReason.CAPTION_TOO_LONG
        if "file is too big" in detail or "request entity too large" in detail:
            return ApprovalMediaRejectionReason.FILE_TOO_LARGE
        if any(
            marker in detail
            for marker in (
                "can't parse entities",
                "cannot parse entities",
                "entity offset",
                "entity length",
                "text_url",
                "text url",
                "url is invalid",
            )
        ):
            return ApprovalMediaRejectionReason.ENTITY_PARSE_FAILED
        return ApprovalMediaRejectionReason.GENERIC_BAD_REQUEST

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
            reason = ApprovalMediaRejectionReason.GENERIC_BAD_REQUEST
            if isinstance(error, TelegramBadRequest):
                reason = AiogramAdminMessagingGateway._bad_request_reason(error)
            return ApprovalMediaRejectedError(reason)
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
