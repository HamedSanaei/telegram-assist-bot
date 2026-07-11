from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application import ValidateTelegramSession
from telegram_assist_bot.application.ports import (
    TelegramChannelReference,
    TelegramChannelRole,
)
from telegram_assist_bot.infrastructure.telegram.user import TelethonSessionAdapter

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path

pytestmark = pytest.mark.contract


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class RecordedValidationClient:
    entities: dict[str | int, object]
    calls: list[object] = field(default_factory=list)

    async def connect(self) -> None:
        self.calls.append("connect")

    async def disconnect(self) -> None:
        self.calls.append("disconnect")

    async def is_user_authorized(self) -> bool:
        self.calls.append("authorized")
        return True

    async def get_me(self) -> object:
        self.calls.append("get_me")
        return SimpleNamespace(id=42, premium=True)

    async def get_entity(self, entity: str | int) -> object:
        self.calls.append(("resolve", entity))
        return self.entities[entity]

    async def get_permissions(self, entity: object, user: str) -> object:
        self.calls.append(("permissions", user))
        return SimpleNamespace(
            is_creator=False,
            post_messages=getattr(entity, "publish", False),
        )

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


def test_synthetic_sdk_fixtures_map_to_owned_validation_contract(
    tmp_path: Path,
) -> None:
    source_entity = SimpleNamespace(
        canonical_id=-1001,
        username="source_fixture",
        title="منبع آزمایشی 😀",
        publish=False,
    )
    destination_entity = SimpleNamespace(
        canonical_id=-1002,
        username="destination_fixture",
        title="مقصد آزمایشی ✨",
        publish=True,
    )
    client = RecordedValidationClient(
        {
            "source_fixture": source_entity,
            -1002: destination_entity,
        }
    )
    session_path = tmp_path / "source.session"
    session_path.write_text("synthetic-session-v1", encoding="utf-8")
    gateway = TelethonSessionAdapter(
        session_path=session_path,
        runtime_root=tmp_path,
        api_id=123456,
        api_hash="synthetic-api-hash",
        timeout_seconds=1.0,
        client_factory=lambda *_args: client,
        peer_id_resolver=lambda entity: entity.canonical_id,  # type: ignore[attr-defined]
    )
    references = (
        TelegramChannelReference(
            "source",
            -1001,
            "source_fixture",
            TelegramChannelRole.SOURCE,
            "source_channels.0",
        ),
        TelegramChannelReference(
            "destination",
            -1002,
            None,
            TelegramChannelRole.DESTINATION,
            "destination_channels.0",
        ),
    )

    report = run(ValidateTelegramSession(gateway).execute(references))

    assert report.account_id == 42
    assert [item.channel.channel_id for item in report.channels] == [-1001, -1002]
    assert ("resolve", "source_fixture") in client.calls
    assert ("resolve", -1002) in client.calls
    assert all(
        "history" not in str(call) and "send" not in str(call) for call in client.calls
    )
