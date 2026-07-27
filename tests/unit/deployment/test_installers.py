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
    assert 'configuration.json" && "$UPDATE" -eq 0' in installer
    assert "chmod 600" in installer
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
    assert "icacls" in installer
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
