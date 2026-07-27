"""Verify strict backward-compatible independent media retention settings."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.shared.config import (
    ConfigurationValidationError,
    load_configuration,
)

if TYPE_CHECKING:
    from tests.unit.shared.config.conftest import ConfigurationWriter, JsonObject


def _media(payload: JsonObject) -> JsonObject:
    return cast("JsonObject", payload["media"])


def test_missing_media_retention_fields_use_safe_defaults(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    media = _media(valid_payload)
    media.pop("retention_days")
    media.pop("cleanup_interval_seconds")

    loaded = load_configuration(
        configuration_writer(valid_payload), environ=synthetic_environ
    )

    assert loaded.settings.media.retention_days == 2
    assert loaded.settings.media.cleanup_interval_seconds == 3600


def test_custom_media_retention_settings_are_loaded(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    media = _media(valid_payload)
    media["retention_days"] = 30
    media["cleanup_interval_seconds"] = 600

    loaded = load_configuration(
        configuration_writer(valid_payload), environ=synthetic_environ
    )

    assert loaded.settings.media.retention_days == 30
    assert loaded.settings.media.cleanup_interval_seconds == 600


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retention_days", 0),
        ("retention_days", -1),
        ("retention_days", 1.5),
        ("retention_days", "2"),
        ("retention_days", True),
        ("retention_days", 3651),
        ("cleanup_interval_seconds", 59),
        ("cleanup_interval_seconds", 60.5),
        ("cleanup_interval_seconds", "3600"),
        ("cleanup_interval_seconds", False),
        ("cleanup_interval_seconds", 604_801),
    ],
)
def test_invalid_media_retention_values_report_exact_path(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    field: str,
    value: object,
) -> None:
    _media(valid_payload)[field] = value

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload), environ=synthetic_environ
        )

    assert f"media.{field}" in {issue.formatted_path for issue in captured.value.issues}
