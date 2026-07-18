"""Application-owned handlers for completed canonical AI tasks."""

from telegram_assist_bot.application.ai.task_handlers.advertisement_detection import (
    AdvertisementDetectionHandler,
    AdvertisementHandlerOutcome,
    AdvertisementTaskValidationError,
)
from telegram_assist_bot.application.ai.task_handlers.categorization import (
    CategorizationHandler,
    CategorizationHandlerOutcome,
    CategorizationTaskValidationError,
)
from telegram_assist_bot.application.ai.task_handlers.semantic_duplicate import (
    SemanticDuplicateHandler,
)

__all__ = (
    "AdvertisementDetectionHandler",
    "AdvertisementHandlerOutcome",
    "AdvertisementTaskValidationError",
    "CategorizationHandler",
    "CategorizationHandlerOutcome",
    "CategorizationTaskValidationError",
    "SemanticDuplicateHandler",
)
