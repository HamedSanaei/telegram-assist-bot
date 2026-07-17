"""Unit tests for environment-only secret resolution and redaction."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr

from telegram_assist_bot.shared.config import (
    ConfigurationValidationError,
    SecretReference,
    load_configuration,
)

type JsonObject = dict[str, object]
type ConfigurationWriter = Callable[[Mapping[str, object]], Path]


class _RecordingEnvironment(Mapping[str, str]):
    """Record direct key lookups and reject broad environment iteration."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)
        self.requested: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.requested.append(key)
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("the complete environment must not be iterated")

    def __len__(self) -> int:
        raise AssertionError("the complete environment size must not be read")


def _as_object(value: object) -> JsonObject:
    assert isinstance(value, dict)
    return cast("JsonObject", value)


def _as_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast("list[object]", value)


def _secret_references(payload: JsonObject) -> dict[str, str]:
    mongodb = _as_object(payload["mongodb"])
    telegram = _as_object(payload["telegram"])
    telegram_user = _as_object(telegram["user"])
    telegram_bot = _as_object(telegram["bot"])
    ai = _as_object(payload["ai"])
    providers = _as_list(ai["providers"])
    res = {
        "mongodb.uri": _environment_name(mongodb["uri"]),
        "telegram.user.api_id": _environment_name(telegram_user["api_id"]),
        "telegram.user.api_hash": _environment_name(telegram_user["api_hash"]),
        "telegram.user.phone_number": _environment_name(telegram_user["phone_number"]),
        "telegram.bot.token": _environment_name(telegram_bot["token"]),
    }
    for idx, provider_val in enumerate(providers):
        provider = _as_object(provider_val)
        if "api_key" in provider and provider["api_key"] is not None:
            res[f"ai.providers[{idx}].api_key"] = _environment_name(provider["api_key"])
    return res


def _environment_name(value: object) -> str:
    reference = _as_object(value)
    name = reference["environment_variable"]
    assert isinstance(name, str)
    return name


def _paths(error: ConfigurationValidationError) -> set[str]:
    return {issue.formatted_path for issue in error.issues}


def _redaction_sentinel() -> str:
    return "resolved-value-must-stay-private"


def _assign_attribute(target: object, name: str, value: object) -> None:
    setattr(target, name, value)


def _write_configuration(
    directory: Path,
    payload: Mapping[str, object],
    filename: str,
) -> Path:
    """Write one UTF-8 configuration fixture under an explicit filename."""
    path = directory / filename
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _replace_with_inline_secrets(payload: JsonObject) -> tuple[str, ...]:
    """Replace every supported reference with a distinct direct fixture value."""
    mongodb = _as_object(payload["mongodb"])
    telegram = _as_object(payload["telegram"])
    telegram_user = _as_object(telegram["user"])
    telegram_bot = _as_object(telegram["bot"])
    providers = _as_list(_as_object(payload["ai"])["providers"])
    provider0 = _as_object(providers[0])
    provider1 = _as_object(providers[1])
    mongodb_value = "mongodb://inline.example.invalid:27017"
    api_id_value = "123456"
    hash_value = "fixture-one"
    phone_value = "+989120000000"
    bot_value = "inline-bot-credential"
    provider0_value = "fixture-two"
    provider1_value = "fixture-three"
    hash_field = next(name for name in telegram_user if name.endswith("hash"))
    bot_field = next(name for name in telegram_bot if name.endswith("ken"))
    provider0_field = next(name for name in provider0 if name.endswith("_key"))
    provider1_field = next(name for name in provider1 if name.endswith("_key"))
    mongodb["uri"] = mongodb_value
    telegram_user["api_id"] = int(api_id_value)
    telegram_user[hash_field] = hash_value
    telegram_user["phone_number"] = phone_value
    telegram_bot[bot_field] = bot_value
    provider0[provider0_field] = provider0_value
    provider1[provider1_field] = provider1_value
    return (
        mongodb_value,
        api_id_value,
        hash_value,
        phone_value,
        bot_value,
        provider0_value,
        provider1_value,
    )


