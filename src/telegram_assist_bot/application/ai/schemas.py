"""AI input contexts and output schemas."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from telegram_assist_bot.application.ai.contracts import AITaskType

# Request Contexts (Inputs)


class AdvertisementDetectionContext(BaseModel):
    """Input context for advertisement detection."""

    text: str = Field(
        ...,
        min_length=1,
        description="The post text or caption to analyze",
    )


class SemanticDuplicateContext(BaseModel):
    """Input context for semantic duplicate checking."""

    text: str = Field(
        ...,
        min_length=1,
        description="The text of the new post",
    )
    compare_text: str = Field(
        ...,
        min_length=1,
        description="The text of the existing candidate post",
    )
    similarity_threshold: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="The minimum semantic similarity threshold",
    )


class CategorizationContext(BaseModel):
    """Input context for categorizing a post."""

    text: str = Field(
        ...,
        min_length=1,
        description="The text or caption to categorize",
    )
    allowed_categories: list[str] = Field(
        ...,
        min_length=1,
        description="List of valid category names",
    )


class ScoringContext(BaseModel):
    """Input context for scoring a post's quality/relevance."""

    text: str = Field(
        ...,
        min_length=1,
        description="The text or caption to evaluate",
    )


# Base Output Schema


class BaseAIOutput(BaseModel):
    """Base class for all Phase 1 AI output schemas."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    model_config = ConfigDict(strict=True, extra="forbid")


# Output Schemas (AI Outputs)


class AdvertisementDetectionOutput(BaseAIOutput):
    """Expected output structure for advertisement detection."""

    is_advertisement: bool = Field(
        ...,
        description="True if the content is classified as promotional/ad",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 to 1.0",
    )
    reason: str = Field(
        ...,
        min_length=1,
        description="Brief explanation in English or Persian",
    )


class SemanticDuplicateOutput(BaseAIOutput):
    """Expected output structure for semantic duplicate checking."""

    SCHEMA_VERSION: ClassVar[str] = "2"

    is_duplicate: bool = Field(
        ...,
        description="True if the content is semantically identical or highly similar",
    )
    similarity: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Semantic similarity score from 0.0 to 1.0",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 to 1.0",
    )
    reason: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Brief explanation of why it is or is not a duplicate",
    )


class CategorizationOutput(BaseAIOutput):
    """Expected output structure for categorization."""

    category: str = Field(
        ...,
        min_length=1,
        description="The predicted category name",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 to 1.0",
    )
    reason: str = Field(
        ...,
        min_length=1,
        description="Reasoning for selecting this category",
    )


class ScoringOutput(BaseAIOutput):
    """Expected output structure for post scoring."""

    score: int = Field(
        ...,
        ge=1,
        le=10,
        description="The quality score assigned to the post, from 1 to 10",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 to 1.0",
    )
    reason: str = Field(
        ...,
        min_length=1,
        description="Reasoning for the assigned score",
    )


# Mappings for Registry Validation

TASK_OUTPUT_SCHEMAS: dict[AITaskType, type[BaseAIOutput]] = {
    AITaskType.ADVERTISEMENT_DETECTION: AdvertisementDetectionOutput,
    AITaskType.SEMANTIC_DUPLICATE: SemanticDuplicateOutput,
    AITaskType.CATEGORIZATION: CategorizationOutput,
    AITaskType.SCORING: ScoringOutput,
}

TASK_INPUT_CONTEXTS: dict[AITaskType, type[BaseModel]] = {
    AITaskType.ADVERTISEMENT_DETECTION: AdvertisementDetectionContext,
    AITaskType.SEMANTIC_DUPLICATE: SemanticDuplicateContext,
    AITaskType.CATEGORIZATION: CategorizationContext,
    AITaskType.SCORING: ScoringContext,
}


def get_expected_schema_version(task_type: AITaskType) -> str:
    """Retrieve the expected schema version string for a given task type."""
    schema_cls = TASK_OUTPUT_SCHEMAS.get(task_type)
    if not schema_cls:
        raise ValueError(f"Unknown task type '{task_type}'")
    return schema_cls.SCHEMA_VERSION
