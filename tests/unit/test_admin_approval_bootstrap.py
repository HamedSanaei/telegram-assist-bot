from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from pydantic import SecretStr

import telegram_assist_bot.bootstrap.admin_approval as module
from telegram_assist_bot.application.ports import BotUpdate
from telegram_assist_bot.domain import AdminPermission

if TYPE_CHECKING:
    import pytest


class Secrets:
    def get(self, _reference: object) -> SecretStr:
        return SecretStr("123456:synthetic_token_value_for_unit_test")


class FakeSession:
    async def close(self) -> None:
        return None


class FakeBot:
    def __init__(self, *, token: str) -> None:
        assert "synthetic" in token
        self.session = FakeSession()


class FakeDatabase(dict[str, object]):
    def __missing__(self, key: str) -> object:
        value = object()
        self[key] = value
        return value


def test_composition_factory_is_explicit_and_wires_owned_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        calls = 0

        async def initialize(*_collections: object) -> None:
            nonlocal calls
            calls += 1

        monkeypatch.setattr(module, "Bot", FakeBot)
        monkeypatch.setattr(module, "initialize_approval_indexes", initialize)
        bot = SimpleNamespace(token=object(), operation_timeout_seconds=1)
        telegram = SimpleNamespace(bot=bot)
        destination = SimpleNamespace(name="dest", telegram_channel_id=-2001)
        administrator = SimpleNamespace(
            telegram_user_id=1001,
            active=True,
            role="admin",
            permissions=("approval.view", "approval.toggle"),
            allowed_destination_ids=(),
            allowed_destination_names=("dest",),
        )
        settings = SimpleNamespace(
            telegram=telegram,
            destination_channels=(destination,),
            admins=(administrator,),
        )
        configuration = SimpleNamespace(settings=settings, secrets=Secrets())
        database = FakeDatabase()
        components = await module.create_admin_approval_components(
            cast("Any", configuration), cast("Any", database)
        )
        assert calls == 1
        decision = components.authorize.execute(
            BotUpdate(1001, 1001, "private"), AdminPermission.VIEW
        )
        assert decision.administrator is not None
        await components.gateway.close()

    asyncio.run(scenario())
