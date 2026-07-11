"""Validate the contents and metadata of built distribution artifacts."""

from __future__ import annotations

import argparse
import sys
import tarfile
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

EXPECTED_DISTRIBUTION = "telegram-assist-bot"
EXPECTED_IMPORT_PACKAGE = "telegram_assist_bot"
EXPECTED_VERSION = "0.1.0"
EXPECTED_PYTHON_SPECIFIERS = frozenset({">=3.12", "<3.14"})


class DistributionValidationError(RuntimeError):
    """Report an invalid or unexpected distribution artifact."""


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dist_directory",
        nargs="?",
        type=Path,
        default=Path("dist"),
        help="Directory containing exactly one wheel and one source archive.",
    )
    return parser.parse_args(argv)


def _single_artifact(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        message = f"expected exactly one {pattern!r} artifact, found {len(matches)}"
        raise DistributionValidationError(message)
    return matches[0]


def _validate_archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise DistributionValidationError(f"unsafe archive member: {name!r}")
    return path


def _metadata_headers(metadata: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in metadata.splitlines():
        if not line:
            break
        if line[0].isspace():
            continue
        name, separator, value = line.partition(":")
        if separator:
            headers[name] = value.strip()
    return headers


def validate_wheel(wheel_path: Path) -> None:
    """Validate wheel membership and the project-owned metadata contract."""
    try:
        with ZipFile(wheel_path) as archive:
            member_names = archive.namelist()
            members = [_validate_archive_path(name) for name in member_names]
            top_level = {member.parts[0] for member in members}
            dist_info = {name for name in top_level if name.endswith(".dist-info")}

            if len(dist_info) != 1:
                raise DistributionValidationError(
                    "wheel must contain one .dist-info directory"
                )
            allowed_top_level = {EXPECTED_IMPORT_PACKAGE, *dist_info}
            unexpected = sorted(top_level - allowed_top_level)
            if unexpected:
                raise DistributionValidationError(
                    f"unexpected wheel top-level entries: {unexpected}"
                )

            required_members = {
                f"{EXPECTED_IMPORT_PACKAGE}/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/py.typed",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/config/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/config/errors.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/config/loader.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/config/models.py",
                *{
                    f"{EXPECTED_IMPORT_PACKAGE}/{layer}/__init__.py"
                    for layer in (
                        "application",
                        "bootstrap",
                        "domain",
                        "infrastructure",
                        "presentation",
                        "shared",
                        "workers",
                    )
                },
            }
            missing = sorted(required_members - set(member_names))
            if missing:
                raise DistributionValidationError(f"wheel members missing: {missing}")

            dist_info_root = next(iter(dist_info))
            allowed_members = required_members | {
                f"{dist_info_root}/METADATA",
                f"{dist_info_root}/RECORD",
                f"{dist_info_root}/WHEEL",
            }
            extra_members = sorted(set(member_names) - allowed_members)
            if extra_members:
                raise DistributionValidationError(
                    f"unexpected wheel members: {extra_members}"
                )

            metadata_members = [
                name for name in member_names if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_members) != 1:
                raise DistributionValidationError(
                    "wheel must contain one METADATA file"
                )
            metadata = archive.read(metadata_members[0]).decode(
                "utf-8", errors="strict"
            )
    except (BadZipFile, UnicodeDecodeError) as error:
        raise DistributionValidationError(f"invalid wheel: {error}") from error

    headers = _metadata_headers(metadata)
    expected_headers = {
        "Name": EXPECTED_DISTRIBUTION,
        "Version": EXPECTED_VERSION,
    }
    for name, expected in expected_headers.items():
        if headers.get(name) != expected:
            raise DistributionValidationError(
                f"unexpected {name} metadata: {headers.get(name)!r}"
            )

    python_specifiers = frozenset(
        part.strip()
        for part in headers.get("Requires-Python", "").split(",")
        if part.strip()
    )
    if python_specifiers != EXPECTED_PYTHON_SPECIFIERS:
        raise DistributionValidationError(
            f"unexpected Requires-Python metadata: {headers.get('Requires-Python')!r}"
        )


def validate_source_distribution(archive_path: Path) -> None:
    """Validate source archive safety and its minimum build inputs."""
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = [_validate_archive_path(name) for name in archive.getnames()]
    except tarfile.TarError as error:
        raise DistributionValidationError(f"invalid source archive: {error}") from error

    roots = {member.parts[0] for member in members}
    if len(roots) != 1:
        raise DistributionValidationError("source archive must have one root directory")
    root = next(iter(roots))
    member_names = {member.as_posix() for member in members}
    required = {
        f"{root}/README.md",
        f"{root}/config/configuration.example.json",
        f"{root}/pyproject.toml",
        f"{root}/src/{EXPECTED_IMPORT_PACKAGE}/__init__.py",
    }
    missing = sorted(required - member_names)
    if missing:
        raise DistributionValidationError(f"source archive members missing: {missing}")


def main(argv: list[str] | None = None) -> int:
    """Validate exactly one wheel and source archive in a directory."""
    args = _parse_args(argv)
    directory: Path = args.dist_directory
    try:
        if not directory.is_dir():
            raise DistributionValidationError(
                f"distribution directory not found: {directory}"
            )
        validate_wheel(_single_artifact(directory, "*.whl"))
        validate_source_distribution(_single_artifact(directory, "*.tar.gz"))
    except (OSError, DistributionValidationError) as error:
        sys.stderr.write(f"distribution check failed: {error}\n")
        return 1

    sys.stdout.write("distribution artifacts are valid\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
