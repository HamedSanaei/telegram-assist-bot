"""Authorization-first handlers without persistence or business transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram_assist_bot.application.approvals import (
    UNAUTHORIZED_TEXT,
    AuthorizationStatus,
    AuthorizeAdminAction,
)
from telegram_assist_bot.application.ports import AdminMessagingGateway, BotUpdate

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiogram.types import Update

    from telegram_assist_bot.application.approvals import AuthorizationDecision
    from telegram_assist_bot.domain import AdminPermission


def map_aiogram_update(update: Update) -> BotUpdate | None:
    """Extract actor and private-chat identity from typed aiogram updates."""
    callback = update.callback_query
    if callback is None or callback.message is None:
        return None
    message = callback.message
    return BotUpdate(
        actor_id=callback.from_user.id,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        callback_data=callback.data,
        callback_query_id=callback.id,
    )


class ProtectedCallbackHandler:
    """Reject unsupported or unauthorized updates before protected dispatch."""

    def __init__(
        self, authorize: AuthorizeAdminAction, gateway: AdminMessagingGateway
    ) -> None:
        """Store authorization and response boundaries."""
        self._authorize = authorize
        self._gateway = gateway

    async def handle(
        self,
        update: Update,
        dispatch: Callable[[BotUpdate, AuthorizationDecision], Awaitable[None]],
        *,
        permission: AdminPermission,
        destination_id: int | None = None,
    ) -> bool:
        """Dispatch only after trusted mapping and current authorization."""
        mapped = map_aiogram_update(update)
        if mapped is None:
            return False
        decision = self._authorize.execute(
            mapped, permission, destination_id=destination_id
        )
        if decision.status is not AuthorizationStatus.ALLOWED:
            if mapped.callback_query_id:
                await self._gateway.answer_callback(
                    mapped.callback_query_id, UNAUTHORIZED_TEXT, alert=True
                )
            return False
        await dispatch(mapped, decision)
        return True


__all__ = ("ProtectedCallbackHandler", "map_aiogram_update")
