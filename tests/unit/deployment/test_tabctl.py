"""Registry and dispatch tests for the global multi-instance manager."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from io import BufferedReader, BufferedWriter
    from types import ModuleType

ROOT = Path(__file__).parents[3]


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "tabctl_deployment", ROOT / "deploy" / "tabctl.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tabctl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    module = _load_module()
    monkeypatch.setenv("TAB_REGISTRY_PATH", str(tmp_path / "registry.json"))
    return module


def _instance(path: Path, *, project: str = "telegram-assist-demo") -> None:
    path.mkdir(parents=True)
    (path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (path / ".env").write_text(
        "\n".join(
            (
                f"COMPOSE_PROJECT_NAME={project}",
                "TAB_IMAGE=example.invalid/app:1.0.0",
                "TAB_MONGODB_DATABASE=telegram_assist_demo",
                "TAB_MONGODB_IMAGE=mongo:7.0.32",
                "TAB_MONGODB_PASSWORD=must-not-enter-metadata",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (path / "config").mkdir()
    (path / "config" / "configuration.json").write_text(
        json.dumps(
            {
                "admins": [
                    {"telegram_user_id": 7001, "permissions": ["approval.view"]}
                ],
                "source_channels": [{"username": "sourceone", "enabled": True}],
                "destination_channels": [{"name": "primary", "enabled": True}],
                "media": {"retention_days": 2},
            }
        ),
        encoding="utf-8",
    )


def test_import_custom_path_registers_explicit_slug_without_secrets(
    tabctl: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "admin1"
    _instance(path)
    metadata = tabctl.import_instance(path, "kingofilter")

    assert metadata.instance_slug == "kingofilter"
    assert Path(metadata.installation_path).name == "admin1"
    registry_text = Path(os.environ["TAB_REGISTRY_PATH"]).read_text(encoding="utf-8")
    assert "must-not-enter-metadata" not in registry_text
    assert "TAB_MONGODB_PASSWORD" not in registry_text
    assert json.loads(registry_text)["schema_version"] == 1


def test_legacy_kingofilter_import_repair_and_update_preserve_instance_data(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "admin1"
    _instance(path, project="telegram-assist-kingofilter")
    env_path = path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "example.invalid/app:1.0.0",
            "ghcr.io/hamedsanaei/telegram-assist-bot:1.0.0",
        ),
        encoding="utf-8",
    )
    compose_path = path / "compose.yaml"
    compose_path.write_text(
        """services:
  mongodb:
    image: mongo:8.0.21
volumes:
  mongodb_data:
  telegram_session:
  media:
