"""Build distributable outputs into the ``publish/`` directory.

Produces, on every run:

* ``publish/windows/`` — standalone one-file executables built with
  PyInstaller for the three entrypoints (main bot, collector, Iran VPN
  worker) plus the configuration template. Only possible on Windows.
* ``publish/ubuntu/`` — ``telegram-admin-bot-<version>.tar.gz`` source
  bundle (src, deploy, docs, requirements, config template) with an
  ``install.sh`` that sets up /opt/telegram-admin-bot on Ubuntu.
* ``publish/BUILD_INFO.txt`` — version, timestamp, and build platform.

Usage:
    python scripts/build_publish.py            # build everything possible
    python scripts/build_publish.py --skip-exe # only the Ubuntu bundle
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISH_DIR = ROOT / "publish"
WORK_DIR = ROOT / "build"

ENTRYPOINTS: dict[str, str] = {
    "telegram-admin-bot": "scripts/entry_main.py",
    "telegram-collector": "scripts/entry_collector.py",
    "telegram-suite": "scripts/entry_run_all.py",
    "iran-vpn-worker": "scripts/entry_iran_vpn_worker.py",
}

UBUNTU_BUNDLE_ITEMS: list[str] = [
    "src",
    "deploy",
    "docs",
    "config/configuration.example.json",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "README.md",
    "CLAUDE.md",
]


def read_version() -> str:
    """
    Read the project version from ``pyproject.toml``.

    Returns:
        The version string, or ``"0.0.0"`` when it cannot be read.
    """
    try:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "0.0.0"


def clean_publish_dir() -> None:
    """Remove and recreate ``publish/`` so outputs never go stale."""
    shutil.rmtree(PUBLISH_DIR, ignore_errors=True)
    PUBLISH_DIR.mkdir(parents=True)


def build_windows_executables() -> None:
    """
    Build one-file Windows executables with PyInstaller.

    Raises:
        SystemExit: When a PyInstaller invocation fails.

    Side effects:
        Writes executables to ``publish/windows`` and intermediate files
        to ``build/`` (both git-ignored).
    """
    dist_dir = PUBLISH_DIR / "windows"
    dist_dir.mkdir(parents=True, exist_ok=True)
    for name, script in ENTRYPOINTS.items():
        print(f"==> Building executable: {name}")
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--noconfirm",
            "--name",
            name,
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(WORK_DIR / "pyinstaller"),
            "--specpath",
            str(WORK_DIR / "spec"),
            "--paths",
            str(ROOT),
            str(ROOT / script),
        ]
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            raise SystemExit(f"PyInstaller failed for {name}")
    config_dir = dist_dir / "config"
    config_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "config" / "configuration.example.json", config_dir)
    (dist_dir / "README.txt").write_text(
        "Telegram Admin Bot - Windows build\n"
        "\n"
        "1. Copy config/configuration.example.json to config/configuration.json\n"
        "   next to the executables and fill in your secrets (keep it UTF-8).\n"
        "2. Run telegram-collector.exe once interactively (Telegram login).\n"
        "3. Run telegram-suite.exe - it starts everything in one process\n"
        "   (approval bot + queue + scheduler + collector).\n"
        "   Alternatively run telegram-admin-bot.exe and telegram-collector.exe\n"
        "   as two separate processes.\n"
        "iran-vpn-worker.exe is normally deployed on the Iran (Ubuntu) server\n"
        "instead; the Windows build exists for local testing only.\n",
        encoding="utf-8",
    )


def build_ubuntu_bundle(version: str) -> Path:
    """
    Build the Ubuntu source bundle tarball.

    Args:
        version: Project version used in the archive name.

    Returns:
        Path of the created ``.tar.gz`` file.

    Side effects:
        Writes the tarball to ``publish/ubuntu``.
    """
    dest_dir = PUBLISH_DIR / "ubuntu"
    dest_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = f"telegram-admin-bot-{version}"
    tar_path = dest_dir / f"{bundle_name}.tar.gz"

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        """Exclude caches and compiled artifacts from the bundle."""
        parts = Path(info.name).parts
        if "__pycache__" in parts or info.name.endswith((".pyc", ".pyo")):
            return None
        return info

    print(f"==> Building Ubuntu bundle: {tar_path.name}")
    with tarfile.open(tar_path, "w:gz") as tar:
        for item in UBUNTU_BUNDLE_ITEMS:
            source = ROOT / item
            if not source.exists():
                print(f"    (skipping missing item: {item})")
                continue
            tar.add(source, arcname=f"{bundle_name}/{item}", filter=_filter)
        tar.add(
            ROOT / "scripts" / "install_ubuntu.sh",
            arcname=f"{bundle_name}/install.sh",
        )
    return tar_path


def write_build_info(version: str, exe_built: bool) -> None:
    """Write ``publish/BUILD_INFO.txt`` describing this build."""
    info = (
        f"version: {version}\n"
        f"built_at: {datetime.now(timezone.utc).isoformat()}\n"
        f"platform: {platform.platform()}\n"
        f"python: {platform.python_version()}\n"
        f"windows_exe: {'yes' if exe_built else 'no (built on non-Windows or skipped)'}\n"
    )
    (PUBLISH_DIR / "BUILD_INFO.txt").write_text(info, encoding="utf-8")


def main() -> None:
    """Command-line entrypoint building all requested outputs."""
    parser = argparse.ArgumentParser(description="Build publish/ outputs")
    parser.add_argument(
        "--skip-exe",
        action="store_true",
        help="Skip the Windows executable build (faster; Ubuntu bundle only)",
    )
    args = parser.parse_args()

    version = read_version()
    clean_publish_dir()

    exe_built = False
    if args.skip_exe:
        print("==> Skipping Windows executables (--skip-exe)")
    elif platform.system() != "Windows":
        print("==> Not on Windows; skipping executable build")
    else:
        build_windows_executables()
        exe_built = True

    build_ubuntu_bundle(version)
    write_build_info(version, exe_built)
    print(f"==> Publish outputs ready in {PUBLISH_DIR}")


if __name__ == "__main__":
    main()
