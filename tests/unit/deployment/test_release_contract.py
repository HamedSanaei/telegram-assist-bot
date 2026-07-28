"""Production release workflow and acceptance contracts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[3]


def test_release_workflow_has_safe_ghcr_multi_platform_contract() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert 'tags:\n      - "v*.*.*"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "packages: write" in workflow
    assert "contents: read" in workflow
    assert "ghcr.io/hamedsanaei/telegram-assist-bot" in workflow
    assert "linux/amd64,linux/arm64" in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow
    assert "type=semver,pattern={{version}}" in workflow
    assert "type=semver,pattern={{major}}.{{minor}}" in workflow
    assert "type=semver,pattern={{major}}" in workflow
    assert "type=sha,prefix=sha-" in workflow
    assert "GITHUB_TOKEN" in workflow
    assert "pull_request" not in workflow


def test_quality_workflow_runs_docker_and_installer_acceptance_without_push() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert "docker-acceptance:" in workflow
    assert "name: Docker and installer acceptance" in workflow
    assert 'version: "0.11.28"' in workflow
    assert 'python-version: "3.12"' in workflow
    assert "docker --version" in workflow
    assert "docker compose version" in workflow
    assert "bash scripts/v1_acceptance.sh" in workflow
    assert "bash -n install.sh deploy/manage.sh scripts/v1_acceptance.sh" in workflow
    assert "Upload Docker acceptance diagnostics on failure" in workflow
    assert "if: failure()" in workflow
    assert "docker/login-action" not in workflow
    assert "packages: write" not in workflow


def test_release_versions_and_assets_are_exactly_v1_1() -> None:
    package = (ROOT / "src/telegram_assist_bot/__init__.py").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert '__version__: Final[str] = "1.1.0"' in package
    assert 'version = "1.1.0"' in project
    assert "ARG VERSION=1.1.0" in dockerfile
    assert "telegram-assist-bot-v1.1.0.tar.gz" in workflow
    for public_asset in (
        "compose.yaml",
        "install.sh",
        "install.ps1",
        "config/configuration.example.json",
        "docs/RELEASE_CHECKLIST.md",
    ):
        assert public_asset in workflow


def test_acceptance_keeps_instances_isolated_and_down_preserves_data() -> None:
    acceptance = (ROOT / "scripts/v1_acceptance.sh").read_text(encoding="utf-8")

    assert "acceptance-one" in acceptance
    assert "acceptance-two" in acceptance
    assert "--retention-days 7" in acceptance
    assert "-RetentionDays 7" in acceptance
    assert "down --volumes --remove-orphans" in acceptance
    assert "collect_diagnostics" in acceptance
    assert "mongodb_data telegram_session media" in acceptance
    assert "mongo:7.0.32" in acceptance
    assert "tabctl --instance acceptance-one restart" in acceptance


def test_acceptance_exercises_required_management_commands_and_rollbacks() -> None:
    acceptance = (ROOT / "scripts/v1_acceptance.sh").read_text(encoding="utf-8")

    for command in (
        "tabctl instance list",
        "tabctl --instance acceptance-one status",
        "tabctl --instance acceptance-one config check",
        'tabctl --instance acceptance-one admin add "200000001,200000002"',
        "tabctl --instance acceptance-one source add",
        "tabctl --instance acceptance-one source disable SourceOne",
        "tabctl --instance acceptance-one source enable SourceOne",
        "tabctl --instance acceptance-one repair --dry-run",
        "tabctl --instance acceptance-one diagnostics",
    ):
        assert command in acceptance
    assert "Repeated administrator add unexpectedly succeeded" in acceptance
    assert "Repeated source add unexpectedly succeeded" in acceptance
    assert "Invalid retention mutation unexpectedly succeeded" in acceptance
    assert "backups/config" in acceptance
    assert "stat -c '%a'" in acceptance
    assert "Diagnostics exposed an acceptance credential fixture" in acceptance
