"""Thin authorization-first Bot handlers for read-only advertisement reports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram_assist_bot.application.approvals import AuthorizationStatus
from telegram_assist_bot.domain import AdminPermission

if TYPE_CHECKING:
    from telegram_assist_bot.application.advertisements.report_advertisement_runs import (  # noqa: E501
        RenderAdvertisementReport,
        ReportAdvertisementRuns,
    )
    from telegram_assist_bot.application.approvals import AuthorizeAdminAction
    from telegram_assist_bot.application.ports import AdminMessagingGateway, BotUpdate
    from telegram_assist_bot.application.ports.advertisement_repository import (
        AdvertisementReportKind,
    )


class AdvertisementReportHandlers:
    """Authorize private report commands before loading any business data."""

    def __init__(
        self,
        authorize: AuthorizeAdminAction,
        reports: ReportAdvertisementRuns,
        renderer: RenderAdvertisementReport,
        gateway: AdminMessagingGateway,
    ) -> None:
        """Store application and Bot boundaries."""
        self._authorize = authorize
        self._reports = reports
        self._renderer = renderer
        self._gateway = gateway

    async def handle(self, update: BotUpdate, kind: AdvertisementReportKind) -> bool:
        """Send data only to an active private administrator with view access."""
        decision = self._authorize.execute(update, AdminPermission.VIEW)
        if (
            decision.status is not AuthorizationStatus.ALLOWED
            or decision.administrator is None
        ):
            return False
        report = await self._reports.execute(
            kind,
            allowed_destination_ids=decision.administrator.allowed_destination_ids,
        )
        await self._gateway.send_header(update.chat_id, self._renderer.render(report))
        return True


__all__ = ("AdvertisementReportHandlers",)
