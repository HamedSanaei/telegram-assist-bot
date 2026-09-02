#!/usr/bin/env python3
"""Global, secret-safe operator manager for Telegram Assist Bot instances."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
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
SERVICE_NAMES: Final = (
    "runtime",
    "approval-bot",
    "media-cleanup-worker",
    "mongodb",
    "all",
)
ENV_VALUE_ALLOWLIST: Final = frozenset({"TAB_TELEGRAM_BOT_TOKEN"})
ENV_COORDINATE_KEYS: Final = (
    "TAB_TELEGRAM_BOT_TOKEN",
    "TAB_TELEGRAM_API_ID",
    "TAB_TELEGRAM_API_HASH",
    "TAB_TELEGRAM_PHONE_NUMBER",
    "TAB_MONGODB_USERNAME",
    "TAB_MONGODB_PASSWORD",
)
BACKUP_MODE_CORE: Final = "core"
BACKUP_MODE_FULL: Final = "full"
BACKUP_MODES: Final = (BACKUP_MODE_CORE, BACKUP_MODE_FULL)
BACKUP_COMPONENTS_CORE: Final = (
    "configuration.json",
    "instance.json",
    "mongodb.archive.gz",
)
BACKUP_ENV_FILE: Final = ".env"
BACKUP_COMPOSE_FILE: Final = "compose.yaml"
BACKUP_SESSION_ARCHIVE: Final = "session.tar.gz"
BACKUP_MEDIA_ARCHIVE: Final = "media.tar.gz"
ENCRYPTION_ITERATIONS: Final = 200_000
HEALTH_CHECK_ATTEMPTS: Final = 3
HEALTH_CHECK_RETRY_DELAY_SECONDS: Final = 2


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
        "--pull",
        "never",
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


def _volume_run(
    metadata: InstanceMetadata,
    volume: str,
    shell_command: str,
    *,
    timeout_seconds: int = 180,
) -> tuple[int, str]:
    """Run one bounded root helper command inside a named project volume."""
    command = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--user",
        "0:0",
        "--volume",
        f"{metadata.compose_project_name}_{volume}:/mnt",
        "--entrypoint",
        "/bin/sh",
        metadata.application_image,
        "-ec",
        shell_command,
    ]
    try:
        result = subprocess.run(  # noqa: S603
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return EXIT_INFRASTRUCTURE, "unavailable"
    return result.returncode, _redact_text(result.stdout + result.stderr)


def _capture_volume_archive(
    metadata: InstanceMetadata, volume: str, target: Path
) -> None:
    """Archive a named project volume into a gzip tar file."""
    command = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--user",
        "0:0",
        "--volume",
        f"{metadata.compose_project_name}_{volume}:/mnt",
        "--entrypoint",
        "/bin/sh",
        metadata.application_image,
        "-ec",
        "tar -czf - -C /mnt .",
    ]
    try:
        with target.open("wb") as output:
            result = subprocess.run(  # noqa: S603
                command, check=False, stdout=output, timeout=3600
            ).returncode
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TabctlError(
            f"Volume backup could not run for {volume}.", EXIT_INFRASTRUCTURE
        ) from error
    if result != 0:
        target.unlink(missing_ok=True)
        raise TabctlError(f"Volume backup failed for {volume}.", EXIT_INFRASTRUCTURE)
    target.chmod(0o600)


def _restore_volume_archive(
    metadata: InstanceMetadata, volume: str, archive: Path
) -> None:
    """Replace a named volume's contents from a verified tar archive."""
    code, _ = _volume_run(
        metadata, volume, "find /mnt -mindepth 1 -delete", timeout_seconds=600
    )
    if code != 0:
        raise TabctlError(
            f"Volume could not be cleared for restore: {volume}.", EXIT_INFRASTRUCTURE
        )
    command = [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--pull",
        "never",
        "--user",
        "0:0",
        "--volume",
        f"{metadata.compose_project_name}_{volume}:/mnt",
        "--entrypoint",
        "/bin/sh",
        metadata.application_image,
        "-ec",
        "tar -xzf - -C /mnt",
    ]
    try:
        with archive.open("rb") as source:
            result = subprocess.run(  # noqa: S603
                command, check=False, stdin=source, timeout=3600
            ).returncode
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TabctlError(
            f"Volume restore could not run for {volume}.", EXIT_INFRASTRUCTURE
        ) from error
    if result != 0:
        raise TabctlError(f"Volume restore failed for {volume}.", EXIT_INFRASTRUCTURE)


def session_status(metadata: InstanceMetadata) -> dict[str, Any]:
    """Report read-only Telegram session file presence without any secrets."""
    code, output = _volume_run(
        metadata,
        "telegram_session",
        "find /mnt -maxdepth 1 -type f "
        "-printf '%f\\t%TY-%Tm-%TdT%TH:%TM:%TSZ\\n' || true",
    )
    if code != 0 or output.strip() == "unavailable":
        return {"state": "unavailable", "files": []}
    files: list[dict[str, str]] = []
    for line in output.splitlines():
        name, separator, modified = line.partition("\t")
        if separator:
            files.append({"name": name, "modified_at": modified})
    return {"state": "present" if files else "absent", "files": files}


