"""Authorization-first tests for the thin aiogram callback boundary."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from telegram_assist_bot.application.approvals import (
    AuthorizationDecision,
    AuthorizationStatus,
)
from telegram_assist_bot.domain import AdminPermission
from telegram_assist_bot.presentation.bot.handlers import (
    ProtectedCallbackHandler,
    map_aiogram_update,
)


class Authorize:
    def __init__(self, decision: AuthorizationDecision) -> None:
        self.decision = decision

    def execute(self, *_: object, **__: object) -> AuthorizationDecision:
        return self.decision


class Gateway:
    def __init__(self) -> None:
        self.answers: list[tuple[str, str, bool]] = []

    async def answer_callback(self, query_id: str, text: str, *, alert: bool) -> None:
        self.answers.append((query_id, text, alert))


def _update(*, message: bool = True, callback_id: str = "callback") -> object:
    callback = SimpleNamespace(
        message=(
            SimpleNamespace(chat=SimpleNamespace(id=7, type="private"))
            if message
            else None
        ),
        from_user=SimpleNamespace(id=7),
        data="action",
        id=callback_id,
    )
    return SimpleNamespace(callback_query=callback)


def test_update_mapper_rejects_unsupported_shapes_and_maps_callback() -> None:
    assert map_aiogram_update(cast("Any", SimpleNamespace(callback_query=None))) is None
    assert map_aiogram_update(cast("Any", _update(message=False))) is None
    mapped = map_aiogram_update(cast("Any", _update()))
    assert mapped is not None
    assert (mapped.actor_id, mapped.chat_id, mapped.callback_data) == (7, 7, "action")


def test_protected_handler_denies_or_dispatches_after_authorization() -> None:
    async def scenario() -> None:
        dispatches: list[object] = []

        async def dispatch(update: object, decision: object) -> None:
            dispatches.append((update, decision))

        gateway = Gateway()
        denied = ProtectedCallbackHandler(
            cast("Any", Authorize(AuthorizationDecision(AuthorizationStatus.DENIED))),
            cast("Any", gateway),
        )
        assert not await denied.handle(
            cast("Any", _update()),
            cast("Any", dispatch),
            permission=AdminPermission.VIEW,
        )
        assert len(gateway.answers) == 1
        assert not await denied.handle(
            cast("Any", _update(callback_id="")),
            cast("Any", dispatch),
            permission=AdminPermission.VIEW,
        )
        assert len(gateway.answers) == 1
        assert not await denied.handle(
            cast("Any", SimpleNamespace(callback_query=None)),
            cast("Any", dispatch),
            permission=AdminPermission.VIEW,
        )

        allowed = ProtectedCallbackHandler(
            cast("Any", Authorize(AuthorizationDecision(AuthorizationStatus.ALLOWED))),
            cast("Any", gateway),
        )
        assert await allowed.handle(
            cast("Any", _update()),
            cast("Any", dispatch),
            permission=AdminPermission.VIEW,
        )
        assert len(dispatches) == 1

    asyncio.run(scenario())
