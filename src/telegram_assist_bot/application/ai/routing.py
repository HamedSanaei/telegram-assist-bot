"""AI routing logic for selecting and ordering fallback candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.shared.errors import ConfigurationError

if TYPE_CHECKING:
    from telegram_assist_bot.shared.config.models import (
        AiConfig,
        AiRouteCandidateConfig,
    )

# Registered capabilities for validation
SUPPORTED_CAPABILITIES: dict[str, dict[str, set[AITaskType]]] = {
    "z-ai": {
        "glm-4.7-flash": {
            AITaskType.ADVERTISEMENT_DETECTION,
            AITaskType.SEMANTIC_DUPLICATE,
            AITaskType.CATEGORIZATION,
            AITaskType.SCORING,
        }
    },
    "deepseek": {
        "deepseek-v4-flash": {
            AITaskType.ADVERTISEMENT_DETECTION,
            AITaskType.SEMANTIC_DUPLICATE,
            AITaskType.CATEGORIZATION,
            AITaskType.SCORING,
        },
        "deepseek-v4-pro": {
            AITaskType.ADVERTISEMENT_DETECTION,
            AITaskType.SEMANTIC_DUPLICATE,
            AITaskType.CATEGORIZATION,
            AITaskType.SCORING,
        },
    },
    "fake-provider": {
        "fake-model": {
            AITaskType.ADVERTISEMENT_DETECTION,
            AITaskType.SEMANTIC_DUPLICATE,
            AITaskType.CATEGORIZATION,
            AITaskType.SCORING,
        }
    },
    "mock-provider": {
        "mock-model": {
            AITaskType.ADVERTISEMENT_DETECTION,
            AITaskType.SEMANTIC_DUPLICATE,
            AITaskType.CATEGORIZATION,
            AITaskType.SCORING,
        }
    },
}


def select_route_candidates(
    config: AiConfig, task_type: AITaskType
) -> list[AiRouteCandidateConfig]:
    """Selects and orders enabled AI route candidates for a given task type.

    Performs strict route validation and raises ``ConfigurationError`` for invalid
    definitions.
    """
    # 1. Locate route for task_type
    target_route = None
    for route in config.routes:
        if route.task == task_type:
            target_route = route
            break

    if not target_route:
        raise ConfigurationError(
            cause=ValueError(f"No configured AI route for task: {task_type}")
        )

    if not target_route.candidates:
        raise ConfigurationError(
            cause=ValueError(f"Route for task {task_type} contains no candidates.")
        )

    # 2. Get declared providers
    providers_map = {p.name: p for p in config.providers}

    seen_candidates: set[tuple[str, str]] = set()
    validated_candidates: list[tuple[AiRouteCandidateConfig, int]] = []

    for index, candidate in enumerate(target_route.candidates):
        p_name = candidate.provider_name
        m_name = candidate.model_name

        # Validate unknown provider
        if p_name not in providers_map:
            raise ConfigurationError(
                cause=ValueError(
                    f"Candidate references unknown provider '{p_name}' "
                    f"in task '{task_type}'"
                )
            )

        # Validate unsupported model & task
        if p_name in SUPPORTED_CAPABILITIES:
            model_caps = SUPPORTED_CAPABILITIES[p_name]
            if m_name not in model_caps:
                raise ConfigurationError(
                    cause=ValueError(
                        f"Unsupported model '{m_name}' for provider '{p_name}'"
                    )
                )
            if task_type not in model_caps[m_name]:
                raise ConfigurationError(
                    cause=ValueError(
                        f"Model '{m_name}' of provider '{p_name}' does not "
                        f"support task '{task_type}'"
                    )
                )

        # Validate duplicate candidates
        identity = (p_name, m_name)
        if identity in seen_candidates:
            raise ConfigurationError(
                cause=ValueError(
                    f"Duplicate candidate '{p_name}/{m_name}' in route "
                    f"for task '{task_type}'"
                )
            )
        seen_candidates.add(identity)

        # Keep only candidates whose provider is enabled
        if providers_map[p_name].enabled:
            validated_candidates.append((candidate, index))

    # Sort by ascending priority and retain original order as a stable tie-breaker.
    validated_candidates.sort(key=lambda item: (item[0].priority, item[1]))

    return [item[0] for item in validated_candidates]
