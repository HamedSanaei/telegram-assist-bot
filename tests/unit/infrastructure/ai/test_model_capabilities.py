"""Unit tests for model capabilities and task validations of DeepSeek provider."""

from __future__ import annotations

import pytest

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.infrastructure.ai.deepseek import (
    DEEPSEEK_MODEL_CAPABILITIES,
)


def test_deepseek_models_support_all_tasks() -> None:
    assert set(DEEPSEEK_MODEL_CAPABILITIES) == {
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    }
    expected_tasks = frozenset(AITaskType)
    assert all(
        capabilities == expected_tasks
        for capabilities in DEEPSEEK_MODEL_CAPABILITIES.values()
    )


def test_deepseek_unapproved_model_rejected() -> None:
    assert "deepseek-chat" not in DEEPSEEK_MODEL_CAPABILITIES
    assert "deepseek-reasoner" not in DEEPSEEK_MODEL_CAPABILITIES


def test_deepseek_capability_registry_is_immutable() -> None:
    with pytest.raises(TypeError):
        DEEPSEEK_MODEL_CAPABILITIES["unapproved-model"] = frozenset(AITaskType)  # type: ignore[index]