def reset_session(metadata: InstanceMetadata, *, confirmed: bool) -> None:
    """Delete only the selected instance's Telegram session after confirmation."""
    if not confirmed:
        raise TabctlError(
            "Session reset requires explicit --yes confirmation.", EXIT_CONFIRMATION
        )
    code, _ = _volume_run(metadata, "telegram_session", "find /mnt -mindepth 1 -delete")
    if code != 0:
        raise TabctlError(
            "Telegram session files could not be removed.", EXIT_INFRASTRUCTURE
        )
    sys.stdout.write("session_reset=completed\n")


def media_usage(metadata: InstanceMetadata) -> dict[str, Any]:
    """Report media volume and preview directory sizes in bytes."""
    code, output = _volume_run(
        metadata,
        "media",
        "du -sb /mnt 2>/dev/null | cut -f1; "
        "printf '|'; if [ -d /mnt/.preview ]; then "
        "du -sb /mnt/.preview 2>/dev/null | cut -f1; fi",
    )
    if code != 0 or output.strip() == "unavailable":
        return {"state": "unavailable"}
    total_part, _, preview_part = output.strip().partition("|")
    media_bytes = int(total_part.strip() or "0")
    preview_bytes = int(preview_part.strip()) if preview_part.strip() else None
    return {
        "state": "available",
        "media_bytes": media_bytes,
        "preview_bytes": preview_bytes,
    }


