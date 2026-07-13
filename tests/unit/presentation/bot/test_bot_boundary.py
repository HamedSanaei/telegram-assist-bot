from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from telegram_assist_bot.application.approvals import AuthorizeAdminAction
from telegram_assist_bot.application.ports import (
    ApprovalContent,
    ApprovalDeliveryRateLimitError,
    ApprovalDeliveryRejectedError,
    ApprovalDeliveryTransientError,
    ApprovalDeliveryUnavailableError,
    BotEditOutcome,
    InlineKeyboard,
)
from telegram_assist_bot.domain import Administrator, AdminPermission
from telegram_assist_bot.domain.posts import TelegramEntity
from telegram_assist_bot.infrastructure.telegram.bot import AiogramAdminMessagingGateway
from telegram_assist_bot.presentation.bot import (
    ProtectedCallbackHandler,
    map_aiogram_update,
)


class FakeSession:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class Result:
    message_id = 7


class FakeBot:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.answers: list[str] = []
        self.media_count = 0

    async def send_message(self, *_args: object, **_kwargs: object) -> Result:
        return Result()

    async def send_document(self, *_args: object, **_kwargs: object) -> Result:
        return Result()

    async def send_media_group(
        self, *_args: object, **_kwargs: object
    ) -> tuple[Result, Result]:
        self.media_count += 1
        return (Result(), Result())

    async def edit_message_text(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def answer_callback_query(self, query_id: str, **_kwargs: object) -> None:
        self.answers.append(query_id)


def update(actor: int = 1001, *, chat_type: str = "private") -> Update:
    chat_id = actor if chat_type == "private" else -100
    return Update(
        update_id=1,
        callback_query=CallbackQuery(
            id="query",
            from_user=User(id=actor, is_bot=False, first_name="مدیر"),
            chat_instance="synthetic",
            data="c1_synthetic",
            message=Message(
                message_id=3,
                date=datetime(2026, 7, 12, tzinfo=UTC),
                chat=Chat(id=chat_id, type=chat_type),
            ),
        ),
    )


def test_typed_update_mapping_uses_authenticated_actor() -> None:
    mapped = map_aiogram_update(update())
    assert mapped is not None
    assert mapped.actor_id == 1001
    assert mapped.chat_id == 1001
    assert mapped.callback_data == "c1_synthetic"


def test_handler_rejects_before_dispatch_and_adapter_closes_once() -> None:
    async def scenario() -> None:
        fake = FakeBot()
        gateway = AiogramAdminMessagingGateway(cast("Any", fake), timeout_seconds=1)
        administrator = Administrator(
            1001, True, "admin", frozenset({AdminPermission.TOGGLE}), frozenset({-2001})
        )
        handler = ProtectedCallbackHandler(
            AuthorizeAdminAction((administrator,)), gateway
        )
        calls = 0

        async def dispatch(*_args: object) -> None:
            nonlocal calls
            calls += 1

        assert not await handler.handle(
            update(999), dispatch, permission=AdminPermission.TOGGLE
        )
        assert calls == 0
        assert fake.answers == ["query"]
        assert await gateway.send_header(1001, "هدر", InlineKeyboard(())) == 7
        assert await gateway.send_content(1001, ApprovalContent("متن", None)) == (7,)
        media = ApprovalContent(
            None,
            "کپشن‌فارسی 😀",
            caption_entities=(TelegramEntity(0, 6, "bold"),),
            media_paths=("first.bin", "second.bin"),
        )
        assert await gateway.send_content(1001, media) == (7, 7)
        assert fake.media_count == 1
        assert (
            await gateway.edit_header(1001, 7, "هدر", InlineKeyboard(()))
            is BotEditOutcome.UPDATED
        )
        await gateway.close()
        await gateway.close()
        assert fake.session.closed == 1

    asyncio.run(scenario())


def test_aiogram_gateway_maps_delivery_media_and_edit_boundaries() -> None:
    class FailingBot(FakeBot):
        def __init__(self) -> None:
            super().__init__()
            self.send_error: Exception | None = None
            self.edit_error: Exception | None = None

        async def send_message(self, *_args: object, **_kwargs: object) -> Result:
            if self.send_error is not None:
                raise self.send_error
            return Result()

        async def edit_message_text(self, *_args: object, **_kwargs: object) -> None:
            if self.edit_error is not None:
                raise self.edit_error

    async def scenario() -> None:
        fake = FailingBot()
        gateway = AiogramAdminMessagingGateway(cast("Any", fake), timeout_seconds=1)
        method = cast("Any", object())

        assert gateway.bot is cast("Any", fake)
        assert isinstance(
            gateway._delivery_error(TelegramForbiddenError(method, "forbidden")),
            ApprovalDeliveryUnavailableError,
        )
        rate_limit = gateway._delivery_error(
            TelegramRetryAfter(method, "retry", retry_after=12)
        )
        assert isinstance(rate_limit, ApprovalDeliveryRateLimitError)
        assert rate_limit.retry_after_seconds == 12
        assert isinstance(
            gateway._delivery_error(TelegramNetworkError(method, "network")),
            ApprovalDeliveryTransientError,
        )
        assert isinstance(
            gateway._delivery_error(TelegramBadRequest(method, "bad")),
            ApprovalDeliveryRejectedError,
        )

        single_media = ApprovalContent(
            None,
            "کپشن",
            caption_entities=(TelegramEntity(0, 5, "bold"),),
            media_paths=("single.bin",),
        )
        assert await gateway.send_content(1, single_media) == (7,)
        text = ApprovalContent(
            "متن",
            None,
            text_entities=(TelegramEntity(0, 3, "italic"),),
        )
        assert await gateway.send_content(1, text) == (7,)

        fake.send_error = TelegramForbiddenError(method, "forbidden")
        with pytest.raises(ApprovalDeliveryUnavailableError):
            await gateway.send_header(1, "header")
        fake.send_error = TelegramBadRequest(method, "bad")
        with pytest.raises(ApprovalDeliveryRejectedError):
            await gateway.send_content(1, text)
        fake.send_error = None

        fake.edit_error = TelegramBadRequest(method, "message is not modified")
        assert (
            await gateway.edit_header(1, 7, "header", InlineKeyboard(()))
            is BotEditOutcome.NOT_MODIFIED
        )
        fake.edit_error = TelegramBadRequest(method, "message to edit not found")
        assert (
            await gateway.edit_header(1, 7, "header", InlineKeyboard(()))
            is BotEditOutcome.DELETED
        )
        fake.edit_error = TelegramBadRequest(method, "other")
        with pytest.raises(RuntimeError, match="rejected"):
            await gateway.edit_header(1, 7, "header", InlineKeyboard(()))
        fake.edit_error = TelegramNetworkError(method, "network")
        with pytest.raises(TimeoutError, match="Temporary"):
            await gateway.edit_header(1, 7, "header", InlineKeyboard(()))

    asyncio.run(scenario())
