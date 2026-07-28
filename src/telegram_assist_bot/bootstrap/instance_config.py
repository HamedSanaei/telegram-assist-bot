"""Typed generation of one isolated deployment configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from pydantic import ValidationError

from telegram_assist_bot.shared.config import ApplicationConfig

INSTANCE_SLUG_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,31}", re.ASCII)
TELEGRAM_USERNAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}", re.ASCII)
MAX_TELEGRAM_IDENTIFIER = (1 << 63) - 1


class InstanceConfigurationError(ValueError):
    """Report safe instance-generation input or filesystem failure."""


def validate_instance_slug(value: str) -> str:
    """Return one collision-safe lowercase instance slug."""
    if INSTANCE_SLUG_PATTERN.fullmatch(value) is None:
        raise InstanceConfigurationError("Instance must match [a-z][a-z0-9-]{0,31}.")
    return value


def parse_admin_user_ids(value: str) -> tuple[int, ...]:
    """Parse distinct positive Telegram user identifiers from CSV text."""
    result: list[int] = []
    seen: set[int] = set()
    for index, raw_item in enumerate(value.split(","), start=1):
        item = raw_item.strip()
        if not item or not item.isascii() or not item.isdecimal():
            raise InstanceConfigurationError(
                f"Administrator item {index} must be a positive decimal identifier."
            )
        identifier = int(item)
        if not 1 <= identifier <= MAX_TELEGRAM_IDENTIFIER:
            raise InstanceConfigurationError(
                f"Administrator item {index} is outside the supported identifier range."
            )
        if identifier in seen:
            raise InstanceConfigurationError(
                f"Administrator item {index} duplicates identifier {identifier}."
            )
        seen.add(identifier)
        result.append(identifier)
    return tuple(result)


def normalize_source_username(value: str, *, item_index: int = 1) -> str:
    """Return a canonical lowercase username from a supported source form."""
    candidate = value.strip()
    if not candidate:
        raise InstanceConfigurationError(f"Source item {item_index} is empty.")
    if candidate.startswith(("http://", "https://")):
        parsed = urlsplit(candidate)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.hostname.casefold() != "t.me"
            or port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise InstanceConfigurationError(
                f"Source item {item_index} is not a supported public Telegram URL."
            )
        segments = [part for part in parsed.path.split("/") if part]
        if len(segments) != 1:
            raise InstanceConfigurationError(
                f"Source item {item_index} must identify one public channel."
            )
        candidate = segments[0]
    elif candidate.casefold().startswith("t.me/"):
        segments = candidate[5:].split("/")
        if len(segments) != 1 or not segments[0]:
            raise InstanceConfigurationError(
                f"Source item {item_index} must identify one public channel."
            )
        candidate = segments[0]
    candidate = candidate.removeprefix("@")
    if (
        candidate.startswith("+")
        or TELEGRAM_USERNAME_PATTERN.fullmatch(candidate) is None
    ):
        raise InstanceConfigurationError(
            f"Source item {item_index} has an invalid public username."
        )
    return candidate.lower()


def parse_source_usernames(value: str) -> tuple[str, ...]:
    """Parse and case-insensitively deduplicate public source usernames."""
    result: list[str] = []
    seen: set[str] = set()
    for index, raw_item in enumerate(value.split(","), start=1):
        username = normalize_source_username(raw_item, item_index=index)
        key = username.casefold()
        if key not in seen:
            seen.add(key)
            result.append(username)
    if not result:
        raise InstanceConfigurationError("At least one source username is required.")
    return tuple(result)


def render_instance_configuration(
    *,
    template_path: Path,
    output_path: Path,
    instance: str,
    retention_days: int,
    approval_chat_id: int,
    admin_user_id: int | None = None,
    source_username: str | None = None,
    admin_user_ids: tuple[int, ...] | None = None,
    source_usernames: tuple[str, ...] | None = None,
    destination_name: str,
    destination_id: int,
    destination_username: str | None,
    timezone: str,
    force: bool = False,
) -> None:
    """Validate and atomically write one model-backed instance Config."""
    slug = validate_instance_slug(instance)
    if isinstance(retention_days, bool) or not 1 <= retention_days <= 3650:
        raise InstanceConfigurationError("Retention days must be between 1 and 3650.")
    if output_path.exists() and not force:
        raise InstanceConfigurationError("Instance configuration already exists.")
    normalized_admin_ids = (
        admin_user_ids
        if admin_user_ids is not None
        else parse_admin_user_ids(
            str(admin_user_id) if admin_user_id is not None else ""
        )
    )
    normalized_sources = (
        source_usernames
        if source_usernames is not None
        else parse_source_usernames(source_username or "")
    )
    if len(normalized_admin_ids) != len(set(normalized_admin_ids)):
        raise InstanceConfigurationError("Administrator identifiers must be unique.")
    try:
        data = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise InstanceConfigurationError(
            "Configuration template cannot be read safely."
        ) from None
    data["mongodb"]["database_name"] = f"telegram_assist_{slug.replace('-', '_')}"
    data["telegram"]["bot"]["approval_chat_id"] = approval_chat_id
    data["telegram"]["user"]["session_path"] = "var/sessions/source_account.session"
    data["admins"] = [
        {
            "name": f"instance-admin-{index}",
            "telegram_user_id": identifier,
            "active": True,
            "role": "admin",
            "permissions": ["approval.view", "approval.toggle"],
            "allowed_destination_names": [destination_name],
            "allowed_destination_ids": [destination_id],
        }
        for index, identifier in enumerate(normalized_admin_ids, start=1)
    ]
    data["source_channels"] = [
        {
            "name": username,
            "username": username,
            "enabled": True,
            "advertisement_detection_enabled": False,
            "duplicate_detection_enabled": False,
            "default_category_id": None,
            "allowed_destination_names": [destination_name],
        }
        for username in normalized_sources
    ]
    destination: dict[str, object] = {
        "name": destination_name,
        "telegram_channel_id": destination_id,
        "enabled": True,
    }
    if destination_username:
        destination["username"] = destination_username.removeprefix("@")
    data["destination_channels"] = [destination]
    data["features"] = {
        "advertisement_detection_enabled": False,
        "duplicate_detection_enabled": False,
        "ai_scoring_enabled": False,
        "ai_categorization_enabled": False,
    }
    data["semantic_duplicate"] = None
    data["scoring"] = None
    data["categorization"] = {"categories": [], "keyword_rules": []}
    data["ai"]["providers"] = []
    data["ai"]["routes"] = []
    data["ai"]["failure_policies"] = []
    data["ai"]["cache_policies"] = []
    data["advertisements"]["routes"] = []
    data["advertisements"]["campaigns"] = []
    data["timezone"] = timezone
    data["media"]["root"] = "var/media"
    data["media"]["retention_days"] = retention_days
    try:
        configuration = ApplicationConfig.model_validate(data)
        serialized = json.dumps(
            configuration.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    except (ValidationError, TypeError, ValueError):
        raise InstanceConfigurationError(
            "Generated instance configuration is invalid."
        ) from None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(f"{serialized}\n")
            temporary_name = temporary.name
        temporary_path = Path(temporary_name)
        temporary_path.chmod(0o600)
        temporary_path.replace(output_path)
    except OSError:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise InstanceConfigurationError(
            "Instance configuration cannot be written safely."
        ) from None


__all__ = (
    "INSTANCE_SLUG_PATTERN",
    "InstanceConfigurationError",
    "normalize_source_username",
    "parse_admin_user_ids",
    "parse_source_usernames",
    "render_instance_configuration",
    "validate_instance_slug",
)
