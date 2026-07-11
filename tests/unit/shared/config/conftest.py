"""Shared synthetic fixtures for configuration unit tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

type JsonObject = dict[str, object]
type ConfigurationWriter = Callable[[Mapping[str, object]], Path]

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_CONFIGURATION_PATH = REPOSITORY_ROOT / "config" / "configuration.example.json"


def _read_example_payload() -> JsonObject:
    raw_text = EXAMPLE_CONFIGURATION_PATH.read_text(encoding="utf-8")
    decoded: object = json.loads(raw_text)
    assert isinstance(decoded, dict)
    return cast("JsonObject", decoded)


@pytest.fixture
def valid_payload() -> JsonObject:
    """Return an isolated mutable copy of the committed safe example."""
    return deepcopy(_read_example_payload())


def _synthetic_value(label: str) -> str:
    return f"fixture-{label}-value"


@pytest.fixture
def synthetic_environ() -> dict[str, str]:
    """Return deterministic non-production values for every example reference."""
    return {
        "TAB_MONGODB_URI": "mongodb://database.example.invalid:27017",
        "TAB_TELEGRAM_API_ID": "123456",
        "TAB_TELEGRAM_API_HASH": _synthetic_value("telegram-api-hash"),
        "TAB_TELEGRAM_PHONE_NUMBER": "synthetic-phone-number",
        "TAB_TELEGRAM_BOT_TOKEN": _synthetic_value("telegram-bot-token"),
        "TAB_AI_PROVIDER_KEY": _synthetic_value("ai-provider-key"),
    }


@pytest.fixture
def configuration_writer(tmp_path: Path) -> ConfigurationWriter:
    """Write JSON fixtures with the repository's UTF-8 safety policy."""

    def write(payload: Mapping[str, object]) -> Path:
        path = tmp_path / "configuration.json"
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        path.write_text(serialized + "\n", encoding="utf-8")
        return path

    return write
