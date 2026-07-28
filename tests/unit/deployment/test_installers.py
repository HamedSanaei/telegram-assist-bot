"""Cross-platform installer safety and dry-run contract tests."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[3]


def test_linux_installer_has_strict_inputs_and_safe_uninstall() -> None:
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    manager = (ROOT / "deploy" / "manage.sh").read_text(encoding="utf-8")

    assert "^[a-z][a-z0-9-]{0,31}$" in installer
    assert "RETENTION_DAYS < 1 || RETENTION_DAYS > 3650" in installer
    assert "--dry-run" in installer
    assert "--update" in installer
    assert "--non-interactive" in installer
    assert "--admin-user-ids" in installer
    assert "--source-usernames" in installer
    assert "TAB_ADMIN_USER_IDS" in installer
    assert "TAB_SOURCE_USERNAMES" in installer
    assert 'configuration.json" && "$UPDATE" -eq 0' in installer
    assert "chmod 600" in installer
    assert "TAB_MONGODB_IMAGE" in installer
    assert "mongo:7.0.32" in installer
    assert "TAB_RUNTIME_UID" in installer
    assert "deployment-preflight" in installer
    assert "permissions.sh" in installer
    assert "repair permissions" in manager
    assert "deploy/tabctl.py" in installer
    assert "/usr/local/bin/tabctl" in installer
    assert ".local/bin/tabctl" in installer
    assert "docker compose" in installer
    assert "down --volumes" not in installer.partition("install_docker")[0]
    assert 'uninstall) "${COMPOSE[@]}" down' in manager
    assert "purge)" in manager
    assert '"${2:-}" != "--yes"' in manager


def test_windows_installer_has_dry_run_resume_acl_and_bounded_engine_wait() -> None:
    installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
    manager = (ROOT / "deploy" / "manage.ps1").read_text(encoding="utf-8")

    assert "[ValidateRange(1, 3650)]" in installer
    assert "^[a-z][a-z0-9-]{0,31}$" in installer
    assert "Docker.DockerDesktop" in installer
    assert "VirtualMachinePlatform" in installer
    assert ".resume" in installer
    assert "AddMinutes(3)" in installer
    assert "Read-Host $Prompt -AsSecureString" in installer
    assert "[string]$AdminUserIds" in installer
    assert "[string]$SourceUsernames" in installer
    assert "TAB_ADMIN_USER_IDS" in installer
    assert "TAB_SOURCE_USERNAMES" in installer
    assert "icacls" in installer
    assert "TAB_MONGODB_IMAGE" in installer
    assert "mongo:7.0.32" in installer
    assert "deployment-preflight" in installer
    assert "permissions.ps1" in installer
    assert '"repair"' in manager
    assert "deploy/tabctl.py" in installer
    assert "deploy/tabctl.ps1" in installer
    assert "Python.Python.3.12" in installer
    assert "-not $Update" in installer
    assert '"uninstall" { & docker @Compose down' in manager
    assert "if (-not $Yes)" in manager


def test_installers_do_not_embed_real_project_credentials() -> None:
    text = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "install.sh",
            "install.ps1",
            "deploy/manage.sh",
            "deploy/manage.ps1",
        )
    )

    assert re.search(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b", text) is None
    assert re.search(r"\b[a-fA-F0-9]{32}\b", text) is None
    assert "github_pat_" not in text
    assert "-----BEGIN PRIVATE KEY-----" not in text


def test_permission_helpers_are_centralized_and_content_preserving() -> None:
    linux = (ROOT / "deploy" / "permissions.sh").read_text(encoding="utf-8")
    windows = (ROOT / "deploy" / "permissions.ps1").read_text(encoding="utf-8")

    assert "chmod 0600" in linux
    assert "chmod 0640" in linux
    assert "chmod 0700" in linux
    assert "chmod 777" not in linux
    assert "rm " not in linux
    assert "truncate" not in linux
    assert "icacls" in windows
    assert "Remove-Item" not in windows
    assert "Set-Content" not in windows


def test_acceptance_covers_nonroot_plural_config_and_two_instance_registry() -> None:
    acceptance = (ROOT / "scripts" / "v1_acceptance.sh").read_text(encoding="utf-8")
    assert "--user 10001:10001" in acceptance
    assert "render-instance-config" in acceptance
    assert "--admin-user-ids" in acceptance
    assert "--source-usernames" in acceptance
    assert "mongo:7.0.32" in acceptance
    assert "instance import" in acceptance
    assert "acceptance-one" in acceptance
    assert "acceptance-two" in acceptance
    assert "runtime check" in acceptance
