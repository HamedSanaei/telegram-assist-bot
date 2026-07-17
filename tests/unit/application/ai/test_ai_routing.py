"""Unit tests for AI route candidate selection, ordering, and validation."""

from __future__ import annotations

import pytest

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.routing import select_route_candidates
from telegram_assist_bot.shared.config import (
    AiConfig,
    AiProviderConfig,
    AiRouteCandidateConfig,
    AiTaskRouteConfig,
)
from telegram_assist_bot.shared.errors import ConfigurationError


def test_routing_orders_by_priority_and_stable_tie_breaker() -> None:
    """Verifies sorting by priority and tie-breaker."""
    config = AiConfig(
        providers=(
            AiProviderConfig(name="z-ai", enabled=True),
            AiProviderConfig(name="deepseek", enabled=True),
        ),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    # Candidate A: priority 2
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-flash",
                        priority=2,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                    # Candidate B: priority 1
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                    # Candidate C: priority 2 (equal to A, index-ordered)
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-pro",
                        priority=2,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                ),
            ),
        ),
    )

    selected = select_route_candidates(config, AITaskType.ADVERTISEMENT_DETECTION)
    assert len(selected) == 3
    # Order: B (priority 1), A (priority 2, index 0), C (priority 2, index 2)
    assert selected[0].provider_name == "z-ai"
    assert selected[0].model_name == "glm-4.7-flash"
    assert selected[1].provider_name == "deepseek"
    assert selected[1].model_name == "deepseek-v4-flash"
    assert selected[2].provider_name == "deepseek"
    assert selected[2].model_name == "deepseek-v4-pro"


def test_routing_filters_disabled_providers() -> None:
    """Verifies candidates belonging to disabled providers are filtered out."""
    config = AiConfig(
        providers=(
            # z-ai is disabled
            AiProviderConfig(name="z-ai", enabled=False),
            # deepseek is enabled
            AiProviderConfig(name="deepseek", enabled=True),
        ),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-flash",
                        priority=2,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                ),
            ),
        ),
    )

    selected = select_route_candidates(config, AITaskType.ADVERTISEMENT_DETECTION)
    assert len(selected) == 1
    assert selected[0].provider_name == "deepseek"
    assert selected[0].model_name == "deepseek-v4-flash"


def test_routing_rejects_duplicate_candidates() -> None:
    """Verifies that duplicate candidate definitions raise ConfigurationError."""
    config = AiConfig(
        providers=(AiProviderConfig(name="deepseek", enabled=True),),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                    # Duplicate
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-flash",
                        priority=2,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(ConfigurationError) as exc_info:
        select_route_candidates(config, AITaskType.ADVERTISEMENT_DETECTION)
    assert "Duplicate candidate" in str(exc_info.value.__cause__)


def test_routing_rejects_unknown_providers() -> None:
    """Verifies unknown providers raise ConfigurationError."""
    config = AiConfig(
        providers=(AiProviderConfig(name="deepseek", enabled=True),),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    AiRouteCandidateConfig(
                        provider_name="unknown-provider",
                        model_name="deepseek-v4-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(ConfigurationError) as exc_info:
        select_route_candidates(config, AITaskType.ADVERTISEMENT_DETECTION)
    assert "unknown provider" in str(exc_info.value.__cause__)


def test_routing_rejects_unsupported_models() -> None:
    """Verifies unsupported models raise ConfigurationError."""
    config = AiConfig(
        providers=(AiProviderConfig(name="deepseek", enabled=True),),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="unsupported-model-name",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(ConfigurationError) as exc_info:
        select_route_candidates(config, AITaskType.ADVERTISEMENT_DETECTION)
    assert "Unsupported model" in str(exc_info.value.__cause__)


def test_routing_compatibility_alias_mapping() -> None:
    """Verifies that legacy task names are mapped to canonical AITaskType values."""
    # 'duplicate_detection' -> 'semantic_duplicate'
    route_duplicate = AiTaskRouteConfig(
        task="duplicate_detection",  # type: ignore[arg-type]
        candidates=(
            AiRouteCandidateConfig(
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                priority=1,
                timeout_seconds=30,
                max_attempts=3,
            ),
        ),
    )
    assert route_duplicate.task == AITaskType.SEMANTIC_DUPLICATE

    # 'content_scoring' -> 'scoring'
    route_scoring = AiTaskRouteConfig(
        task="content_scoring",  # type: ignore[arg-type]
        candidates=(
            AiRouteCandidateConfig(
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                priority=1,
                timeout_seconds=30,
                max_attempts=3,
            ),
        ),
    )
    assert route_scoring.task == AITaskType.SCORING


def test_routing_raises_error_on_missing_route() -> None:
    """Verifies missing route raises ConfigurationError."""
    config = AiConfig(
        providers=(),
        routes=(),
    )
    with pytest.raises(ConfigurationError) as exc_info:
        select_route_candidates(config, AITaskType.ADVERTISEMENT_DETECTION)
    assert "No configured AI route" in str(exc_info.value.__cause__)
