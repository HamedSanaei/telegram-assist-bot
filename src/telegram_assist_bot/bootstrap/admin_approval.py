"""Composition Root factory for Milestone 3 administrator approval resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiogram import Bot

from telegram_assist_bot.application.approvals import AuthorizeAdminAction
from telegram_assist_bot.domain import Administrator, AdminPermission
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoApprovalRepository,
    initialize_approval_indexes,
)
from telegram_assist_bot.infrastructure.telegram.bot import AiogramAdminMessagingGateway

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

    from telegram_assist_bot.shared.config import LoadedConfiguration


@dataclass(frozen=True, slots=True)
class AdminApprovalComponents:
    """Hold explicitly constructed Milestone 3 resources and services."""

    gateway: AiogramAdminMessagingGateway
    repository: MongoApprovalRepository
    authorize: AuthorizeAdminAction


async def create_admin_approval_components(
    configuration: LoadedConfiguration,
    database: AsyncDatabase[dict[str, object]],
) -> AdminApprovalComponents:
    """Create Bot and MongoDB adapters only through an explicit async factory."""
    settings = configuration.settings
    token = configuration.secrets.get(settings.telegram.bot.token).get_secret_value()
    bot = Bot(token=token)
    gateway = AiogramAdminMessagingGateway(
        bot, timeout_seconds=settings.telegram.bot.operation_timeout_seconds
    )
    callbacks = database["approval_callbacks"]
    references = database["approval_references"]
    selections = database["destination_selections"]
    try:
        await initialize_approval_indexes(callbacks, references, selections)
    except BaseException:
        await gateway.close()
        raise
    repository = MongoApprovalRepository(callbacks, references, selections)
    by_name = {
        item.name: item.telegram_channel_id for item in settings.destination_channels
    }
    administrators = tuple(
        Administrator(
            item.telegram_user_id,
            item.active,
            item.role,
            frozenset(AdminPermission(value) for value in item.permissions),
            frozenset(item.allowed_destination_ids)
            or frozenset(by_name[name] for name in item.allowed_destination_names),
        )
        for item in settings.admins
    )
    return AdminApprovalComponents(
        gateway, repository, AuthorizeAdminAction(administrators)
    )


__all__ = ("AdminApprovalComponents", "create_admin_approval_components")