def test_resolves_every_reference_into_a_separate_redacting_store(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """All references resolve explicitly while typed settings retain only names."""
    loaded = load_configuration(
        configuration_writer(valid_payload),
        environ=synthetic_environ,
    )
    expected_references = _secret_references(valid_payload)

    assert len(loaded.secrets) == len(expected_references)
    for environment_variable in expected_references.values():
        protected = loaded.secrets.get(environment_variable)
        assert isinstance(protected, SecretStr)
        assert protected.get_secret_value() == synthetic_environ[environment_variable]
        assert environment_variable in loaded.secrets

    token_reference = loaded.settings.telegram.bot.token
    assert isinstance(token_reference, SecretReference)
    assert token_reference in loaded.secrets
    assert (
        loaded.secrets.get(token_reference).get_secret_value()
        == synthetic_environ[token_reference.environment_variable]
    )


def test_local_configuration_resolves_direct_secrets_without_environment(
    valid_payload: JsonObject,
    tmp_path: Path,
) -> None:
    """Allow every supported direct secret only in an explicit local profile."""
    values = _replace_with_inline_secrets(valid_payload)
    loaded = load_configuration(
        _write_configuration(tmp_path, valid_payload, "configuration.local.json"),
        environ={},
    )
    settings = loaded.settings
    provider0 = settings.ai.providers[0]
    provider1 = settings.ai.providers[1]
    assert provider0.api_key is not None
    assert provider1.api_key is not None
    resolved = (
        loaded.secrets.get(settings.mongodb.uri).get_secret_value(),
        loaded.secrets.get(settings.telegram.user.api_id).get_secret_value(),
        loaded.secrets.get(settings.telegram.user.api_hash).get_secret_value(),
        loaded.secrets.get(settings.telegram.user.phone_number).get_secret_value(),
        loaded.secrets.get(settings.telegram.bot.token).get_secret_value(),
        loaded.secrets.get(provider0.api_key).get_secret_value(),
        loaded.secrets.get(provider1.api_key).get_secret_value(),
    )

    assert resolved == values
    rendered = (str(loaded), repr(loaded), str(loaded.secrets), repr(loaded.secrets))
    assert all(
        value not in rendered_value for value in values for rendered_value in rendered
    )


@pytest.mark.parametrize(
    "filename",
    ["configuration.json", "configuration.example.json", "custom.json"],
)
def test_inline_secrets_are_rejected_outside_local_configuration_profiles(
    valid_payload: JsonObject,
    tmp_path: Path,
    filename: str,
) -> None:
    """Prevent plaintext credentials in committed or arbitrary configurations."""
    sentinel = "direct-secret-must-not-leak"
    telegram = _as_object(valid_payload["telegram"])
    _as_object(telegram["bot"])["token"] = sentinel

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            _write_configuration(tmp_path, valid_payload, filename),
            environ={},
        )

    assert any(
        issue.code == "inline_secret_not_allowed" for issue in captured.value.issues
    )
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_id", "123456"),
        ("token", True),
    ],
)
def test_local_inline_secrets_reject_invalid_scalar_types(
    valid_payload: JsonObject,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    """Keep direct secret input strict instead of coercing JSON values."""
    telegram = _as_object(valid_payload["telegram"])
    section = _as_object(telegram["user" if field == "api_id" else "bot"])
    section[field] = value

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            _write_configuration(tmp_path, valid_payload, "configuration.local.json"),
            environ={},
        )

    assert any(issue.code == "invalid_inline_secret" for issue in captured.value.issues)


def test_empty_local_inline_secret_is_rejected_without_echoing_value(
    valid_payload: JsonObject,
    tmp_path: Path,
) -> None:
    """Reject blank direct credentials without exposing a synthetic binding name."""
    telegram = _as_object(valid_payload["telegram"])
    _as_object(telegram["bot"])["token"] = ""

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            _write_configuration(tmp_path, valid_payload, "configuration.local.json"),
            environ={},
        )

    issue = next(
        item
        for item in captured.value.issues
        if item.formatted_path == "telegram.bot.token"
    )
    assert issue.code == "empty_secret"
    assert "TAB_INLINE_SECRET" not in str(captured.value)


def test_local_configuration_keeps_optional_provider_api_key_null(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    tmp_path: Path,
) -> None:
    """Preserve the existing optional-null behavior for disabled providers."""
    provider = _as_object(_as_list(_as_object(valid_payload["ai"])["providers"])[0])
    provider["enabled"] = False
    provider["api_key"] = None

    loaded = load_configuration(
        _write_configuration(tmp_path, valid_payload, "configuration.local.json"),
        environ=synthetic_environ,
    )

    assert loaded.settings.ai.providers[0].api_key is None


