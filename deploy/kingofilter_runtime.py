#!/usr/bin/env python3
"""Restore only the existing Kingofilter runtime under bounded Docker recovery.

The systemd unit owns host/daemon recovery; Docker owns unexpected process exits.
There is deliberately no polling or retry loop that could restart terminal auth
failures. Persistent administrative shutdown is `systemctl disable --now` on the
scoped unit, not a direct Docker stop. The matching marker excludes only runtime
from legacy boot recovery; other instance services retain their recovery path.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

PROJECT = "telegram-assist-kingofilter"
CONTAINER = f"{PROJECT}-runtime-1"
UNIT = "telegram-assist-kingofilter-runtime.service"
MARKER = ".kingofilter-runtime-systemd"
OVERRIDE = "kingofilter-runtime.override.yaml"


def compose_command(instance_dir: Path, release_override: str) -> list[str]:
    """Build an explicitly scoped command retaining the deployed image override."""
    instance_dir = instance_dir.resolve(strict=True)
    release_path = (instance_dir / release_override).resolve(strict=True)
    if release_path.parent != instance_dir or release_path.name in {
        "compose.yaml",
        OVERRIDE,
    }:
        raise ValueError("The release override must be a separate instance file.")
    paths = (instance_dir / "compose.yaml", release_path, instance_dir / OVERRIDE)
    for path in (*paths, instance_dir / ".env"):
        if not path.is_file():
            raise ValueError("A required instance deployment file is missing.")
    if (instance_dir / MARKER).read_text(encoding="utf-8").strip() != UNIT:
        raise ValueError("The runtime must opt out of legacy boot recovery.")
    command = [
        "docker",
        "compose",
        "--project-name",
        PROJECT,
        "--project-directory",
        str(instance_dir),
        "--env-file",
        str(instance_dir / ".env"),
    ]
    for path in paths:
        command.extend(("-f", str(path)))
    return command


def manage_runtime(action: str, instance_dir: Path, release_override: str) -> None:
    """Restore or stop one existing, identity-checked runtime without dependencies."""
    if action not in {"restore", "stop"}:
        raise ValueError("Unknown runtime lifecycle action.")
    command = compose_command(instance_dir, release_override)
    result = subprocess.run(  # noqa: S603
        ["docker", "container", "inspect", CONTAINER],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    containers = json.loads(result.stdout)
    if not isinstance(containers, list) or len(containers) != 1:
        raise ValueError("The existing runtime identity could not be verified.")
    container = containers[0]
    labels = container.get("Config", {}).get("Labels", {})
    if (
        container.get("Name") != f"/{CONTAINER}"
        or labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != "runtime"
        or labels.get("com.docker.compose.oneoff", "False").lower() != "false"
    ):
        raise ValueError("The existing runtime is not the expected Compose service.")
    if action == "restore":
        command.extend(("up", "-d", "--no-deps", "--scale", "runtime=1", "runtime"))
    else:
        command.extend(("stop", "--timeout", "30", "runtime"))
    subprocess.run(command, check=True, timeout=120)  # noqa: S603


def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded lifecycle action, reporting no captured secret-bearing data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("restore", "stop"))
    parser.add_argument("--instance-dir", required=True, type=Path)
    parser.add_argument("--release-override", required=True)
    args = parser.parse_args(argv)
    try:
        manage_runtime(args.action, args.instance_dir, args.release_override)
    except (OSError, ValueError, subprocess.SubprocessError):
        sys.stderr.write(
            "Kingofilter runtime lifecycle failed; inspect host diagnostics.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