def collect_status(metadata: InstanceMetadata) -> dict[str, Any]:
    """Collect bounded structured operational status with no credentials."""
    root = Path(metadata.installation_path)
    containers: list[dict[str, Any]] = []
    _, raw = _capture(_compose_command(metadata, ["ps", "--format", "json"]))
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        containers.append(
            {
                "service": item.get("Service"),
                "name": item.get("Name"),
                "state": item.get("State"),
                "health": item.get("Health"),
                "image": item.get("Image"),
            }
        )
    config: dict[str, object] = {"present": False}
    try:
        document = _configuration(metadata)
    except TabctlError:
        pass
    else:
        config = {
            "present": True,
            "schema_version": document.get("configuration_schema_version"),
        }
    manifests = sorted((root / "backups").glob("*/manifest.json"))
    latest_backup: dict[str, object] | None = None
    if manifests:
        try:
            manifest = json.loads(manifests[-1].read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        else:
            latest_backup = {
                "backup_id": manifests[-1].parent.name,
                "timestamp": manifest.get("timestamp"),
                "components": manifest.get("included_components", []),
            }
    try:
        disk_free = shutil.disk_usage(root).free
    except OSError:
        disk_free = None
    mongodb = next((item for item in containers if item["service"] == "mongodb"), None)
    return {
        "schema_version": 1,
        "instance": metadata.instance_slug,
        "installation_path": metadata.installation_path,
        "application_image": metadata.application_image,
        "application_version": metadata.application_image.rsplit(":", 1)[-1],
        "mongodb_image": metadata.mongodb_image,
        "containers": containers,
        "mongodb_health": (
            mongodb.get("health") if mongodb is not None else "not_defined"
        ),
        "session": session_status(metadata),
        "media": media_usage(metadata),
        "config": config,
        "latest_backup": latest_backup,
        "disk_free_bytes": disk_free,
    }


def env_list(metadata: InstanceMetadata) -> dict[str, bool]:
    """Report which secret-bearing environment keys are configured, never values."""
    env_path = Path(metadata.installation_path) / ".env"
    present: set[str] = set()
    try:
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, _ = line.partition("=")
            if separator:
                present.add(key)
    except OSError:
        pass
    return {key: key in present for key in ENV_COORDINATE_KEYS}


def env_set(metadata: InstanceMetadata, key: str) -> None:
    """Replace one allow-listed environment secret reading the value from stdin."""
    if key not in ENV_VALUE_ALLOWLIST:
        raise TabctlError(
            "This environment value cannot be changed with the manager.", EXIT_INPUT
        )
    value = sys.stdin.read().rstrip("\r\n")
    if not value:
        raise TabctlError("An empty value is not allowed.", EXIT_INPUT)
    env_path = Path(metadata.installation_path) / ".env"
    _replace_env_value(env_path, key, value)
    sys.stdout.write(f"{key}=updated (value hidden)\n")


def _backup_components(mode: str, *, exclude_media: bool) -> tuple[str, ...]:
    if mode == BACKUP_MODE_CORE:
        return BACKUP_COMPONENTS_CORE
    components = [
        *BACKUP_COMPONENTS_CORE,
        BACKUP_ENV_FILE,
        BACKUP_COMPOSE_FILE,
        BACKUP_SESSION_ARCHIVE,
    ]
    if not exclude_media:
        components.append(BACKUP_MEDIA_ARCHIVE)
    return tuple(components)


def _backup_passphrase(*, confirm: bool) -> str:
    """Read a non-empty hidden passphrase from the environment or the operator."""
    value = os.environ.get("TAB_BACKUP_PASSPHRASE", "")
    if value:
        return value
    first = getpass.getpass("Backup passphrase (hidden): ")
    if not first:
        raise TabctlError("An empty passphrase is not allowed.", EXIT_INPUT)
    if confirm:
        second = getpass.getpass("Repeat passphrase (hidden): ")
        if first != second:
            raise TabctlError("Passphrases do not match.", EXIT_INPUT)
    return first


def _resolve_passphrase() -> str | None:
    """Return the automation passphrase when present, otherwise None."""
    return os.environ.get("TAB_BACKUP_PASSPHRASE") or None


def _openssl_transform(
    source: Path, target: Path, passphrase: str, *, decrypt: bool
) -> None:
    """Encrypt or decrypt one file with pbkdf2-salted AES-256-CBC."""
    arguments = [
        "openssl",
        "enc",
        "-aes-256-cbc",
        "-pbkdf2",
        "-iter",
        str(ENCRYPTION_ITERATIONS),
    ]
    if decrypt:
        arguments.append("-d")
    arguments.extend(
        ["-salt", "-pass", "stdin", "-in", str(source), "-out", str(target)]
    )
    try:
        result = subprocess.run(  # noqa: S603
            arguments,
            check=False,
            input=passphrase.encode("utf-8"),
            capture_output=True,
        )
    except OSError as error:
        raise TabctlError(
            "OpenSSL is required for encrypted backups.", EXIT_INFRASTRUCTURE
        ) from error
    if result.returncode != 0:
        raise TabctlError(
            "Backup decryption failed." if decrypt else "Backup encryption failed.",
            EXIT_INPUT,
        )


def create_backup(
    metadata: InstanceMetadata,
    *,
    mode: str = BACKUP_MODE_FULL,
    exclude_media: bool = False,
    encrypt: bool = False,
) -> str:
    """Create a manifest-backed mode-scoped backup with checksums."""
    if mode not in BACKUP_MODES:
        raise TabctlError("Backup mode must be 'core' or 'full'.", EXIT_INPUT)
    if exclude_media and mode != BACKUP_MODE_FULL:
        raise TabctlError("--exclude-media requires full mode.", EXIT_INPUT)
    root = Path(metadata.installation_path)
    backup_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = root / "backups" / backup_id
    config = _configuration(metadata)
    if _contains_direct_secret(config):
        raise TabctlError(
            "Direct secrets in Config prevent a non-secret backup.", EXIT_INPUT
        )
    target.mkdir(parents=True, exist_ok=False)
    component_names = _backup_components(mode, exclude_media=exclude_media)
    plaintext_files: list[Path] = []

    def stage(name: str, payload: bytes) -> None:
        path = target / name
        path.write_bytes(payload)
        path.chmod(0o600)
        plaintext_files.append(path)

    config_path = target / "configuration.json"
    metadata_path = target / "instance.json"
    archive_path = target / "mongodb.archive.gz"
    _atomic_json_write(config_path, config)
    _atomic_json_write(metadata_path, asdict(metadata))
    plaintext_files.extend((config_path, metadata_path))
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
                command, check=False, stdout=output, timeout=3600
            ).returncode
    except (OSError, subprocess.TimeoutExpired) as error:
        shutil.rmtree(target, ignore_errors=True)
        raise TabctlError(
            "MongoDB backup could not run.", EXIT_INFRASTRUCTURE
        ) from error
    if result != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise TabctlError("MongoDB backup failed.", EXIT_INFRASTRUCTURE)
    archive_path.chmod(0o600)
    plaintext_files.append(archive_path)
    if BACKUP_ENV_FILE in component_names:
        env_path = root / ".env"
        if env_path.is_file():
            stage(BACKUP_ENV_FILE, env_path.read_bytes())
    if BACKUP_COMPOSE_FILE in component_names:
        compose_path = root / "compose.yaml"
        if compose_path.is_file():
            stage(BACKUP_COMPOSE_FILE, compose_path.read_bytes())
    if BACKUP_SESSION_ARCHIVE in component_names:
        session_path = target / BACKUP_SESSION_ARCHIVE
        _capture_volume_archive(metadata, "telegram_session", session_path)
        plaintext_files.append(session_path)
    if BACKUP_MEDIA_ARCHIVE in component_names:
        media_path = target / BACKUP_MEDIA_ARCHIVE
        _capture_volume_archive(metadata, "media", media_path)
        plaintext_files.append(media_path)
    plaintext_checksums = {path.name: _sha256(path) for path in plaintext_files}
    stored_files = plaintext_files
    if encrypt:
        passphrase = _backup_passphrase(confirm=True)
        for path in plaintext_files:
            encrypted_path = path.with_name(f"{path.name}.enc")
            _openssl_transform(path, encrypted_path, passphrase, decrypt=False)
            encrypted_path.chmod(0o600)
            path.unlink()
        stored_files = [path.with_name(f"{path.name}.enc") for path in plaintext_files]
    manifest = {
        "schema_version": 1,
        "backup_id": backup_id,
        "instance_name": metadata.instance_slug,
        "timestamp": datetime.now(UTC).isoformat(),
        "application_version": metadata.application_image.rsplit(":", 1)[-1],
        "config_schema_version": config["configuration_schema_version"],
        "mongodb_version": metadata.mongodb_image.rsplit(":", 1)[-1],
        "mode": mode,
        "included_components": list(component_names),
        "encrypted": bool(encrypt),
        "checksums": {path.name: _sha256(path) for path in stored_files},
    }
    if encrypt:
        manifest["algorithm"] = "aes-256-cbc"
        manifest["kdf"] = "pbkdf2-sha256"
        manifest["kdf_iterations"] = ENCRYPTION_ITERATIONS
        manifest["plaintext_checksums"] = plaintext_checksums
    _atomic_json_write(target / "manifest.json", manifest)
    return backup_id