""",
        encoding="utf-8",
    )
    config_path = path / "config" / "configuration.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["source_channels"][0]["username"] = "customsource"
    config["destination_channels"][0]["name"] = "مقصد سفارشی"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False),
        encoding="utf-8",
    )
    session_path = path / "var" / "sessions" / "kingofilter.session"
    session_path.parent.mkdir(parents=True)
    session_path.write_bytes(b"synthetic-session-fixture")
    original_env = env_path.read_bytes()
    original_config = config_path.read_bytes()
    original_session = session_path.read_bytes()

    def unexpected_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("import must not touch Docker resources")

    monkeypatch.setattr(tabctl.subprocess, "run", unexpected_subprocess)
    metadata = tabctl.import_instance(path, "kingofilter")

    assert metadata.instance_slug == "kingofilter"
    assert metadata.installation_path == str(path.resolve())
    assert metadata.compose_project_name == "telegram-assist-kingofilter"
    assert metadata.application_image.endswith(":1.0.0")
    assert metadata.mongodb_image == "mongo:7.0.32"
    assert env_path.read_bytes() == original_env
    assert config_path.read_bytes() == original_config
    assert session_path.read_bytes() == original_session

    plan = tabctl.repair_plan(metadata)
    assert "replace legacy hardcoded MongoDB image" in plan
    monkeypatch.setattr(
        tabctl, "create_backup", lambda value, **kwargs: "repair-backup"
    )
    monkeypatch.setattr(
        tabctl.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0})(),
    )
    tabctl.apply_repair(metadata, confirmed=True)
    repaired_compose = compose_path.read_text(encoding="utf-8")
    assert "image: ${TAB_MONGODB_IMAGE:-mongo:7.0.32}" in repaired_compose
    assert "mongodb_data:" in repaired_compose
    assert "telegram_session:" in repaired_compose
    assert env_path.read_bytes() == original_env
    assert config_path.read_bytes() == original_config
    assert session_path.read_bytes() == original_session

    monkeypatch.setattr(tabctl, "_compose", lambda *args: 0)
    tabctl.update_instance(metadata, version="1.1.0", check_only=False)

    updated_env = env_path.read_text(encoding="utf-8")
    assert "TAB_IMAGE=ghcr.io/hamedsanaei/telegram-assist-bot:1.1.0" in updated_env
    assert "TAB_MONGODB_IMAGE=mongo:7.0.32" in updated_env
    assert compose_path.read_text(encoding="utf-8") == repaired_compose
    assert config_path.read_bytes() == original_config
    assert session_path.read_bytes() == original_session
    registered = tabctl.InstanceRegistry().load()["kingofilter"]
    assert registered.application_image.endswith(":1.1.0")
    assert registered.compose_project_name == "telegram-assist-kingofilter"


def test_registry_rejects_duplicate_path_and_name_conflicts(
    tabctl: ModuleType, tmp_path: Path
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _instance(first)
    _instance(second, project="telegram-assist-other")
    tabctl.import_instance(first, "one")

    with pytest.raises(tabctl.TabctlError, match="path"):
        tabctl.import_instance(first, "two")
    with pytest.raises(tabctl.TabctlError, match="another path"):
        tabctl.import_instance(second, "one")


def test_unregister_preserves_instance_files(
    tabctl: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    tabctl.InstanceRegistry().unregister("demo")
    assert (path / ".env").exists()
    assert (path / "compose.yaml").exists()
    assert tabctl.InstanceRegistry().load() == {}


def test_invalid_metadata_is_rejected(tabctl: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata_dir = path / "metadata"
    metadata_dir.mkdir()
    (metadata_dir / "instance.json").write_text(
        '{"schema_version": 99}\n', encoding="utf-8"
    )
    with pytest.raises(tabctl.TabctlError, match="invalid"):
        tabctl.import_instance(path, "demo")


def test_status_dispatch_uses_metadata_project_not_basename(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "admin1"
    _instance(path, project="telegram-assist-kingofilter")
    tabctl.import_instance(path, "kingofilter")
    commands: list[list[str]] = []

    class _Result:
        returncode = 0

    def fake_run(command: list[str], *, check: bool) -> _Result:
        assert check is False
        commands.append(command)
        return _Result()

    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)
    result = tabctl.main(["--instance", "kingofilter", "status"])
    assert result == 0
    assert "--project-name" in commands[0]
    assert "telegram-assist-kingofilter" in commands[0]
    assert commands[0][-1] == "ps"


def test_logs_default_is_bounded_and_not_following(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    commands: list[list[str]] = []

    class _Result:
        returncode = 0

    def fake_run(command: list[str], check: bool) -> _Result:
        commands.append(command)
        return _Result()

    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)
    assert tabctl.main(["--instance", "demo", "logs"]) == 0
    assert commands[0][-3:] == ["logs", "--tail", "200"]
    assert "--follow" not in commands[0]


def test_admin_add_delegates_to_typed_container_and_restarts_services(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    commands: list[list[str]] = []

    class _Result:
        returncode = 0

    def fake_run(command: list[str], *, check: bool) -> _Result:
        commands.append(command)
        return _Result()

    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)
    monkeypatch.setattr(
        tabctl,
        "_running_application_services",
        lambda metadata: ["runtime", "approval-bot", "media-cleanup-worker"],
    )
    assert (
        tabctl.main(
            [
                "--instance",
                "demo",
                "admin",
                "add",
                "7002,7003",
            ]
        )
        == 0
    )
    assert "operator-config" in commands[0]
    assert commands[0][commands[0].index("--user") + 1] == "0:0"
    assert "admin-add" in commands[0]
    assert "primary" in commands[0]
    assert commands[1][-4:] == [
        "restart",
        "runtime",
        "approval-bot",
        "media-cleanup-worker",
    ]


def test_config_mutation_does_not_start_stopped_application_services(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata = tabctl.import_instance(path, "demo")
    commands: list[list[str]] = []

    class _Result:
        returncode = 0

    def fake_run(command: list[str], **kwargs: object) -> _Result:
        commands.append(command)
        return _Result()

    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)
    monkeypatch.setattr(tabctl, "_running_application_services", lambda value: [])

    assert (
        tabctl._run_config_mutation(
            metadata,
            operation="retention-set",
            value="3",
        )
        == 0
    )
    assert len(commands) == 1


def test_manager_defines_complete_interactive_menus() -> None:
    text = (ROOT / "deploy" / "tabctl.py").read_text(encoding="utf-8")
    assert "1. List Instances" in text
    assert "8. Exit" in text
    assert "1. Status" in text
    assert "18. Purge Instance" in text
    assert "19. Back" in text
    assert "jq" not in text


def test_backup_manifest_and_checksum_verification(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    config = json.loads(
        (path / "config" / "configuration.json").read_text(encoding="utf-8")
    )
    config["configuration_schema_version"] = 1
    (path / "config" / "configuration.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    metadata = tabctl.import_instance(path, "demo")

    class _Result:
        returncode = 0

    def fake_run(command: list[str], **kwargs: object) -> _Result:
        output = kwargs.get("stdout")
        if output is not None:
            cast("BufferedWriter", output).write(b"synthetic mongodb archive")
        return _Result()

    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)
    backup_id = tabctl.create_backup(metadata, mode=tabctl.BACKUP_MODE_FULL)
    manifest = tabctl.verify_backup(metadata, backup_id)
    assert manifest["included_components"] == [
        "configuration.json",
        "instance.json",
        "mongodb.archive.gz",
        ".env",
        "compose.yaml",
        "session.tar.gz",
        "media.tar.gz",
    ]
    assert manifest["mode"] == "full"
    assert "configuration.json" in manifest["checksums"]
    archive = path / "backups" / backup_id / "mongodb.archive.gz"
    archive.write_bytes(b"tampered")
    with pytest.raises(tabctl.TabctlError, match="checksum"):
        tabctl.verify_backup(metadata, backup_id)


def test_restore_volume_archive_forwards_archive_on_stdin(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata = tabctl.import_instance(path, "demo")
    archive = tmp_path / "session.tar.gz"
    archive.write_bytes(b"verified archive")
    commands: list[list[str]] = []
    stdin_payloads: list[bytes] = []

    class _Result:
        returncode = 0

    def fake_run(command: list[str], **kwargs: object) -> _Result:
        commands.append(command)
        stream = kwargs.get("stdin")
        assert stream is not None
        stdin_payloads.append(cast("BufferedReader", stream).read())
        return _Result()

    monkeypatch.setattr(tabctl, "_volume_run", lambda *args, **kwargs: (0, ""))
    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)

    tabctl._restore_volume_archive(metadata, "telegram_session", archive)

    assert "--interactive" in commands[0]
    assert stdin_payloads == [b"verified archive"]


def test_core_backup_keeps_original_component_set(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    config = json.loads(
        (path / "config" / "configuration.json").read_text(encoding="utf-8")
    )
    config["configuration_schema_version"] = 1
    (path / "config" / "configuration.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    metadata = tabctl.import_instance(path, "demo")

    class _Result:
        returncode = 0

    def fake_run(command: list[str], **kwargs: object) -> _Result:
        output = kwargs.get("stdout")
        if output is not None:
            cast("BufferedWriter", output).write(b"synthetic mongodb archive")
        return _Result()

    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)
    backup_id = tabctl.create_backup(metadata, mode=tabctl.BACKUP_MODE_CORE)
    manifest = tabctl.verify_backup(metadata, backup_id)
    assert manifest["included_components"] == [
        "configuration.json",
        "instance.json",
        "mongodb.archive.gz",
    ]
    assert manifest["mode"] == "core"
    assert not (path / "backups" / backup_id / "session.tar.gz").exists()


def test_backup_rejects_direct_config_secrets(
    tabctl: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    config_path = path / "config" / "configuration.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["configuration_schema_version"] = 1
    config["telegram"] = {"bot": {"token": {"direct": "do-not-back-up"}}}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    metadata = tabctl.import_instance(path, "demo")
    with pytest.raises(tabctl.TabctlError, match="Direct secrets"):
        tabctl.create_backup(metadata)


def test_restore_requires_explicit_confirmation(
    tabctl: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata = tabctl.import_instance(path, "demo")
    with pytest.raises(tabctl.TabctlError) as failure:
        tabctl.restore_backup(metadata, "missing", confirmed=False)
    assert failure.value.exit_code == 4


def test_update_failure_restores_env_and_config(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata = tabctl.import_instance(path, "demo")
    original_env = (path / ".env").read_bytes()
    original_config = (path / "config" / "configuration.json").read_bytes()
    monkeypatch.setattr(tabctl, "create_backup", lambda value, **kwargs: "backup-id")

    class _Result:
        returncode = 0

    monkeypatch.setattr(tabctl.subprocess, "run", lambda *args, **kwargs: _Result())
    results = iter((0, 1, 0))
    monkeypatch.setattr(tabctl, "_compose", lambda *args: next(results))
    with pytest.raises(tabctl.TabctlError, match="rolled back"):
        tabctl.update_instance(metadata, version="1.1.0", check_only=False)
    assert (path / ".env").read_bytes() == original_env
    assert (path / "config" / "configuration.json").read_bytes() == original_config
    assert tabctl.InstanceRegistry().load()["demo"].application_image.endswith(":1.0.0")


def test_update_rejects_floating_version(tabctl: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata = tabctl.import_instance(path, "demo")
    with pytest.raises(tabctl.TabctlError, match="exact SemVer"):
        tabctl.update_instance(metadata, version="latest", check_only=False)


def test_update_check_reports_current_release_without_mutation(
    tabctl: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata = tabctl.import_instance(path, "demo")
    original_env = (path / ".env").read_bytes()

    tabctl.update_instance(metadata, version=None, check_only=True)

    output = capsys.readouterr().out
    assert "old_image=example.invalid/app:1.0.0" in output
    assert "new_image=example.invalid/app:1.1.3" in output
    assert (path / ".env").read_bytes() == original_env


def test_update_rollback_restores_image_config_and_registry(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata = tabctl.import_instance(path, "demo")
    backup_id = "before-update"
    backup_root = path / "backups" / backup_id
    backup_root.mkdir(parents=True)
    original_config = (path / "config" / "configuration.json").read_bytes()
    (backup_root / "configuration.json").write_bytes(original_config)
    (path / "config" / "configuration.json").write_text("{}", encoding="utf-8")
    tabctl._replace_env_value(
        path / ".env",
        "TAB_IMAGE",
        "example.invalid/app:1.1.0",
    )
    updated = tabctl.InstanceMetadata(
        **{
            **tabctl.asdict(metadata),
            "application_image": "example.invalid/app:1.1.0",
            "last_successful_update_version": "1.1.0",
        }
    )
    tabctl._atomic_json_write(
        path / "metadata" / "instance.json",
        tabctl.asdict(updated),
    )
    tabctl.InstanceRegistry().register(updated)
    tabctl._atomic_json_write(
        path / "metadata" / "update-state.json",
        {
            "schema_version": 1,
            "old_image": "example.invalid/app:1.0.0",
            "backup_id": backup_id,
        },
    )
    monkeypatch.setattr(tabctl, "verify_backup", lambda *args: {})
    monkeypatch.setattr(tabctl, "_compose", lambda *args: 0)

    tabctl.rollback_update(updated)

    assert "TAB_IMAGE=example.invalid/app:1.0.0" in (path / ".env").read_text(
        encoding="utf-8"
    )
    assert (path / "config" / "configuration.json").read_bytes() == original_config
    assert tabctl.InstanceRegistry().load()["demo"].application_image.endswith(":1.0.0")
    assert not (path / "metadata" / "update-state.json").exists()


def test_repair_plan_is_bounded_and_idempotent(
    tabctl: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    env_path = path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "TAB_MONGODB_IMAGE=mongo:7.0.32\n", ""
        ),
        encoding="utf-8",
    )
    (path / "compose.yaml").write_text(
        "services:\n  mongodb:\n    image: mongo:8.0.21\n", encoding="utf-8"
    )
    metadata = tabctl.import_instance(path, "demo")
    plan = tabctl.repair_plan(metadata)
    assert "add TAB_MONGODB_IMAGE=mongo:7.0.32" in plan
    assert "replace legacy hardcoded MongoDB image" in plan
    assert not any("delete" in action.casefold() for action in plan)


def test_repair_apply_prompts_unless_yes_is_explicit(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    confirmations: list[bool] = []

    monkeypatch.setattr("builtins.input", lambda prompt: "yes")

    def fake_apply_repair(metadata: object, *, confirmed: bool) -> list[str]:
        confirmations.append(confirmed)
        return []

    monkeypatch.setattr(
        tabctl,
        "apply_repair",
        fake_apply_repair,
    )

    assert tabctl.main(["--instance", "demo", "repair", "--apply"]) == 0
    assert confirmations == [True]


def test_diagnostics_export_contains_only_redacted_report(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata = tabctl.import_instance(path, "demo")
    monkeypatch.setattr(
        tabctl,
        "_capture",
        lambda command: (
            0,
            # pragma: allowlist nextline secret
            'mongodb://user:password@host {"level":"ERROR","token":"123456:'
            + ("A" * 35)
            + '"}',
        ),
    )
    archive_path = tabctl.export_diagnostics(metadata)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["diagnostics.json"]
        report = archive.read("diagnostics.json").decode("utf-8")
    assert "password@host" not in report
    assert "123456:" not in report
    assert ".env" not in archive.namelist()


def test_status_json_reports_structured_state_without_secrets(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")

    class _Result:
        returncode = 0

    monkeypatch.setattr(tabctl.subprocess, "run", lambda *a, **k: _Result())
    monkeypatch.setattr(
        tabctl,
        "_capture",
        lambda command: (
            0,
            json.dumps(
                {
                    "Service": "runtime",
                    "Name": "/demo-runtime",
                    "State": "running",
                    "Health": "healthy",
                    "Image": "example.invalid/app:1.0.0",
                }
            ),
        ),
    )
    monkeypatch.setattr(
        tabctl,
        "session_status",
        lambda metadata: {
            "state": "present",
            "files": [{"name": "demo.session", "modified_at": "2026-01-01T00:00:00Z"}],
        },
    )
    monkeypatch.setattr(
        tabctl,
        "media_usage",
        lambda metadata: {
            "state": "available",
            "media_bytes": 1024,
            "preview_bytes": 0,
        },
    )

    assert tabctl.main(["--instance", "demo", "status", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["instance"] == "demo"
    assert report["containers"][0]["service"] == "runtime"
    assert report["session"]["state"] == "present"
    assert report["media"]["media_bytes"] == 1024
    assert "must-not-enter-metadata" not in capsys.readouterr().out


def test_session_status_dispatch_and_reset_confirmation(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    monkeypatch.setattr(
        tabctl,
        "session_status",
        lambda metadata: {
            "state": "present",
            "files": [{"name": "demo.session", "modified_at": "2026-01-01T00:00:00Z"}],
        },
    )

    assert tabctl.main(["--instance", "demo", "session", "status"]) == 0
    output = capsys.readouterr().out
    assert "state=present" in output
    assert "file=demo.session" in output

    assert tabctl.main(["--instance", "demo", "session", "reset"]) == 4
    monkeypatch.setattr(tabctl, "_volume_run", lambda *a, **k: (0, ""))
    assert tabctl.main(["--instance", "demo", "session", "reset", "--yes"]) == 0
    assert "session_reset=completed" in capsys.readouterr().out


def test_service_dispatch_maps_bounded_compose_arguments(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    commands: list[list[str]] = []

    class _Result:
        returncode = 0

    def fake_run(command: list[str], **kwargs: object) -> _Result:
        commands.append(command)
        return _Result()

    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)

    assert tabctl.main(["--instance", "demo", "service", "restart", "runtime"]) == 0
    assert commands[0][-2:] == ["restart", "runtime"]
    assert tabctl.main(["--instance", "demo", "service", "start", "approval-bot"]) == 0
    assert commands[1][-2:] == ["start", "approval-bot"]
    assert tabctl.main(["--instance", "demo", "service", "stop", "mongodb"]) == 0
    assert commands[2][-2:] == ["stop", "mongodb"]
    assert tabctl.main(["--instance", "demo", "service", "recreate", "all"]) == 0
    assert commands[3][-2:] == ["up", "-d", "--force-recreate"][-2:]


def test_queue_dispatch_builds_application_arguments(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    captured: list[list[str]] = []

    def fake_run_app(metadata: object, arguments: list[str]) -> int:
        captured.append(arguments)
        return 0

    monkeypatch.setattr(tabctl, "_run_app_command", fake_run_app)

    assert (
        tabctl.main(
            [
                "--instance",
                "demo",
                "queue",
                "inspect",
                "--kind",
                "approval",
                "--status",
                "retry",
                "--limit",
                "10",
            ]
        )
        == 0
    )
    assert captured[0] == ["approval-queue", "--status", "retry", "--limit", "10"]
    assert (
        tabctl.main(["--instance", "demo", "queue", "cancel", "--job-id", "job-1"]) == 0
    )
    assert captured[1] == ["publication-cancel", "--job-id", "job-1"]
    assert (
        tabctl.main(
            [
                "--instance",
                "demo",
                "queue",
                "recover",
                "immediate",
                "--approval-post-id",
                "post-1",
                "--dry-run",
            ]
        )
        == 0
    )
    assert captured[2] == [
        "publication-recover-immediate",
        "--approval-post-id",
        "post-1",
        "--dry-run",
    ]


def test_config_set_maps_to_typed_mutation(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    captured: list[tuple[str, str]] = []

    def fake_mutation(metadata: object, *, operation: str, value: str) -> int:
        del metadata
        captured.append((operation, value))
        return 0

    monkeypatch.setattr(tabctl, "_run_config_mutation", fake_mutation)

    assert (
        tabctl.main(["--instance", "demo", "config", "set", "timezone", "Asia/Tehran"])
        == 0
    )
    assert captured == [("timezone-set", "Asia/Tehran")]
    assert tabctl.main(["--instance", "demo", "config", "set", "bogus", "x"]) == 2
    assert len(captured) == 1


def test_env_set_reads_stdin_and_never_exposes_value(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    env_path = path / ".env"

    monkeypatch.setattr(tabctl.sys, "stdin", io.StringIO("123456:SECRETTOKENVALUE\n"))
    assert (
        tabctl.main(["--instance", "demo", "env", "set", "TAB_TELEGRAM_BOT_TOKEN"]) == 0
    )
    output = capsys.readouterr().out
    assert "updated (value hidden)" in output
    assert "SECRETTOKENVALUE" not in output
    assert "TAB_TELEGRAM_BOT_TOKEN=123456:SECRETTOKENVALUE" in env_path.read_text(
        encoding="utf-8"
    )

    with pytest.raises(SystemExit):
        tabctl.main(["--instance", "demo", "env", "set", "TAB_TELEGRAM_API_ID"])
    assert "TAB_TELEGRAM_API_ID=not-allowed" not in env_path.read_text(encoding="utf-8")


def test_env_list_never_prints_values(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    assert tabctl.main(["--instance", "demo", "env", "list"]) == 0
    output = capsys.readouterr().out
    assert "TAB_TELEGRAM_BOT_TOKEN=missing" in output
    assert "TAB_MONGODB_PASSWORD=configured" in output
    assert "must-not-enter-metadata" not in output


def test_media_clear_requires_confirmation_and_creates_safety_backup(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    monkeypatch.setattr(tabctl, "create_backup", lambda *a, **k: "safety-id")
    monkeypatch.setattr(tabctl, "_compose", lambda *a: 0)
    monkeypatch.setattr(tabctl, "_volume_run", lambda *a, **k: (0, ""))

    assert tabctl.main(["--instance", "demo", "media", "clear"]) == 4
    assert tabctl.main(["--instance", "demo", "media", "clear", "--yes"]) == 0
    output = capsys.readouterr().out
    assert "safety_backup=safety-id" in output
    assert "media_reset=completed" in output


def test_backup_encryption_roundtrip(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    config = json.loads(
        (path / "config" / "configuration.json").read_text(encoding="utf-8")
    )
    config["configuration_schema_version"] = 1
    (path / "config" / "configuration.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    metadata = tabctl.import_instance(path, "demo")

    class _Result:
        returncode = 0

    class _EncryptResult:
        returncode = 0

    class _FailResult:
        returncode = 1

    def fake_run(command: list[str], **kwargs: object) -> _Result:
        if command[0] == "openssl":
            source = Path(str(command[command.index("-in") + 1]))
            target = Path(str(command[command.index("-out") + 1]))
            payload = source.read_bytes()
            secret = cast("bytes", kwargs.get("input", b"pw"))
            if "-d" in command:
                if secret != b"pw" or not payload.startswith(b"enc:"):
                    return cast("_Result", _FailResult())
                target.write_bytes(payload[4:])
            else:
                target.write_bytes(b"enc:" + payload)
            return cast("_Result", _EncryptResult())
        output = kwargs.get("stdout")
        if output is not None:
            cast("BufferedWriter", output).write(b"synthetic mongodb archive")
        return cast("_Result", _EncryptResult())

    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)
    monkeypatch.setattr(tabctl, "_backup_passphrase", lambda *, confirm: "pw")

    backup_id = tabctl.create_backup(
        metadata, mode=tabctl.BACKUP_MODE_CORE, encrypt=True
    )
    manifest = tabctl.verify_backup(metadata, backup_id, passphrase="pw")  # noqa: S106
    assert manifest["encrypted"] is True
    assert manifest["algorithm"] == "aes-256-cbc"
    with pytest.raises(tabctl.TabctlError, match="passphrase is required"):
        tabctl.verify_backup(metadata, backup_id)
    stored = path / "backups" / backup_id
    assert not (stored / "configuration.json").exists()
    assert (stored / "configuration.json.enc").exists()
    with pytest.raises(tabctl.TabctlError, match="decryption failed"):
        tabctl.verify_backup(
            metadata,
            backup_id,
            passphrase="wrong",  # noqa: S106
        )


def test_restore_conflict_requires_to_instance(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _instance(first)
    _instance(second, project="telegram-assist-other")
    source = tabctl.import_instance(first, "demo")
    tabctl.import_instance(second, "other")
    backup_id = "20260101T000000.000000Z"
    backup_root = first / "backups" / backup_id
    backup_root.mkdir(parents=True)
    config_bytes = (first / "config" / "configuration.json").read_bytes()
    (backup_root / "configuration.json").write_bytes(config_bytes)
    (backup_root / "instance.json").write_text("{}\n", encoding="utf-8")
    (backup_root / "mongodb.archive.gz").write_bytes(b"archive")
    manifest = {
        "schema_version": 1,
        "backup_id": backup_id,
        "instance_name": "other",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "application_version": "1.0.0",
        "config_schema_version": 1,
        "mongodb_version": "7.0.32",
        "mode": "core",
        "included_components": [
            "configuration.json",
            "instance.json",
            "mongodb.archive.gz",
        ],
        "encrypted": False,
        "checksums": {
            name: tabctl._sha256(backup_root / name)
            for name in ("configuration.json", "instance.json", "mongodb.archive.gz")
        },
    }
    (backup_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(tabctl.TabctlError, match="belongs to instance"):
        tabctl.restore_backup(source, backup_id, confirmed=True)

    class _Result:
        returncode = 0

    monkeypatch.setattr(tabctl, "create_backup", lambda *a, **k: "pre")
    monkeypatch.setattr(tabctl, "_compose", lambda *a: 0)
    monkeypatch.setattr(tabctl, "_app_check", lambda *a: 0)
    monkeypatch.setattr(tabctl.subprocess, "run", lambda *a, **k: _Result())
    tabctl.restore_backup(source, backup_id, confirmed=True, to_instance="other")
    output = capsys.readouterr().out
    assert "env_skipped=preserve target credentials and identity" in output
    assert "restore_status=healthy" in output
    assert (second / "config" / "configuration.json").read_bytes() == config_bytes
    assert "TAB_MONGODB_PASSWORD" in (second / ".env").read_text(encoding="utf-8")


def _restore_fixture(
    tabctl: ModuleType, path: Path, backup_id: str, backup_config: bytes
) -> Path:
    """Scaffold a verified single-component backup next to an instance."""
    backup_root = path / "backups" / backup_id
    backup_root.mkdir(parents=True)
    (backup_root / "configuration.json").write_bytes(backup_config)
    manifest = {
        "schema_version": 1,
        "backup_id": backup_id,
        "instance_name": "demo",
        "timestamp": "2026-01-01T01:00:00+00:00",
        "application_version": "1.0.0",
        "config_schema_version": 1,
        "mongodb_version": "7.0.32",
        "mode": "core",
        "included_components": ["configuration.json"],
        "encrypted": False,
        "checksums": {
            "configuration.json": tabctl._sha256(backup_root / "configuration.json")
        },
    }
    (backup_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return backup_root


def test_restore_config_writes_through_runtime_boundary(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore must not depend on the host manager owning configuration.json."""
    path = tmp_path / "first"
    other = tmp_path / "second"
    _instance(path)
    _instance(other, project="telegram-assist-other")
    source = tabctl.import_instance(path, "demo")
    tabctl.import_instance(other, "other")
    other_config = (other / "config" / "configuration.json").read_bytes()
    backup_id = "20260101T010000.000000Z"
    backup_config = b'{"restored": true, "key": "value"}'
    _restore_fixture(tabctl, path, backup_id, backup_config)

    commands: list[list[str]] = []
    payloads: list[bytes] = []

    class _Result:
        returncode = 0

    def fake_run(command: list[str], **kwargs: object) -> _Result:
        commands.append(command)
        payload = cast("bytes", kwargs.get("input", b""))
        payloads.append(payload)
        if "/bin/sh" in command and any(
            "/restore/config/configuration.json" in arg for arg in command
        ):
            volume = command[command.index("--volume") + 1]
            host_dir = Path(volume.rsplit(":", 1)[0])
            (host_dir / "configuration.json").write_bytes(payload)
        return _Result()

    monkeypatch.setattr(tabctl, "create_backup", lambda *a, **k: "pre")
    monkeypatch.setattr(tabctl, "_compose", lambda *a: 0)
    monkeypatch.setattr(tabctl, "_app_check", lambda *a: 0)
    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)

    tabctl.restore_backup(source, backup_id, confirmed=True)

    write_commands = [
        index
        for index, command in enumerate(commands)
        if "/bin/sh" in command
        and any("/restore/config/configuration.json" in arg for arg in command)
    ]
    assert len(write_commands) == 1
    write_command = commands[write_commands[0]]
    assert write_command[:2] == ["docker", "run"]
    assert write_command[write_command.index("--user") + 1] == "10001:10001"
    assert write_command[write_command.index("--network") + 1] == "none"
    volume = write_command[write_command.index("--volume") + 1]
    assert Path(volume.rsplit(":", 1)[0]).resolve() == (path / "config").resolve()
    assert "--pull" in write_command
    assert all(backup_config not in arg.encode() for arg in write_command)
    assert payloads[write_commands[0]] == backup_config
    assert (path / "config" / "configuration.json").read_bytes() == backup_config
    assert (other / "config" / "configuration.json").read_bytes() == other_config


