"""AI application package containing contracts, schemas, and prompt registry."""

from __future__ import annotations

from telegram_assist_bot.application.ai.contracts import (
    AIResult,
    AITaskType,
    RawResponseEnvelope,
)
from telegram_assist_bot.application.ai.prompt_registry import (
    PromptMetadata,
    PromptRegistry,
)
from telegram_assist_bot.application.ai.schemas import (
    AdvertisementDetectionContext,
    AdvertisementDetectionOutput,
    BaseAIOutput,
    CategorizationContext,
    CategorizationOutput,
    ScoringContext,
    ScoringOutput,
    SemanticDuplicateContext,
    SemanticDuplicateOutput,
)

__all__ = (
    "AIResult",
    "AITaskType",
    "AdvertisementDetectionContext",
    "AdvertisementDetectionOutput",
    "BaseAIOutput",
    "CategorizationContext",
    "CategorizationOutput",
    "PromptMetadata",
    "PromptRegistry",
    "RawResponseEnvelope",
    "ScoringContext",
    "ScoringOutput",
    "SemanticDuplicateContext",
    "SemanticDuplicateOutput",
)
