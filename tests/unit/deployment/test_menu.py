"""Bash management menu dispatch and safety smoke tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]


@pytest.fixture
def instance(tmp_path: Path) -> Path:
    """Scaffold a registered single-instance registry for menu tests."""
    path = tmp_path / "demo"
    path.mkdir(parents=True)
    (path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    fake_token = "123456:" + ("A" * 35)  # pragma: allowlist secret
    (path / ".env").write_text(
        "COMPOSE_PROJECT_NAME=telegram-assist-demo\n"  # pragma: allowlist secret
        "TAB_IMAGE=example.invalid/app:1.0.0\n"
        "TAB_MONGODB_DATABASE=telegram_assist_demo\n"
        "TAB_MONGODB_IMAGE=mongo:7.0.32\n"
        f"TAB_TELEGRAM_BOT_TOKEN={fake_token}\n",
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
    environment = {
        "TABCTL_PYTHON": sys.executable,
        "TABCTL_MANAGER": str(ROOT / "deploy" / "tabctl.py"),
        "TAB_REGISTRY_PATH": str(tmp_path / "registry.json"),
        "TABCTL_NO_COLOR": "1",
    }
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(ROOT / "deploy" / "tabctl.py"),
            "instance",
            "import",
            "--path",
            str(path),
            "--name",
            "demo",
        ],
        check=True,
        capture_output=True,
        env={**os.environ, **environment},
    )
    return path


def _menu(
    arguments: list[str], environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    bash_executable = shutil.which("bash")
    assert bash_executable is not None, "bash is required for menu tests"
    return subprocess.run(  # noqa: S603
        [bash_executable, str(ROOT / "deploy" / "menu.sh"), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


@pytest.fixture
def menu_env(tmp_path: Path, instance: Path) -> dict[str, str]:
    del instance
    return {
        "TABCTL_PYTHON": sys.executable,
        "TABCTL_MANAGER": str(ROOT / "deploy" / "tabctl.py"),
        "TAB_REGISTRY_PATH": str(tmp_path / "registry.json"),
        "TABCTL_NO_COLOR": "1",
        "PATH": os.environ.get("PATH", ""),
    }


def test_menu_help_requires_no_instance() -> None:
    bash_executable = shutil.which("bash")
    assert bash_executable is not None, "bash is required for menu tests"
    result = subprocess.run(  # noqa: S603
        [bash_executable, str(ROOT / "deploy" / "menu.sh"), "--help"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    assert "Usage: menu.sh" in result.stdout


def test_menu_unknown_action_fails(menu_env: dict[str, str]) -> None:
    result = _menu(["--instance", "demo", "--action", "bogus"], menu_env)
    assert result.returncode == 2
    assert "Unknown action" in result.stderr


def test_menu_status_action_renders_instance_and_hides_secrets(
    menu_env: dict[str, str],
) -> None:
    result = _menu(["--instance", "demo", "--action", "status"], menu_env)
    assert result.returncode == 0
    assert "Instance:" in result.stdout
    assert "demo" in result.stdout
    assert "123456:AAAAAAAA" not in result.stdout
    assert "TAB_TELEGRAM_BOT_TOKEN" not in result.stdout


def test_menu_services_action_lists_container_table(
    menu_env: dict[str, str],
) -> None:
    result = _menu(["--instance", "demo", "--action", "services"], menu_env)
    assert result.returncode == 0
    assert "SERVICE" in result.stdout
    assert "NAME" in result.stdout


def test_menu_session_action_is_read_only(menu_env: dict[str, str]) -> None:
    result = _menu(["--instance", "demo", "--action", "session"], menu_env)
    assert result.returncode == 0
    assert "state=" in result.stdout


def test_menu_queues_action_is_bounded(menu_env: dict[str, str]) -> None:
    result = _menu(["--instance", "demo", "--action", "queues"], menu_env)
    assert result.returncode == 0
    assert "Pending publications" in result.stdout
    assert "Pending approvals" in result.stdout


def test_menu_backups_action_lists_or_empty(menu_env: dict[str, str]) -> None:
    result = _menu(["--instance", "demo", "--action", "backups"], menu_env)
    assert result.returncode == 0
    assert "No backups yet." in result.stdout


def test_menu_doctor_action_reports_without_failing_on_missing_docker(
    menu_env: dict[str, str],
) -> None:
    result = _menu(["--instance", "demo", "--action", "doctor"], menu_env)
    assert "Doctor report" in result.stdout
    if shutil.which("docker") is None:
        assert result.returncode == 1
    assert "Docker executable" in result.stdout or "Docker daemon" in result.stdout


def test_menu_instances_action_lists_registry(menu_env: dict[str, str]) -> None:
    result = _menu(["--instance", "demo", "--action", "instances"], menu_env)
    assert result.returncode == 0
    assert "demo" in result.stdout


def test_menu_requires_terminal_for_interactive_mode(
    menu_env: dict[str, str],
) -> None:
    result = _menu(["--instance", "demo"], menu_env)
    assert result.returncode == 2
    assert "requires a terminal" in result.stderr
