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
EXPECTED_PYTHON_SPECIFIERS = frozenset({">=3.12", "<3.15"})
EXPECTED_RUNTIME_REQUIREMENTS = frozenset(
    {
        "aiogram==3.29.1",
        "pydantic<3,>=2.12.0",
        "pymongo<5,>=4.13.0",
        "telethon==1.44.0",
        "tzdata>=2025.2",
    }
)


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


def _metadata_header_values(metadata: str, expected_name: str) -> frozenset[str]:
    """Return every non-folded value for one metadata header."""
    values: set[str] = set()
    for line in metadata.splitlines():
        if not line:
            break
        if line[0].isspace():
            continue
        name, separator, value = line.partition(":")
        if separator and name == expected_name:
            values.add(value.strip())
    return frozenset(values)


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
                f"{EXPECTED_IMPORT_PACKAGE}/__main__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/py.typed",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/cli.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/runtime.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/telegram_login.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/telegram_validation.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/text_ingestion.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/media_cleanup.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/admin_approval.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/approval_bot.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/approval_queue.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/publication_queue.py",
                f"{EXPECTED_IMPORT_PACKAGE}/bootstrap/scheduling.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/config/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/config/errors.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/config/loader.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/config/models.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/errors.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/observability/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/observability/context.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/observability/logging.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/observability/redaction.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/retry/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/retry/executor.py",
                f"{EXPECTED_IMPORT_PACKAGE}/shared/retry/policy.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/posts/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/posts/entities.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/posts/errors.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/posts/models.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/posts/status.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/media/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/media/models.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/duplicates/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/duplicates/models.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/categories/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/categories/models.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/admin_approval.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/publication.py",
                f"{EXPECTED_IMPORT_PACKAGE}/domain/scheduling.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ports/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ports/clock.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ports/post_repository.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ports/telegram_source_gateway.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ports/media.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ports/admin.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ports/publication.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ports/scheduling.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ports/operational_approval.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ports/native_scheduling.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/publication/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/publication/publish_immediately.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/scheduling/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/scheduling/cancel_scheduled_post.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/scheduling/run_due_publication.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/scheduling/schedule_post.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/approvals/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/approvals/services.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/operational_approval.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/native_scheduling.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/assemble_media_group.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/categorize_post.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/cleanup_expired_media.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/content/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/content/entity_rebaser.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/content/models.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/content/telegram_links.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/detect_exact_duplicate.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/download_post_media.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/prepare_destination_content.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/prepare_post_pipeline.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/runtime_ingestion.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/text_normalization.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/authenticate_telegram_session.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/crawl_today_text_posts.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/handle_live_message.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/ingest_post_idempotently.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/text_ingestion.py",
                f"{EXPECTED_IMPORT_PACKAGE}/application/validate_telegram_session.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/client.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/content_repository.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/approval_repository.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/errors.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/indexes.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/post_mapper.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/post_repository.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/publication_payload_loader.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/publication_repository.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/operational_approval_repository.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/persistence/mongodb/native_schedule_repository.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/bot/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/bot/adapter.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/user/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/user/history_adapter.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/user/live_adapter.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/user/media_adapter.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/user/message_mapper.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/user/session_adapter.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/user/text_ingestion_gateway.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/user_publisher.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/media_serializer.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/telegram/native_scheduler.py",
                f"{EXPECTED_IMPORT_PACKAGE}/workers/crawl_once.py",
                f"{EXPECTED_IMPORT_PACKAGE}/workers/live_text_listener.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/media/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/infrastructure/media/local_storage.py",
                f"{EXPECTED_IMPORT_PACKAGE}/workers/content_preparation.py",
                f"{EXPECTED_IMPORT_PACKAGE}/workers/media_cleanup.py",
                f"{EXPECTED_IMPORT_PACKAGE}/workers/media_group_assembler.py",
                f"{EXPECTED_IMPORT_PACKAGE}/workers/scheduled_publication_worker.py",
                f"{EXPECTED_IMPORT_PACKAGE}/presentation/bot/__init__.py",
                f"{EXPECTED_IMPORT_PACKAGE}/presentation/bot/handlers.py",
                f"{EXPECTED_IMPORT_PACKAGE}/presentation/bot/runtime_handlers.py",
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

    runtime_requirements = _metadata_header_values(metadata, "Requires-Dist")
    if runtime_requirements != EXPECTED_RUNTIME_REQUIREMENTS:
        raise DistributionValidationError(
            f"unexpected runtime requirements: {sorted(runtime_requirements)}"
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
