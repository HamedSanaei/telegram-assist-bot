"""Typed, locked and rollback-safe mutation of one instance configuration."""

from __future__ import annotations

import copy
import importlib
import json
import os
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from telegram_assist_bot.bootstrap.instance_config import (
    InstanceConfigurationError,
    normalize_source_username,
    parse_admin_user_ids,
    parse_source_usernames,
)
from telegram_assist_bot.shared.config import ApplicationConfig, load_configuration

ConfigDocument = dict[str, Any]
ConfigMutator = Callable[[ConfigDocument], None]


class OperatorConfigError(ValueError):
    """Base class for safe operator configuration failures."""


class ConfigMutationConflictError(OperatorConfigError):
    """Reject a mutation that violates a durable configuration invariant."""


class ConfigTransactionError(OperatorConfigError):
    """Report a failed mutation transaction without exposing configuration data."""


class ServiceController(Protocol):
    """Control only services affected by a committed configuration mutation."""

    def restart(self, services: Sequence[str]) -> None:
        """Restart the named services or raise a safe infrastructure error."""

    def healthy(self, services: Sequence[str]) -> bool:
        """Return whether all named services are running after restart."""


class _InstanceFileLock(AbstractContextManager["_InstanceFileLock"]):
    """Cross-platform advisory lock held for one complete Config transaction."""

    def __init__(self, path: Path, *, timeout_seconds: float = 10.0) -> None:
        self._path = path
        self._timeout_seconds = timeout_seconds
        self._stream: Any = None

    def __enter__(self) -> Self:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self._path.open("a+b")
        self._stream.seek(0)
        if self._stream.tell() == 0:
            self._stream.write(b"\0")
            self._stream.flush()
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    msvcrt = importlib.import_module("msvcrt")
                    self._stream.seek(0)
                    msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl = importlib.import_module("fcntl")
                    fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self._stream.close()
                    raise ConfigTransactionError(
                        "Another configuration transaction is active."
                    ) from None
                time.sleep(0.05)

    def __exit__(self, *args: object) -> None:
        if self._stream is None:
            return
        try:
            if os.name == "nt":
                msvcrt = importlib.import_module("msvcrt")
                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()


def _destination_names(document: ConfigDocument) -> set[str]:
    return {str(item["name"]) for item in document["destination_channels"]}


def add_administrators(
    identifiers_csv: str, *, allowed_destinations: Sequence[str]
) -> ConfigMutator:
    """Build a mutation that adds one or more administrators."""
    identifiers = parse_admin_user_ids(identifiers_csv)

    def mutate(document: ConfigDocument) -> None:
        known_destinations = _destination_names(document)
        if (
            not allowed_destinations
            or not set(allowed_destinations) <= known_destinations
        ):
            raise ConfigMutationConflictError("Administrator destinations are invalid.")
        existing = {int(item["telegram_user_id"]) for item in document["admins"]}
        duplicate = existing.intersection(identifiers)
        if duplicate:
            raise ConfigMutationConflictError(
                f"Administrator identifier {min(duplicate)} already exists."
            )
        for identifier in identifiers:
            document["admins"].append(
                {
                    "name": f"instance-admin-{identifier}",
                    "telegram_user_id": identifier,
                    "active": True,
                    "role": "admin",
                    "permissions": ["approval.view", "approval.toggle"],
                    "allowed_destination_names": list(allowed_destinations),
                    "allowed_destination_ids": [],
                }
            )

    return mutate


def remove_administrator(identifier: int) -> ConfigMutator:
    """Build a mutation that preserves at least one active administrator."""

    def mutate(document: ConfigDocument) -> None:
        admins = document["admins"]
        target = next(
            (item for item in admins if item["telegram_user_id"] == identifier), None
        )
        if target is None:
            raise ConfigMutationConflictError("Administrator does not exist.")
        active_after = sum(
            bool(item["active"])
            for item in admins
            if item["telegram_user_id"] != identifier
        )
        if bool(target["active"]) and active_after == 0:
            raise ConfigMutationConflictError(
                "The last active administrator cannot be removed."
            )
        admins.remove(target)

    return mutate


