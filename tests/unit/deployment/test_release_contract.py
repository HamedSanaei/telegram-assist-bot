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
    assert "bash scripts/v1_acceptance.sh" in workflow
    assert "bash -n install.sh deploy/manage.sh scripts/v1_acceptance.sh" in workflow
    assert "docker/login-action" not in workflow
    assert "packages: write" not in workflow


def test_release_versions_and_assets_are_exactly_v1() -> None:
    package = (ROOT / "src/telegram_assist_bot/__init__.py").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert '__version__: Final[str] = "1.0.0"' in package
    assert 'version = "1.0.0"' in project
    assert "ARG VERSION=1.0.0" in dockerfile
    assert "telegram-assist-bot-v1.0.0.tar.gz" in workflow
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

    assert "telegram-assist-acceptance-a" in acceptance
    assert "telegram-assist-acceptance-b" in acceptance
    assert "--retention-days 7" in acceptance
    assert "-RetentionDays 7" in acceptance
    assert "down" in acceptance
    assert "docker volume inspect telegram-assist-acceptance-a_mongodb_data" in (
        acceptance
    )
    assert "down --volumes" not in acceptance
