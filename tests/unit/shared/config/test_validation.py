"""Unit tests for typed and cross-field configuration validation."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from telegram_assist_bot.shared.config import (
    AiTask,
    ConfigurationValidationError,
    LogLevel,
    load_configuration,
)

type JsonObject = dict[str, object]
type ConfigurationWriter = Callable[[Mapping[str, object]], Path]
type JsonPath = tuple[str | int, ...]

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_EXAMPLE_PATH = _REPOSITORY_ROOT / "config" / "configuration.example.json"


def _as_object(value: object) -> JsonObject:
    assert isinstance(value, dict)
    return cast("JsonObject", value)


def _as_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast("list[object]", value)


def _value_at(document: JsonObject, path: Sequence[str | int]) -> object:
    current: object = document
    for segment in path:
        if isinstance(segment, str):
            current = _as_object(current)[segment]
        else:
            current = _as_list(current)[segment]
    return current


def _set_at(document: JsonObject, path: JsonPath, value: object) -> None:
    assert path
    parent = _value_at(document, path[:-1]) if len(path) > 1 else document
    final_segment = path[-1]
    if isinstance(final_segment, str):
        _as_object(parent)[final_segment] = value
    else:
        _as_list(parent)[final_segment] = value


def _paths(error: ConfigurationValidationError) -> set[str]:
    return {issue.formatted_path for issue in error.issues}


def _matching_issues(
    error: ConfigurationValidationError,
    path: str,
) -> list[str]:
    return [issue.code for issue in error.issues if issue.formatted_path == path]


def _assign_attribute(target: object, name: str, value: object) -> None:
    setattr(target, name, value)


def test_structural_errors_are_aggregated_with_exact_paths(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Independent required, type, enum, range, path, and extra errors coexist."""
    _as_object(valid_payload["mongodb"]).pop("database_name")
    _set_at(valid_payload, ("logging", "level"), "VERBOSE")
    _set_at(
        valid_payload,
        ("publishing", "scheduled_publication_interval_seconds"),
        0,
    )
    _set_at(valid_payload, ("timezone",), "Mars/Olympus")
    _set_at(
        valid_payload,
        ("features", "duplicate_detection_enabled"),
        1,
    )
    _set_at(
        valid_payload,
        ("telegram", "user", "session_path"),
        "https://sessions.example.invalid/account.session",
    )
    _as_object(valid_payload["ai"])["unexpected_option"] = True
    path = configuration_writer(valid_payload)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(path, environ=synthetic_environ)

    expected_paths = {
        "mongodb.database_name",
        "logging.level",
        "publishing.scheduled_publication_interval_seconds",
        "timezone",
        "features.duplicate_detection_enabled",
        "telegram.user.session_path",
        "ai.unexpected_option",
    }
    assert expected_paths <= _paths(captured.value)
    for expected_path in expected_paths:
        assert expected_path in str(captured.value)


