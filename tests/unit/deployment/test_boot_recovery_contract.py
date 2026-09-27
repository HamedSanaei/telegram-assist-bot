"""Contract tests for host-layer boot recovery after a real host reboot.

`restart: on-failure:20` intentionally caps restarts so a permanent startup
validation failure cannot create an unbounded crash loop. That policy does not
start an already stopped container again after a host reboot, so recovery lives at
the host/service layer: an idempotent `docker compose up -d` unit. These tests
keep that mechanism from silently weakening the bounded runtime contract.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[3]
SERVICE = ROOT / "deploy" / "systemd" / "telegram-assist-boot.service"
SCRIPT = ROOT / "deploy" / "boot_recovery.sh"


def test_boot_service_is_an_idempotent_start_unit_without_restart_loop() -> None:
    service = SERVICE.read_text(encoding="utf-8")

    assert "Type=oneshot" in service
    assert "RemainAfterExit=no" in service
    assert "After=docker.service" in service
    assert "WantedBy=multi-user.target" in service
    assert "ExecStart=/usr/local/bin/telegram-assist-boot-recovery" in service
    assert "TAB_INSTANCE_ROOT=/opt/telegram-assist-bot/instances" in service
    for forbidden in (
        "Restart=always",
        "Restart=on-failure",
        "unless-stopped",
        "docker restart",
        "ExecStop=",
    ):
        assert forbidden not in service


def test_boot_recovery_script_only_starts_existing_instances() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert '"${COMPOSE_BIN}" compose --env-file .env up -d' in script
    assert '"${COMPOSE_BIN}" compose up -d' in script
    assert "compose.yaml" in script
    for forbidden in (
        "--force-recreate",
        "down -v",
        "down --volumes",
        "docker volume rm",
        "docker system prune",
        "unless-stopped",
        "docker restart",
        "--restart",
    ):
        assert forbidden not in script


def test_operations_documentation_explains_bounded_restart_and_boot_recovery() -> None:
    operations = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")

    assert "telegram-assist-boot.service" in operations
    assert "deploy/boot_recovery.sh" in operations
    assert "on-failure:20" in operations
    assert "systemctl enable" in operations
