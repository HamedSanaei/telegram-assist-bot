"""AI response validator module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pydantic

from telegram_assist_bot.application.ai.exceptions import AISchemaValidationError
from telegram_assist_bot.application.ai.schemas import (
    TASK_OUTPUT_SCHEMAS,
    get_expected_schema_version,
)

if TYPE_CHECKING:
    from telegram_assist_bot.application.ai.contracts import AITaskType


class ResponseValidator:
    """Validates parsed AI payloads against task schemas and versions."""

    def validate(
        self, parsed_data: dict[str, Any], task_type: AITaskType, schema_version: str
    ) -> pydantic.BaseModel:
        """Validates the parsed dictionary against the expected Pydantic schema.

        Raises AISchemaValidationError on failure.
        """
        # 1. Validate schema version
        expected_version = get_expected_schema_version(task_type)
        if schema_version != expected_version:
            raise AISchemaValidationError(
                cause=ValueError(
                    f"Unsupported schema version: got '{schema_version}', "
                    f"expected '{expected_version}'"
                )
            )

        # 2. Get target Pydantic schema class
        schema_cls = TASK_OUTPUT_SCHEMAS.get(task_type)
        if not schema_cls:
            raise AISchemaValidationError(
                cause=ValueError(f"No schema registered for task type '{task_type}'")
            )

        # 3. Perform strict validation
        try:
            validated_model = schema_cls.model_validate(parsed_data)
        except pydantic.ValidationError as e:
            raise AISchemaValidationError(cause=e) from e

        return validated_model
