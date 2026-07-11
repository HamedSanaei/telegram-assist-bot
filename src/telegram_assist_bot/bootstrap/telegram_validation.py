"""Composition helpers for fail-fast Telegram startup validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram_assist_bot.application import (
    TelegramValidationReport,
    ValidateTelegramSession,
)
from telegram_assist_bot.application.ports import (
    TelegramChannelReference,
    TelegramChannelRole,
    TelegramValidationGateway,
)

if TYPE_CHECKING:
    from telegram_assist_bot.shared.config import ApplicationConfig


def required_channel_references(
    settings: ApplicationConfig,
) -> tuple[TelegramChannelReference, ...]:
    """Build required active-source and referenced-destination validation inputs."""
    references: list[TelegramChannelReference] = []
    required_destination_names: set[str] = set()
    for index, source in enumerate(settings.source_channels):
        if not source.enabled:
            continue
        required_destination_names.update(source.allowed_destination_names)
        references.append(
            TelegramChannelReference(
                config_name=source.name,
                configured_channel_id=source.telegram_channel_id,
                configured_username=source.username,
                role=TelegramChannelRole.SOURCE,
                configuration_path=f"source_channels.{index}",
            )
        )
    for index, destination in enumerate(settings.destination_channels):
        if destination.name not in required_destination_names:
            continue
        references.append(
            TelegramChannelReference(
                config_name=destination.name,
                configured_channel_id=destination.telegram_channel_id,
                configured_username=destination.username,
                role=TelegramChannelRole.DESTINATION,
                configuration_path=f"destination_channels.{index}",
            )
        )
    return tuple(references)


async def validate_telegram_startup(
    settings: ApplicationConfig,
    gateway: TelegramValidationGateway,
) -> TelegramValidationReport:
    """Run the non-interactive validation gate before any product worker."""
    return await ValidateTelegramSession(gateway).execute(
        required_channel_references(settings)
    )


__all__ = ("required_channel_references", "validate_telegram_startup")