def verify_backup(
    metadata: InstanceMetadata,
    backup_id: str,
    *,
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Verify backup manifest schema and every stored file checksum."""
    target = Path(metadata.installation_path) / "backups" / backup_id
    try:
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TabctlError("Backup manifest is invalid.", EXIT_INPUT) from error
    if manifest.get("schema_version") != 1 or not isinstance(
        manifest.get("checksums"), dict
    ):
        raise TabctlError("Backup identity or schema is invalid.", EXIT_INPUT)
    encrypted = bool(manifest.get("encrypted", False))
    if encrypted and not passphrase:
        raise TabctlError("Backup is encrypted; a passphrase is required.", EXIT_INPUT)
    for filename, expected in manifest["checksums"].items():
        path = target / filename
        if not path.is_file() or _sha256(path) != expected:
            raise TabctlError(f"Backup checksum failed for {filename}.", EXIT_INPUT)
    if encrypted:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            for filename in manifest["checksums"]:
                _openssl_transform(
                    target / filename,
                    directory / filename,
                    passphrase or "",
                    decrypt=True,
                )
            for filename, expected in (
                manifest.get("plaintext_checksums") or {}
            ).items():
                stored_name = f"{filename}.enc"
                candidate = directory / stored_name
                if not candidate.is_file() or _sha256(candidate) != expected:
                    raise TabctlError(
                        f"Backup checksum failed for {filename}.", EXIT_INPUT
                    )
    return cast("dict[str, Any]", manifest)


def _copy_restored_file(
    source: Path, destination: Path, *, mode: int | None = None
) -> None:
    """Copy a restored file while retaining the existing destination owner."""
    shutil.copy2(source, destination)
    if mode is not None:
        destination.chmod(mode)


def _write_runtime_config(metadata: InstanceMetadata, payload: bytes) -> None:
    """Overwrite configuration.json as the configured runtime user.

    The instance configuration is intentionally owned by the runtime UID
    (group-readable by the host manager), so a host-side write can fail with
    PermissionError when the manager is a different, unprivileged user.  This
    helper streams the verified payload through stdin into a narrowly scoped
    ephemeral container that runs as the configured runtime UID/GID and bind
    mounts the instance config directory read-write.  Truncating the existing
    file in place preserves its runtime ownership; mode 0640 is restored
    explicitly.  Configuration bytes never appear in argv or output.
    """
    root = Path(metadata.installation_path)
    coordinates = _read_env_coordinates(root)
    runtime_uid = coordinates.get("TAB_RUNTIME_UID", "10001")
    runtime_gid = coordinates.get("TAB_RUNTIME_GID", "10001")
    command = [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--pull",
        "never",
        "--network",
        "none",
        "--user",
        f"{runtime_uid}:{runtime_gid}",
        "--volume",
        f"{root / 'config'}:/restore/config",
        "--entrypoint",
        "/bin/sh",
        metadata.application_image,
        "-ec",
        "cat > /restore/config/configuration.json; "
        "chmod 0640 /restore/config/configuration.json",
    ]
    try:
        result = subprocess.run(  # noqa: S603
            command, check=False, input=payload, timeout=120
        ).returncode
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TabctlError(
            "Configuration restore could not run.", EXIT_INFRASTRUCTURE
        ) from error
    if result != 0:
        raise TabctlError("Configuration restore failed.", EXIT_INFRASTRUCTURE)


def restore_backup(
    metadata: InstanceMetadata,
    backup_id: str,
    *,
    confirmed: bool,
    to_instance: str | None = None,
) -> None:
    """Restore a verified backup with plan, conflict and rollback protections."""
    if not confirmed:
        raise TabctlError(
            "Restore requires explicit --yes confirmation.", EXIT_CONFIRMATION
        )
    passphrase = _resolve_passphrase()
    manifest = verify_backup(metadata, backup_id, passphrase=passphrase)
    target = _metadata(to_instance) if to_instance else metadata
    source_name = str(manifest.get("instance_name", metadata.instance_slug))
    if to_instance is None and source_name != metadata.instance_slug:
        raise TabctlError(
            f"Backup belongs to instance '{source_name}'; "
            "pass --to-instance to restore it elsewhere.",
            EXIT_INPUT,
        )
    sys.stdout.write(
        f"backup_id={backup_id}\n"
        f"source_instance={source_name}\n"
        f"target_instance={target.instance_slug}\n"
        "components=" + ",".join(map(str, manifest["included_components"])) + "\n"
    )
    root = Path(metadata.installation_path)
    target_root = Path(target.installation_path)
    archive_dir = root / "backups" / backup_id
    if _compose(target, ["up", "-d", "--no-deps", "mongodb"]):
        raise TabctlError("MongoDB could not be started.", EXIT_INFRASTRUCTURE)
    pre_restore_id = create_backup(target, mode=BACKUP_MODE_CORE)
    original_config = (target_root / "config" / "configuration.json").read_bytes()
    try:
        original_env = (target_root / ".env").read_bytes()
    except OSError:
        original_env = None
    try:
        original_compose = (target_root / "compose.yaml").read_bytes()
    except OSError:
        original_compose = None
    if _compose(target, ["stop", "runtime", "approval-bot", "media-cleanup-worker"]):
        raise TabctlError("Services could not be stopped.", EXIT_INFRASTRUCTURE)

    def rollback_files() -> None:
        _write_runtime_config(target, original_config)
        if original_env is not None:
            (target_root / ".env").write_bytes(original_env)
        if original_compose is not None:
            (target_root / "compose.yaml").write_bytes(original_compose)

    staging = archive_dir
    temporary_staging: tempfile.TemporaryDirectory[str] | None = None
    try:
        if manifest.get("encrypted"):
            effective = passphrase or _backup_passphrase(confirm=False)
            temporary_staging = tempfile.TemporaryDirectory()
            staging = Path(temporary_staging.name)
            for filename in manifest["checksums"]:
                plaintext_name = (
                    filename[:-4] if filename.endswith(".enc") else filename
                )
                _openssl_transform(
                    archive_dir / filename,
                    staging / plaintext_name,
                    effective,
                    decrypt=True,
                )
        if (staging / "configuration.json").is_file():
            _write_runtime_config(target, (staging / "configuration.json").read_bytes())
        if to_instance is None:
            if (staging / BACKUP_ENV_FILE).is_file():
                _copy_restored_file(
                    staging / BACKUP_ENV_FILE,
                    target_root / BACKUP_ENV_FILE,
                    mode=0o600,
                )
            if (staging / BACKUP_COMPOSE_FILE).is_file():
                _copy_restored_file(
                    staging / BACKUP_COMPOSE_FILE,
                    target_root / BACKUP_COMPOSE_FILE,
                )
            if (staging / "instance.json").is_file():
                metadata_dir = target_root / "metadata"
                metadata_dir.mkdir(parents=True, exist_ok=True)
                _copy_restored_file(
                    staging / "instance.json",
                    metadata_dir / "instance.json",
                )
        else:
            sys.stdout.write("env_skipped=preserve target credentials and identity\n")
        if (staging / BACKUP_SESSION_ARCHIVE).is_file():
            _restore_volume_archive(
                target, "telegram_session", staging / BACKUP_SESSION_ARCHIVE
            )
        if (staging / BACKUP_MEDIA_ARCHIVE).is_file():
            _restore_volume_archive(target, "media", staging / BACKUP_MEDIA_ARCHIVE)
        if (staging / "mongodb.archive.gz").is_file():
            command = _compose_command(
                target,
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
                with (staging / "mongodb.archive.gz").open("rb") as source:
                    result = subprocess.run(  # noqa: S603
                        command, check=False, stdin=source, timeout=3600
                    ).returncode
            except (OSError, subprocess.TimeoutExpired) as error:
                result = 1
                failure: Exception | None = error
            else:
                failure = None
            if result != 0:
                rollback_files()
                _compose(target, ["up", "-d"])
                raise TabctlError(
                    "Database restore failed; pre-restore backup "
                    f"{pre_restore_id} was preserved.",
                    EXIT_INFRASTRUCTURE,
                ) from failure
    except TabctlError:
        rollback_files()
        _compose(target, ["up", "-d"])
        temporary_close = temporary_staging
        if temporary_close is not None:
            temporary_close.cleanup()
        raise
    if temporary_staging is not None:
        temporary_staging.cleanup()
    if _compose(target, ["up", "-d"]):
        rollback_files()
        raise TabctlError(
            f"Restore completed but startup failed; use backup {pre_restore_id}.",
            EXIT_INFRASTRUCTURE,
        )
    health = _app_check(target)
    if health != 0:
        rollback_files()
        _compose(target, ["up", "-d"])
        raise TabctlError(
            "Restored configuration failed health checks; pre-restore backup "
            f"{pre_restore_id} was preserved.",
            EXIT_INFRASTRUCTURE,
        )
    sys.stdout.write("restore_status=healthy\n")


def export_backup(
    metadata: InstanceMetadata, backup_id: str, *, output: Path | None = None
) -> Path:
    """Bundle a verified backup directory into one portable tar.gz archive."""
    verify_backup(metadata, backup_id, passphrase=_resolve_passphrase())
    root = Path(metadata.installation_path)
    source = root / "backups" / backup_id
    target = output or (root / "backups" / f"backup-{backup_id}.tar.gz")
    if target.exists():
        raise TabctlError("Export target already exists.", EXIT_INPUT)
    with tarfile.open(target, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(source, arcname=f"backup-{backup_id}")
    target.chmod(0o600)
    return target


def import_backup(
    metadata: InstanceMetadata, archive: Path, *, passphrase: str | None = None
) -> str:
    """Import a portable backup archive into an instance's backups directory."""
    if not archive.is_file():
        raise TabctlError("Backup archive was not found.", EXIT_INPUT)
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        try:
            with tarfile.open(archive, "r:gz") as handle:
                handle.extractall(directory, filter="data")
        except (OSError, tarfile.TarError) as error:
            raise TabctlError(
                "Backup archive could not be read.", EXIT_INPUT
            ) from error
        entries = [item for item in directory.iterdir() if item.is_dir()]
        if len(entries) != 1:
            raise TabctlError("Backup archive layout is invalid.", EXIT_INPUT)
        manifest_path = entries[0] / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise TabctlError(
                "Backup archive manifest is invalid.", EXIT_INPUT
            ) from error
        backup_id = str(manifest.get("backup_id", ""))
        if not backup_id or not re.fullmatch(r"[0-9TZ.:-]+", backup_id):
            raise TabctlError("Backup archive identity is invalid.", EXIT_INPUT)
        effective = passphrase or _resolve_passphrase()
        if manifest.get("encrypted") and not effective:
            effective = _backup_passphrase(confirm=False)
        target_dir = Path(metadata.installation_path) / "backups" / backup_id
        if target_dir.exists():
            raise TabctlError("Backup id already exists.", EXIT_INPUT)
        try:
            shutil.copytree(entries[0], target_dir, dirs_exist_ok=False)
            verify_backup(metadata, backup_id, passphrase=effective)
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise
    return backup_id


def _app_check(metadata: InstanceMetadata) -> int:
    """Run a bounded, retryable application configuration health check."""
    command = _compose_command(
        metadata,
        [
            "run",
            "--rm",
            "runtime",
            "check",
            "--config",
            "/app/config/configuration.json",
        ],
    )
    last_code = EXIT_INFRASTRUCTURE
    last_output = ""
    for attempt in range(HEALTH_CHECK_ATTEMPTS):
        try:
            result = subprocess.run(  # noqa: S603
                command,
                check=False,
                timeout=300,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            last_code = EXIT_INFRASTRUCTURE
            last_output = "health check timed out"
        except OSError:
            last_code = EXIT_INFRASTRUCTURE
            last_output = "health check could not be executed"
        else:
            last_code = result.returncode
            last_output = _redact_text(result.stdout + result.stderr)
            if last_code == 0:
                return EXIT_SUCCESS
        if attempt + 1 < HEALTH_CHECK_ATTEMPTS:
            time.sleep(HEALTH_CHECK_RETRY_DELAY_SECONDS)
    details = [
        line.strip()
        for line in last_output.splitlines()
        if line.strip()
        and (
            "error" in line.casefold()
            or "fail" in line.casefold()
            or "exception" in line.casefold()
            or "timed out" in line.casefold()
            or "could not" in line.casefold()
        )
    ][-5:]
    if details:
        sys.stderr.write("health_check_failed=" + " | ".join(details)[:2000] + "\n")
    return last_code


def _run_app_command(metadata: InstanceMetadata, arguments: list[str]) -> int:
    """Run one bounded application CLI command through the runtime service."""
    command = _compose_command(
        metadata,
        [
            "run",
            "--rm",
            "runtime",
            *arguments,
            "--config",
            "/app/config/configuration.json",
        ],
    )
    try:
        result = subprocess.run(command, check=False).returncode  # noqa: S603
    except OSError as error:
        raise TabctlError(
            "Application command could not be executed.", EXIT_INFRASTRUCTURE
        ) from error
    return result


def clear_media(metadata: InstanceMetadata, *, confirmed: bool) -> str:
    """Destructively empty only the selected instance's media volume safely."""
    if not confirmed:
        raise TabctlError(
            "Media reset requires explicit --yes confirmation.", EXIT_CONFIRMATION
        )
    backup_id = create_backup(metadata, mode=BACKUP_MODE_CORE)
    if _compose(metadata, ["stop", "runtime", "approval-bot", "media-cleanup-worker"]):
        raise TabctlError("Services could not be stopped.", EXIT_INFRASTRUCTURE)
    code, _ = _volume_run(
        metadata, "media", "find /mnt -mindepth 1 -delete", timeout_seconds=600
    )
    _compose(metadata, ["up", "-d"])
    if code != 0:
        raise TabctlError(
            f"Media could not be cleared; safety backup {backup_id} is available.",
            EXIT_INFRASTRUCTURE,
        )
    sys.stdout.write(f"safety_backup={backup_id}\nmedia_reset=completed\n")
    return backup_id


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
    backup_id = create_backup(metadata, mode=BACKUP_MODE_CORE)
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
    create_backup(metadata, mode=BACKUP_MODE_CORE)
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
    status = subparsers.add_parser("status")
    status.add_argument("--json", action="store_true")
    subparsers.add_parser("start")
    subparsers.add_parser("stop")
    subparsers.add_parser("restart")
    subparsers.add_parser("login")
    session = subparsers.add_parser("session")
    session_sub = session.add_subparsers(dest="session_action", required=True)
    session_sub.add_parser("status")
    session_reset = session_sub.add_parser("reset")
    session_reset.add_argument("--yes", action="store_true")
    service = subparsers.add_parser("service")
    service_sub = service.add_subparsers(dest="service_action", required=True)
    for action in ("start", "stop", "restart", "recreate"):
        action_parser = service_sub.add_parser(action)
        action_parser.add_argument("name", choices=SERVICE_NAMES)
    env = subparsers.add_parser("env")
    env_sub = env.add_subparsers(dest="env_action", required=True)
    env_sub.add_parser("list")
    env_set = env_sub.add_parser("set")
    env_set.add_argument("key", choices=sorted(ENV_VALUE_ALLOWLIST))
    queue = subparsers.add_parser("queue")
    queue_sub = queue.add_subparsers(dest="queue_action", required=True)
    queue_inspect = queue_sub.add_parser("inspect")
    queue_inspect.add_argument(
        "--kind", choices=("publication", "approval"), default="publication"
    )
    queue_inspect.add_argument(
        "--status",
        choices=("pending", "retry", "permanent-failed", "completed"),
        default="pending",
    )
    queue_inspect.add_argument("--limit", type=int, default=25)
    queue_cancel = queue_sub.add_parser("cancel")
    queue_cancel.add_argument("--job-id", required=True)
    queue_retry = queue_sub.add_parser("retry")
    queue_retry.add_argument("--approval-post-id", required=True)
    queue_recover = queue_sub.add_parser("recover")
    recover_sub = queue_recover.add_subparsers(dest="recover_action", required=True)
    recover_presend = recover_sub.add_parser("presend")
    recover_presend.add_argument("--approval-post-id", required=True)
    recover_immediate = recover_sub.add_parser("immediate")
    recover_immediate.add_argument("--approval-post-id", required=True)
    recover_immediate.add_argument("--dry-run", action="store_true")
    recover_immediate.add_argument("--requeue", action="store_true")
    recover_documents = recover_sub.add_parser("documents")
    recover_documents.add_argument("--approval-post-id")
    recover_documents.add_argument("--from-time")
    recover_documents.add_argument("--to-time")
    recover_documents.add_argument("--dry-run", action="store_true")
    recover_documents.add_argument("--limit", type=int, default=25)
    media = subparsers.add_parser("media")
    media_sub = media.add_subparsers(dest="media_action", required=True)
    media_sub.add_parser("usage")
    media_sub.add_parser("cleanup")
    media_clear = media_sub.add_parser("clear")
    media_clear.add_argument("--yes", action="store_true")
    config = subparsers.add_parser("config")
    config.add_argument("config_action", choices=("check", "set"))
    config.add_argument("key", nargs="?")
    config.add_argument("value", nargs="?")
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
    backup_create = backup_sub.add_parser("create")
    backup_create.add_argument("--mode", choices=BACKUP_MODES, default=BACKUP_MODE_FULL)
    backup_create.add_argument("--exclude-media", action="store_true")
    backup_create.add_argument("--encrypt", action="store_true")
    backup_sub.add_parser("list")
    backup_verify = backup_sub.add_parser("verify")
    backup_verify.add_argument("backup_id")
    backup_restore = backup_sub.add_parser("restore")
    backup_restore.add_argument("backup_id")
    backup_restore.add_argument("--yes", action="store_true")
    backup_restore.add_argument("--to-instance")
    backup_delete = backup_sub.add_parser("delete")
    backup_delete.add_argument("backup_id")
    backup_delete.add_argument("--yes", action="store_true")
    backup_export = backup_sub.add_parser("export")
    backup_export.add_argument("backup_id")
    backup_export.add_argument("--output", type=Path)
    backup_import = backup_sub.add_parser("import")
    backup_import.add_argument("--file", type=Path, required=True)
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
        registry = InstanceRegistry().load()
        if not registry:
            raise TabctlError(
                "No instance is installed yet; run the installer first.",
                EXIT_REGISTRY,
            )
        if len(registry) > 1:
            raise TabctlError(
                "--instance is required when multiple instances exist; "
                "use 'tabctl instance list' first.",
                EXIT_INPUT,
            )
        arguments.instance = next(iter(registry))
    metadata = _metadata(arguments.instance)
    if arguments.command == "session":
        if arguments.session_action == "status":
            status = session_status(metadata)
            sys.stdout.write(f"state={status['state']}\n")
            for entry in status["files"]:
                sys.stdout.write(
                    f"file={entry['name']}\tmodified={entry['modified_at']}\n"
                )
        else:
            reset_session(metadata, confirmed=arguments.yes)
        return EXIT_SUCCESS
    if arguments.command == "service":
        action = arguments.service_action
        name: str = arguments.name
        if action in ("start", "stop", "restart"):
            compose_arguments = [action] if name == "all" else [action, name]
        else:
            compose_arguments = (
                ["up", "-d", "--force-recreate"]
                if name == "all"
                else ["up", "-d", "--force-recreate", "--no-deps", name]
            )
        if _compose(metadata, compose_arguments):
            if name != "all" and action == "start":
                sys.stdout.write(
                    "hint=the container may not exist yet; use 'service recreate' "
                    "or the start-all menu action.\n"
                )
            return EXIT_INFRASTRUCTURE
        return EXIT_SUCCESS
    if arguments.command == "env":
        if arguments.env_action == "list":
            for key, configured in env_list(metadata).items():
                sys.stdout.write(
                    f"{key}=configured\n" if configured else f"{key}=missing\n"
                )
        else:
            env_set(metadata, arguments.key)
        return EXIT_SUCCESS
    if arguments.command == "queue":
        if arguments.queue_action == "inspect":
            if arguments.kind == "approval":
                app_arguments = [
                    "approval-queue",
                    "--status",
                    arguments.status,
                    "--limit",
                    str(arguments.limit),
                ]
            else:
                app_arguments = [
                    "publication-queue",
                    "--status",
                    arguments.status,
                    "--limit",
                    str(arguments.limit),
                ]
        elif arguments.queue_action == "cancel":
            app_arguments = ["publication-cancel", "--job-id", arguments.job_id]
        elif arguments.queue_action == "retry":
            app_arguments = [
                "approval-retry",
                "--approval-post-id",
                arguments.approval_post_id,
            ]
        elif arguments.recover_action == "presend":
            app_arguments = [
                "publication-recover-presend",
                "--approval-post-id",
                arguments.approval_post_id,
            ]
        elif arguments.recover_action == "immediate":
            app_arguments = [
                "publication-recover-immediate",
                "--approval-post-id",
                arguments.approval_post_id,
            ]
            if arguments.dry_run:
                app_arguments.append("--dry-run")
            if arguments.requeue:
                app_arguments.append("--requeue")
        else:
            app_arguments = ["approval-recover-documents"]
            if arguments.approval_post_id:
                app_arguments.extend(["--approval-post-id", arguments.approval_post_id])
            else:
                app_arguments.extend(
                    ["--from-time", arguments.from_time, "--to-time", arguments.to_time]
                )
            if arguments.dry_run:
                app_arguments.append("--dry-run")
            app_arguments.extend(["--limit", str(arguments.limit)])
        result = _run_app_command(metadata, app_arguments)
        return EXIT_SUCCESS if result == 0 else EXIT_INFRASTRUCTURE
    if arguments.command == "media":
        if arguments.media_action == "usage":
            usage = media_usage(metadata)
            if usage["state"] == "unavailable":
                sys.stdout.write("state=unavailable\n")
            else:
                preview = usage["preview_bytes"]
                sys.stdout.write(
                    f"media_bytes={usage['media_bytes']}\n"
                    f"preview_bytes={preview if preview is not None else 'missing'}\n"
                )
        elif arguments.media_action == "cleanup":
            result = _run_app_command(metadata, ["media-cleanup"])
            return EXIT_SUCCESS if result == 0 else EXIT_INFRASTRUCTURE
        else:
            clear_media(metadata, confirmed=arguments.yes)
        return EXIT_SUCCESS
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
            backup_id = create_backup(
                metadata,
                mode=arguments.mode,
                exclude_media=arguments.exclude_media,
                encrypt=arguments.encrypt,
            )
            sys.stdout.write(f"backup_id={backup_id}\n")
        elif arguments.backup_action == "list":
            for manifest in sorted(backup_root.glob("*/manifest.json")):
                sys.stdout.write(f"{manifest.parent.name}\n")
        elif arguments.backup_action == "verify":
            _show_json(
                verify_backup(
                    metadata, arguments.backup_id, passphrase=_resolve_passphrase()
                )
            )
        elif arguments.backup_action == "export":
            path = export_backup(metadata, arguments.backup_id, output=arguments.output)
            sys.stdout.write(f"archive={path}\n")
        elif arguments.backup_action == "import":
            backup_id = import_backup(metadata, arguments.file)
            sys.stdout.write(f"backup_id={backup_id}\n")
        elif arguments.backup_action == "restore":
            restore_backup(
                metadata,
                arguments.backup_id,
                confirmed=arguments.yes,
                to_instance=arguments.to_instance,
            )
        else:
            if not arguments.yes:
                raise TabctlError(
                    "Backup deletion requires explicit --yes.",
                    EXIT_CONFIRMATION,
                )
            verify_backup(
                metadata, arguments.backup_id, passphrase=_resolve_passphrase()
            )
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
    if arguments.command == "status" and arguments.json:
        _show_json(collect_status(metadata))
        return EXIT_SUCCESS
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
        if arguments.config_action == "set":
            operations = {
                "timezone": "timezone-set",
                "preview": "preview-set",
                "cleanup-interval": "cleanup-interval-set",
                "approval-chat": "approval-chat-set",
                "retention": "retention-set",
                "logging": "logging-set",
            }
            operation = operations.get(arguments.key or "")
            if operation is None or arguments.value is None:
                raise TabctlError(
                    "A known configuration key and value are required.", EXIT_INPUT
                )
            return _run_config_mutation(
                metadata, operation=operation, value=arguments.value
            )
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
