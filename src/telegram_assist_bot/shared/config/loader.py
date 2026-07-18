"""Load and validate an immutable application configuration snapshot."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from json import JSONDecodeError
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import urlsplit

from pydantic import SecretStr, TypeAdapter, ValidationError

from telegram_assist_bot.shared.config.errors import (
    ConfigurationEncodingError,
    ConfigurationFileNotFoundError,
    ConfigurationIssue,
    ConfigurationJsonError,
    ConfigurationPath,
    ConfigurationReadError,
    ConfigurationRootError,
    ConfigurationValidationError,
    UnsupportedConfigurationSchemaVersionError,
)
from telegram_assist_bot.shared.config.models import (
    SUPPORTED_CONFIGURATION_SCHEMA_VERSION,
    AiTask,
    ApplicationConfig,
    SecretReference,
)

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA_VERSION_FIELD: Final[str] = "configuration_schema_version"
_MISSING: Final[object] = object()
_INLINE_SECRET_PREFIX: Final[str] = "TAB_INLINE_SECRET_"  # noqa: S105


class _NonStandardJsonError(ValueError):
    """Signal a duplicate key or a non-finite JSON number internally."""


type SecretValidator = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class _SecretBinding:
    """Associate one secret reference with its safe configuration path."""

    reference: SecretReference
    path: ConfigurationPath
    validator: SecretValidator | None = None
    invalid_message: str = "secret value has an invalid format"


@dataclass(frozen=True, slots=True, init=False)
class ResolvedSecrets:
    """Store a private immutable snapshot of resolved startup secrets."""

    _values: Mapping[str, SecretStr] = field(repr=False)

    def __init__(self, values: Mapping[str, str]) -> None:
        """Copy plaintext values into redacting wrappers and freeze the mapping."""
        protected_values = {
            name: SecretStr(secret_value) for name, secret_value in values.items()
        }
        object.__setattr__(self, "_values", MappingProxyType(protected_values))

    def get(self, reference: SecretReference | str) -> SecretStr:
        """Return one redacted secret through an explicit accessor."""
        environment_variable = (
            reference.environment_variable
            if isinstance(reference, SecretReference)
            else reference
        )
        return self._values[environment_variable]

    def __contains__(self, reference: object) -> bool:
        """Return whether a reference or environment-variable name was resolved."""
        if isinstance(reference, SecretReference):
            return reference.environment_variable in self._values
        if isinstance(reference, str):
            return reference in self._values
        return False

    def __len__(self) -> int:
        """Return the number of distinct resolved environment variables."""
        return len(self._values)

    def __repr__(self) -> str:
        """Never expose values when the container is inspected or logged."""
        return f"ResolvedSecrets(count={len(self)}, values=<redacted>)"


@dataclass(frozen=True, slots=True)
class LoadedConfiguration:
    """Combine typed settings with their separately protected secret snapshot."""

    settings: ApplicationConfig
    secrets: ResolvedSecrets = field(repr=False)

    def __repr__(self) -> str:
        """Render settings and an explicit redaction marker, never secret values."""
        return f"LoadedConfiguration(settings={self.settings!r}, secrets=<redacted>)"


def _reject_non_finite_number(_value: str) -> object:
    """Reject JavaScript numeric extensions that are not valid JSON."""
    raise _NonStandardJsonError


def _reject_duplicate_object_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject duplicate JSON object keys without displaying their contents."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _NonStandardJsonError
        result[key] = value
    return result


def _read_configuration_text(path: Path) -> str:
    """Read a configuration document as strict UTF-8 with safe failures."""
    text: str | None = None
    failure: tuple[str, int | None] | None = None
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        failure = ("missing", None)
    except UnicodeDecodeError as error:
        failure = ("encoding", error.start)
    except OSError:
        failure = ("read", None)

    if failure is not None:
        failure_kind, byte_offset = failure
        if failure_kind == "missing":
            raise ConfigurationFileNotFoundError(path)
        if failure_kind == "encoding":
            if byte_offset is None:
                raise AssertionError("encoding failure requires a byte offset")
            raise ConfigurationEncodingError(path, byte_offset)
        raise ConfigurationReadError(path)
    if text is None:
        raise AssertionError("configuration read produced no result")
    return text


def _decode_configuration_document(path: Path, text: str) -> dict[str, object]:
    """Decode strict JSON and require an object at the document root."""
    decoded: object = _MISSING
    invalid_position: tuple[int, int] | None = None
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_non_finite_number,
        )
    except JSONDecodeError as error:
        invalid_position = (error.lineno, error.colno)
    except _NonStandardJsonError:
        invalid_position = (1, 1)

    if invalid_position is not None:
        line, column = invalid_position
        raise ConfigurationJsonError(path, line, column)

    if not isinstance(decoded, dict):
        raise ConfigurationRootError(path)
    return cast("dict[str, object]", decoded)


def _validate_schema_version(document: Mapping[str, object]) -> None:
    """Fail fast only when an unknown integer schema version is explicit."""
    value = document.get(_SCHEMA_VERSION_FIELD, _MISSING)
    if (
        type(value) is int  # Exact type deliberately rejects bool-as-int.
        and value != SUPPORTED_CONFIGURATION_SCHEMA_VERSION
    ):
        raise UnsupportedConfigurationSchemaVersionError(
            SUPPORTED_CONFIGURATION_SCHEMA_VERSION
        )


def _pydantic_issues(error: ValidationError) -> list[ConfigurationIssue]:
    """Convert provider-owned validation details into safe application issues."""
    issues: list[ConfigurationIssue] = []
    for detail in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = tuple(detail["loc"])
        code = str(detail["type"])
        message = str(detail["msg"])
        if location == (_SCHEMA_VERSION_FIELD,) and code == "value_error":
            code = "invalid_type"
            message = "schema version must be an integer"
        issues.append(
            ConfigurationIssue(
                path=location,
                code=code,
                message=message,
            )
        )
    return issues


def _validated_or_none[ItemT](
    adapter: TypeAdapter[ItemT],
    value: object,
) -> ItemT | None:
    """Return a typed section when independently valid, otherwise ``None``."""
    with suppress(ValidationError):
        return adapter.validate_python(value)
    return None


def _indexed_objects(
    value: object,
) -> tuple[tuple[int, Mapping[str, object]], ...]:
    """Return valid object members of a decoded JSON array with original indices."""
    if not isinstance(value, list):
        return ()
    return tuple(
        (index, cast("dict[str, object]", item))
        for index, item in enumerate(value)
        if isinstance(item, dict)
    )


def _raw_non_blank_string(
    item: Mapping[str, object],
    field_name: str,
) -> object:
    """Read one valid identity string without altering Unicode contents."""
    value = item.get(field_name, _MISSING)
    if isinstance(value, str) and value and not value.isspace():
        return value
    return _MISSING


def _raw_integer(
    item: Mapping[str, object],
    field_name: str,
) -> object:
    """Read one exact JSON integer while rejecting bool-as-int."""
    value = item.get(field_name, _MISSING)
    return value if type(value) is int else _MISSING


def _raw_duplicate_field_issues(
    items: Sequence[tuple[int, Mapping[str, object]]],
    *,
    path_prefix: ConfigurationPath,
    field_name: str,
    value: Callable[[Mapping[str, object]], object],
) -> list[ConfigurationIssue]:
    """Detect duplicate valid identities even beside malformed list members."""
    issues: list[ConfigurationIssue] = []
    seen: set[object] = set()
    for index, item in items:
        current = value(item)
        if current is _MISSING:
            continue
        if current in seen:
            issues.append(
                ConfigurationIssue(
                    path=(*path_prefix, index, field_name),
                    code="duplicate_value",
                    message=f"{field_name} must be unique",
                )
            )
        else:
            seen.add(current)
    return issues


def _raw_reference_issues(
    value: object,
    *,
    path_prefix: ConfigurationPath,
    known_destinations: set[str],
) -> list[ConfigurationIssue]:
    """Validate every well-formed destination reference in a raw list."""
    if not isinstance(value, list):
        return []
    issues: list[ConfigurationIssue] = []
    seen: set[str] = set()
    for index, reference in enumerate(value):
        if not isinstance(reference, str) or not reference or reference.isspace():
            continue
        path = (*path_prefix, index)
        if reference in seen:
            issues.append(
                ConfigurationIssue(
                    path=path,
                    code="duplicate_reference",
                    message="destination reference must be unique",
                )
            )
        else:
            seen.add(reference)
        if reference not in known_destinations:
            issues.append(
                ConfigurationIssue(
                    path=path,
                    code="unknown_destination",
                    message="destination reference does not exist",
                )
            )
    return issues


def _raw_channel_identity_issues(
    source_channels: Sequence[tuple[int, Mapping[str, object]]],
    destination_channels: Sequence[tuple[int, Mapping[str, object]]],
) -> list[ConfigurationIssue]:
    """Detect channel identity collisions across source and destination arrays."""
    issues: list[ConfigurationIssue] = []
    seen_names: set[str] = set()
    seen_ids: set[int] = set()
    channel_sections = (
        ("source_channels", source_channels),
        ("destination_channels", destination_channels),
    )
    for section_name, channels in channel_sections:
        for index, channel in channels:
            name = _raw_non_blank_string(channel, "name")
            if isinstance(name, str):
                if name in seen_names:
                    issues.append(
                        ConfigurationIssue(
                            path=(section_name, index, "name"),
                            code="duplicate_value",
                            message=("channel name must be unique across all channels"),
                        )
                    )
                else:
                    seen_names.add(name)
            channel_id = _raw_integer(channel, "telegram_channel_id")
            if isinstance(channel_id, int):
                if channel_id in seen_ids:
                    issues.append(
                        ConfigurationIssue(
                            path=(section_name, index, "telegram_channel_id"),
                            code="duplicate_value",
                            message=(
                                "telegram_channel_id must be unique across all channels"
                            ),
                        )
                    )
                else:
                    seen_ids.add(channel_id)
    return issues


def _raw_ai_issues(
    ai_value: object,
    features_value: object,
    source_channels_value: object,
    semantic_duplicate_value: object,
) -> list[ConfigurationIssue]:
    """Recover AI identity and reference issues from independently valid fields."""
    if not isinstance(ai_value, dict):
        return []
    issues: list[ConfigurationIssue] = []
    providers = _indexed_objects(ai_value.get("providers", _MISSING))
    issues.extend(
        _raw_duplicate_field_issues(
            providers,
            path_prefix=("ai", "providers"),
            field_name="name",
            value=lambda provider: _raw_non_blank_string(provider, "name"),
        )
    )
    provider_names = {
        name
        for _, provider in providers
        if isinstance((name := _raw_non_blank_string(provider, "name")), str)
    }
    for index, provider in providers:
        if provider.get("enabled") is True and provider.get("api_key") is None:
            issues.append(
                ConfigurationIssue(
                    path=("ai", "providers", index, "api_key"),
                    code="missing_secret_reference",
                    message="enabled provider requires an API-key reference",
                )
            )
        base_url = provider.get("base_url")
        if isinstance(base_url, str):
            try:
                parsed_url = urlsplit(base_url)
            except ValueError:
                parsed_url = None
            if parsed_url is not None and (
                parsed_url.username is not None
                or parsed_url.password is not None
                or bool(parsed_url.query)
                or bool(parsed_url.fragment)
            ):
                issues.append(
                    ConfigurationIssue(
                        path=("ai", "providers", index, "base_url"),
                        code="unsafe_base_url",
                        message=(
                            "provider base URL must not contain credentials, query, "
                            "or fragment"
                        ),
                    )
                )

    def _canonicalize_task_name(task_name: str) -> str:
        if task_name == "duplicate_detection":
            return "semantic_duplicate"
        if task_name == "content_scoring":
            return "scoring"
        return task_name

    routes = _indexed_objects(ai_value.get("routes", _MISSING))
    issues.extend(
        _raw_duplicate_field_issues(
            routes,
            path_prefix=("ai", "routes"),
            field_name="task",
            value=lambda route: (
                _canonicalize_task_name(task_str)
                if isinstance((task_str := _raw_non_blank_string(route, "task")), str)
                else task_str
            ),
        )
    )
    configured_tasks = {
        _canonicalize_task_name(task)
        for _, route in routes
        if isinstance((task := _raw_non_blank_string(route, "task")), str)
    }
    for route_index, route in routes:
        candidates = _indexed_objects(route.get("candidates", _MISSING))
        issues.extend(
            _raw_duplicate_field_issues(
                candidates,
                path_prefix=("ai", "routes", route_index, "candidates"),
                field_name="priority",
                value=lambda candidate: _raw_integer(candidate, "priority"),
            )
        )
        seen_candidates: set[tuple[str, str]] = set()
        for candidate_index, candidate in candidates:
            provider_name = _raw_non_blank_string(candidate, "provider_name")
            model_name = _raw_non_blank_string(candidate, "model_name")
            candidate_path = (
                "ai",
                "routes",
                route_index,
                "candidates",
                candidate_index,
            )
            if isinstance(provider_name, str):
                if provider_name not in provider_names:
                    issues.append(
                        ConfigurationIssue(
                            path=(*candidate_path, "provider_name"),
                            code="unknown_provider",
                            message="AI provider reference does not exist",
                        )
                    )
                if isinstance(model_name, str):
                    identity = (provider_name, model_name)
                    if identity in seen_candidates:
                        issues.append(
                            ConfigurationIssue(
                                path=(*candidate_path, "model_name"),
                                code="duplicate_candidate",
                                message=(
                                    "provider and model pair must be unique in a route"
                                ),
                            )
                        )
                    else:
                        seen_candidates.add(identity)

    if isinstance(features_value, dict):
        sources = _indexed_objects(source_channels_value)
        advertisement_globally_enabled = (
            features_value.get("advertisement_detection_enabled") is True
        )
        advertisement_effectively_enabled = False
        semantic_effectively_enabled = False
        if advertisement_globally_enabled:
            for source_index, source in sources:
                if source.get("enabled") is not True:
                    continue
                per_source = source.get("advertisement_detection_enabled", _MISSING)
                if per_source is _MISSING:
                    issues.append(
                        ConfigurationIssue(
                            path=(
                                "source_channels",
                                source_index,
                                "advertisement_detection_enabled",
                            ),
                            code="missing_feature_policy",
                            message=(
                                "enabled source requires an explicit advertisement "
                                "detection flag"
                            ),
                        )
                    )
                elif per_source is True:
                    advertisement_effectively_enabled = True
        if features_value.get("duplicate_detection_enabled") is True:
            for source_index, source in sources:
                if source.get("enabled") is not True:
                    continue
                per_source = source.get("duplicate_detection_enabled", _MISSING)
                if per_source is _MISSING:
                    issues.append(
                        ConfigurationIssue(
                            path=(
                                "source_channels",
                                source_index,
                                "duplicate_detection_enabled",
                            ),
                            code="missing_feature_policy",
                            message=(
                                "enabled source requires an explicit semantic "
                                "duplicate detection flag"
                            ),
                        )
                    )
                elif per_source is True:
                    semantic_effectively_enabled = True
        required_routes = (
            (
                "advertisement_detection_enabled",
                AiTask.ADVERTISEMENT_DETECTION.value,
                advertisement_effectively_enabled,
            ),
            (
                "duplicate_detection_enabled",
                AiTask.DUPLICATE_DETECTION.value,
                semantic_effectively_enabled,
            ),
            (
                "ai_scoring_enabled",
                AiTask.CONTENT_SCORING.value,
                features_value.get("ai_scoring_enabled") is True,
            ),
            (
                "ai_categorization_enabled",
                AiTask.CATEGORIZATION.value,
                features_value.get("ai_categorization_enabled") is True,
            ),
        )
        for field_name, task, enabled in required_routes:
            canonical_task = _canonicalize_task_name(task)
            if enabled and canonical_task not in configured_tasks:
                issues.append(
                    ConfigurationIssue(
                        path=("features", field_name),
                        code="missing_ai_route",
                        message="enabled feature requires a matching AI route",
                    )
                )
        failure_policies = _indexed_objects(ai_value.get("failure_policies", _MISSING))
        configured_failure_tasks = {
            _canonicalize_task_name(task)
            for _, policy in failure_policies
            if isinstance((task := _raw_non_blank_string(policy, "task")), str)
        }
        if (
            advertisement_effectively_enabled
            and AiTask.ADVERTISEMENT_DETECTION.value not in configured_failure_tasks
        ):
            issues.append(
                ConfigurationIssue(
                    path=("ai", "failure_policies"),
                    code="missing_failure_policy",
                    message=(
                        "enabled advertisement detection requires an explicit "
                        "failure policy"
                    ),
                )
            )
        if semantic_effectively_enabled:
            if not isinstance(semantic_duplicate_value, dict):
                issues.append(
                    ConfigurationIssue(
                        path=("semantic_duplicate",),
                        code="missing_semantic_policy",
                        message=(
                            "enabled semantic duplicate detection requires explicit "
                            "threshold and duplicate policy"
                        ),
                    )
                )
            if (
                _canonicalize_task_name(AiTask.DUPLICATE_DETECTION.value)
                not in configured_failure_tasks
            ):
                issues.append(
                    ConfigurationIssue(
                        path=("ai", "failure_policies"),
                        code="missing_failure_policy",
                        message=(
                            "enabled semantic duplicate detection requires an "
                            "explicit AI failure policy"
                        ),
                    )
                )
    return issues


def _raw_semantic_issues(
    document: Mapping[str, object],
) -> list[ConfigurationIssue]:
    """Collect cross-field issues even when sibling fields are structurally invalid."""
    issues: list[ConfigurationIssue] = []
    admins = _indexed_objects(document.get("admins", _MISSING))
    issues.extend(
        _raw_duplicate_field_issues(
            admins,
            path_prefix=("admins",),
            field_name="telegram_user_id",
            value=lambda admin: _raw_integer(admin, "telegram_user_id"),
        )
    )
    issues.extend(
        _raw_duplicate_field_issues(
            admins,
            path_prefix=("admins",),
            field_name="name",
            value=lambda admin: _raw_non_blank_string(admin, "name"),
        )
    )

    source_channels = _indexed_objects(document.get("source_channels", _MISSING))
    destination_channels = _indexed_objects(
        document.get("destination_channels", _MISSING)
    )
    issues.extend(_raw_channel_identity_issues(source_channels, destination_channels))
    known_destinations = {
        name
        for _, destination in destination_channels
        if isinstance((name := _raw_non_blank_string(destination, "name")), str)
    }
    for index, admin in admins:
        issues.extend(
            _raw_reference_issues(
                admin.get("allowed_destination_names", _MISSING),
                path_prefix=("admins", index, "allowed_destination_names"),
                known_destinations=known_destinations,
            )
        )
    for index, source in source_channels:
        issues.extend(
            _raw_reference_issues(
                source.get("allowed_destination_names", _MISSING),
                path_prefix=(
                    "source_channels",
                    index,
                    "allowed_destination_names",
                ),
                known_destinations=known_destinations,
            )
        )

    advertisements = document.get("advertisements", _MISSING)
    if isinstance(advertisements, dict):
        routes = _indexed_objects(advertisements.get("routes", _MISSING))
        issues.extend(
            _raw_duplicate_field_issues(
                routes,
                path_prefix=("advertisements", "routes"),
                field_name="name",
                value=lambda route: _raw_non_blank_string(route, "name"),
            )
        )
        for index, route in routes:
            issues.extend(
                _raw_reference_issues(
                    route.get("destination_names", _MISSING),
                    path_prefix=(
                        "advertisements",
                        "routes",
                        index,
                        "destination_names",
                    ),
                    known_destinations=known_destinations,
                )
            )

    issues.extend(
        _raw_ai_issues(
            document.get("ai", _MISSING),
            document.get("features", _MISSING),
            document.get("source_channels", _MISSING),
            document.get("semantic_duplicate", _MISSING),
        )
    )
    issues.extend(_raw_categorization_issues(document))
    return issues


def _raw_categorization_issues(
    document: Mapping[str, object],
) -> list[ConfigurationIssue]:
    """Collect cross-field issues for AI categorization."""
    issues: list[ConfigurationIssue] = []
    features_value = document.get("features", {})
    if not isinstance(features_value, dict):
        return []

    ai_categorization_enabled = features_value.get("ai_categorization_enabled") is True

    categorization_value = document.get("categorization", {})
    if not isinstance(categorization_value, dict):
        if ai_categorization_enabled:
            issues.append(
                ConfigurationIssue(
                    path=("categorization",),
                    code="missing_categorization_config",
                    message=(
                        "ai_categorization_enabled is True but categorization "
                        "config is missing"
                    ),
                )
            )
        return issues

    categories = _indexed_objects(categorization_value.get("categories", _MISSING))
    active_categories = set()
    category_ids = set()

    for index, category in categories:
        cat_id = _raw_non_blank_string(category, "category_id")
        if isinstance(cat_id, str):
            if cat_id in category_ids:
                issues.append(
                    ConfigurationIssue(
                        path=("categorization", "categories", index, "category_id"),
                        code="duplicate_value",
                        message="category_id must be unique across categories",
                    )
                )
            else:
                category_ids.add(cat_id)
            active = category.get("active", True)
            if active is True:
                active_categories.add(cat_id)

    if ai_categorization_enabled:
        if not active_categories:
            issues.append(
                ConfigurationIssue(
                    path=("categorization", "categories"),
                    code="empty_active_taxonomy",
                    message=(
                        "when AI categorization is enabled, at least one active "
                        "category is required in taxonomy"
                    ),
                )
            )

        method_order = categorization_value.get("method_order")
        if method_order is None:
            issues.append(
                ConfigurationIssue(
                    path=("categorization", "method_order"),
                    code="missing_method_order",
                    message=(
                        "when AI categorization is enabled, method_order must be "
                        "explicitly configured"
                    ),
                )
            )

        fallback_policy = categorization_value.get("fallback_policy")
        if fallback_policy is None:
            issues.append(
                ConfigurationIssue(
                    path=("categorization", "fallback_policy"),
                    code="missing_fallback_policy",
                    message=(
                        "when AI categorization is enabled, fallback_policy must "
                        "be explicitly configured"
                    ),
                )
            )
        elif fallback_policy != "fallback_baseline":
            issues.append(
                ConfigurationIssue(
                    path=("categorization", "fallback_policy"),
                    code="invalid_fallback_policy",
                    message="fallback_policy must be fallback_baseline",
                )
            )

    method_order = categorization_value.get("method_order")
    if method_order is not None and isinstance(method_order, (list, tuple)):
        methods = list(method_order)
        if not methods:
            issues.append(
                ConfigurationIssue(
                    path=("categorization", "method_order"),
                    code="empty_method_order",
                    message="method_order must not be empty",
                )
            )
        else:
            if methods[-1] != "source_default":
                issues.append(
                    ConfigurationIssue(
                        path=("categorization", "method_order"),
                        code="invalid_method_order",
                        message=(
                            "source_default must be the final method in method_order"
                        ),
                    )
                )
            if methods.count("source_default") != 1:
                issues.append(
                    ConfigurationIssue(
                        path=("categorization", "method_order"),
                        code="invalid_method_order",
                        message=(
                            "source_default must appear exactly once in method_order"
                        ),
                    )
                )
            if methods.count("ai") > 1:
                issues.append(
                    ConfigurationIssue(
                        path=("categorization", "method_order"),
                        code="invalid_method_order",
                        message="ai method may appear at most once in method_order",
                    )
                )
            if methods.count("keyword") > 1:
                issues.append(
                    ConfigurationIssue(
                        path=("categorization", "method_order"),
                        code="invalid_method_order",
                        message=(
                            "keyword method may appear at most once in method_order"
                        ),
                    )
                )
            issues.extend(
                ConfigurationIssue(
                    path=("categorization", "method_order"),
                    code="invalid_method_order",
                    message=f"unknown method {m} in method_order",
                )
                for m in methods
                if m not in ("ai", "keyword", "source_default")
            )

    source_channels = _indexed_objects(document.get("source_channels", _MISSING))
    for index, source in source_channels:
        default_cat = _raw_non_blank_string(source, "default_category_id")
        if ai_categorization_enabled and not isinstance(default_cat, str):
            issues.append(
                ConfigurationIssue(
                    path=("source_channels", index, "default_category_id"),
                    code="missing_source_default",
                    message=(
                        "enabled AI categorization requires an explicit active "
                        "source default category"
                    ),
                )
            )
        elif isinstance(default_cat, str) and default_cat not in active_categories:
            issues.append(
                ConfigurationIssue(
                    path=("source_channels", index, "default_category_id"),
                    code="inactive_source_default",
                    message=(
                        "source default category must reference an existing active "
                        "category"
                    ),
                )
            )

    keyword_rules = _indexed_objects(
        categorization_value.get("keyword_rules", _MISSING)
    )
    for index, rule in keyword_rules:
        rule_cat = _raw_non_blank_string(rule, "category_id")
        if isinstance(rule_cat, str) and rule_cat not in active_categories:
            issues.append(
                ConfigurationIssue(
                    path=("categorization", "keyword_rules", index, "category_id"),
                    code="inactive_keyword_category",
                    message=(
                        "keyword rule category must reference an existing active "
                        "category"
                    ),
                )
            )

    aliases = categorization_value.get("aliases")
    if isinstance(aliases, dict):
        for alias_key, target_cat_id in aliases.items():
            if not isinstance(alias_key, str) or not alias_key:
                issues.append(
                    ConfigurationIssue(
                        path=("categorization", "aliases"),
                        code="invalid_alias_key",
                        message="alias key must be a non-empty string",
                    )
                )
            if (
                not isinstance(target_cat_id, str)
                or target_cat_id not in active_categories
            ):
                issues.append(
                    ConfigurationIssue(
                        path=("categorization", "aliases", alias_key),
                        code="unknown_alias_target",
                        message=(
                            "alias target category must reference an existing "
                            "active category"
                        ),
                    )
                )

    return issues


def _unique_issues(
    issues: Sequence[ConfigurationIssue],
) -> list[ConfigurationIssue]:
    """Remove duplicate reports produced by typed and recovery validation paths."""
    unique: list[ConfigurationIssue] = []
    seen: set[tuple[ConfigurationPath, str, str]] = set()
    for issue in issues:
        identity = (issue.path, issue.code, issue.message)
        if identity not in seen:
            seen.add(identity)
            unique.append(issue)
    return unique


def _is_positive_integer(value: str) -> bool:
    """Return whether a secret contains a positive base-10 integer."""
    return value.isascii() and value.isdecimal() and int(value, 10) > 0


def _is_mongodb_uri(value: str) -> bool:
    """Validate the minimum safe shape of a MongoDB connection URI locally."""
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"mongodb", "mongodb+srv"} and bool(parsed.hostname)
    except ValueError:
        return False


def _raw_value_at(
    document: Mapping[str, object],
    path: ConfigurationPath,
) -> object:
    """Read one decoded JSON value without coercion or shape exceptions."""
    current: object = document
    for segment in path:
        if isinstance(segment, str):
            if not isinstance(current, dict):
                return _MISSING
            current = current.get(segment, _MISSING)
        else:
            if not isinstance(current, list) or segment < 0 or segment >= len(current):
                return _MISSING
            current = current[segment]
        if current is _MISSING:
            return _MISSING
    return current


def _set_raw_value_at(
    document: dict[str, object],
    path: ConfigurationPath,
    value: object,
) -> bool:
    """Replace one existing decoded JSON value without coercing sibling values."""
    if not path:
        return False
    current: object = document
    for segment in path[:-1]:
        if isinstance(segment, str):
            if not isinstance(current, dict):
                return False
            current = current.get(segment, _MISSING)
        else:
            if not isinstance(current, list) or segment < 0 or segment >= len(current):
                return False
            current = current[segment]
        if current is _MISSING:
            return False
    final_segment = path[-1]
    if isinstance(final_segment, str) and isinstance(current, dict):
        if final_segment not in current:
            return False
        current[final_segment] = value
        return True
    if isinstance(final_segment, int) and isinstance(current, list):
        if final_segment < 0 or final_segment >= len(current):
            return False
        current[final_segment] = value
        return True
    return False


def _is_local_configuration_path(path: Path) -> bool:
    """Return whether a filename is an explicitly local configuration profile."""
    name = path.name
    return name == "configuration.local.json" or (
        name.startswith("configuration.") and name.endswith(".local.json")
    )


def _inline_secret_identifier(path: ConfigurationPath) -> str:
    """Build one deterministic opaque binding identifier from a safe field path."""
    segments = (
        str(segment).upper() if isinstance(segment, int) else segment.upper()
        for segment in path
    )
    return _INLINE_SECRET_PREFIX + "_".join(segments)


def _inline_secret_paths(
    document: Mapping[str, object],
) -> tuple[tuple[ConfigurationPath, bool], ...]:
    """Enumerate secret fields and whether each one requires an integer literal."""
    paths: list[tuple[ConfigurationPath, bool]] = [
        (("mongodb", "uri"), False),
        (("telegram", "user", "api_id"), True),
        (("telegram", "user", "api_hash"), False),
        (("telegram", "user", "phone_number"), False),
        (("telegram", "bot", "token"), False),
    ]
    providers = _raw_value_at(document, ("ai", "providers"))
    if isinstance(providers, list):
        paths.extend(
            (("ai", "providers", index, "api_key"), False)
            for index, provider in enumerate(providers)
            if isinstance(provider, dict) and provider.get("api_key") is not None
        )
    return tuple(paths)


def _normalize_inline_secrets(
    document: dict[str, object],
    path: Path,
) -> tuple[dict[str, str], list[ConfigurationIssue]]:
    """Replace allowed local literals with opaque references before typed parsing."""
    inline_values: dict[str, str] = {}
    issues: list[ConfigurationIssue] = []
    local_configuration = _is_local_configuration_path(path)
    for field_path, requires_integer in _inline_secret_paths(document):
        value = _raw_value_at(document, field_path)
        if value is _MISSING or value is None or isinstance(value, dict):
            continue
        if requires_integer:
            if type(value) is not int:
                issues.append(
                    ConfigurationIssue(
                        path=field_path,
                        code="invalid_inline_secret",
                        message="inline secret must be an integer",
                    )
                )
                continue
            secret_value = str(value)
        else:
            if not isinstance(value, str):
                issues.append(
                    ConfigurationIssue(
                        path=field_path,
                        code="invalid_inline_secret",
                        message="inline secret must be a string",
                    )
                )
                continue
            secret_value = value
        if not local_configuration:
            issues.append(
                ConfigurationIssue(
                    path=field_path,
                    code="inline_secret_not_allowed",
                    message=(
                        "inline secrets are allowed only in local configuration files"
                    ),
                )
            )
        identifier = _inline_secret_identifier(field_path)
        if not _set_raw_value_at(
            document,
            field_path,
            {"environment_variable": identifier},
        ):
            raise AssertionError("inline secret path disappeared during normalization")
        inline_values[identifier] = secret_value
    return inline_values, issues


def _secret_binding_from_document(
    document: Mapping[str, object],
    path: ConfigurationPath,
    *,
    validator: SecretValidator | None = None,
    invalid_message: str = "secret value has an invalid format",
) -> _SecretBinding | None:
    """Recover one independently valid environment reference from raw JSON."""
    reference = _validated_or_none(
        TypeAdapter(SecretReference),
        _raw_value_at(document, path),
    )
    if reference is None:
        return None
    return _SecretBinding(
        reference=reference,
        path=path,
        validator=validator,
        invalid_message=invalid_message,
    )


def _secret_bindings(
    document: Mapping[str, object],
) -> tuple[_SecretBinding, ...]:
    """Enumerate valid secret references independently of sibling fields."""
    specifications: tuple[
        tuple[ConfigurationPath, SecretValidator | None, str], ...
    ] = (
        (
            ("mongodb", "uri"),
            _is_mongodb_uri,
            "secret must contain a valid MongoDB URI",
        ),
        (
            ("telegram", "user", "api_id"),
            _is_positive_integer,
            "secret must contain a positive Telegram API ID",
        ),
        (
            ("telegram", "user", "api_hash"),
            None,
            "secret value has an invalid format",
        ),
        (
            ("telegram", "user", "phone_number"),
            None,
            "secret value has an invalid format",
        ),
        (
            ("telegram", "bot", "token"),
            None,
            "secret value has an invalid format",
        ),
    )
    bindings: list[_SecretBinding] = []
    for path, validator, invalid_message in specifications:
        binding = _secret_binding_from_document(
            document,
            path,
            validator=validator,
            invalid_message=invalid_message,
        )
        if binding is not None:
            bindings.append(binding)

    providers = _raw_value_at(document, ("ai", "providers"))
    if isinstance(providers, list):
        for index, provider in enumerate(providers):
            if not isinstance(provider, dict) or provider.get("api_key") is None:
                continue
            path = ("ai", "providers", index, "api_key")
            binding = _secret_binding_from_document(document, path)
            if binding is not None:
                bindings.append(binding)
    return tuple(bindings)


def _resolve_secrets(
    bindings: Sequence[_SecretBinding],
    environ: Mapping[str, str],
    inline_values: Mapping[str, str],
) -> tuple[ResolvedSecrets, list[ConfigurationIssue]]:
    """Resolve inline or environment secrets and aggregate safe failures."""
    issues: list[ConfigurationIssue] = []
    resolved: dict[str, str] = {}
    looked_up: dict[str, str | None] = {}
    for binding in bindings:
        environment_variable = binding.reference.environment_variable
        value: str | None
        inline_value = inline_values.get(environment_variable)
        if inline_value is not None:
            value = inline_value
            source = "inline"
        else:
            if environment_variable not in looked_up:
                looked_up[environment_variable] = environ.get(environment_variable)
            value = looked_up[environment_variable]
            source = "environment"
        if value is None:
            issues.append(
                ConfigurationIssue(
                    path=binding.path,
                    code="missing_secret",
                    message=(
                        "required environment variable is missing: "
                        f"{environment_variable}"
                    ),
                )
            )
            continue
        if not value or value.isspace():
            message = (
                "required inline secret is empty"
                if source == "inline"
                else f"required environment variable is empty: {environment_variable}"
            )
            issues.append(
                ConfigurationIssue(
                    path=binding.path,
                    code="empty_secret",
                    message=message,
                )
            )
            continue
        if binding.validator is not None and not binding.validator(value):
            issues.append(
                ConfigurationIssue(
                    path=binding.path,
                    code="invalid_secret",
                    message=binding.invalid_message,
                )
            )
            continue
        resolved[environment_variable] = value
    return ResolvedSecrets(resolved), issues


def load_configuration(
    path: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> LoadedConfiguration:
    """Load, validate, and resolve one immutable configuration snapshot.

    Args:
        path: Local JSON configuration path. Session paths inside the document are
            parsed but never opened or canonicalized here.
        environ: Optional injectable secret source. ``None`` snapshots the process
            environment; an empty mapping remains deliberately empty.

    Returns:
        A typed, frozen settings model and a separate redacting secret store.

    Raises:
        ConfigurationError: If reading, parsing, validation, or secret resolution
            fails. No external service is contacted by this operation.
    """
    text = _read_configuration_text(path)
    document = _decode_configuration_document(path, text)
    _validate_schema_version(document)
    inline_values, issues = _normalize_inline_secrets(document, path)
    validation_issues: list[ConfigurationIssue] | None = None
    settings: ApplicationConfig | None = None
    try:
        settings = ApplicationConfig.model_validate(document)
    except ValidationError as error:
        validation_issues = _pydantic_issues(error)

    if validation_issues is not None:
        issues.extend(validation_issues)
    semantic_issues = _raw_semantic_issues(document)
    secret_bindings = _secret_bindings(document)
    resolved_secrets, secret_issues = _resolve_secrets(
        secret_bindings,
        os.environ if environ is None else environ,
        inline_values,
    )
    environ = None
    all_issues = _unique_issues([*issues, *semantic_issues, *secret_issues])
    if all_issues:
        text = ""
        document = {}
        del settings
        del secret_bindings
        del resolved_secrets
        del inline_values
        raise ConfigurationValidationError(all_issues)
    if settings is None:
        raise AssertionError("configuration validation produced no result")
    return LoadedConfiguration(settings=settings, secrets=resolved_secrets)


__all__ = [
    "LoadedConfiguration",
    "ResolvedSecrets",
    "load_configuration",
]
