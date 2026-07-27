"""Typed generation of one isolated deployment configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import ValidationError

from telegram_assist_bot.shared.config import ApplicationConfig

INSTANCE_SLUG_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,31}", re.ASCII)


class InstanceConfigurationError(ValueError):
    """Report safe instance-generation input or filesystem failure."""


def validate_instance_slug(value: str) -> str:
    """Return one collision-safe lowercase instance slug."""
    if INSTANCE_SLUG_PATTERN.fullmatch(value) is None:
        raise InstanceConfigurationError("Instance must match [a-z][a-z0-9-]{0,31}.")
    return value


def render_instance_configuration(
    *,
    template_path: Path,
    output_path: Path,
    instance: str,
    retention_days: int,
    approval_chat_id: int,
    admin_user_id: int,
    source_username: str,
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
            "name": "instance-admin",
            "telegram_user_id": admin_user_id,
            "active": True,
            "role": "admin",
            "permissions": ["approval.view", "approval.toggle"],
            "allowed_destination_names": [destination_name],
            "allowed_destination_ids": [destination_id],
        }
    ]
    data["source_channels"] = [
        {
            "name": source_username,
            "username": source_username.removeprefix("@"),
            "enabled": True,
            "advertisement_detection_enabled": False,
            "duplicate_detection_enabled": False,
            "default_category_id": "general",
            "allowed_destination_names": [destination_name],
        }
    ]
    destination: dict[str, object] = {
        "name": destination_name,
        "telegram_channel_id": destination_id,
        "enabled": True,
    }
    if destination_username:
        destination["username"] = destination_username.removeprefix("@")
    data["destination_channels"] = [destination]
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
    "render_instance_configuration",
    "validate_instance_slug",
)
