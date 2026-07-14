from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.application.ports import (
    ResolvedTelegramChannel,
    TelegramAccount,
    TelegramChannelNotFoundError,
    TelegramChannelReference,
    TelegramChannelRole,
    TelegramHistoryQuery,
    TelegramLoginStep,
    TelegramSessionInvalidError,
    TelegramSessionStatus,
)
from telegram_assist_bot.infrastructure.telegram.user import (
    TelethonSessionAdapter,
    TelethonTextIngestionGateway,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine


class MessageMediaPhoto: ...


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class Client:
    messages: list[object]
    callback: object | None = None
    builder: object | None = None

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def is_user_authorized(self) -> bool:
        return True

    async def send_code_request(self, phone: str) -> object:
        del phone
        return object()

    async def sign_in(
        self,
        *,
        phone: str | None = None,
        code: str | None = None,
        password: str | None = None,
    ) -> object:
        del phone, code, password
        return object()

    async def _history(self) -> AsyncIterator[object]:
        for message in self.messages:
            yield message

    def iter_messages(
        self,
        entity: int,
        *,
        limit: int,
        offset_date: datetime,
    ) -> AsyncIterator[object]:
        del entity, limit, offset_date
        return self._history()

    def add_event_handler(self, callback: object, event: object) -> None:
        self.callback = callback
        self.builder = event

    def remove_event_handler(self, callback: object, event: object) -> int:
        assert callback is self.callback
        assert event is self.builder
        return 1

    async def get_messages(self, entity: int, *, ids: int) -> object:
        assert (entity, ids) == (-1001, 7)
        return SimpleNamespace(
            media=MessageMediaPhoto(),
            photo=object(),
            document=None,
        )

    def iter_download(self, file: object, *, chunk_size: int) -> AsyncIterator[bytes]:
        assert file is not None
        assert chunk_size == 64 * 1024

        async def stream() -> AsyncIterator[bytes]:
            yield b"shared-client-media"

        return stream()


@dataclass
class Session:
    client: Client
    timeout_seconds: float = 1
    calls: list[str] = field(default_factory=list)

    async def inspect_session(self) -> TelegramSessionStatus:
        self.calls.append("inspect")
        return TelegramSessionStatus.AUTHORIZED

    async def begin_login(self, phone_number: str) -> None:
        del phone_number
        self.calls.append("begin")

    async def submit_login_code(self, code: str) -> TelegramLoginStep:
        del code
        self.calls.append("code")
        return TelegramLoginStep.AUTHORIZED

    async def submit_two_factor_password(self, password: str) -> None:
        del password
        self.calls.append("password")

    async def abort_login(self) -> None:
        self.calls.append("abort")

    async def validate_account(self) -> TelegramAccount:
        self.calls.append("account")
        return TelegramAccount(42, True)

    async def resolve_channel(
        self,
        reference: TelegramChannelReference,
    ) -> ResolvedTelegramChannel:
        self.calls.append("resolve")
        return ResolvedTelegramChannel(
            reference.configured_channel_id or -1000000000101,
            reference.configured_username,
            reference.config_name,
            True,
            True,
        )

    async def open_authorized_client(self) -> Client:
        self.calls.append("open")
        return self.client

    async def close(self) -> None:
        self.calls.append("close")


def raw(message_id: int) -> object:
    return SimpleNamespace(
        id=message_id,
        date=datetime(2099, 3, 20, 7, 59, tzinfo=UTC),
        message="متن Gateway",
        entities=[],
        media=None,
        action=None,
    )


def test_combined_gateway_delegates_validation_history_live_and_close() -> None:
    async def scenario() -> None:
        client = Client([raw(1)])
        session = Session(client)
        gateway = TelethonTextIngestionGateway(cast("TelethonSessionAdapter", session))
        reference = TelegramChannelReference(
            "source",
            -1001,
            "source_fixture",
            TelegramChannelRole.SOURCE,
            "source_channels.0",
        )

        assert await gateway.inspect_session() is TelegramSessionStatus.AUTHORIZED
        await gateway.begin_login("synthetic-phone")
        assert (
            await gateway.submit_login_code("synthetic-code")
            is TelegramLoginStep.AUTHORIZED
        )
        await gateway.submit_two_factor_password("synthetic-two-factor")
        await gateway.abort_login()
        assert (await gateway.validate_account()).is_premium is True
        channel = await gateway.resolve_channel(reference)
        gateway.register_channel(channel)
        await gateway.open()
        query = TelegramHistoryQuery(
            -1001,
            datetime(2099, 3, 19, 20, 30, tzinfo=UTC),
            datetime(2099, 3, 20, 8, 0, tzinfo=UTC),
            10,
            2,
        )
        pages = [page async for page in gateway.iter_history_pages(query)]
        subscription = await gateway.subscribe(-1001, buffer_size=1)
        media = gateway.media_source()
        assert [chunk async for chunk in await media.open("-1001:7:0")] == [
            b"shared-client-media"
        ]
        callback = client.callback
        assert callable(callback)
        await callback(SimpleNamespace(message=raw(2)))
        live = await anext(subscription)
        await subscription.close()
        await gateway.close()

        assert pages[0].messages[0].source_message_id == 1
        assert live.source_message_id == 2
        assert session.calls == [
            "inspect",
            "begin",
            "code",
            "password",
            "abort",
            "account",
            "resolve",
            "open",
            "close",
        ]

    run(scenario())


def test_gateway_rejects_unregistered_channel_and_unopened_client() -> None:
    gateway = TelethonTextIngestionGateway(
        cast("TelethonSessionAdapter", Session(Client([])))
    )
    query = TelegramHistoryQuery(
        -1001,
        datetime(2099, 3, 19, 20, 30, tzinfo=UTC),
        datetime(2099, 3, 20, 8, 0, tzinfo=UTC),
        10,
        2,
    )

    with pytest.raises(TelegramChannelNotFoundError):
        gateway.iter_history_pages(query)

    gateway.register_channel(
        ResolvedTelegramChannel(-1001, None, "Source", True, False)
    )
    with pytest.raises(TelegramSessionInvalidError):
        gateway.iter_history_pages(query)