def set_administrator_active(identifier: int, *, active: bool) -> ConfigMutator:
    """Build an enable/disable mutation with last-active-admin protection."""

    def mutate(document: ConfigDocument) -> None:
        target = next(
            (
                item
                for item in document["admins"]
                if item["telegram_user_id"] == identifier
            ),
            None,
        )
        if target is None:
            raise ConfigMutationConflictError("Administrator does not exist.")
        if target["active"] is active:
            return
        if not active and sum(bool(item["active"]) for item in document["admins"]) == 1:
            raise ConfigMutationConflictError(
                "The last active administrator cannot be disabled."
            )
        target["active"] = active

    return mutate


def set_administrator_destinations(
    identifier: int, destinations: Sequence[str]
) -> ConfigMutator:
    """Replace one administrator's destination authorization safely."""

    def mutate(document: ConfigDocument) -> None:
        if not destinations or not set(destinations) <= _destination_names(document):
            raise ConfigMutationConflictError("Administrator destinations are invalid.")
        target = next(
            (
                item
                for item in document["admins"]
                if item["telegram_user_id"] == identifier
            ),
            None,
        )
        if target is None:
            raise ConfigMutationConflictError("Administrator does not exist.")
        target["allowed_destination_names"] = list(destinations)
        by_name = {
            item["name"]: item["telegram_channel_id"]
            for item in document["destination_channels"]
        }
        target["allowed_destination_ids"] = [by_name[name] for name in destinations]

    return mutate


def add_sources(
    sources_csv: str, *, allowed_destinations: Sequence[str], enabled: bool = True
) -> ConfigMutator:
    """Build a mutation that adds canonical public source channels."""
    usernames = parse_source_usernames(sources_csv)

    def mutate(document: ConfigDocument) -> None:
        if not allowed_destinations or not set(
            allowed_destinations
        ) <= _destination_names(document):
            raise ConfigMutationConflictError("Source destinations are invalid.")
        existing = {
            str(item["username"]).casefold() for item in document["source_channels"]
        }
        duplicate = existing.intersection(username.casefold() for username in usernames)
        if duplicate:
            raise ConfigMutationConflictError("Source username already exists.")
        for username in usernames:
            document["source_channels"].append(
                {
                    "name": username,
                    "username": username,
                    "enabled": enabled,
                    "advertisement_detection_enabled": False,
                    "duplicate_detection_enabled": False,
                    "default_category_id": None,
                    "allowed_destination_names": list(allowed_destinations),
                }
            )

    return mutate


def set_source_active(source: str, *, active: bool) -> ConfigMutator:
    """Build a source enable/disable mutation."""
    key = normalize_source_username(source).casefold()

    def mutate(document: ConfigDocument) -> None:
        target = next(
            (
                item
                for item in document["source_channels"]
                if str(item["username"]).casefold() == key
            ),
            None,
        )
        if target is None:
            raise ConfigMutationConflictError("Source does not exist.")
        target["enabled"] = active

    return mutate


def remove_source(source: str) -> ConfigMutator:
    """Build a mutation that removes routing only, never historical posts."""
    key = normalize_source_username(source).casefold()

    def mutate(document: ConfigDocument) -> None:
        target = next(
            (
                item
                for item in document["source_channels"]
                if str(item["username"]).casefold() == key
            ),
            None,
        )
        if target is None:
            raise ConfigMutationConflictError("Source does not exist.")
        if len(document["source_channels"]) == 1:
            raise ConfigMutationConflictError(
                "At least one source must remain configured."
            )
        document["source_channels"].remove(target)

    return mutate


