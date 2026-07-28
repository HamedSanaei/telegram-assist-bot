"""Production release workflow and acceptance contracts."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[3]


def _release_workflow() -> tuple[str, dict[object, Any]]:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    yaml: Any = import_module("yaml")
    parsed = yaml.safe_load(workflow)
    assert isinstance(parsed, dict)
    return workflow, cast("dict[object, Any]", parsed)


def test_release_workflow_has_safe_ghcr_multi_platform_contract() -> None:
    workflow, parsed = _release_workflow()
    triggers = parsed.get("on", parsed.get(True))
    assert isinstance(triggers, dict)
    dispatch = triggers["workflow_dispatch"]
    assert dispatch["inputs"]["tag"] == {
        "description": "Release tag مثل v1.1.0",
        "required": True,
        "type": "string",
    }

    assert 'tags:\n      - "v*.*.*"' in workflow
    assert parsed["permissions"] == {
        "contents": "write",
        "packages": "write",
    }
    assert "ghcr.io/hamedsanaei/telegram-assist-bot" in workflow
    assert "linux/amd64,linux/arm64" in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow
    assert "type=semver,pattern={{version}}" in workflow
    assert "type=semver,pattern={{major}}.{{minor}}" in workflow
    assert "type=semver,pattern={{major}}" in workflow
    assert "type=raw,value=sha-${{ needs.validate.outputs.short_sha }}" in workflow
    assert "GITHUB_TOKEN" in workflow
    assert "pull_request" not in workflow
    assert "inputs.publish" not in workflow
    assert "push: true" in workflow


def test_release_workflow_validates_existing_exact_version_tag() -> None:
    workflow, parsed = _release_workflow()
    jobs = parsed["jobs"]

    assert jobs["validate"]["name"] == "Validate release tag"
    assert "fetch-depth: 0" in workflow
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in workflow
    assert 'git show-ref --verify --quiet "refs/tags/$tag"' in workflow
    assert 'git checkout --detach "$commit_sha"' in workflow
    assert 'Path("pyproject.toml").read_text(encoding="utf-8")' in workflow
    assert 'if [[ "$tag" != "v$version" ]]' in workflow
    assert jobs["package"]["needs"] == "validate"
    assert jobs["image"]["needs"] == "validate"
    assert jobs["release-assets"]["needs"] == "validate"


def test_release_workflow_publishes_idempotent_github_release_after_dependencies() -> (
    None
):
    workflow, parsed = _release_workflow()
    publish = parsed["jobs"]["publish-release"]

    assert publish["name"] == "Publish GitHub Release"
    assert publish["needs"] == [
        "validate",
        "package",
        "image",
        "release-assets",
    ]
    assert "gh release create" in workflow
    assert "gh release upload" in workflow
    assert "--clobber" in workflow
    assert "--verify-tag" in workflow
    assert "--generate-notes" in workflow
    assert "--draft=false" in workflow
    assert 'title="Telegram Assist Bot $RELEASE_TAG"' in workflow
    assert '"repos/$GITHUB_REPOSITORY/git/ref/tags/$RELEASE_TAG"' in workflow
    assert 'test "$is_draft" = "false"' in workflow
    assert "docker buildx imagetools inspect" in workflow
    assert "SHA256SUMS" in workflow
    assert "release-files/*" in workflow
    assert "$GITHUB_STEP_SUMMARY" in workflow
    assert "needs.image.outputs.digest" in workflow


def test_release_job_checks_out_tag_and_targets_repository_explicitly() -> None:
    _, parsed = _release_workflow()
    publish = parsed["jobs"]["publish-release"]
    steps = publish["steps"]

    checkout = steps[0]
    assert checkout == {
        "name": "Check out release tag",
        "uses": "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5",
        "with": {
            "ref": "${{ needs.validate.outputs.tag }}",
            "fetch-depth": 0,
            "persist-credentials": False,
        },
    }
    download = next(step for step in steps if step["name"] == "Download release files")
    assert download["with"]["path"] == "release-files"

    validation = next(
        step for step in steps if step["name"] == "Validate release files"
    )
    assert 'require_one "wheel" "*.whl"' in validation["run"]
    assert (
        'require_one "source distribution" "telegram_assist_bot-*.tar.gz"'
        in validation["run"]
    )
    assert (
        'require_one "release bundle" "telegram-assist-bot-$RELEASE_TAG.tar.gz"'
        in validation["run"]
    )

    shell = "\n".join(str(step.get("run", "")) for step in steps)
    logical_shell = shell.replace("\\\n", " ")
    release_commands = [
        line.strip() for line in logical_shell.splitlines() if "gh release " in line
    ]
    assert len(release_commands) == 7
    assert all('--repo "$GITHUB_REPOSITORY"' in line for line in release_commands)


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
    assert 'bundle_dir="telegram-assist-bot-$RELEASE_TAG"' in workflow
    assert 'tar -czf "$bundle_dir.tar.gz" "$bundle_dir"' in workflow
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
