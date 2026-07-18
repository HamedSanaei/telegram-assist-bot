"""Unit tests for the AI prompt registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.prompt_registry import (
    PromptRegistry,
    calculate_prompt_hash,
    parse_prompt_file,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_parse_prompt_file_valid() -> None:
    content = """---
task_type: advertisement_detection
prompt_version: 1.0.0
schema_version: 1
---
شما یک دستیار هوشمند برای پست‌های تبلیغاتی هستید.
متن: {text}
"""
    metadata, body = parse_prompt_file(content)
    assert metadata == {
        "task_type": "advertisement_detection",
        "prompt_version": "1.0.0",
        "schema_version": "1",
    }
    assert body == "شما یک دستیار هوشمند برای پست‌های تبلیغاتی هستید.\nمتن: {text}"


def test_parse_prompt_file_no_frontmatter() -> None:
    content = "ساده بدون سربرگ"
    metadata, body = parse_prompt_file(content)
    assert metadata == {}
    assert body == "ساده بدون سربرگ"


def test_calculate_prompt_hash_deterministic() -> None:
    body1 = "متن اول\r\nبا نیم‌فاصله (‌) و ایموجی 😊"  # noqa: RUF001
    body2 = "متن اول\nبا نیم‌فاصله (‌) و ایموجی 😊"  # noqa: RUF001

    # Both CRLF and LF normalized should produce the same hash
    hash1 = calculate_prompt_hash(body1)
    hash2 = calculate_prompt_hash(body2)
    assert hash1 == hash2

    # A tiny change changes the hash
    hash3 = calculate_prompt_hash(body2 + " ")
    assert hash1 != hash3


def test_registry_loads_default_prompts() -> None:
    # Loads the actual prompts under src/telegram_assist_bot/application/ai/prompts
    registry = PromptRegistry()
    prompts = registry.list_prompts()

    assert len(prompts) == 4
    for prompt in prompts:
        assert isinstance(prompt.task_type, AITaskType)
        expected_version = (
            "2.0.0"
            if prompt.task_type
            in (AITaskType.SEMANTIC_DUPLICATE, AITaskType.CATEGORIZATION)
            else "1.0.0"
        )
        expected_schema = (
            "2"
            if prompt.task_type
            in (AITaskType.SEMANTIC_DUPLICATE, AITaskType.CATEGORIZATION)
            else "1"
        )
        assert prompt.prompt_version == expected_version
        assert prompt.schema_version == expected_schema
        assert len(prompt.prompt_hash) == 64
        # Assert Persian and emoji are preserved
        assert "دستیار" in prompt.body or "پست" in prompt.body


def test_registry_raises_on_missing_metadata(tmp_path: Path) -> None:
    # Missing task_type
    p = tmp_path / "test.txt"
    p.write_text(
        """---
prompt_version: 1.0.0
schema_version: 1
---
Prompt body
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Missing 'task_type'"):
        PromptRegistry(prompts_dir=tmp_path)


def test_registry_raises_on_invalid_task_type(tmp_path: Path) -> None:
    p = tmp_path / "test.txt"
    p.write_text(
        """---
task_type: invalid_task
prompt_version: 1.0.0
schema_version: 1
---
Prompt body
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid task_type 'invalid_task'"):
        PromptRegistry(prompts_dir=tmp_path)


def test_registry_raises_on_duplicate_version(tmp_path: Path) -> None:
    p1 = tmp_path / "p1.txt"
    p1.write_text(
        """---
task_type: scoring
prompt_version: 1.0.0
schema_version: 1
---
Body 1
""",
        encoding="utf-8",
    )

    p2 = tmp_path / "p2.txt"
    p2.write_text(
        """---
task_type: scoring
prompt_version: 1.0.0
schema_version: 1
---
Body 2
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate prompt version"):
        PromptRegistry(prompts_dir=tmp_path)


def test_registry_raises_on_schema_version_mismatch(tmp_path: Path) -> None:
    p = tmp_path / "test.txt"
    p.write_text(
        """---
task_type: scoring
prompt_version: 1.0.0
schema_version: 2
---
Prompt body
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Schema version mismatch"):
        PromptRegistry(prompts_dir=tmp_path)


def test_registry_get_prompt() -> None:
    registry = PromptRegistry()
    prompt = registry.get_prompt(AITaskType.SCORING, "1.0.0")
    assert prompt.task_type == AITaskType.SCORING
    assert prompt.prompt_version == "1.0.0"

    with pytest.raises(KeyError):
        registry.get_prompt(AITaskType.SCORING, "9.9.9")
