"""Unit tests for safe configuration file loading and typed construction."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator, Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Never, cast
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr, ValidationError

from telegram_assist_bot.shared.config import (
    SUPPORTED_CONFIGURATION_SCHEMA_VERSION,
    AdminConfig,
    AdvertisementConfig,
    AdvertisementRouteConfig,
    AiConfig,
    AiProviderConfig,
    AiRouteCandidateConfig,
    AiTask,
    AiTaskRouteConfig,
    ApplicationConfig,
    ConfigurationEncodingError,
    ConfigurationFileNotFoundError,
    ConfigurationJsonError,
    ConfigurationReadError,
    ConfigurationRootError,
    ConfigurationValidationError,
    DestinationChannelConfig,
    FeatureFlags,
    LoadedConfiguration,
    LoggingConfig,
    LogLevel,
    MongoConfig,
    PublishingConfig,
    SecretReference,
    SourceChannelConfig,
    TelegramBotConfig,
    TelegramConfig,
    TelegramUserConfig,
    UnsupportedConfigurationSchemaVersionError,
    load_configuration,
)

type JsonObject = dict[str, object]
type ConfigurationWriter = Callable[[Mapping[str, object]], Path]

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_EXAMPLE_PATH = _REPOSITORY_ROOT / "config" / "configuration.example.json"


class _FailOnAccessMapping(Mapping[str, str]):
    """Prove that fail-fast schema validation does not inspect secrets."""

    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"environment was unexpectedly read: {key}")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("environment was unexpectedly iterated")

    def __len__(self) -> int:
        raise AssertionError("environment length was unexpectedly requested")


def _as_object(value: object) -> JsonObject:
    assert isinstance(value, dict)
    return cast("JsonObject", value)


def _assign_attribute(target: object, name: str, value: object) -> None:
    setattr(target, name, value)


def test_loads_example_into_all_typed_immutable_sections(
    synthetic_environ: dict[str, str],
) -> None:
    """The committed example constructs every public typed configuration model."""
    loaded = load_configuration(_EXAMPLE_PATH, environ=synthetic_environ)
    settings = loaded.settings

    assert isinstance(loaded, LoadedConfiguration)
    assert isinstance(settings, ApplicationConfig)
    assert (
        settings.configuration_schema_version == SUPPORTED_CONFIGURATION_SCHEMA_VERSION
    )
    assert isinstance(settings.mongodb, MongoConfig)
    assert isinstance(settings.mongodb.uri, SecretReference)
    assert isinstance(settings.telegram, TelegramConfig)
    assert isinstance(settings.telegram.user, TelegramUserConfig)
    assert isinstance(settings.telegram.bot, TelegramBotConfig)
    assert isinstance(settings.admins, tuple)
    assert isinstance(settings.admins[0], AdminConfig)
    assert isinstance(settings.source_channels, tuple)
    assert isinstance(settings.source_channels[0], SourceChannelConfig)
    assert isinstance(settings.destination_channels, tuple)
    assert isinstance(settings.destination_channels[0], DestinationChannelConfig)
    assert isinstance(settings.features, FeatureFlags)
    assert isinstance(settings.publishing, PublishingConfig)
    assert isinstance(settings.timezone, ZoneInfo)
    assert settings.timezone.key == "Asia/Tehran"
    assert isinstance(settings.logging, LoggingConfig)
    assert settings.logging.level is LogLevel.INFO
    assert isinstance(settings.ai, AiConfig)
    assert isinstance(settings.ai.providers[0], AiProviderConfig)
    assert isinstance(settings.ai.routes[0], AiTaskRouteConfig)
    assert settings.ai.routes[0].task is AiTask.ADVERTISEMENT_DETECTION
    assert isinstance(settings.ai.routes[0].candidates[0], AiRouteCandidateConfig)
    assert isinstance(settings.advertisements, AdvertisementConfig)
    assert isinstance(settings.advertisements.routes[0], AdvertisementRouteConfig)
    assert settings.telegram.user.session_path == Path(
        "var/sessions/source_account.session"
    )
    assert isinstance(
        loaded.secrets.get(settings.telegram.bot.token),
        SecretStr,
    )

    with pytest.raises(ValidationError):
        _assign_attribute(settings.logging, "level", LogLevel.ERROR)
    with pytest.raises(FrozenInstanceError):
        _assign_attribute(loaded, "settings", settings)


def test_missing_file_has_a_specific_safe_error(tmp_path: Path) -> None:
    """A missing path fails before parsing or environment access."""
    missing_path = tmp_path / "missing-configuration.json"

    with pytest.raises(ConfigurationFileNotFoundError) as captured:
        load_configuration(missing_path, environ=_FailOnAccessMapping())

    assert captured.value.path == missing_path
    assert str(missing_path) in str(captured.value)


def test_other_filesystem_failures_are_safely_classified(tmp_path: Path) -> None:
    """Unexpected filesystem errors do not expose platform exception details."""
    path = tmp_path / "configuration.json"
    platform_detail = "sensitive-operating-system-detail"

    with (
        patch.object(Path, "read_text", side_effect=OSError(platform_detail)),
        pytest.raises(ConfigurationReadError) as captured,
    ):
        load_configuration(path, environ=_FailOnAccessMapping())

    assert captured.value.path == path
    assert platform_detail not in str(captured.value)
    assert platform_detail not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_invalid_utf8_reports_only_a_safe_byte_offset(tmp_path: Path) -> None:
    """Strict UTF-8 decoding rejects undecodable bytes without echoing them."""
    path = tmp_path / "configuration.json"
    path.write_bytes(b'{"configuration_schema_version": 1, "text": "\xff"}')

    with pytest.raises(ConfigurationEncodingError) as captured:
        load_configuration(path, environ=_FailOnAccessMapping())

    assert captured.value.path == path
    assert captured.value.byte_offset > 0
    assert "UTF-8" in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_malformed_json_reports_coordinates_without_source_text(tmp_path: Path) -> None:
    """JSON syntax errors retain coordinates but redact the malformed document."""
    path = tmp_path / "configuration.json"
    source_sentinel = "source-value-must-not-appear"
    path.write_text(
        '{"configuration_schema_version": 1, "field": '
        f'"{source_sentinel}", "broken": }}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationJsonError) as captured:
        load_configuration(path, environ=_FailOnAccessMapping())

    assert captured.value.line == 1
    assert captured.value.column > 0
    assert source_sentinel not in str(captured.value)
    assert source_sentinel not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "raw_document",
    [
        '{"configuration_schema_version":1,"configuration_schema_version":1}',
        '{"nested":{"name":1,"name":2}}',
    ],
)
def test_duplicate_json_keys_are_rejected(
    tmp_path: Path,
    raw_document: str,
) -> None:
    """Duplicate keys cannot silently override security-sensitive settings."""
    path = tmp_path / "configuration.json"
    path.write_text(raw_document, encoding="utf-8")

    with pytest.raises(ConfigurationJsonError) as captured:
        load_configuration(path, environ=_FailOnAccessMapping())

    assert captured.value.line == 1
    assert captured.value.column == 1
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("non_finite_number", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_numbers_are_rejected(
    tmp_path: Path,
    non_finite_number: str,
) -> None:
    """The loader accepts standard JSON rather than Python numeric extensions."""
    path = tmp_path / "configuration.json"
    path.write_text(
        f'{{"configuration_schema_version":1,"number":{non_finite_number}}}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationJsonError) as captured:
        load_configuration(path, environ=_FailOnAccessMapping())

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("raw_root", ["[]", '"text"', "123", "true", "null"])
def test_json_root_must_be_an_object(tmp_path: Path, raw_root: str) -> None:
    """Every valid non-object JSON root receives a distinct root error."""
    path = tmp_path / "configuration.json"
    path.write_text(raw_root, encoding="utf-8")

    with pytest.raises(ConfigurationRootError) as captured:
        load_configuration(path, environ=_FailOnAccessMapping())

    assert captured.value.path == path


@pytest.mark.parametrize("unsupported_version", [0, 2, -1, 999])
def test_unknown_integer_schema_version_fails_before_other_validation(
    valid_payload: JsonObject,
    configuration_writer: ConfigurationWriter,
    unsupported_version: int,
) -> None:
    """Unknown versions fail fast without model validation or secret access."""
    valid_payload["configuration_schema_version"] = unsupported_version
    valid_payload.pop("mongodb")
    path = configuration_writer(valid_payload)

    with pytest.raises(UnsupportedConfigurationSchemaVersionError) as captured:
        load_configuration(path, environ=_FailOnAccessMapping())

    assert captured.value.supported_version == SUPPORTED_CONFIGURATION_SCHEMA_VERSION
    assert str(unsupported_version) not in str(captured.value)


@pytest.mark.parametrize("invalid_version", ["1", True, 1.0, None])
def test_schema_version_rejects_non_integer_types_once(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    invalid_version: object,
) -> None:
    """A non-integer schema value has one stable path and one reported issue."""
    valid_payload["configuration_schema_version"] = invalid_version
    path = configuration_writer(valid_payload)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(path, environ=synthetic_environ)

    matching = [
        issue
        for issue in captured.value.issues
        if issue.formatted_path == "configuration_schema_version"
    ]
    assert len(matching) == 1
    assert matching[0].code == "invalid_type"


def test_missing_schema_version_is_a_typed_validation_issue(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """A missing version is reported at the public schema-version path."""
    valid_payload.pop("configuration_schema_version")
    path = configuration_writer(valid_payload)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(path, environ=synthetic_environ)

    assert [issue.formatted_path for issue in captured.value.issues] == [
        "configuration_schema_version"
    ]
    assert captured.value.issues[0].code == "missing"


def test_loading_does_not_touch_session_path_or_network(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parsing remains local and never opens a session or external connection."""
    session_path = tmp_path / "runtime" / "absent-account.session"
    telegram = _as_object(valid_payload["telegram"])
    user = _as_object(telegram["user"])
    user["session_path"] = str(session_path)
    path = configuration_writer(valid_payload)

    def reject_network(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("configuration loading attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)

    loaded = load_configuration(path, environ=synthetic_environ)

    assert loaded.settings.telegram.user.session_path == session_path
    assert not session_path.exists()
