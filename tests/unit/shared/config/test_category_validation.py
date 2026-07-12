"""Verify Milestone 2 category and media configuration validation."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from telegram_assist_bot.shared.config.models import ApplicationConfig


def test_category_references_are_validated(valid_payload: dict[str, object]) -> None:
    raw = deepcopy(valid_payload)
    raw["categorization"] = {
        "categories": [{"category_id": "general", "display_name": "عمومی"}],
        "keyword_rules": [
            {
                "rule_id": "r1",
                "category_id": "missing",
                "keyword": "فناوری",
                "priority": 1,
            }
        ],
    }
    with pytest.raises(ValidationError, match="unknown category"):
        ApplicationConfig.model_validate(raw)


def test_media_bounds_reject_unbounded_values(
    valid_payload: dict[str, object],
) -> None:
    raw = deepcopy(valid_payload)
    raw["media"] = {"maximum_bytes": 0}
    with pytest.raises(ValidationError, match="greater than or equal"):
        ApplicationConfig.model_validate(raw)
