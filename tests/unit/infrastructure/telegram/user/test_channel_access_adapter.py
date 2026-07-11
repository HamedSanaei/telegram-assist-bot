from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.ports import (
    TelegramChannelReference,
    TelegramChannelRole,
    TelegramSessionInvalidError,
    TelegramTransientError,
)
from telegram_assist_bot.infrastructure.telegram.user import TelethonSessionAdapter

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class ValidationClient:
    account: object | None
    entity: object
    permissions: object
    authorized: bool = True
    connect_error: Exception | None = None
    calls: list[object] = field(default_factory=list)

    async def connect(self) -> None:
        self.calls.append("connect")
        if self.connect_error is not None:
            raise self.connect_error

    async def disconnect(self) -> None:
        self.calls.append("disconnect")

    async def is_user_authorized(self) -> bool:
        self.calls.append("authorized")
        return self.authorized

    async def send_code_request(self, phone: str) -> object:
        raise AssertionError(phone)

    async def sign_in(
        self,
        *,
        phone: str | None = None,
        code: str | None = None,
        password: str | None = None,
    ) -> object:
        raise AssertionError((phone, code, password))

    async def get_me(self) -> object | None:
        self.calls.append("get_me")
        return self.account

    async def get_entity(self, entity: str | int) -> object:
        self.calls.append(("get_entity", entity))
        return self.entity

    async def get_permissions(self, entity: object, user: str) -> object:
        del entity
        self.calls.append(("get_permissions", user))
        return self.permissions


def create_adapter(tmp_path: Path, client: ValidationClient) -> TelethonSessionAdapter:
    session_path = tmp_path / "source.session"
    session_path.write_text("synthetic-session", encoding="utf-8")
    return TelethonSessionAdapter(
        session_path=session_path,
        runtime_root=tmp_path,
        api_id=123456,
        api_hash="synthetic-api-hash",
        timeout_seconds=1.0,
        client_factory=lambda *_args: client,
        peer_id_resolver=lambda entity: entity.canonical_id,  # type: ignore[attr-defined]
    )


def test_maps_authorized_premium_account_without_private_details(
    tmp_path: Path,
) -> None:
    client = ValidationClient(
        account=SimpleNamespace(id=42, premium=True, phone="not-exposed"),
        entity=object(),
        permissions=object(),
    )

    account = run(create_adapter(tmp_path, client).validate_account())

    assert account.account_id == 42
    assert account.is_premium is True
    assert not hasattr(account, "phone")


def test_resolves_username_to_canonical_id_and_publication_permission(
    tmp_path: Path,
) -> None:
    entity = SimpleNamespace(
        canonical_id=-100200,
        username="synthetic_channel",
        title="کانال آزمایشی 📣",
    )
    client = ValidationClient(
        account=SimpleNamespace(id=42, premium=True),
        entity=entity,
        permissions=SimpleNamespace(is_creator=False, post_messages=True),
    )
    reference = TelegramChannelReference(
        config_name="destination",
        configured_channel_id=-100200,
        configured_username="@synthetic_channel",
        role=TelegramChannelRole.DESTINATION,
        configuration_path="destination_channels.0",
    )

    resolved = run(create_adapter(tmp_path, client).resolve_channel(reference))

    assert resolved.channel_id == -100200
    assert resolved.display_name == "کانال آزمایشی 📣"
    assert resolved.can_read is True
    assert resolved.can_publish is True
    assert ("get_entity", "synthetic_channel") in client.calls
    assert all(
        "history" not in str(call) and "send" not in str(call) for call in client.calls
    )


def test_unauthorized_session_fails_before_resolve(tmp_path: Path) -> None:
    client = ValidationClient(
        account=None,
        entity=object(),
        permissions=object(),
        authorized=False,
    )
    reference = TelegramChannelReference(
        config_name="source",
        configured_channel_id=-1001,
        configured_username=None,
        role=TelegramChannelRole.SOURCE,
        configuration_path="source_channels.0",
    )

    with pytest.raises(TelegramSessionInvalidError):
        run(create_adapter(tmp_path, client).resolve_channel(reference))

    assert not any(isinstance(call, tuple) for call in client.calls)


def test_network_failure_stays_transient_and_preserves_session(tmp_path: Path) -> None:
    client = ValidationClient(
        account=None,
        entity=object(),
        permissions=object(),
        connect_error=ConnectionError("synthetic offline"),
    )
    gateway = create_adapter(tmp_path, client)

    with pytest.raises(TelegramTransientError):
        run(gateway.validate_account())

    assert gateway.session_path.read_text(encoding="utf-8") == "synthetic-session"