def configure_source(
    source: str,
    *,
    telegram_channel_id: int | None = None,
    default_category_id: str | None = None,
    allowed_destinations: Sequence[str] | None = None,
) -> ConfigMutator:
    """Update optional source identity, category and routing fields."""
    key = normalize_source_username(source).casefold()

    def mutate(document: ConfigDocument) -> None:
        target = next(
            (
                item
                for item in document["source_channels"]
                if str(item["username"]).casefold() == key
            ),
            None,
        )
        if target is None:
            raise ConfigMutationConflictError("Source does not exist.")
        if telegram_channel_id is not None:
            if telegram_channel_id == 0 or isinstance(telegram_channel_id, bool):
                raise ConfigMutationConflictError(
                    "Source Telegram identifier is invalid."
                )
            conflict = any(
                item is not target
                and item.get("telegram_channel_id") == telegram_channel_id
                for item in document["source_channels"]
            )
            if conflict:
                raise ConfigMutationConflictError(
                    "Source Telegram identifier already exists."
                )
            target["telegram_channel_id"] = telegram_channel_id
        if default_category_id is not None:
            target["default_category_id"] = default_category_id
        if allowed_destinations is not None:
            if not allowed_destinations or not set(
                allowed_destinations
            ) <= _destination_names(document):
                raise ConfigMutationConflictError("Source destinations are invalid.")
            target["allowed_destination_names"] = list(allowed_destinations)

    return mutate


def add_destination(
    *,
    name: str,
    telegram_channel_id: int,
    username: str | None = None,
    enabled: bool = True,
) -> ConfigMutator:
    """Build a destination addition with unique name and Telegram identifier."""

    def mutate(document: ConfigDocument) -> None:
        if not name.strip() or telegram_channel_id == 0:
            raise ConfigMutationConflictError("Destination identity is invalid.")
        if any(
            item["name"] == name or item["telegram_channel_id"] == telegram_channel_id
            for item in document["destination_channels"]
        ):
            raise ConfigMutationConflictError(
                "Destination name or identifier already exists."
            )
        destination: ConfigDocument = {
            "name": name,
            "telegram_channel_id": telegram_channel_id,
            "enabled": enabled,
        }
        if username is not None:
            destination["username"] = normalize_source_username(username)
        document["destination_channels"].append(destination)

    return mutate


def set_destination_active(name: str, *, active: bool) -> ConfigMutator:
    """Build a destination enable/disable mutation."""

    def mutate(document: ConfigDocument) -> None:
        target = next(
            (item for item in document["destination_channels"] if item["name"] == name),
            None,
        )
        if target is None:
            raise ConfigMutationConflictError("Destination does not exist.")
        target["enabled"] = active

    return mutate


def remove_destination(name: str) -> ConfigMutator:
    """Reject destination removal while an admin or source references it."""

    def mutate(document: ConfigDocument) -> None:
        if any(
            name in item["allowed_destination_names"] for item in document["admins"]
        ) or any(
            name in item["allowed_destination_names"]
            for item in document["source_channels"]
        ):
            raise ConfigMutationConflictError("Destination is still referenced.")
        target = next(
            (item for item in document["destination_channels"] if item["name"] == name),
            None,
        )
        if target is None:
            raise ConfigMutationConflictError("Destination does not exist.")
        if len(document["destination_channels"]) == 1:
            raise ConfigMutationConflictError(
                "At least one destination must remain configured."
            )
        document["destination_channels"].remove(target)

    return mutate


def set_media_retention(days: int) -> ConfigMutator:
    """Build a strict media-retention mutation."""
    if isinstance(days, bool) or not 1 <= days <= 3650:
        raise InstanceConfigurationError("Retention days must be between 1 and 3650.")

    def mutate(document: ConfigDocument) -> None:
        document["media"]["retention_days"] = days

    return mutate


def set_logging_level(level: str) -> ConfigMutator:
    """Build a logging-level mutation validated by ``ApplicationConfig``."""

    def mutate(document: ConfigDocument) -> None:
        document["logging"]["level"] = level

    return mutate


def set_timezone(timezone: str) -> ConfigMutator:
    """Build a timezone mutation validated against the IANA timezone database."""
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, TypeError):
        raise InstanceConfigurationError(
            "Timezone must be a valid IANA timezone identifier."
        ) from None

    def mutate(document: ConfigDocument) -> None:
        document["timezone"] = timezone

    return mutate


def set_media_preview_enabled(enabled: bool) -> ConfigMutator:
    """Build a preview-generation toggle mutation with strict input."""
    if not isinstance(enabled, bool):
        raise InstanceConfigurationError("Preview value must be true or false.")

    def mutate(document: ConfigDocument) -> None:
        document["media"]["preview_enabled"] = enabled

    return mutate


