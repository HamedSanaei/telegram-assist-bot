"""Immutable destination-specific content values."""

from dataclasses import dataclass

from telegram_assist_bot.domain.posts import TelegramEntity


@dataclass(frozen=True, slots=True)
class DestinationPreparedContent:
    """Hold independently prepared destination text and entities."""

    text: str
    entities: tuple[TelegramEntity, ...]
    content_policy_version: int
