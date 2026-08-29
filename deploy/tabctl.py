#!/usr/bin/env python3
"""Global, secret-safe operator manager for Telegram Assist Bot instances."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

EXIT_SUCCESS: Final = 0
EXIT_INPUT: Final = 2
EXIT_INFRASTRUCTURE: Final = 3
EXIT_CONFIRMATION: Final = 4
EXIT_REGISTRY: Final = 5
METADATA_SCHEMA_VERSION: Final = 1
DEFAULT_RELEASE_VERSION: Final = "1.1.3"
DEFAULT_APPLICATION_IMAGE: Final = (
    f"ghcr.io/hamedsanaei/telegram-assist-bot:{DEFAULT_RELEASE_VERSION}"
)
SLUG_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,31}", re.ASCII)


class TabctlError(RuntimeError):
    """Base error carrying one stable operator exit code."""

    def __init__(self, message: str, exit_code: int) -> None:
        """Store a safe message and stable exit code."""
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class InstanceMetadata:
    """Non-secret identity and deployment coordinates for one instance."""

    schema_version: int
    instance_slug: str
    installation_path: str
    compose_project_name: str
    mongodb_database_name: str
    application_image: str
    mongodb_image: str
    installed_at: str
    last_successful_update_version: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> InstanceMetadata:
        """Validate a metadata mapping without accepting extra or secret fields."""
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected or value.get("schema_version") != 1:
            raise TabctlError("Instance metadata is invalid.", EXIT_REGISTRY)
        metadata = cls(**value)
        if SLUG_PATTERN.fullmatch(metadata.instance_slug) is None:
            raise TabctlError("Instance metadata has an invalid slug.", EXIT_REGISTRY)
        path = Path(metadata.installation_path)
        if not path.is_absolute():
            raise TabctlError("Instance metadata path must be absolute.", EXIT_REGISTRY)
        return metadata


def _registry_path() -> Path:
    override = os.environ.get("TAB_REGISTRY_PATH")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    elif hasattr(os, "geteuid") and os.geteuid() == 0:
        base = Path("/var/lib")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "telegram-assist-bot" / "registry.json"


def _atomic_json_write(path: Path, payload: object, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        temporary_path = Path(temporary_name)
        temporary_path.chmod(mode)
        temporary_path.replace(path)
    except OSError as error:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise TabctlError(
            "Registry cannot be written safely.", EXIT_REGISTRY
        ) from error


class InstanceRegistry:
    """Atomic registry keyed by explicit instance slug, never directory basename."""

    def __init__(self, path: Path | None = None) -> None:
        """Use the platform registry unless an explicit test path is supplied."""
        self.path = _registry_path() if path is None else path

    def load(self) -> dict[str, InstanceMetadata]:
        """Read and validate all registered instances."""
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise TabctlError("Instance registry is invalid.", EXIT_REGISTRY) from error
        if payload.get("schema_version") != 1 or not isinstance(
            payload.get("instances"), list
        ):
            raise TabctlError("Instance registry schema is invalid.", EXIT_REGISTRY)
        instances: dict[str, InstanceMetadata] = {}
        paths: set[str] = set()
        for item in payload["instances"]:
            metadata = InstanceMetadata.from_mapping(item)
            normalized_path = os.path.normcase(metadata.installation_path)
            if metadata.instance_slug in instances or normalized_path in paths:
                raise TabctlError(
                    "Instance registry contains a conflict.", EXIT_REGISTRY
                )
            instances[metadata.instance_slug] = metadata
            paths.add(normalized_path)
        return instances

    def save(self, instances: dict[str, InstanceMetadata]) -> None:
        """Atomically persist a deterministic, versioned registry."""
        _atomic_json_write(
            self.path,
            {
                "schema_version": METADATA_SCHEMA_VERSION,
                "instances": [asdict(instances[name]) for name in sorted(instances)],
            },
        )

    def register(self, metadata: InstanceMetadata) -> None:
        """Register or update the same identity while rejecting name/path clashes."""
        instances = self.load()
        normalized_path = os.path.normcase(metadata.installation_path)
        for name, existing in instances.items():
            if (
                name != metadata.instance_slug
                and os.path.normcase(existing.installation_path) == normalized_path
            ):
                raise TabctlError(
                    "Another instance already uses this installation path.",
                    EXIT_REGISTRY,
                )
        registered = instances.get(metadata.instance_slug)
        if (
            registered is not None
            and os.path.normcase(registered.installation_path) != normalized_path
        ):
            raise TabctlError(
                "Instance name is already registered at another path.", EXIT_REGISTRY
            )
        instances[metadata.instance_slug] = metadata
        self.save(instances)

    def unregister(self, name: str) -> None:
        """Remove registry identity without touching containers or data."""
        instances = self.load()
        if name not in instances:
            raise TabctlError("Instance was not found.", EXIT_REGISTRY)
        del instances[name]
        self.save(instances)


def _read_env_coordinates(path: Path) -> dict[str, str]:
    env_path = path / ".env"
    if not env_path.is_file():
        raise TabctlError("Instance .env file was not found.", EXIT_REGISTRY)
    allowed = {
        "COMPOSE_PROJECT_NAME",
        "TAB_IMAGE",
        "TAB_MONGODB_DATABASE",
        "TAB_MONGODB_IMAGE",
        "TAB_RUNTIME_UID",
        "TAB_RUNTIME_GID",
    }
    coordinates: dict[str, str] = {}
    try:
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and key in allowed:
                coordinates[key] = value
    except (OSError, UnicodeError) as error:
        raise TabctlError(
            "Instance coordinates cannot be read.", EXIT_REGISTRY
        ) from error
    return coordinates


def import_instance(path: Path, name: str) -> InstanceMetadata:
    """Adopt an existing instance without changing its data or deployment files."""
    resolved = path.expanduser().resolve()
    if SLUG_PATTERN.fullmatch(name) is None:
        raise TabctlError("Instance name is invalid.", EXIT_INPUT)
    if not (resolved / "compose.yaml").is_file():
        raise TabctlError("Instance Compose asset was not found.", EXIT_REGISTRY)
    coordinates = _read_env_coordinates(resolved)
    metadata_path = resolved / "metadata" / "instance.json"
    installed_at = datetime.now(UTC).isoformat()
    if metadata_path.exists():
        existing = InstanceMetadata.from_mapping(
            json.loads(metadata_path.read_text(encoding="utf-8"))
        )
        installed_at = existing.installed_at
    metadata = InstanceMetadata(
        schema_version=METADATA_SCHEMA_VERSION,
        instance_slug=name,
        installation_path=str(resolved),
        compose_project_name=coordinates.get(
            "COMPOSE_PROJECT_NAME", f"telegram-assist-{name}"
        ),
        mongodb_database_name=coordinates.get(
            "TAB_MONGODB_DATABASE", f"telegram_assist_{name.replace('-', '_')}"
        ),
        application_image=coordinates.get("TAB_IMAGE", DEFAULT_APPLICATION_IMAGE),
        mongodb_image=coordinates.get("TAB_MONGODB_IMAGE", "mongo:7.0.32"),
        installed_at=installed_at,
        last_successful_update_version=None,
    )
    _atomic_json_write(metadata_path, asdict(metadata))
    InstanceRegistry().register(metadata)
    return metadata


def _metadata(name: str) -> InstanceMetadata:
    try:
        return InstanceRegistry().load()[name]
    except KeyError:
        raise TabctlError("Instance was not found.", EXIT_REGISTRY) from None


def _compose(metadata: InstanceMetadata, arguments: list[str]) -> int:
    command = _compose_command(metadata, arguments)
    try:
        return subprocess.run(command, check=False).returncode  # noqa: S603
    except OSError as error:
        raise TabctlError(
            "Docker Compose could not be executed.", EXIT_INFRASTRUCTURE
        ) from error


def _compose_command(metadata: InstanceMetadata, arguments: list[str]) -> list[str]:
    root = Path(metadata.installation_path)
    return [
        "docker",
        "compose",
        "--project-name",
        metadata.compose_project_name,
        "--env-file",
        str(root / ".env"),
        "--file",
        str(root / "compose.yaml"),
        *arguments,
    ]


def _configuration(metadata: InstanceMetadata) -> dict[str, Any]:
    path = Path(metadata.installation_path) / "config" / "configuration.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TabctlError("Instance configuration is invalid.", EXIT_INPUT) from error
    if not isinstance(value, dict):
        raise TabctlError("Instance configuration is invalid.", EXIT_INPUT)
    return value


def _run_config_mutation(
    metadata: InstanceMetadata,
    *,
    operation: str,
    value: str,
    destinations: tuple[str, ...] = (),
) -> int:
    root = Path(metadata.installation_path)
    command = [
        "docker",
        "run",
        "--rm",
        "--user",
        "0:0",
        "--env-file",
        str(root / ".env"),
        "--volume",
        f"{root}:/instance",
        metadata.application_image,
        "operator-config",
        "--config",
        "/instance/config/configuration.json",
        "--operation",
        operation,
        "--value",
        value,
    ]
    if destinations:
        command.extend(["--destinations", ",".join(destinations)])
    try:
        result = subprocess.run(command, check=False).returncode  # noqa: S603
    except OSError as error:
        raise TabctlError(
            "Config mutation container failed.", EXIT_INFRASTRUCTURE
        ) from error
    if result != 0:
        return EXIT_INPUT
    services = _running_application_services(metadata)
    if not services or _compose(metadata, ["restart", *services]) == 0:
        return EXIT_SUCCESS
    backup_directory = root / "backups" / "config"
    backups = sorted(backup_directory.glob("configuration-*.json"))
    if backups:
        shutil.copy2(backups[-1], root / "config" / "configuration.json")
        _compose(metadata, ["restart", *services])
    raise TabctlError(
        "Service restart failed; the previous Config was restored.",
        EXIT_INFRASTRUCTURE,
    )


def _running_application_services(metadata: InstanceMetadata) -> list[str]:
    """Return only long-running application services that are currently active."""
    code, output = _capture(
        _compose_command(metadata, ["ps", "--services", "--status", "running"])
    )
    if code != 0:
        raise TabctlError(
            "Running application services could not be inspected.",
            EXIT_INFRASTRUCTURE,
        )
    running = set(output.splitlines())
    return [
        service
        for service in ("runtime", "approval-bot", "media-cleanup-worker")
        if service in running
    ]


def _default_destinations(metadata: InstanceMetadata) -> tuple[str, ...]:
    return tuple(
        str(item["name"])
        for item in _configuration(metadata)["destination_channels"]
        if item.get("enabled", True)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_direct_secret(value: object) -> bool:
    if isinstance(value, dict):
        if "direct" in value:
            return True
        return any(_contains_direct_secret(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_direct_secret(item) for item in value)
    return False


def create_backup(metadata: InstanceMetadata) -> str:
    """Create a manifest-backed Config, metadata and MongoDB backup."""
    root = Path(metadata.installation_path)
    backup_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = root / "backups" / backup_id
    config = _configuration(metadata)
    if _contains_direct_secret(config):
        raise TabctlError(
            "Direct secrets in Config prevent a non-secret backup.", EXIT_INPUT
        )
    target.mkdir(parents=True, exist_ok=False)
    config_path = target / "configuration.json"
    metadata_path = target / "instance.json"
    archive_path = target / "mongodb.archive.gz"
    _atomic_json_write(config_path, config)
    _atomic_json_write(metadata_path, asdict(metadata))
    command = _compose_command(
        metadata,
        [
            "exec",
            "-T",
            "mongodb",
            "sh",
            "-c",
            (
                "mongodump --quiet --archive --gzip "
                '--username "$MONGO_INITDB_ROOT_USERNAME" '
                '--password "$MONGO_INITDB_ROOT_PASSWORD" '
                "--authenticationDatabase admin"
            ),
        ],
    )
    try:
        with archive_path.open("wb") as output:
            result = subprocess.run(  # noqa: S603
                command, check=False, stdout=output
            ).returncode
    except OSError as error:
        shutil.rmtree(target, ignore_errors=True)
        raise TabctlError(
            "MongoDB backup could not run.", EXIT_INFRASTRUCTURE
        ) from error
    if result != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise TabctlError("MongoDB backup failed.", EXIT_INFRASTRUCTURE)
    files = (config_path, metadata_path, archive_path)
    manifest = {
        "schema_version": 1,
        "backup_id": backup_id,
        "instance_name": metadata.instance_slug,
        "timestamp": datetime.now(UTC).isoformat(),
        "application_version": metadata.application_image.rsplit(":", 1)[-1],
        "config_schema_version": config["configuration_schema_version"],
        "mongodb_version": metadata.mongodb_image.rsplit(":", 1)[-1],
        "included_components": ["configuration", "metadata", "mongodb"],
        "checksums": {path.name: _sha256(path) for path in files},
    }
    _atomic_json_write(target / "manifest.json", manifest)
    return backup_id


def verify_backup(metadata: InstanceMetadata, backup_id: str) -> dict[str, Any]:
    """Verify backup identity, manifest schema and every file checksum."""
    target = Path(metadata.installation_path) / "backups" / backup_id
    try:
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TabctlError("Backup manifest is invalid.", EXIT_INPUT) from error
    if (
        manifest.get("schema_version") != 1
        or manifest.get("instance_name") != metadata.instance_slug
        or not isinstance(manifest.get("checksums"), dict)
    ):
        raise TabctlError("Backup identity or schema is invalid.", EXIT_INPUT)
    for filename, expected in manifest["checksums"].items():
        path = target / filename
        if not path.is_file() or _sha256(path) != expected:
            raise TabctlError(f"Backup checksum failed for {filename}.", EXIT_INPUT)
    return cast("dict[str, Any]", manifest)


def restore_backup(
    metadata: InstanceMetadata, backup_id: str, *, confirmed: bool
) -> None:
    """Restore verified Config/MongoDB with pre-restore backup and rollback."""
    if not confirmed:
        raise TabctlError(
            "Restore requires explicit --yes confirmation.", EXIT_CONFIRMATION
        )
    verify_backup(metadata, backup_id)
    pre_restore_id = create_backup(metadata)
    root = Path(metadata.installation_path)
    target = root / "backups" / backup_id
    config_path = root / "config" / "configuration.json"
    original_config = config_path.read_bytes()
    if _compose(metadata, ["stop", "runtime", "approval-bot", "media-cleanup-worker"]):
        raise TabctlError("Services could not be stopped.", EXIT_INFRASTRUCTURE)
    shutil.copy2(target / "configuration.json", config_path)
    command = _compose_command(
        metadata,
        [
            "exec",
            "-T",
            "mongodb",
            "sh",
            "-c",
            (
                "mongorestore --quiet --archive --gzip --drop "
                '--username "$MONGO_INITDB_ROOT_USERNAME" '
                '--password "$MONGO_INITDB_ROOT_PASSWORD" '
                "--authenticationDatabase admin"
            ),
        ],
    )
    try:
        with (target / "mongodb.archive.gz").open("rb") as source:
            result = subprocess.run(  # noqa: S603
                command, check=False, stdin=source
            ).returncode
    except OSError as error:
        result = 1
        failure: Exception | None = error
    else:
        failure = None
    if result != 0:
        config_path.write_bytes(original_config)
        _compose(metadata, ["up", "-d"])
        raise TabctlError(
            f"Restore failed; pre-restore backup {pre_restore_id} was preserved.",
            EXIT_INFRASTRUCTURE,
        ) from failure
    if _compose(metadata, ["up", "-d"]):
        raise TabctlError(
            f"Restore completed but startup failed; use backup {pre_restore_id}.",
            EXIT_INFRASTRUCTURE,
        )


def _replace_env_value(path: Path, key: str, value: str) -> None:
    original = path.read_text(encoding="utf-8-sig")
    lines = original.splitlines()
    replacement = f"{key}={value}"
    found = False
    result: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            result.append(replacement)
            found = True
        else:
            result.append(line)
    if not found:
        result.append(replacement)
    temporary = path.with_name(f".{path.name}.update.tmp")
    temporary.write_text("\n".join(result) + "\n", encoding="utf-8")
    temporary.chmod(path.stat().st_mode)
    temporary.replace(path)


def _version_image(image: str, version: str) -> str:
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise TabctlError("Application version must be exact SemVer X.Y.Z.", EXIT_INPUT)
    repository = image.rsplit(":", 1)[0]
    return f"{repository}:{version}"


def update_instance(
    metadata: InstanceMetadata, *, version: str | None, check_only: bool
) -> None:
    """Apply a pinned Image update and restore Image/Config on startup failure."""
    current = metadata.application_image
    target = _version_image(current, version or DEFAULT_RELEASE_VERSION)
    sys.stdout.write(f"old_image={current}\nnew_image={target}\n")
    if check_only:
        return
    backup_id = create_backup(metadata)
    root = Path(metadata.installation_path)
    env_path = root / ".env"
    original_env = env_path.read_bytes()
    original_config = (root / "config" / "configuration.json").read_bytes()
    _atomic_json_write(
        root / "metadata" / "update-state.json",
        {"schema_version": 1, "old_image": current, "backup_id": backup_id},
    )
    pull_command = ["docker", "pull", target]
    pull = subprocess.run(pull_command, check=False).returncode  # noqa: S603
    if pull != 0:
        raise TabctlError(
            "Target application Image could not be pulled.", EXIT_INFRASTRUCTURE
        )
    _replace_env_value(env_path, "TAB_IMAGE", target)
    if _compose(metadata, ["config", "--quiet"]) or _compose(metadata, ["up", "-d"]):
        env_path.write_bytes(original_env)
        (root / "config" / "configuration.json").write_bytes(original_config)
        _compose(metadata, ["up", "-d"])
        raise TabctlError(
            f"Update failed and rolled back; backup {backup_id} is available.",
            EXIT_INFRASTRUCTURE,
        )
    updated = InstanceMetadata(
        **{
            **asdict(metadata),
            "application_image": target,
            "last_successful_update_version": target.rsplit(":", 1)[-1],
        }
    )
    _atomic_json_write(root / "metadata" / "instance.json", asdict(updated))
    InstanceRegistry().register(updated)
    sys.stdout.write("update_status=healthy\n")


def rollback_update(metadata: InstanceMetadata) -> None:
    """Restore the previously recorded application Image and Config backup."""
    root = Path(metadata.installation_path)
    state_path = root / "metadata" / "update-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TabctlError(
            "No valid update rollback state exists.", EXIT_INPUT
        ) from error
    if state.get("schema_version") != 1:
        raise TabctlError("Update rollback state is invalid.", EXIT_INPUT)
    verify_backup(metadata, state["backup_id"])
    _replace_env_value(root / ".env", "TAB_IMAGE", state["old_image"])
    shutil.copy2(
        root / "backups" / state["backup_id"] / "configuration.json",
        root / "config" / "configuration.json",
    )
    if _compose(metadata, ["up", "-d"]):
        raise TabctlError("Update rollback startup failed.", EXIT_INFRASTRUCTURE)
    old_image = str(state["old_image"])
    reverted = InstanceMetadata(
        **{
            **asdict(metadata),
            "application_image": old_image,
            "last_successful_update_version": old_image.rsplit(":", 1)[-1],
        }
    )
    _atomic_json_write(root / "metadata" / "instance.json", asdict(reverted))
    InstanceRegistry().register(reverted)
    state_path.unlink()


def repair_plan(metadata: InstanceMetadata) -> list[str]:
    """Return bounded, non-destructive legacy repair actions."""
    root = Path(metadata.installation_path)
    actions: list[str] = []
    env_text = (root / ".env").read_text(encoding="utf-8-sig")
    compose_text = (root / "compose.yaml").read_text(encoding="utf-8")
    if "TAB_MONGODB_IMAGE=" not in env_text:
        actions.append("add TAB_MONGODB_IMAGE=mongo:7.0.32")
    if "image: mongo:8.0.21" in compose_text:
        actions.append("replace legacy hardcoded MongoDB image")
    if not (root / "metadata" / "instance.json").is_file():
        actions.append("write instance metadata")
    actions.append("audit and repair filesystem permissions")
    return actions


def apply_repair(metadata: InstanceMetadata, *, confirmed: bool) -> list[str]:
    """Apply only known-safe repair actions after a full backup."""
    if not confirmed:
        raise TabctlError("Repair apply requires explicit --yes.", EXIT_CONFIRMATION)
    actions = repair_plan(metadata)
    create_backup(metadata)
    root = Path(metadata.installation_path)
    if any(action.startswith("add TAB_MONGODB_IMAGE") for action in actions):
        _replace_env_value(root / ".env", "TAB_MONGODB_IMAGE", "mongo:7.0.32")
    if "replace legacy hardcoded MongoDB image" in actions:
        compose_path = root / "compose.yaml"
        text = compose_path.read_text(encoding="utf-8")
        text = text.replace(
            "image: mongo:8.0.21",
            "image: ${TAB_MONGODB_IMAGE:-mongo:7.0.32}",
        )
        compose_path.write_text(text, encoding="utf-8")
    _atomic_json_write(root / "metadata" / "instance.json", asdict(metadata))
    permission_script = root / (
        "permissions.ps1" if os.name == "nt" else "permissions.sh"
    )
    if permission_script.exists():
        command = (
            [
                "powershell",
                "-NoProfile",
                "-File",
                str(permission_script),
                "repair",
                "-InstanceDirectory",
                str(root),
            ]
            if os.name == "nt"
            else [
                "sudo",
                "bash",
                str(permission_script),
                "repair",
                "--instance-dir",
                str(root),
            ]
        )
        subprocess.run(command, check=False)  # noqa: S603
    return actions


def _redact_text(value: str) -> str:
    redacted = re.sub(r"\b\d{5,12}:[A-Za-z0-9_-]{20,}\b", "[REDACTED_BOT_TOKEN]", value)
    redacted = re.sub(
        r"mongodb(?:\+srv)?://[^\s\"']+",
        "mongodb://[REDACTED]",
        redacted,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"(?i)(api[_-]?hash|password|token)([=:]\s*)\S+",
        r"\1\2[REDACTED]",
        redacted,
    )


def _capture(command: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(  # noqa: S603
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return EXIT_INFRASTRUCTURE, "unavailable"
    return result.returncode, _redact_text(result.stdout + result.stderr)


def collect_diagnostics(metadata: InstanceMetadata) -> dict[str, Any]:
    """Collect bounded operational facts without Config, env, Session or Media data."""
    root = Path(metadata.installation_path)
    _, docker_version = _capture(["docker", "--version"])
    _, compose_version = _capture(["docker", "compose", "version"])
    _, containers = _capture(_compose_command(metadata, ["ps", "--format", "json"]))
    _, errors = _capture(
        _compose_command(metadata, ["logs", "--tail", "200", "--no-color"])
    )
    docker_version = _redact_text(docker_version)
    compose_version = _redact_text(compose_version)
    containers = _redact_text(containers)
    errors = _redact_text(errors)
    error_lines = [
        line
        for line in errors.splitlines()
        if '"level":"ERROR"' in line
        or " error " in line.casefold()
        or "failed" in line.casefold()
    ][-50:]
    config_path = root / "config" / "configuration.json"
    env_path = root / ".env"

    def file_mode(path: Path) -> dict[str, object]:
        if not path.exists():
            return {"present": False}
        details = path.stat()
        return {
            "present": True,
            "mode": oct(details.st_mode & 0o7777),
            "uid": getattr(details, "st_uid", None),
            "gid": getattr(details, "st_gid", None),
        }

    try:
        disk = shutil.disk_usage(root)
        disk_free = disk.free
    except OSError:
        disk_free = None
    session_root = root / "var" / "sessions"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "os": platform.platform(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "free_disk_bytes": disk_free,
        "docker_version": docker_version.strip(),
        "compose_version": compose_version.strip(),
        "instance_metadata": asdict(metadata),
        "container_state": containers.strip(),
        "config_validation": "present" if config_path.is_file() else "missing",
        "config_file": file_mode(config_path),
        "env_file": file_mode(env_path),
        "telegram_session_present": any(session_root.glob("*.session")),
        "recent_errors": error_lines,
        "source_destination_resolution": "see latest redacted runtime startup events",
    }


def export_diagnostics(metadata: InstanceMetadata) -> Path:
    """Write a support archive containing only the redacted diagnostic report."""
    root = Path(metadata.installation_path)
    target = (
        root
        / "backups"
        / (f"diagnostics-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.zip")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    report = json.dumps(
        collect_diagnostics(metadata), ensure_ascii=False, indent=2
    ).encode("utf-8")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", report)
    return target


def _transaction_active(config_path: Path) -> bool:
    lock_path = config_path.with_suffix(f"{config_path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        try:
            if os.name == "nt":
                module = importlib.import_module("msvcrt")
                stream.seek(0)
                module.locking(stream.fileno(), module.LK_NBLCK, 1)
                stream.seek(0)
                module.locking(stream.fileno(), module.LK_UNLCK, 1)
            else:
                module = importlib.import_module("fcntl")
                module.flock(stream.fileno(), module.LOCK_EX | module.LOCK_NB)
                module.flock(stream.fileno(), module.LOCK_UN)
        except OSError:
            return True
    return False


def self_update() -> None:
    """Replace only the manager after proving no Config transaction is active."""
    for metadata in InstanceRegistry().load().values():
        config = Path(metadata.installation_path) / "config" / "configuration.json"
        if _transaction_active(config):
            raise TabctlError(
                "Manager update is blocked by an active Config transaction.",
                EXIT_CONFIRMATION,
            )
    base_url = os.environ.get(
        "TAB_INSTALL_BASE_URL",
        "https://raw.githubusercontent.com/HamedSanaei/telegram-assist-bot/main",
    ).rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise TabctlError("Manager update URL must use HTTPS.", EXIT_INPUT)
    try:
        with urllib.request.urlopen(  # noqa: S310
            f"{base_url}/deploy/tabctl.py", timeout=30
        ) as response:
            payload = response.read(2_000_000)
        compile(payload, "tabctl.py", "exec")
    except (OSError, SyntaxError, ValueError) as error:
        raise TabctlError(
            "Manager update could not be verified.", EXIT_INFRASTRUCTURE
        ) from error
    target = Path(__file__).resolve()
    temporary = target.with_name(f".{target.name}.update.tmp")
    temporary.write_bytes(payload)
    temporary.chmod(target.stat().st_mode)
    temporary.replace(target)


def _show_json(value: object) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _interactive() -> int:
    menu = (
        "1. List Instances\n2. Select an Instance\n3. Create a new Instance\n"
        "4. Import an existing Instance\n5. Show all-instance status\n"
        "6. Update the manager\n7. Diagnostics\n8. Exit"
    )
    while True:
        sys.stdout.write(f"{menu}\n")
        choice = input("Select: ").strip()
        if choice == "1":
            _list_instances()
        elif choice == "2":
            name = input("Instance name: ").strip()
            _selected_menu(name)
        elif choice == "4":
            name = input("Instance name: ").strip()
            path = Path(input("Absolute installation path: ").strip())
            import_instance(path, name)
        elif choice == "5":
            for name in InstanceRegistry().load():
                _compose(_metadata(name), ["ps"])
        elif choice == "8":
            return EXIT_SUCCESS
        else:
            sys.stdout.write(
                "This operation requires its documented noninteractive command.\n"
            )


def _selected_menu(name: str) -> None:
    labels = (
        "1. Status\n2. Start\n3. Stop\n4. Restart\n5. View logs\n"
        "6. Telegram login\n7. Config validation\n8. Manage administrators\n"
        "9. Manage source channels\n10. Manage destination channels\n"
        "11. Media retention settings\n12. Backup\n13. Restore\n"
        "14. Update application\n15. Repair Instance\n"
        "16. Export redacted diagnostics\n"
        "17. Uninstall containers while preserving data\n"
        "18. Purge Instance\n19. Back"
    )
    while True:
        sys.stdout.write(f"{labels}\n")
        choice = input("Select: ").strip()
        actions = {
            "1": ["ps"],
            "2": ["up", "-d"],
            "3": ["stop"],
            "4": ["restart"],
            "5": ["logs", "--tail", "200"],
        }
        if choice == "19":
            return
        if choice in actions:
            _compose(_metadata(name), actions[choice])
        else:
            sys.stdout.write(
                "Use the corresponding documented noninteractive command.\n"
            )


def _list_instances() -> None:
    instances = InstanceRegistry().load()
    for name in sorted(instances):
        metadata = instances[name]
        sys.stdout.write(
            f"{name}\t{metadata.installation_path}\t{metadata.compose_project_name}\n"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tabctl")
    parser.add_argument("--instance")
    subparsers = parser.add_subparsers(dest="command")
    instance = subparsers.add_parser("instance")
    instance_sub = instance.add_subparsers(dest="instance_command", required=True)
    instance_sub.add_parser("list")
    show = instance_sub.add_parser("show")
    show.add_argument("name")
    unregister = instance_sub.add_parser("unregister")
    unregister.add_argument("name")
    importer = instance_sub.add_parser("import")
    importer.add_argument("--path", type=Path, required=True)
    importer.add_argument("--name", required=True)
    instance_sub.add_parser("select")
    subparsers.add_parser("status")
    subparsers.add_parser("start")
    subparsers.add_parser("stop")
    subparsers.add_parser("restart")
    subparsers.add_parser("login")
    config = subparsers.add_parser("config")
    config.add_argument("config_action", choices=("check",))
    logs = subparsers.add_parser("logs")
    logs.add_argument(
        "--service",
        choices=("runtime", "approval-bot", "media-cleanup-worker", "mongodb", "all"),
        default="all",
    )
    logs.add_argument("--tail", type=int, default=200)
    logs.add_argument("--since")
    logs.add_argument("--follow", action="store_true")
    logs.add_argument("--timestamps", action="store_true")
    logs.add_argument("--errors-only", action="store_true")
    admin = subparsers.add_parser("admin")
    admin_sub = admin.add_subparsers(dest="admin_action", required=True)
    admin_sub.add_parser("list")
    admin_add = admin_sub.add_parser("add")
    admin_add.add_argument("identifiers")
    admin_add.add_argument("--destinations")
    for action in ("remove", "enable", "disable"):
        action_parser = admin_sub.add_parser(action)
        action_parser.add_argument("identifier")
    admin_permissions = admin_sub.add_parser("permissions")
    admin_permissions.add_argument("identifier")
    source = subparsers.add_parser("source")
    source_sub = source.add_subparsers(dest="source_action", required=True)
    source_sub.add_parser("list")
    source_add = source_sub.add_parser("add")
    source_add.add_argument("sources")
    source_add.add_argument("--destinations")
    for action in ("remove", "enable", "disable"):
        action_parser = source_sub.add_parser(action)
        action_parser.add_argument("source")
    destination = subparsers.add_parser("destination")
    destination_sub = destination.add_subparsers(
        dest="destination_action", required=True
    )
    destination_sub.add_parser("list")
    destination_add = destination_sub.add_parser("add")
    destination_add.add_argument("--name", required=True)
    destination_add.add_argument("--id", type=int, required=True)
    destination_add.add_argument("--username")
    for action in ("remove", "enable", "disable"):
        action_parser = destination_sub.add_parser(action)
        action_parser.add_argument("name")
    retention = subparsers.add_parser("retention")
    retention_sub = retention.add_subparsers(dest="retention_action", required=True)
    retention_sub.add_parser("show")
    retention_set = retention_sub.add_parser("set")
    retention_set.add_argument("days", type=int)
    backup = subparsers.add_parser("backup")
    backup_sub = backup.add_subparsers(dest="backup_action", required=True)
    backup_sub.add_parser("create")
    backup_sub.add_parser("list")
    for action in ("verify", "restore", "delete"):
        action_parser = backup_sub.add_parser(action)
        action_parser.add_argument("backup_id")
        if action in {"restore", "delete"}:
            action_parser.add_argument("--yes", action="store_true")
    update = subparsers.add_parser("update")
    update.add_argument("--version")
    update.add_argument("--check", action="store_true")
    update.add_argument("--rollback", action="store_true")
    repair = subparsers.add_parser("repair")
    repair_mode = repair.add_mutually_exclusive_group()
    repair_mode.add_argument("--dry-run", action="store_true")
    repair_mode.add_argument("--apply", action="store_true")
    repair.add_argument("--yes", action="store_true")
    diagnostics = subparsers.add_parser("diagnostics")
    diagnostics.add_argument(
        "diagnostics_action", nargs="?", choices=("export",), default=None
    )
    uninstall = subparsers.add_parser("uninstall")
    uninstall.add_argument("--yes", action="store_true")
    purge = subparsers.add_parser("purge")
    purge.add_argument("--yes", action="store_true")
    self_parser = subparsers.add_parser("self")
    self_parser.add_argument("self_action", choices=("update",))
    return parser


def _dispatch(arguments: argparse.Namespace) -> int:
    if arguments.command is None:
        return _interactive()
    if arguments.command == "instance":
        if arguments.instance_command == "list":
            _list_instances()
        elif arguments.instance_command == "show":
            _show_json(asdict(_metadata(arguments.name)))
        elif arguments.instance_command == "unregister":
            InstanceRegistry().unregister(arguments.name)
            sys.stdout.write(
                "Instance unregistered; containers and data were not changed.\n"
            )
        elif arguments.instance_command == "import":
            _show_json(asdict(import_instance(arguments.path, arguments.name)))
        else:
            return _interactive()
        return EXIT_SUCCESS
    if arguments.command == "self":
        self_update()
        return EXIT_SUCCESS
    if not arguments.instance:
        raise TabctlError("--instance is required for this command.", EXIT_INPUT)
    metadata = _metadata(arguments.instance)
    if arguments.command == "admin":
        document = _configuration(metadata)
        if arguments.admin_action == "list":
            _show_json(document["admins"])
            return EXIT_SUCCESS
        if arguments.admin_action == "permissions":
            identifier = int(arguments.identifier)
            values = [
                item
                for item in document["admins"]
                if item["telegram_user_id"] == identifier
            ]
            _show_json(values)
            return EXIT_SUCCESS
        destinations = (
            tuple(
                value.strip()
                for value in (arguments.destinations or "").split(",")
                if value.strip()
            )
            if arguments.admin_action == "add"
            else ()
        ) or _default_destinations(metadata)
        value = (
            arguments.identifiers
            if arguments.admin_action == "add"
            else arguments.identifier
        )
        return _run_config_mutation(
            metadata,
            operation=f"admin-{arguments.admin_action}",
            value=value,
            destinations=destinations,
        )
    if arguments.command == "source":
        if arguments.source_action == "list":
            _show_json(_configuration(metadata)["source_channels"])
            return EXIT_SUCCESS
        destinations = (
            tuple(
                value.strip()
                for value in (arguments.destinations or "").split(",")
                if value.strip()
            )
            if arguments.source_action == "add"
            else ()
        ) or _default_destinations(metadata)
        value = (
            arguments.sources if arguments.source_action == "add" else arguments.source
        )
        return _run_config_mutation(
            metadata,
            operation=f"source-{arguments.source_action}",
            value=value,
            destinations=destinations,
        )
    if arguments.command == "destination":
        if arguments.destination_action == "list":
            _show_json(_configuration(metadata)["destination_channels"])
            return EXIT_SUCCESS
        if arguments.destination_action == "add":
            value = json.dumps(
                {
                    "name": arguments.name,
                    "telegram_channel_id": arguments.id,
                    "username": arguments.username,
                    "enabled": True,
                },
                ensure_ascii=False,
            )
        else:
            value = arguments.name
        return _run_config_mutation(
            metadata,
            operation=f"destination-{arguments.destination_action}",
            value=value,
        )
    if arguments.command == "retention":
        if arguments.retention_action == "show":
            sys.stdout.write(f"{_configuration(metadata)['media']['retention_days']}\n")
            return EXIT_SUCCESS
        return _run_config_mutation(
            metadata,
            operation="retention-set",
            value=str(arguments.days),
        )
    if arguments.command == "backup":
        backup_root = Path(metadata.installation_path) / "backups"
        if arguments.backup_action == "create":
            sys.stdout.write(f"backup_id={create_backup(metadata)}\n")
        elif arguments.backup_action == "list":
            for manifest in sorted(backup_root.glob("*/manifest.json")):
                sys.stdout.write(f"{manifest.parent.name}\n")
        elif arguments.backup_action == "verify":
            _show_json(verify_backup(metadata, arguments.backup_id))
        elif arguments.backup_action == "restore":
            restore_backup(metadata, arguments.backup_id, confirmed=arguments.yes)
        else:
            if not arguments.yes:
                raise TabctlError(
                    "Backup deletion requires explicit --yes.",
                    EXIT_CONFIRMATION,
                )
            verify_backup(metadata, arguments.backup_id)
            shutil.rmtree(backup_root / arguments.backup_id)
        return EXIT_SUCCESS
    if arguments.command == "update":
        if arguments.rollback:
            rollback_update(metadata)
        else:
            update_instance(
                metadata,
                version=arguments.version,
                check_only=arguments.check,
            )
        return EXIT_SUCCESS
    if arguments.command == "repair":
        if arguments.apply:
            confirmed = arguments.yes
            if not confirmed:
                confirmed = input(
                    "Apply the bounded repair plan after creating a backup? [y/N]: "
                ).strip().casefold() in {"y", "yes"}
            _show_json(apply_repair(metadata, confirmed=confirmed))
        else:
            _show_json(repair_plan(metadata))
        return EXIT_SUCCESS
    if arguments.command == "diagnostics":
        if arguments.diagnostics_action == "export":
            sys.stdout.write(f"{export_diagnostics(metadata)}\n")
        else:
            _show_json(collect_diagnostics(metadata))
        return EXIT_SUCCESS
    if arguments.command == "uninstall":
        if not arguments.yes:
            raise TabctlError(
                "Uninstall requires --yes and preserves all data.",
                EXIT_CONFIRMATION,
            )
        return (
            EXIT_SUCCESS if _compose(metadata, ["down"]) == 0 else EXIT_INFRASTRUCTURE
        )
    if arguments.command == "purge":
        if not arguments.yes:
            raise TabctlError(
                "Purge requires explicit --yes confirmation.",
                EXIT_CONFIRMATION,
            )
        return (
            EXIT_SUCCESS
            if _compose(metadata, ["down", "--volumes", "--remove-orphans"]) == 0
            else EXIT_INFRASTRUCTURE
        )
    if arguments.command == "status":
        compose_arguments = ["ps"]
    elif arguments.command == "start":
        compose_arguments = ["up", "-d"]
    elif arguments.command == "stop":
        compose_arguments = ["stop"]
    elif arguments.command == "restart":
        compose_arguments = ["restart"]
    elif arguments.command == "login":
        compose_arguments = [
            "run",
            "--rm",
            "runtime",
            "login",
            "--config",
            "/app/config/configuration.json",
        ]
    elif arguments.command == "config":
        compose_arguments = [
            "run",
            "--rm",
            "runtime",
            "check",
            "--config",
            "/app/config/configuration.json",
        ]
    else:
        compose_arguments = ["logs", "--tail", str(arguments.tail)]
        if arguments.since:
            compose_arguments.extend(["--since", arguments.since])
        if arguments.follow:
            compose_arguments.append("--follow")
        if arguments.timestamps:
            compose_arguments.append("--timestamps")
        if arguments.service != "all":
            compose_arguments.append(arguments.service)
        if arguments.errors_only:
            code, output = _capture(_compose_command(metadata, compose_arguments))
            for line in output.splitlines():
                if (
                    '"level":"ERROR"' in line
                    or " error " in line.casefold()
                    or "failed" in line.casefold()
                ):
                    sys.stdout.write(f"{line}\n")
            return EXIT_SUCCESS if code == 0 else EXIT_INFRASTRUCTURE
    result = _compose(metadata, compose_arguments)
    return EXIT_SUCCESS if result == 0 else EXIT_INFRASTRUCTURE


def main(argv: list[str] | None = None) -> int:
    """Run tabctl and map all expected failures to stable exit codes."""
    try:
        return _dispatch(_parser().parse_args(argv))
    except TabctlError as error:
        sys.stderr.write(f"{error}\n")
        return error.exit_code
    except (KeyboardInterrupt, EOFError):
        sys.stderr.write("Operation cancelled.\n")
        return EXIT_CONFIRMATION


if __name__ == "__main__":
    raise SystemExit(main())