def test_semantic_and_secret_errors_are_aggregated_together(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """All independent cross-field, reference, and secret issues are returned."""
    admins = _as_list(valid_payload["admins"])
    admins.append(deepcopy(admins[0]))
    first_admin = _as_object(admins[0])
    admin_destinations = _as_list(first_admin["allowed_destination_names"])
    admin_destinations.extend(["destination-fa", "missing-destination"])

    sources = _as_list(valid_payload["source_channels"])
    sources.append(deepcopy(sources[0]))
    first_source = _as_object(sources[0])
    source_destinations = _as_list(first_source["allowed_destination_names"])
    source_destinations.extend(["destination-fa", "missing-destination"])

    destinations = _as_list(valid_payload["destination_channels"])
    destinations.append(deepcopy(destinations[0]))

    advertisements = _as_object(valid_payload["advertisements"])
    advertisement_routes = _as_list(advertisements["routes"])
    advertisement_routes.append(deepcopy(advertisement_routes[0]))
    first_advertisement_route = _as_object(advertisement_routes[0])
    advertisement_destinations = _as_list(
        first_advertisement_route["destination_names"]
    )
    advertisement_destinations.extend(["destination-fa", "missing-destination"])

    ai = _as_object(valid_payload["ai"])
    providers = _as_list(ai["providers"])
    providers.append(deepcopy(providers[0]))
    routes = _as_list(ai["routes"])
    routes.append(deepcopy(routes[0]))
    first_route = _as_object(routes[0])
    candidates = _as_list(first_route["candidates"])
    duplicate_candidate = _as_object(deepcopy(candidates[0]))
    candidates.append(duplicate_candidate)
    unknown_candidate = _as_object(deepcopy(candidates[0]))
    unknown_candidate["provider_name"] = "missing-provider"
    unknown_candidate["model_name"] = "other-model"
    unknown_candidate["priority"] = 1
    candidates.append(unknown_candidate)

    incomplete_environ = synthetic_environ.copy()
    incomplete_environ.pop("TAB_MONGODB_URI")
    incomplete_environ.pop("TAB_TELEGRAM_BOT_TOKEN")
    incomplete_environ["TAB_TELEGRAM_API_HASH"] = "   "
    path = configuration_writer(valid_payload)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(path, environ=incomplete_environ)

    expected_paths = {
        "admins[1].telegram_user_id",
        "admins[1].name",
        "admins[0].allowed_destination_names[1]",
        "admins[0].allowed_destination_names[2]",
        "source_channels[1].name",
        "source_channels[0].allowed_destination_names[1]",
        "source_channels[0].allowed_destination_names[2]",
        "destination_channels[1].telegram_channel_id",
        "destination_channels[1].name",
        "advertisements.routes[1].name",
        "advertisements.routes[0].destination_names[1]",
        "advertisements.routes[0].destination_names[2]",
        "ai.providers[3].name",
        "ai.routes[1].task",
        "ai.routes[0].candidates[1].priority",
        "ai.routes[0].candidates[1].model_name",
        "ai.routes[0].candidates[2].provider_name",
        "mongodb.uri",
        "telegram.user.api_hash",
        "telegram.bot.token",
    }
    assert expected_paths <= _paths(captured.value)
    assert len(captured.value.issues) >= len(expected_paths)

    with pytest.raises(AttributeError):
        _assign_attribute(captured.value, "args", ("mutated",))
    with pytest.raises(FrozenInstanceError):
        _assign_attribute(captured.value.issues[0], "message", "mutated")


def test_structural_semantic_and_secret_phases_aggregate_in_one_error(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """A failure in one top-level section does not hide independent issues."""
    valid_payload["timezone"] = "Mars/Olympus"
    admins = _as_list(valid_payload["admins"])
    duplicate_admin = _as_object(deepcopy(admins[0]))
    duplicate_admin["name"] = "second-admin"
    admins.append(duplicate_admin)
    incomplete_environ = synthetic_environ.copy()
    incomplete_environ.pop("TAB_MONGODB_URI")

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=incomplete_environ,
        )

    assert {
        "timezone",
        "admins[1].telegram_user_id",
        "mongodb.uri",
    } <= _paths(captured.value)


def test_sibling_field_error_does_not_hide_secret_resolution_error(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Secret references remain independently checkable within an invalid section."""
    _as_object(valid_payload["mongodb"])["connect_timeout_seconds"] = 0
    incomplete_environ = synthetic_environ.copy()
    incomplete_environ.pop("TAB_MONGODB_URI")

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=incomplete_environ,
        )

    assert {
        "mongodb.connect_timeout_seconds",
        "mongodb.uri",
    } <= _paths(captured.value)


def test_malformed_list_item_does_not_hide_duplicate_identity(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Valid identity fields remain comparable when a sibling field is invalid."""
    admins = _as_list(valid_payload["admins"])
    duplicate_admin = _as_object(deepcopy(admins[0]))
    duplicate_admin["name"] = "second-admin"
    admins.append(duplicate_admin)
    _as_object(admins[0])["active"] = "not-a-boolean"

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert {
        "admins[0].active",
        "admins[1].telegram_user_id",
    } <= _paths(captured.value)


@pytest.mark.parametrize(
    ("field_path", "invalid_value"),
    [
        (("mongodb", "connect_timeout_seconds"), "10"),
        (("telegram", "bot", "approval_chat_id"), True),
        (("admins", 0, "telegram_user_id"), True),
        (("admins", 0, "active"), 1),
        (("source_channels", 0, "enabled"), "true"),
        (("destination_channels", 0, "enabled"), 1),
        (("features", "advertisement_detection_enabled"), 1),
        (("publishing", "scheduled_publication_interval_seconds"), "300"),
        (("ai", "routes", 0, "candidates", 0, "priority"), True),
        (("ai", "routes", 0, "candidates", 0, "timeout_seconds"), "15"),
        (("ai", "routes", 0, "candidates", 0, "max_attempts"), 2.0),
        (("admins", 0, "name"), 123),
    ],
)
def test_scalar_types_are_strict_without_coercion(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    field_path: JsonPath,
    invalid_value: object,
) -> None:
    """Strings, integers, and booleans cannot be silently coerced."""
    _set_at(valid_payload, field_path, invalid_value)
    path = configuration_writer(valid_payload)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(path, environ=synthetic_environ)

    expected_path = _format_test_path(field_path)
    assert expected_path in _paths(captured.value)


def _format_test_path(path: Sequence[str | int]) -> str:
    rendered = ""
    for segment in path:
        if isinstance(segment, int):
            rendered += f"[{segment}]"
        else:
            rendered += f"{'.' if rendered else ''}{segment}"
    return rendered


@pytest.mark.parametrize("level", list(LogLevel))
def test_every_logging_enum_member_is_typed(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    level: LogLevel,
) -> None:
    """Every supported exact log level round-trips as the enum member."""
    _set_at(valid_payload, ("logging", "level"), level.value)
    loaded = load_configuration(
        configuration_writer(valid_payload),
        environ=synthetic_environ,
    )

    assert loaded.settings.logging.level is level


@pytest.mark.parametrize("invalid_level", ["info", "VERBOSE", 1])
def test_logging_enum_rejects_wrong_case_unknown_and_non_string_values(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    invalid_level: object,
) -> None:
    """Log-level validation is exact and reports the public field path."""
    _set_at(valid_payload, ("logging", "level"), invalid_level)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "logging.level" in _paths(captured.value)


_FEATURE_BY_AI_TASK = {
    AiTask.ADVERTISEMENT_DETECTION: "advertisement_detection_enabled",
    AiTask.DUPLICATE_DETECTION: "duplicate_detection_enabled",
    AiTask.CONTENT_SCORING: "ai_scoring_enabled",
}


@pytest.mark.parametrize("task", list(AiTask))
def test_every_ai_task_enum_member_is_typed_and_route_compatible(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    task: AiTask,
) -> None:
    """Every supported task parses and satisfies its matching enabled feature."""
    from telegram_assist_bot.application.ai.contracts import AITaskType

    canonical_map = {
        AiTask.ADVERTISEMENT_DETECTION: AITaskType.ADVERTISEMENT_DETECTION,
        AiTask.DUPLICATE_DETECTION: AITaskType.SEMANTIC_DUPLICATE,
        AiTask.CONTENT_SCORING: AITaskType.SCORING,
    }

    features = _as_object(valid_payload["features"])
    for feature_name in _FEATURE_BY_AI_TASK.values():
        features[feature_name] = False
    features[_FEATURE_BY_AI_TASK[task]] = True
    _set_at(valid_payload, ("ai", "routes", 0, "task"), task.value)

    loaded = load_configuration(
        configuration_writer(valid_payload),
        environ=synthetic_environ,
    )

    assert loaded.settings.ai.routes[0].task is canonical_map[task]


@pytest.mark.parametrize(
    "invalid_task",
    ["summarization", "ADVERTISEMENT_DETECTION", 1],
)
def test_ai_task_enum_rejects_unknown_wrong_case_and_non_string_values(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    invalid_task: object,
) -> None:
    """AI task names are a strict versioned contract rather than free text."""
    _set_at(valid_payload, ("ai", "routes", 0, "task"), invalid_task)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "ai.routes[0].task" in _paths(captured.value)


@pytest.mark.parametrize(
    ("field_path", "boundary_value"),
    [
        (("mongodb", "connect_timeout_seconds"), 1),
        (("mongodb", "connect_timeout_seconds"), 120),
        (("publishing", "scheduled_publication_interval_seconds"), 1),
        (("ai", "routes", 0, "candidates", 0, "priority"), 0),
        (("ai", "routes", 0, "candidates", 0, "timeout_seconds"), 1),
        (("ai", "routes", 0, "candidates", 0, "timeout_seconds"), 300),
        (("ai", "routes", 0, "candidates", 0, "max_attempts"), 1),
        (("ai", "routes", 0, "candidates", 0, "max_attempts"), 10),
        (("telegram", "bot", "approval_chat_id"), -1),
        (("telegram", "bot", "approval_chat_id"), 1),
        (("admins", 0, "telegram_user_id"), 1),
    ],
)
def test_numeric_range_boundaries_are_accepted(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    field_path: JsonPath,
    boundary_value: int,
) -> None:
    """Documented inclusive numeric boundaries remain valid."""
    _set_at(valid_payload, field_path, boundary_value)

    loaded = load_configuration(
        configuration_writer(valid_payload),
        environ=synthetic_environ,
    )

    assert (
        _value_at(
            cast("JsonObject", loaded.settings.model_dump(mode="json")),
            field_path,
        )
        == boundary_value
    )


@pytest.mark.parametrize(
    ("field_path", "outside_value"),
    [
        (("mongodb", "connect_timeout_seconds"), 0),
        (("mongodb", "connect_timeout_seconds"), 121),
        (("publishing", "scheduled_publication_interval_seconds"), 0),
        (("publishing", "scheduled_publication_interval_seconds"), -1),
        (("ai", "routes", 0, "candidates", 0, "priority"), -1),
        (("ai", "routes", 0, "candidates", 0, "timeout_seconds"), 0),
        (("ai", "routes", 0, "candidates", 0, "timeout_seconds"), 301),
        (("ai", "routes", 0, "candidates", 0, "max_attempts"), 0),
        (("ai", "routes", 0, "candidates", 0, "max_attempts"), 11),
        (("telegram", "bot", "approval_chat_id"), 0),
        (("admins", 0, "telegram_user_id"), 0),
        (("admins", 0, "telegram_user_id"), -1),
        (("destination_channels", 0, "telegram_channel_id"), 0),
    ],
)
def test_numeric_values_outside_ranges_are_rejected(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    field_path: JsonPath,
    outside_value: int,
) -> None:
    """Zero, negative-only violations, and bounded maxima fail safely."""
    _set_at(valid_payload, field_path, outside_value)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert _format_test_path(field_path) in _paths(captured.value)


def test_source_channel_accepts_legacy_numeric_identifier(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Keep existing source configurations compatible during username migration."""
    source = _as_object(_as_list(valid_payload["source_channels"])[0])
    source["telegram_channel_id"] = -1000000000101

    loaded = load_configuration(
        configuration_writer(valid_payload), environ=synthetic_environ
    )

    assert loaded.settings.source_channels[0].telegram_channel_id == -1000000000101


def test_source_channel_requires_username(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Require an address that startup can resolve without manual IDs."""
    source = _as_object(_as_list(valid_payload["source_channels"])[0])
    source.pop("username")

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload), environ=synthetic_environ
        )

    assert "source_channels[0].username" in _paths(captured.value)


@pytest.mark.parametrize("timezone_name", ["Asia/Tehran", "UTC"])
def test_valid_zoneinfo_names_are_accepted(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    timezone_name: str,
) -> None:
    """The required Tehran zone and another IANA zone become ZoneInfo values."""
    valid_payload["timezone"] = timezone_name

    loaded = load_configuration(
        configuration_writer(valid_payload),
        environ=synthetic_environ,
    )

    assert loaded.settings.timezone.key == timezone_name


@pytest.mark.parametrize("invalid_timezone", ["Mars/Olympus", "", "   ", 7])
def test_invalid_zoneinfo_values_are_rejected(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    invalid_timezone: object,
) -> None:
    """Unknown, blank, and non-string timezone values share the exact path."""
    valid_payload["timezone"] = invalid_timezone

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "timezone" in _paths(captured.value)


def _introduce_duplicate(payload: JsonObject, duplicate_case: str) -> None:
    if duplicate_case.startswith("admin_"):
        items = _as_list(payload["admins"])
        duplicate = _as_object(deepcopy(items[0]))
        if duplicate_case == "admin_id":
            duplicate["name"] = "second-admin"
        else:
            duplicate["telegram_user_id"] = 100000002
        items.append(duplicate)
        return
    if duplicate_case.startswith("source_"):
        items = _as_list(payload["source_channels"])
        duplicate = _as_object(deepcopy(items[0]))
        duplicate["username"] = "second-source"
        items.append(duplicate)
        return
    if duplicate_case.startswith("destination_"):
        items = _as_list(payload["destination_channels"])
        duplicate = _as_object(deepcopy(items[0]))
        if duplicate_case == "destination_id":
            duplicate["name"] = "second-destination"
        else:
            duplicate["telegram_channel_id"] = -1000000000202
        items.append(duplicate)
        return
    if duplicate_case.startswith("cross_channel_"):
        source = _as_object(_as_list(payload["source_channels"])[0])
        destination = _as_object(_as_list(payload["destination_channels"])[0])
        field_name = "name"
        destination[field_name] = source[field_name]
        return

    ai = _as_object(payload["ai"])
    if duplicate_case == "provider_name":
        providers = _as_list(ai["providers"])
        providers.append(deepcopy(providers[0]))
        return
    if duplicate_case == "ai_task":
        routes = _as_list(ai["routes"])
        routes.append(deepcopy(routes[0]))
        return
    if duplicate_case.startswith("candidate_"):
        candidate_list = _as_list(_as_object(_as_list(ai["routes"])[0])["candidates"])
        duplicate = _as_object(deepcopy(candidate_list[0]))
        if duplicate_case == "candidate_priority":
            duplicate["model_name"] = "second-model"
        else:
            duplicate["priority"] = 1
        candidate_list.append(duplicate)
        return
    if duplicate_case == "advertisement_name":
        advertisements = _as_object(payload["advertisements"])
        routes = _as_list(advertisements["routes"])
        routes.append(deepcopy(routes[0]))
        return
    raise AssertionError(f"unknown duplicate case: {duplicate_case}")


@pytest.mark.parametrize(
    ("duplicate_case", "expected_path", "expected_code"),
    [
        ("admin_id", "admins[1].telegram_user_id", "duplicate_value"),
        ("admin_name", "admins[1].name", "duplicate_value"),
        ("source_name", "source_channels[1].name", "duplicate_value"),
        (
            "destination_id",
            "destination_channels[1].telegram_channel_id",
            "duplicate_value",
        ),
        (
            "destination_name",
            "destination_channels[1].name",
            "duplicate_value",
        ),
        (
            "cross_channel_name",
            "destination_channels[0].name",
            "duplicate_value",
        ),
        ("provider_name", "ai.providers[3].name", "duplicate_value"),
        ("ai_task", "ai.routes[1].task", "duplicate_value"),
        (
            "candidate_priority",
            "ai.routes[0].candidates[1].priority",
            "duplicate_value",
        ),
        (
            "candidate_pair",
            "ai.routes[0].candidates[1].model_name",
            "duplicate_candidate",
        ),
        (
            "advertisement_name",
            "advertisements.routes[1].name",
            "duplicate_value",
        ),
    ],
)
def test_identifiers_names_routes_and_candidates_are_unique(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    duplicate_case: str,
    expected_path: str,
    expected_code: str,
) -> None:
    """Every identity contract reports the duplicate occurrence after the first."""
    _introduce_duplicate(valid_payload, duplicate_case)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert expected_code in _matching_issues(captured.value, expected_path)


def _introduce_reference_problem(payload: JsonObject, reference_case: str) -> None:
    if reference_case.startswith("admin_"):
        references = _as_list(
            _as_object(_as_list(payload["admins"])[0])["allowed_destination_names"]
        )
    elif reference_case.startswith("source_"):
        references = _as_list(
            _as_object(_as_list(payload["source_channels"])[0])[
                "allowed_destination_names"
            ]
        )
    elif reference_case.startswith("advertisement_"):
        advertisements = _as_object(payload["advertisements"])
        first_route = _as_object(_as_list(advertisements["routes"])[0])
        references = _as_list(first_route["destination_names"])
    elif reference_case == "unknown_provider":
        ai = _as_object(payload["ai"])
        first_route = _as_object(_as_list(ai["routes"])[0])
        first_candidate = _as_object(_as_list(first_route["candidates"])[0])
        first_candidate["provider_name"] = "missing-provider"
        return
    else:
        raise AssertionError(f"unknown reference case: {reference_case}")

    if reference_case.endswith("duplicate"):
        references.append(references[0])
    else:
        references[0] = "missing-destination"


@pytest.mark.parametrize(
    ("reference_case", "expected_path", "expected_code"),
    [
        (
            "admin_unknown",
            "admins[0].allowed_destination_names[0]",
            "unknown_destination",
        ),
        (
            "admin_duplicate",
            "admins[0].allowed_destination_names[1]",
            "duplicate_reference",
        ),
        (
            "source_unknown",
            "source_channels[0].allowed_destination_names[0]",
            "unknown_destination",
        ),
        (
            "source_duplicate",
            "source_channels[0].allowed_destination_names[1]",
            "duplicate_reference",
        ),
        (
            "advertisement_unknown",
            "advertisements.routes[0].destination_names[0]",
            "unknown_destination",
        ),
        (
            "advertisement_duplicate",
            "advertisements.routes[0].destination_names[1]",
            "duplicate_reference",
        ),
        (
            "unknown_provider",
            "ai.routes[0].candidates[0].provider_name",
            "unknown_provider",
        ),
    ],
)
def test_destination_and_provider_references_are_valid_and_unique(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    reference_case: str,
    expected_path: str,
    expected_code: str,
) -> None:
    """Unknown and repeated references cannot pass startup validation."""
    _introduce_reference_problem(valid_payload, reference_case)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert expected_code in _matching_issues(captured.value, expected_path)


@pytest.mark.parametrize(
    ("feature_name", "route_task"),
    [
        ("advertisement_detection_enabled", AiTask.ADVERTISEMENT_DETECTION),
        ("duplicate_detection_enabled", AiTask.DUPLICATE_DETECTION),
        ("ai_scoring_enabled", AiTask.CONTENT_SCORING),
    ],
)
def test_enabled_ai_feature_requires_its_matching_route(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    feature_name: str,
    route_task: AiTask,
) -> None:
    """An enabled AI-backed feature cannot start without an explicit route."""
    features = _as_object(valid_payload["features"])
    for name in _FEATURE_BY_AI_TASK.values():
        features[name] = False
    features[feature_name] = True
    if route_task is AiTask.ADVERTISEMENT_DETECTION:
        _set_at(
            valid_payload,
            ("source_channels", 0, "advertisement_detection_enabled"),
            True,
        )
    ai = _as_object(valid_payload["ai"])
    routes = _as_list(ai["routes"])
    route = _as_object(routes[0])
    route["task"] = next(task.value for task in AiTask if task is not route_task)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "missing_ai_route" in _matching_issues(
        captured.value,
        f"features.{feature_name}",
    )


def test_disabled_advertisement_detection_needs_no_explicit_source_or_policy(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Legacy configurations remain valid while advertisement AI is disabled."""
    source = _as_object(_as_list(valid_payload["source_channels"])[0])
    source.pop("advertisement_detection_enabled")
    ai = _as_object(valid_payload["ai"])
    ai["failure_policies"] = []

    loaded = load_configuration(
        configuration_writer(valid_payload),
        environ=synthetic_environ,
    )

    assert loaded.settings.features.advertisement_detection_enabled is False
    assert loaded.settings.source_channels[0].advertisement_detection_enabled is None


def test_enabled_advertisement_detection_requires_explicit_source_flag(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Global enablement cannot silently opt an enabled source into AI."""
    _set_at(valid_payload, ("features", "advertisement_detection_enabled"), True)
    source = _as_object(_as_list(valid_payload["source_channels"])[0])
    source.pop("advertisement_detection_enabled")

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "missing_feature_policy" in _matching_issues(
        captured.value,
        "source_channels[0].advertisement_detection_enabled",
    )


def test_effective_advertisement_detection_requires_explicit_failure_policy(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Effective advertisement detection has no implicit failure policy."""
    _set_at(valid_payload, ("features", "advertisement_detection_enabled"), True)
    _set_at(
        valid_payload,
        ("source_channels", 0, "advertisement_detection_enabled"),
        True,
    )
    _as_object(valid_payload["ai"])["failure_policies"] = []

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "missing_failure_policy" in _matching_issues(
        captured.value,
        "ai.failure_policies",
    )


@pytest.mark.parametrize(
    "action",
    [
        "continue_processing",
        "stop_processing",
        "retry_later",
        "manual_review",
    ],
)
def test_advertisement_failure_policy_accepts_only_approved_actions(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    action: str,
) -> None:
    """Every approved explicit policy value loads without becoming a default."""
    _set_at(valid_payload, ("features", "advertisement_detection_enabled"), True)
    _set_at(
        valid_payload,
        ("source_channels", 0, "advertisement_detection_enabled"),
        True,
    )
    _set_at(valid_payload, ("ai", "failure_policies", 0, "action"), action)

    loaded = load_configuration(
        configuration_writer(valid_payload),
        environ=synthetic_environ,
    )

    assert loaded.settings.ai.failure_policies[0].action.value == action


def test_unknown_advertisement_failure_policy_is_rejected(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """An enabled route rejects values outside the approved policy set."""
    _set_at(valid_payload, ("features", "advertisement_detection_enabled"), True)
    _set_at(
        valid_payload,
        ("source_channels", 0, "advertisement_detection_enabled"),
        True,
    )
    _set_at(
        valid_payload,
        ("ai", "failure_policies", 0, "action"),
        "implicit_default",
    )

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "ai.failure_policies[0].action" in _paths(captured.value)


def test_enabled_provider_requires_an_api_key_reference(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Enabled providers cannot rely on an implicit or fabricated credential."""
    _set_at(valid_payload, ("ai", "providers", 0, "api_key"), None)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "missing_secret_reference" in _matching_issues(
        captured.value,
        "ai.providers[0].api_key",
    )


def test_disabled_provider_may_omit_an_api_key_reference(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """A disabled provider skeleton does not force an unused credential."""
    _set_at(valid_payload, ("ai", "providers", 0, "enabled"), False)
    _set_at(valid_payload, ("ai", "providers", 0, "api_key"), None)

    loaded = load_configuration(
        configuration_writer(valid_payload),
        environ=synthetic_environ,
    )

    assert loaded.settings.ai.providers[0].api_key is None


def test_provider_base_url_cannot_embed_credentials(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Provider URLs cannot bypass the environment-secret contract."""
    _set_at(
        valid_payload,
        ("ai", "providers", 0, "base_url"),
        "https://user:password@provider.example.invalid/v1",
    )

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "ai.providers[0].base_url" in _paths(captured.value)
    assert "password" not in str(captured.value)


@pytest.mark.parametrize("url_component", ["query", "fragment"])
def test_provider_base_url_cannot_embed_secret_bearing_components(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    url_component: str,
) -> None:
    """Base URL query and fragment data cannot carry inline credentials."""
    sentinel = "url-value-must-stay-private"
    separator = "?" if url_component == "query" else "#"
    key = "access_credential"
    _set_at(
        valid_payload,
        ("ai", "providers", 0, "base_url"),
        f"https://provider.example.invalid/v1{separator}{key}={sentinel}",
    )

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "ai.providers[0].base_url" in _paths(captured.value)
    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)


@pytest.mark.parametrize(
    "invalid_url",
    ["not-a-url", "ftp://provider.example.invalid"],
)
def test_provider_base_url_requires_absolute_http_or_https(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    invalid_url: str,
) -> None:
    """AI base URLs receive strict local shape validation without a request."""
    _set_at(valid_payload, ("ai", "providers", 0, "base_url"), invalid_url)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "ai.providers[0].base_url" in _paths(captured.value)


@pytest.mark.parametrize(
    "invalid_path",
    ["", "   ", "https://sessions.example.invalid/account.session"],
)
def test_session_path_is_non_blank_local_and_not_canonicalized(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
    invalid_path: str,
) -> None:
    """Only local path shape is checked; runtime canonicalization remains deferred."""
    _set_at(valid_payload, ("telegram", "user", "session_path"), invalid_path)

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "telegram.user.session_path" in _paths(captured.value)


def test_unknown_fields_are_rejected_in_nested_models(
    valid_payload: JsonObject,
    synthetic_environ: dict[str, str],
    configuration_writer: ConfigurationWriter,
) -> None:
    """Typos cannot silently weaken a versioned configuration contract."""
    _as_object(valid_payload["mongodb"])["connect_timout_seconds"] = 10

    with pytest.raises(ConfigurationValidationError) as captured:
        load_configuration(
            configuration_writer(valid_payload),
            environ=synthetic_environ,
        )

    assert "mongodb.connect_timout_seconds" in _paths(captured.value)


def test_persian_zwnj_newline_and_emoji_are_preserved_exactly(
    synthetic_environ: dict[str, str],
) -> None:
    """Configuration loading never normalizes Persian or Telegram-facing text."""
    raw_text = _EXAMPLE_PATH.read_text(encoding="utf-8")
    loaded = load_configuration(_EXAMPLE_PATH, environ=synthetic_environ)
    expected_admin_name = "مدیر\u200cنمون\u0647\nشیفت شب 😀"
    expected_source_name = "منبع فارسی 📣"
    expected_advertisement_name = "آگ\u0647ی\u200cنمون\u0647 ✨"

    assert loaded.settings.admins[0].name == expected_admin_name
    assert loaded.settings.source_channels[0].name == expected_source_name
    assert loaded.settings.advertisements.routes[0].name == expected_advertisement_name
    assert "\u200c" in loaded.settings.admins[0].name
    assert "\n" in loaded.settings.admins[0].name
    assert "😀" in loaded.settings.admins[0].name
    assert "مدیر\u200cنمون\u0647\\nشیفت شب 😀" in raw_text
    assert "\\u06" not in raw_text.lower()
    assert "\\ud83d" not in raw_text.lower()

    decoded: object = json.loads(raw_text)
    serialized = json.dumps(decoded, ensure_ascii=False, sort_keys=True)
    round_tripped: object = json.loads(serialized)
    round_tripped_admins = _as_list(_as_object(round_tripped)["admins"])
    round_tripped_admin = _as_object(round_tripped_admins[0])
    assert round_tripped_admin["name"] == expected_admin_name
