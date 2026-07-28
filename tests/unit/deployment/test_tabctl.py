"""Registry and dispatch tests for the global multi-instance manager."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from io import BufferedWriter
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
    monkeypatch.setattr(tabctl, "create_backup", lambda value: "repair-backup")
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
    backup_id = tabctl.create_backup(metadata)
    manifest = tabctl.verify_backup(metadata, backup_id)
    assert manifest["included_components"] == [
        "configuration",
        "metadata",
        "mongodb",
    ]
    assert "configuration.json" in manifest["checksums"]
    archive = path / "backups" / backup_id / "mongodb.archive.gz"
    archive.write_bytes(b"tampered")
    with pytest.raises(tabctl.TabctlError, match="checksum"):
        tabctl.verify_backup(metadata, backup_id)


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
    monkeypatch.setattr(tabctl, "create_backup", lambda value: "backup-id")

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
    assert "new_image=example.invalid/app:1.1.1" in output
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
