"""Bounded aiogram adapter for private administrator messages."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    MessageEntity,
)

from telegram_assist_bot.application.ports import (
    ApprovalContent,
    ApprovalDeliveryRateLimitError,
    ApprovalDeliveryRejectedError,
    ApprovalDeliveryTransientError,
    ApprovalDeliveryUnavailableError,
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

    def __init__(self, bot: Bot, *, timeout_seconds: float) -> None:
        """Store one owned Bot and bounded operation timeout."""
        self._bot = bot
        self._timeout = timeout_seconds
        self._closed = False

    @property
    def bot(self) -> Bot:
        """Expose the owned Bot only to the concrete polling composition root."""
        return self._bot

    async def send_header(
        self, chat_id: int, text: str, keyboard: InlineKeyboard | None = None
    ) -> int:
        """Send a managerial header with an optional inline keyboard."""
        try:
            async with asyncio.timeout(self._timeout):
                message = await self._bot.send_message(
                    chat_id, text, reply_markup=_keyboard(keyboard)
                )
        except (
            TelegramForbiddenError,
            TelegramRetryAfter,
            TelegramNetworkError,
            TelegramBadRequest,
        ) as error:
            raise self._delivery_error(error) from None
        return message.message_id

    async def send_content(
        self, chat_id: int, content: ApprovalContent
    ) -> tuple[int, ...]:
        """Send prepared content without adding managerial metadata."""
        try:
            async with asyncio.timeout(self._timeout):
                if content.media_paths:
                    # Serialization stays adapter-owned; publication remains absent.
                    caption_entities = _entities(content.caption_entities)
                    if len(content.media_paths) == 1:
                        message = await self._bot.send_document(
                            chat_id,
                            document=FSInputFile(content.media_paths[0]),
                            caption=content.caption,
                            caption_entities=caption_entities,
                        )
                        return (message.message_id,)
                    media = [
                        InputMediaDocument(
                            media=FSInputFile(path),
                            caption=content.caption if index == 0 else None,
                            caption_entities=caption_entities if index == 0 else None,
                        )
                        for index, path in enumerate(content.media_paths)
                    ]
                    messages = await self._bot.send_media_group(
                        chat_id, media=cast("Any", media)
                    )
                    return tuple(message.message_id for message in messages)
                message = await self._bot.send_message(
                    chat_id,
                    content.text or content.caption or "",
                    entities=_entities(content.text_entities),
                )
                return (message.message_id,)
        except (
            TelegramForbiddenError,
            TelegramRetryAfter,
            TelegramNetworkError,
            TelegramBadRequest,
        ) as error:
            raise self._delivery_error(error) from None

    @staticmethod
    def _delivery_error(
        error: (
            TelegramForbiddenError
            | TelegramRetryAfter
            | TelegramNetworkError
            | TelegramBadRequest
        ),
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
        if isinstance(error, TelegramNetworkError):
            return ApprovalDeliveryTransientError(
                "Approval delivery temporarily failed."
            )
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
        except (TelegramNetworkError, TelegramRetryAfter) as error:
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
