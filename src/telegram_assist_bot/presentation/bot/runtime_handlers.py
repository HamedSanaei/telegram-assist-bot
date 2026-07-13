"""SDK-independent operational Bot handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram_assist_bot.application.approvals import (
    UNAUTHORIZED_TEXT,
    AuthorizationStatus,
)
from telegram_assist_bot.domain import AdminPermission

if TYPE_CHECKING:
    from telegram_assist_bot.application.approvals import AuthorizeAdminAction
    from telegram_assist_bot.application.operational_approval import (
        ApprovalCallbackExecutor,
    )
    from telegram_assist_bot.application.ports import AdminMessagingGateway, BotUpdate

AUTHORIZED_START_TEXT = "ربات تأیید فعال است. پست‌های آماده برای بررسی ارسال می‌شوند."


class OperationalBotHandlers:
    """Authorize `/start` and delegate callbacks to application orchestration."""

    def __init__(
        self,
        authorize: AuthorizeAdminAction,
        gateway: AdminMessagingGateway,
        callbacks: ApprovalCallbackExecutor,
    ) -> None:
        """Store authorization, Bot messaging, and callback boundaries."""
        self._authorize = authorize
        self._gateway = gateway
        self._callbacks = callbacks

    async def start(self, update: BotUpdate) -> bool:
        """Return minimal Persian help or a generic denial."""
        decision = self._authorize.execute(update, AdminPermission.VIEW)
        allowed = decision.status is AuthorizationStatus.ALLOWED
        await self._gateway.send_header(
            update.chat_id,
            AUTHORIZED_START_TEXT if allowed else UNAUTHORIZED_TEXT,
        )
        return allowed

    async def callback(self, update: BotUpdate) -> bool:
        """Execute one already-mapped callback update."""
        return await self._callbacks.execute(update)


__all__ = ("AUTHORIZED_START_TEXT", "OperationalBotHandlers")
