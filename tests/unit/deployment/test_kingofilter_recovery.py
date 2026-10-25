"""Scoped host recovery behavior and explicitly opted-in Docker smoke tests."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).parents[3]


@pytest.fixture
def recovery() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "kingofilter_recovery_test", ROOT / "deploy" / "kingofilter_runtime.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recovery_instance(tmp_path: Path, recovery: ModuleType) -> Path:
    instance = tmp_path / "admin1"
    instance.mkdir()
    for name in ("compose.yaml", "runtime-t104.override.yaml", recovery.OVERRIDE):
        (instance / name).write_text("services: {}\n", encoding="utf-8")
    (instance / ".env").write_text("", encoding="utf-8")
    (instance / recovery.MARKER).write_text(recovery.UNIT + "\n", encoding="utf-8")
    return instance


def _container(recovery: ModuleType) -> dict[str, object]:
    return {
        "Name": f"/{recovery.CONTAINER}",
        "Config": {
            "Labels": {
                "com.docker.compose.project": recovery.PROJECT,
                "com.docker.compose.service": "runtime",
                "com.docker.compose.oneoff": "False",
            }
        },
    }


def _recorder(
    monkeypatch: pytest.MonkeyPatch,
    recovery: ModuleType,
    container: dict[str, object],
) -> list[list[str]]:
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert kwargs["check"] is True
        assert 0 < int(str(kwargs["timeout"])) <= 120
        return subprocess.CompletedProcess(command, 0, json.dumps([container]))

    monkeypatch.setattr(recovery.subprocess, "run", run)
    return commands


@pytest.mark.parametrize(
    ("action", "suffix"),
    [
        ("restore", ["up", "-d", "--no-deps", "--scale", "runtime=1", "runtime"]),
        ("stop", ["stop", "--timeout", "30", "runtime"]),
    ],
)
def test_lifecycle_targets_only_existing_runtime_with_last_recovery_override(
    recovery: ModuleType,
    recovery_instance: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    suffix: list[str],
) -> None:
    commands = _recorder(monkeypatch, recovery, _container(recovery))

    recovery.manage_runtime(action, recovery_instance, "runtime-t104.override.yaml")

    assert commands[0] == ["docker", "container", "inspect", recovery.CONTAINER]
    assert commands[1] == [
        "docker",
        "compose",
        "--project-name",
        "telegram-assist-kingofilter",
        "--project-directory",
        str(recovery_instance.resolve()),
        "--env-file",
        str(recovery_instance.resolve() / ".env"),
        "-f",
        str(recovery_instance.resolve() / "compose.yaml"),
        "-f",
        str(recovery_instance.resolve() / "runtime-t104.override.yaml"),
        "-f",
        str(recovery_instance.resolve() / recovery.OVERRIDE),
        *suffix,
    ]


def test_foreign_container_fails_before_any_compose_mutation(
    recovery: ModuleType,
    recovery_instance: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign = _container(recovery)
    foreign["Name"] = "/another-runtime"
    commands = _recorder(monkeypatch, recovery, foreign)

    with pytest.raises(ValueError, match="expected Compose service"):
        recovery.manage_runtime(
            "restore", recovery_instance, "runtime-t104.override.yaml"
        )

    assert len(commands) == 1


@pytest.mark.parametrize(
    "release", ["compose.yaml", "kingofilter-runtime.override.yaml"]
)
def test_release_override_cannot_replace_base_or_recovery_policy(
    recovery: ModuleType, recovery_instance: Path, release: str
) -> None:
    with pytest.raises(ValueError, match="separate instance file"):
        recovery.compose_command(recovery_instance, release)


@pytest.mark.parametrize("marker", [None, "another.service\n"])
def test_missing_legacy_boot_opt_out_refuses_to_start(
    recovery: ModuleType,
    recovery_instance: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str | None,
) -> None:
    marker_path = recovery_instance / recovery.MARKER
    if marker is None:
        marker_path.unlink()
    else:
        marker_path.write_text(marker, encoding="utf-8")
    commands = _recorder(monkeypatch, recovery, _container(recovery))

    assert (
        recovery.main(
            [
                "restore",
                "--instance-dir",
                str(recovery_instance),
                "--release-override",
                "runtime-t104.override.yaml",
            ]
        )
        == 1
    )
    assert commands == []


@pytest.mark.parametrize("failure", ["inspect", "compose"])
def test_transport_failure_is_bounded_and_captured_output_is_never_disclosed(
    recovery: ModuleType,
    recovery_instance: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        if failure == "inspect" or len(commands) == 2:
            raise subprocess.CalledProcessError(
                1, command, output="private-container-env"
            )
        return subprocess.CompletedProcess(
            command, 0, json.dumps([_container(recovery)])
        )

    monkeypatch.setattr(recovery.subprocess, "run", run)
    result = recovery.main(
        [
            "restore",
            "--instance-dir",
            str(recovery_instance),
            "--release-override",
            "runtime-t104.override.yaml",
        ]
    )

    assert result == 1
    assert len(commands) == (1 if failure == "inspect" else 2)
    assert "private-container-env" not in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="host boot script runs on Linux")
@pytest.mark.parametrize("env_file", [False, True])
def test_legacy_boot_excludes_only_owned_runtime_and_keeps_other_services(
    tmp_path: Path, recovery: ModuleType, env_file: bool
) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    instance_root = tmp_path / "instances"
    for name in ("admin1", "other"):
        instance = instance_root / name
        instance.mkdir(parents=True)
        (instance / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (instance_root / "admin1" / recovery.MARKER).write_text(
        recovery.UNIT + "\n", encoding="utf-8"
    )
    if env_file:
        (instance_root / "admin1" / ".env").write_text("", encoding="utf-8")
    record = tmp_path / "calls"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"config --services")\n'
        '    printf "%s\\n" runtime volume-permissions mongodb approval-bot '
        "media-cleanup-worker ;;\n"
        '  *) printf "%s|%s\\n" "$PWD" "$*" >> "$RECORD" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o700)
    environment = {
        **os.environ,
        "TAB_INSTANCE_ROOT": str(instance_root),
        "TAB_COMPOSE_BIN": str(docker),
        "RECORD": str(record),
    }

    for _ in range(2):
        subprocess.run(  # noqa: S603
            [bash, str(ROOT / "deploy" / "boot_recovery.sh")],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

    marked_command = "compose"
    if env_file:
        marked_command += " --env-file .env"
    marked_command += (
        " up -d --no-deps volume-permissions mongodb approval-bot media-cleanup-worker"
    )
    assert (
        record.read_text(encoding="utf-8").splitlines()
        == [
            f"{instance_root / 'admin1'}|{marked_command}",
            f"{instance_root / 'other'}|compose up -d",
        ]
        * 2
    )


def test_shared_session_os_lock_excludes_another_process_and_releases(
    tmp_path: Path,
) -> None:
    from telegram_assist_bot.infrastructure.telegram.user.session_adapter import (
        _SessionFileLock,
    )

    lock_path = tmp_path / "shared.session.lock"
    owner = _SessionFileLock(lock_path)
    assert owner.try_acquire()
    program = (
        "from pathlib import Path; import sys; "
        "from telegram_assist_bot.infrastructure.telegram.user.session_adapter "
        "import _SessionFileLock; "
        "lock = _SessionFileLock(Path(sys.argv[1])); acquired = lock.try_acquire(); "
        "lock.release(); raise SystemExit(0 if acquired else 7)"
    )

    def contender() -> int:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", program, str(lock_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode in {0, 7}, result.stderr
        return result.returncode

    try:
        assert contender() == 7
    finally:
        owner.release()
    assert contender() == 0


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("TAB_TEST_RECOVERY_IMAGE"),
    reason="explicit isolated Docker image is required",
)
@pytest.mark.parametrize("terminal", [False, True])
def test_isolated_docker_restart_and_administrative_stop(terminal: bool) -> None:
    """Use no network, credentials, volumes or production container names."""
    docker = shutil.which("docker")
    assert docker is not None
    image = os.environ["TAB_TEST_RECOVERY_IMAGE"]
    name = f"tab-recovery-smoke-{uuid4().hex}"
    if terminal:
        program = (
            "from telegram_assist_bot.container_entrypoint import container_main; "
            "raise SystemExit(container_main(runner=lambda _: 4))"
        )
    else:
        program = (
            "import time\n"
            "from pathlib import Path\n"
            "marker = Path('/tmp/recovery-smoke-attempt')\n"
            "if not marker.exists():\n"
            "    marker.touch()\n"
            "    time.sleep(10)\n"
            "    raise SystemExit(3)\n"
            "time.sleep(300)\n"
        )

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [docker, *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        )

    run(
        "run",
        "--detach",
        "--name",
        name,
        "--network",
        "none",
        "--restart",
        "on-failure:20",
        "--entrypoint",
        "/app/.venv/bin/python",
        image,
        "-c",
        program,
    )
    try:
        deadline = time.monotonic() + 45
        while True:
            container = json.loads(run("container", "inspect", name).stdout)[0]
            if terminal:
                ready = container["State"]["Status"] == "exited"
            else:
                ready = container["RestartCount"] == 1 and container["State"]["Running"]
            if ready:
                break
            assert time.monotonic() < deadline, "bounded Docker recovery did not settle"
            time.sleep(0.25)
        if terminal:
            assert container["State"]["ExitCode"] == 0
            assert container["RestartCount"] == 0
        else:
            run("container", "stop", "--time", "2", name)
        time.sleep(1)
        stopped = json.loads(run("container", "inspect", name).stdout)[0]
        assert not stopped["State"]["Running"]
        assert stopped["RestartCount"] == (0 if terminal else 1)
    finally:
        run("container", "rm", "--force", name)


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("TAB_TEST_RECOVERY_IMAGE"),
    reason="explicit isolated Docker image is required",
)
def test_compose_recovery_override_configures_bounded_singleton(tmp_path: Path) -> None:
    """Resolve the actual deployment artifact instead of pinning source strings."""
    docker = shutil.which("docker")
    assert docker is not None
    base = tmp_path / "compose.yaml"
    base.write_text(
        json.dumps(
            {"services": {"runtime": {"image": os.environ["TAB_TEST_RECOVERY_IMAGE"]}}}
        ),
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603
        [
            docker,
            "compose",
            "--project-name",
            "tab-recovery-config-smoke",
            "-f",
            str(base),
            "-f",
            str(ROOT / "deploy" / "kingofilter-runtime.override.yaml"),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    runtime = json.loads(result.stdout)["services"]["runtime"]
    assert runtime["restart"] == "on-failure:20"
    assert runtime["deploy"]["replicas"] == 1
    assert runtime["container_name"] == "telegram-assist-kingofilter-runtime-1"