def test_restore_rollback_config_uses_runtime_boundary(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback restores the original config through the runtime boundary."""
    path = tmp_path / "first"
    _instance(path)
    source = tabctl.import_instance(path, "demo")
    original_config = (path / "config" / "configuration.json").read_bytes()
    backup_id = "20260101T020000.000000Z"
    backup_config = b'{"restored": true, "key": "value"}'
    _restore_fixture(tabctl, path, backup_id, backup_config)

    commands: list[list[str]] = []
    payloads: list[bytes] = []

    class _Result:
        returncode = 0

    def fake_run(command: list[str], **kwargs: object) -> _Result:
        commands.append(command)
        payload = cast("bytes", kwargs.get("input", b""))
        payloads.append(payload)
        if "/bin/sh" in command and any(
            "/restore/config/configuration.json" in arg for arg in command
        ):
            volume = command[command.index("--volume") + 1]
            host_dir = Path(volume.rsplit(":", 1)[0])
            (host_dir / "configuration.json").write_bytes(payload)
        return _Result()

    monkeypatch.setattr(tabctl, "create_backup", lambda *a, **k: "pre")
    monkeypatch.setattr(tabctl, "_compose", lambda *a: 0)
    monkeypatch.setattr(tabctl, "_app_check", lambda *a: 1)
    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)

    with pytest.raises(tabctl.TabctlError, match="failed health checks"):
        tabctl.restore_backup(source, backup_id, confirmed=True)

    write_commands = [
        index
        for index, command in enumerate(commands)
        if "/bin/sh" in command
        and any("/restore/config/configuration.json" in arg for arg in command)
    ]
    assert len(write_commands) == 2
    assert payloads[write_commands[0]] == backup_config
    assert payloads[write_commands[1]] == original_config
    assert (path / "config" / "configuration.json").read_bytes() == original_config


def test_default_instance_fallback_uses_single_registered_instance(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "instance"
    _instance(path, project="telegram-assist-solo")
    tabctl.import_instance(path, "solo")
    commands: list[list[str]] = []

    class _Result:
        returncode = 0

    def fake_run(command: list[str], **kwargs: object) -> _Result:
        commands.append(command)
        return _Result()

    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)

    assert tabctl.main(["status"]) == 0
    assert "telegram-assist-solo" in commands[0]
    assert commands[0][-1] == "ps"


def test_multiple_instances_require_explicit_flag(
    tabctl: ModuleType, tmp_path: Path
) -> None:
    _instance(tmp_path / "one", project="telegram-assist-one")
    _instance(tmp_path / "two", project="telegram-assist-two")
    tabctl.import_instance(tmp_path / "one", "one")
    tabctl.import_instance(tmp_path / "two", "two")
    assert tabctl.main(["status"]) == 2


def test_backup_export_and_import_roundtrip(
    tabctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _instance(first)
    _instance(second, project="telegram-assist-second")
    source = tabctl.import_instance(first, "demo")
    tabctl.import_instance(second, "second")
    backup_id = "20260102T000000.000000Z"
    backup_root = first / "backups" / backup_id
    backup_root.mkdir(parents=True)
    (backup_root / "configuration.json").write_bytes(b"cfg")
    (backup_root / "instance.json").write_bytes(b"meta")
    (backup_root / "mongodb.archive.gz").write_bytes(b"db")
    (backup_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "backup_id": backup_id,
                "instance_name": "demo",
                "timestamp": "2026-01-02T00:00:00+00:00",
                "application_version": "1.0.0",
                "config_schema_version": 1,
                "mongodb_version": "7.0.32",
                "mode": "core",
                "included_components": [
                    "configuration.json",
                    "instance.json",
                    "mongodb.archive.gz",
                ],
                "encrypted": False,
                "checksums": {
                    name: tabctl._sha256(backup_root / name)
                    for name in (
                        "configuration.json",
                        "instance.json",
                        "mongodb.archive.gz",
                    )
                },
            }
        ),
        encoding="utf-8",
    )

    archive = tabctl.export_backup(source, backup_id)
    assert archive.name == f"backup-{backup_id}.tar.gz"
    if os.name != "nt":
        assert archive.stat().st_mode & 0o777 == 0o600

    imported = tabctl.import_backup(tabctl._metadata("second"), archive)
    assert imported == backup_id
    assert tabctl.verify_backup(tabctl._metadata("second"), backup_id)["mode"] == "core"


def test_media_usage_dispatch_and_cleanup_routing(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    tabctl.import_instance(path, "demo")
    monkeypatch.setattr(
        tabctl,
        "media_usage",
        lambda metadata: {
            "state": "available",
            "media_bytes": 2048,
            "preview_bytes": 64,
        },
    )
    assert tabctl.main(["--instance", "demo", "media", "usage"]) == 0
    output = capsys.readouterr().out
    assert "media_bytes=2048" in output
    assert "preview_bytes=64" in output

    captured: list[list[str]] = []

    def fake_run(metadata: object, arguments: list[str]) -> int:
        del metadata
        captured.append(arguments)
        return 0

    monkeypatch.setattr(tabctl, "_run_app_command", fake_run)
    assert tabctl.main(["--instance", "demo", "media", "cleanup"]) == 0
    assert captured == [["media-cleanup"]]


def test_app_check_retries_transient_failure_and_redacts_output(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata = tabctl.import_instance(path, "demo")
    attempts: list[int] = []
    sleeps: list[int] = []

    class _Result:
        def __init__(self, returncode: int, output: str) -> None:
            self.returncode = returncode
            self.stdout = output
            self.stderr = output

    def fake_run(command: list[str], **kwargs: object) -> _Result:
        del command, kwargs
        attempts.append(1)
        if len(attempts) < 3:
            return _Result(
                1,
                "error password=secret bot=123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            )
        return _Result(0, "configuration_validation_succeeded")

    monkeypatch.setattr(tabctl.subprocess, "run", fake_run)
    monkeypatch.setattr(tabctl.time, "sleep", sleeps.append)

    assert tabctl._app_check(metadata) == 0
    assert len(attempts) == 3
    assert sleeps == [tabctl.HEALTH_CHECK_RETRY_DELAY_SECONDS] * 2
    assert "health_check_failed=" not in capsys.readouterr().err


def test_app_check_reports_only_redacted_failure_details(
    tabctl: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "instance"
    _instance(path)
    metadata = tabctl.import_instance(path, "demo")

    class _Result:
        returncode = 1
        stdout = "error password=secret bot=123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        stderr = ""

    monkeypatch.setattr(tabctl.subprocess, "run", lambda *a, **k: _Result())
    monkeypatch.setattr(tabctl.time, "sleep", lambda seconds: None)

    assert tabctl._app_check(metadata) == 1
    error_output = capsys.readouterr().err
    assert "health_check_failed=" in error_output
    assert "secret" not in error_output
    assert "123456:AAAAAAAA" not in error_output
    assert "[REDACTED]" in error_output or "[REDACTED_BOT_TOKEN]" in error_output
