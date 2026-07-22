"""Application port for interacting with AI service providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

    from telegram_assist_bot.application.ai.contracts import (
        AITaskType,
        RawResponseEnvelope,
    )


class AIProvider(ABC):
    """Application-owned port for calling an AI provider.

    Implementations of this interface reside in the infrastructure layer.
    """

    @abstractmethod
    async def execute_attempt(
        self,
        task_type: AITaskType,
        prompt: str,
        request_context: BaseModel,
        provider_name: str,
        model_name: str,
        timeout_seconds: float,
    ) -> RawResponseEnvelope:
        """Executes a single attempt for an AI task.

        Args:
            task_type: The type of AI task to perform.
            prompt: The versioned prompt template to send.
            request_context: Pydantic model containing the input parameters.
            provider_name: Name of the AI provider to invoke.
            model_name: Name of the model to use.
            timeout_seconds: Timeout in seconds for the attempt.

        Returns:
            RawResponseEnvelope containing the raw response and metadata.

        Raises:
            Exception: Implementations should raise standard or typed exceptions.
        """
