"""Static production-container and multi-instance isolation contracts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[3]


def test_dockerfile_is_multistage_locked_non_root_and_direct_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.count("\nFROM ") >= 2
    assert "ghcr.io/astral-sh/uv:0.11.28" in dockerfile
    assert "python:3.12.10-slim-bookworm" in dockerfile
    assert "uv sync --locked" in dockerfile
    assert "uv build --no-build-isolation" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["/app/.venv/bin/python", "-m", "telegram_assist_bot"]' in (
        dockerfile
    )
    assert "TAB_TELEGRAM_BOT_TOKEN" not in dockerfile
    assert "TAB_MONGODB_PASSWORD" not in dockerfile


def test_compose_has_required_services_and_no_global_collision_resources() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    for service in ("mongodb:", "runtime:", "approval-bot:", "media-cleanup-worker:"):
        assert service in compose
    assert "volume-permissions:" in compose
    assert "container_name:" not in compose
    assert "\n    ports:" not in compose
    assert "\n  ports:" not in compose
    assert "name:" not in compose.partition("volumes:")[2]
    assert "${TAB_MONGODB_IMAGE:-mongo:7.0.32}" in compose
    assert "mongo:8.0.21" not in compose
    assert "user: ${TAB_RUNTIME_UID:-10001}:${TAB_RUNTIME_GID:-10001}" in compose
    assert 'user: "0:0"' in compose
    assert "service_completed_successfully" in compose
    assert "condition: service_healthy" in compose
    assert "read_only: true" in compose
    assert "restart: unless-stopped" in compose
    assert "max-size: 10m" in compose
    assert "max-file:" in compose


def test_two_projects_resolve_independent_database_and_project_scopes() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    first = {
        "COMPOSE_PROJECT_NAME": "telegram-assist-first",
        "TAB_MONGODB_DATABASE": "telegram_assist_first",
        "TAB_INSTANCE_DIR": "/srv/telegram-assist/first",
    }
    second = {
        "COMPOSE_PROJECT_NAME": "telegram-assist-second",
        "TAB_MONGODB_DATABASE": "telegram_assist_second",
        "TAB_INSTANCE_DIR": "/srv/telegram-assist/second",
    }

    assert first["COMPOSE_PROJECT_NAME"] != second["COMPOSE_PROJECT_NAME"]
    assert first["TAB_MONGODB_DATABASE"] != second["TAB_MONGODB_DATABASE"]
    assert first["TAB_INSTANCE_DIR"] != second["TAB_INSTANCE_DIR"]
    assert "${COMPOSE_PROJECT_NAME" in compose
    assert "${TAB_MONGODB_DATABASE" in compose
    assert "${TAB_INSTANCE_DIR" in compose
    for volume in ("mongodb_data", "telegram_session", "media"):
        assert f"source: {volume}" in compose


def test_only_bounded_volume_initializer_runs_as_root() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    application = compose.partition("x-application:")[2].partition("services:")[0]
    initializer = compose.partition("  volume-permissions:")[2].partition(
        "\n  mongodb:"
    )[0]

    assert "10001" in application
    assert 'user: "0:0"' not in application
    assert 'user: "0:0"' in initializer
    assert "chown" in initializer
    assert "/volumes/sessions" in initializer
    assert "/volumes/media" in initializer


def test_dockerignore_excludes_local_secrets_runtime_and_test_output() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    for required in (
        ".git",
        ".env",
        "tests",
        "var",
        "dist",
        "*.session*",
        "config/configuration.local.json",
    ):
        assert required in ignored
