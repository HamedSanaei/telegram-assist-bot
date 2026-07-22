"""AI response normalizer module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram_assist_bot.application.ai.contracts import AIResult

if TYPE_CHECKING:
    from pydantic import BaseModel

    from telegram_assist_bot.application.ai.contracts import (
        AITaskType,
        RawResponseEnvelope,
    )


class ResponseNormalizer:
    """Normalizes validated AI outputs into standard application AIResult models."""

    def normalize(
        self,
        envelope: RawResponseEnvelope,
        validated_model: BaseModel,
        task_type: AITaskType,
        provider_name: str,
        model_name: str,
        prompt_version: str,
        schema_version: str,
        attempt_number: int = 1,
        fallback_count: int = 0,
    ) -> AIResult:
        """Converts raw execution info and validated output into standard AIResult."""
        result_dict = validated_model.model_dump()

        # Extract confidence and reason if present in the schema
        confidence = getattr(validated_model, "confidence", None)
        reason = getattr(validated_model, "reason", None)

        return AIResult(
            success=True,
            task_type=task_type,
            provider_name=provider_name,
            model_name=model_name,
            result=result_dict,
            confidence=confidence,
            reason=reason,
            prompt_version=prompt_version,
            schema_version=schema_version,
            latency=envelope.latency_seconds,
            input_tokens=envelope.input_tokens,
            output_tokens=envelope.output_tokens,
            attempt_number=attempt_number,
            fallback_count=fallback_count,
        )
