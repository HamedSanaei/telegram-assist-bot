"""AI contracts and generic metadata types."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AITaskType(StrEnum):
    """Supported AI task types for Phase 1."""

    ADVERTISEMENT_DETECTION = "advertisement_detection"
    SEMANTIC_DUPLICATE = "semantic_duplicate"
    CATEGORIZATION = "categorization"
    SCORING = "scoring"


class RawResponseEnvelope(BaseModel):
    """Raw provider response envelope, decoupled from any SDK.

    Used by AIProvider implementations to return raw data and execution metadata
    back to the application layer.
    """

    raw_content: str = Field(
        ..., description="The raw response body returned by the AI provider"
    )
    status_code: int | None = Field(
        None, description="The HTTP status code if applicable"
    )
    headers: dict[str, str] | None = Field(
        None, description="Response headers if applicable"
    )
    latency_seconds: float | None = Field(
        None, description="Latency of the request in seconds"
    )
    input_tokens: int | None = Field(None, description="Number of input tokens billed")
    output_tokens: int | None = Field(
        None, description="Number of output tokens billed"
    )


class AIResult(BaseModel):
    """Standardized normalized AI result, decoupled from any provider details."""

    success: bool = Field(..., description="Whether the AI operation succeeded")
    task_type: AITaskType = Field(..., description="The type of AI task executed")
    provider_name: str = Field(
        ..., description="The name of the provider that handled the request"
    )
    model_name: str = Field(..., description="The specific model used")
    result: dict[str, Any] | None = Field(
        None, description="The parsed/normalized JSON result matching the schema"
    )
    confidence: float | None = Field(
        None, ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0"
    )
    reason: str | None = Field(None, description="Short explanation of the outcome")
    prompt_version: str = Field(
        ..., description="The version of the prompt template used"
    )
    schema_version: str = Field(..., description="The version of the schema used")
    latency: float | None = Field(
        None, description="Latency of the successful call in seconds"
    )
    input_tokens: int | None = Field(None, description="Tokens consumed as input")
    output_tokens: int | None = Field(None, description="Tokens consumed as output")
    attempt_number: int = Field(
        ..., description="The number of attempts taken for this provider"
    )
    fallback_count: int = Field(
        ..., description="The number of times fallbacks occurred"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Timestamp in UTC"
    )

    @property
    def payload(self) -> dict[str, Any] | None:
        """Alias for result, kept for backward compatibility."""
        return self.result
