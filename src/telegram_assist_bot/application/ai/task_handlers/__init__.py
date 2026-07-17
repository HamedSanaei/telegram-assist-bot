"""Application-owned handlers for completed canonical AI tasks."""

from telegram_assist_bot.application.ai.task_handlers.advertisement_detection import (
    AdvertisementDetectionHandler,
    AdvertisementHandlerOutcome,
    AdvertisementTaskValidationError,
)

__all__ = (
    "AdvertisementDetectionHandler",
    "AdvertisementHandlerOutcome",
    "AdvertisementTaskValidationError",
)