def set_media_cleanup_interval(seconds: int) -> ConfigMutator:
    """Build a bounded cleanup-interval mutation."""
    if isinstance(seconds, bool) or not 60 <= seconds <= 86400 * 7:
        raise InstanceConfigurationError(
            "Cleanup interval must be between 60 and 604800 seconds."
        )

    def mutate(document: ConfigDocument) -> None:
        document["media"]["cleanup_interval_seconds"] = seconds

    return mutate


def set_approval_chat_id(chat_id: int) -> ConfigMutator:
    """Build a strict approval-chat mutation."""
    if isinstance(chat_id, bool) or chat_id == 0 or chat_id > 0:
        raise InstanceConfigurationError(
            "Approval chat ID must be a negative Telegram identifier."
        )

    def mutate(document: ConfigDocument) -> None:
        document["telegram"]["bot"]["approval_chat_id"] = chat_id

    return mutate


def _write_candidate(
    path: Path, document: ConfigDocument, *, source_stat: os.stat_result
) -> Path:
    serialized = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        candidate = Path(temporary_name)
        candidate.chmod(stat.S_IMODE(source_stat.st_mode))
        if hasattr(os, "chown"):
            os.chown(candidate, source_stat.st_uid, source_stat.st_gid)
        return candidate
    except OSError as error:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise ConfigTransactionError(
            "Configuration candidate cannot be written."
        ) from error


def mutate_configuration_transaction(
    *,
    config_path: Path,
    backup_directory: Path,
    environ: Mapping[str, str],
    mutator: ConfigMutator,
    affected_services: Sequence[str],
    controller: ServiceController | None = None,
    lock_timeout_seconds: float = 10.0,
) -> Path:
    """Apply one validated mutation and roll back byte-for-byte on restart failure."""
    lock_path = config_path.with_suffix(f"{config_path.suffix}.lock")
    with _InstanceFileLock(lock_path, timeout_seconds=lock_timeout_seconds):
        try:
            original = config_path.read_bytes()
            source_stat = config_path.stat()
            document = json.loads(original.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ConfigTransactionError(
                "Current configuration cannot be read safely."
            ) from error
        candidate_document = copy.deepcopy(document)
        mutator(candidate_document)
        try:
            ApplicationConfig.model_validate(candidate_document)
        except (ValidationError, TypeError, ValueError) as error:
            raise ConfigTransactionError(
                "Configuration mutation is invalid."
            ) from error

        candidate = _write_candidate(
            config_path, candidate_document, source_stat=source_stat
        )
        try:
            load_configuration(candidate, environ=environ)
            backup_directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
            backup_path = backup_directory / f"configuration-{stamp}.json"
            backup_path.write_bytes(original)
            backup_path.chmod(stat.S_IMODE(source_stat.st_mode))
            candidate.replace(config_path)
            if controller is not None:
                controller.restart(affected_services)
                if not controller.healthy(affected_services):
                    raise ConfigTransactionError(
                        "Affected services did not become healthy."
                    )
            return backup_path
        except Exception as error:
            candidate.unlink(missing_ok=True)
            restore = _write_candidate(
                config_path,
                json.loads(original.decode("utf-8")),
                source_stat=source_stat,
            )
            restore.replace(config_path)
            if controller is not None:
                with suppress(Exception):
                    controller.restart(affected_services)
            if isinstance(error, OperatorConfigError):
                raise
            raise ConfigTransactionError(
                "Configuration transaction failed and was rolled back."
            ) from error


__all__ = (
    "ConfigMutationConflictError",
    "ConfigTransactionError",
    "OperatorConfigError",
    "ServiceController",
    "add_administrators",
    "add_destination",
    "add_sources",
    "configure_source",
    "mutate_configuration_transaction",
    "remove_administrator",
    "remove_destination",
    "remove_source",
    "set_administrator_active",
    "set_administrator_destinations",
    "set_approval_chat_id",
    "set_destination_active",
    "set_logging_level",
    "set_media_cleanup_interval",
    "set_media_preview_enabled",
    "set_media_retention",
    "set_source_active",
    "set_timezone",
)