def test_environment_is_read_only_for_declared_reference_names(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Resolution never copies or iterates unrelated process environment data."""
    environment = _RecordingEnvironment(
        {
            **synthetic_environ,
            "UNRELATED_PRIVATE_VALUE": _redaction_sentinel(),
        }
    )

    load_configuration(
        configuration_writer(valid_payload),
        environ=environment,
    )

    assert set(environment.requested) == set(_secret_references(valid_payload).values())
    assert "UNRELATED_PRIVATE_VALUE" not in environment.requested


def test_resolved_values_are_snapshotted_and_immutable(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Later environment mutation cannot alter the startup configuration."""
    environment = synthetic_environ.copy()
    loaded = load_configuration(
        configuration_writer(valid_payload),
        environ=environment,
    )
    token_reference = loaded.settings.telegram.bot.token
    original_value = synthetic_environ[token_reference.environment_variable]

    environment[token_reference.environment_variable] = "changed-after-startup"

    assert loaded.secrets.get(token_reference).get_secret_value() == original_value
    with pytest.raises(FrozenInstanceError):
        _assign_attribute(loaded.secrets, "_values", {})


def test_explicit_empty_environment_never_falls_back_to_process_environment(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An injected empty mapping remains empty even when os.environ has values."""
    for name, value in synthetic_environ.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(configuration_writer(valid_payload), environ={})

    expected_paths = set(_secret_references(valid_payload))
    assert expected_paths == _paths(captured.value)
    assert {issue.code for issue in captured.value.issues} == {"missing_secret"}


def test_multiple_missing_and_empty_secrets_are_aggregated(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Every missing or blank required secret is reported in one startup error."""
    environment = synthetic_environ.copy()
    environment.pop("TAB_MONGODB_URI")
    environment.pop("TAB_TELEGRAM_BOT_TOKEN")
    environment["TAB_TELEGRAM_API_HASH"] = "   "

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=environment,
        )

    assert {
        "mongodb.uri",
        "telegram.user.api_hash",
        "telegram.bot.token",
    } == _paths(captured.value)
    codes_by_path = {
        issue.formatted_path: issue.code for issue in captured.value.issues
    }
    assert codes_by_path == {
        "mongodb.uri": "missing_secret",
        "telegram.user.api_hash": "empty_secret",
        "telegram.bot.token": "missing_secret",
    }
    assert "TAB_MONGODB_URI" in str(captured.value)
    assert "TAB_TELEGRAM_API_HASH" in str(captured.value)
    assert "TAB_TELEGRAM_BOT_TOKEN" in str(captured.value)


@pytest.mark.parametrize("blank_value", ["", " ", "\t\r\n"])
def test_blank_secret_values_are_rejected_without_echoing_them(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    blank_value: str,
) -> None:
    """Empty and whitespace-only environment values never become credentials."""
    environment = synthetic_environ.copy()
    environment["TAB_TELEGRAM_BOT_TOKEN"] = blank_value

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=environment,
        )

    assert _paths(captured.value) == {"telegram.bot.token"}
    assert captured.value.issues[0].code == "empty_secret"


def test_secret_format_errors_are_aggregated_and_values_are_redacted(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Mongo URI and API ID validation never repeats their invalid values."""
    sentinel = _redaction_sentinel()
    environment = synthetic_environ.copy()
    environment["TAB_MONGODB_URI"] = sentinel
    environment["TAB_TELEGRAM_API_ID"] = sentinel

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=environment,
        )

    assert _paths(captured.value) == {
        "mongodb.uri",
        "telegram.user.api_id",
    }
    assert {issue.code for issue in captured.value.issues} == {"invalid_secret"}
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


@pytest.mark.parametrize(
    "direct_secret_shape",
    [
        "direct-value-must-not-appear",
        {"value": "direct-value-must-not-appear"},
    ],
)
def test_direct_secret_values_in_json_are_rejected_and_hidden(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    direct_secret_shape: object,
) -> None:
    """Only environment references are accepted, never inline credentials."""
    telegram = _as_object(valid_payload["telegram"])
    telegram_bot = _as_object(telegram["bot"])
    telegram_bot["token"] = direct_secret_shape

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert any(
        issue.formatted_path.startswith("telegram.bot.token")
        for issue in captured.value.issues
    )
    assert "direct-value-must-not-appear" not in str(captured.value)
    assert "direct-value-must-not-appear" not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "invalid_environment_name",
    ["lowercase_name", "HAS-HYPHEN", "1STARTS_WITH_NUMBER", ""],
)
def test_environment_reference_names_follow_a_portable_contract(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    invalid_environment_name: str,
) -> None:
    """Invalid reference names fail typed validation before environment access."""
    telegram = _as_object(valid_payload["telegram"])
    telegram_bot = _as_object(telegram["bot"])
    token_reference = _as_object(telegram_bot["token"])
    token_reference["environment_variable"] = invalid_environment_name

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "telegram.bot.token.environment_variable" in _paths(captured.value)


def test_successful_loaded_configuration_redacts_sentinel_in_every_representation(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Settings, loaded result, secret container, and SecretStr stay redacted."""
    sentinel = _redaction_sentinel()
    environment = synthetic_environ.copy()
    environment["TAB_TELEGRAM_BOT_TOKEN"] = sentinel
    loaded = load_configuration(
        configuration_writer(valid_payload),
        environ=environment,
    )
    protected = loaded.secrets.get(loaded.settings.telegram.bot.token)
    rendered_values = (
        str(loaded),
        repr(loaded),
        str(loaded.settings),
        repr(loaded.settings),
        str(loaded.secrets),
        repr(loaded.secrets),
        str(protected),
        repr(protected),
    )

    assert protected.get_secret_value() == sentinel
    assert all(sentinel not in rendered for rendered in rendered_values)
    assert "redacted" in repr(loaded).lower()
    assert "redacted" in repr(loaded.secrets).lower()


def test_resolved_sentinel_does_not_leak_when_semantic_validation_fails(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Validation after resolution still reports only safe paths and messages."""
    sentinel = _redaction_sentinel()
    environment = synthetic_environ.copy()
    environment["TAB_TELEGRAM_BOT_TOKEN"] = sentinel
    admins = _as_list(valid_payload["admins"])
    duplicate_admin = _as_object(deepcopy(admins[0]))
    duplicate_admin["name"] = "second-admin"
    admins.append(duplicate_admin)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=environment,
        )

    assert "admins[1].telegram_user_id" in _paths(captured.value)
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
