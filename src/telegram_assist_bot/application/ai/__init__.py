"""AI application package containing contracts, schemas, and prompt registry."""

from __future__ import annotations

from telegram_assist_bot.application.ai.claim_ai_job import ClaimAIJob
from telegram_assist_bot.application.ai.contracts import (
    AIResult,
    AITaskType,
    RawResponseEnvelope,
)
from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
from telegram_assist_bot.application.ai.exceptions import (
    AIEmptyResponseError,
    AIInvalidJSONError,
    AIRepairFailedError,
    AIResponseError,
    AISchemaValidationError,
    AIValidationConstraintError,
)
from telegram_assist_bot.application.ai.prompt_registry import (
    PromptMetadata,
    PromptRegistry,
)
from telegram_assist_bot.application.ai.response_normalizer import ResponseNormalizer
from telegram_assist_bot.application.ai.response_parser import ResponseParser
from telegram_assist_bot.application.ai.response_validator import ResponseValidator
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
    "AIEmptyResponseError",
    "AIInvalidJSONError",
    "AIRepairFailedError",
    "AIResponseError",
    "AIResult",
    "AISchemaValidationError",
    "AITaskType",
    "AIValidationConstraintError",
    "AdvertisementDetectionContext",
    "AdvertisementDetectionOutput",
    "BaseAIOutput",
    "CategorizationContext",
    "CategorizationOutput",
    "ClaimAIJob",
    "EnqueueAIJob",
    "PromptMetadata",
    "PromptRegistry",
    "RawResponseEnvelope",
    "ResponseNormalizer",
    "ResponseParser",
    "ResponseValidator",
    "ScoringContext",
    "ScoringOutput",
    "SemanticDuplicateContext",
    "SemanticDuplicateOutput",
)
