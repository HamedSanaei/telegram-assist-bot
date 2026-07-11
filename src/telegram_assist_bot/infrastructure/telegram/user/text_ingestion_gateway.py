"""Concrete Telethon gateway for the controlled text-ingestion lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast

from telegram_assist_bot.application.ports import (
    ResolvedTelegramChannel,
    TelegramAccount,
    TelegramChannelNotFoundError,
    TelegramChannelReference,
    TelegramHistoryPage,
    TelegramHistoryQuery,
    TelegramLiveSubscription,
    TelegramLoginStep,
    TelegramSessionInvalidError,
    TelegramSessionStatus,
)
from telegram_assist_bot.infrastructure.telegram.user.history_adapter import (
    TelethonHistoryAdapter,
    TelethonHistoryClient,
)
from telegram_assist_bot.infrastructure.telegram.user.live_adapter import (
    TelethonEventClient,
    TelethonLiveAdapter,
)
from telegram_assist_bot.infrastructure.telegram.user.session_adapter import (
    TelethonClientProtocol,
    TelethonSessionAdapter,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class TelethonTextClient(
    TelethonClientProtocol,
    TelethonHistoryClient,
    TelethonEventClient,
    Protocol,
):
    """Combine only SDK surfaces required after validation."""


@dataclass(slots=True)
class TelethonTextIngestionGateway:
    """Share one locked authorized client across history and live adapters."""

    session: TelethonSessionAdapter = field(repr=False)
    _client: TelethonTextClient | None = field(default=None, init=False, repr=False)
    _channels: dict[int, ResolvedTelegramChannel] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    async def inspect_session(self) -> TelegramSessionStatus:
        """Delegate non-interactive session inspection."""
        return await self.session.inspect_session()

    async def begin_login(self, phone_number: str) -> None:
        """Delegate explicit login mutation ownership."""
        await self.session.begin_login(phone_number)

    async def submit_login_code(self, code: str) -> TelegramLoginStep:
        """Delegate one explicit verification-code step."""
        return await self.session.submit_login_code(code)

    async def submit_two_factor_password(self, password: str) -> None:
        """Delegate one explicit two-factor step."""
        await self.session.submit_two_factor_password(password)

    async def abort_login(self) -> None:
        """Delegate cleanup of an incomplete explicit login."""
        await self.session.abort_login()

    async def validate_account(self) -> TelegramAccount:
        """Delegate account validation before opening the shared client."""
        return await self.session.validate_account()

    async def resolve_channel(
        self,
        reference: TelegramChannelReference,
    ) -> ResolvedTelegramChannel:
        """Delegate channel validation before opening the shared client."""
        return await self.session.resolve_channel(reference)

    def register_channel(self, channel: ResolvedTelegramChannel) -> None:
        """Cache one startup-validated channel only for this lifecycle."""
        self._channels[channel.channel_id] = channel

    async def open(self) -> None:
        """Open and retain one authorized client under the session lock."""
        client = await self.session.open_authorized_client()
        self._client = cast("TelethonTextClient", client)

    def iter_history_pages(
        self,
        query: TelegramHistoryQuery,
    ) -> AsyncIterator[TelegramHistoryPage]:
        """Dispatch history through exact metadata from startup validation."""
        channel = self._require_channel(query.source_channel_id)
        client = self._require_client()
        return TelethonHistoryAdapter(
            client,
            channel.username,
            channel.display_name,
            self.session.timeout_seconds,
        ).iter_history_pages(query)

    async def subscribe(
        self,
        source_channel_id: int,
        *,
        buffer_size: int,
    ) -> TelegramLiveSubscription:
        """Create one bounded live subscription on the shared client."""
        channel = self._require_channel(source_channel_id)
        return await TelethonLiveAdapter(
            self._require_client(),
            channel.username,
            channel.display_name,
        ).subscribe(source_channel_id, buffer_size=buffer_size)

    async def close(self) -> None:
        """Close the shared client and release its session lock exactly once."""
        self._client = None
        await self.session.close()

    def _require_client(self) -> TelethonTextClient:
        if self._client is None:
            raise TelegramSessionInvalidError
        return self._client

    def _require_channel(self, channel_id: int) -> ResolvedTelegramChannel:
        try:
            return self._channels[channel_id]
        except KeyError:
            raise TelegramChannelNotFoundError from None


__all__ = ("TelethonTextClient", "TelethonTextIngestionGateway")
