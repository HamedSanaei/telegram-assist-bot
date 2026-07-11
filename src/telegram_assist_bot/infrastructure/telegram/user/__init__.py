"""Telegram User API adapters implemented with Telethon."""

from telegram_assist_bot.infrastructure.telegram.user.history_adapter import (
    TelethonHistoryAdapter,
)
from telegram_assist_bot.infrastructure.telegram.user.live_adapter import (
    TelethonLiveAdapter,
)
from telegram_assist_bot.infrastructure.telegram.user.message_mapper import (
    InvalidTelegramMessageError,
    map_telethon_message,
)
from telegram_assist_bot.infrastructure.telegram.user.session_adapter import (
    TelethonSessionAdapter,
    create_telethon_client,
)
from telegram_assist_bot.infrastructure.telegram.user.text_ingestion_gateway import (
    TelethonTextIngestionGateway,
)

__all__ = (
    "InvalidTelegramMessageError",
    "TelethonHistoryAdapter",
    "TelethonLiveAdapter",
    "TelethonSessionAdapter",
    "TelethonTextIngestionGateway",
    "create_telethon_client",
    "map_telethon_message",
)
