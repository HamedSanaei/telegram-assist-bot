"""Safe command-line boundary for the one-shot foundation startup check."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Never
from uuid import uuid4

from telegram_assist_bot.bootstrap.approval_bot import (
    create_approval_bot_application,
    run_approval_bot_application,
)
from telegram_assist_bot.bootstrap.approval_queue import (
    inspect_approval_queue,
    recover_rejected_document_deliveries,
    retry_approval_delivery,
)
from telegram_assist_bot.bootstrap.deployment_compatibility import (
    DeploymentCompatibilityError,
    require_mongodb_compatibility,
)
from telegram_assist_bot.bootstrap.instance_config import (
    InstanceConfigurationError,
    parse_admin_user_ids,
    parse_source_usernames,
    render_instance_configuration,
)
from telegram_assist_bot.bootstrap.media_cleanup import (
    run_media_cleanup,
    run_media_cleanup_worker,
)
from telegram_assist_bot.bootstrap.operator_config import (
    ConfigMutationConflictError,
    ConfigTransactionError,
    add_administrators,
    add_destination,
    add_sources,
    mutate_configuration_transaction,
    remove_administrator,
    remove_destination,
    remove_source,
    set_administrator_active,
    set_approval_chat_id,
    set_destination_active,
    set_logging_level,
    set_media_cleanup_interval,
    set_media_preview_enabled,
    set_media_retention,
    set_source_active,
    set_timezone,
)
from telegram_assist_bot.bootstrap.publication_queue import (
    cancel_publication_job,
    inspect_publication_queue,
    recover_failed_immediate_selection,
    recover_pre_send_publication,
)
from telegram_assist_bot.bootstrap.runtime import (
    BinaryEventStream,
    FoundationApplication,
    FoundationConfigurationError,
    FoundationExitCode,
    FoundationStartupError,
    JsonLineEventSink,
    create_foundation_application,
)
from telegram_assist_bot.bootstrap.telegram_login import run_telegram_login
from telegram_assist_bot.bootstrap.text_ingestion import (
    create_operational_runtime_application,
    create_text_ingestion_application,
    run_text_ingestion_application,
)
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.observability import (
    CorrelationContext,
    Redactor,
    StructuredLogger,
    bind_log_context,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

CONFIG_PATH_ENVIRONMENT_VARIABLE = "TAB_CONFIG_PATH"
"""Environment variable used for the non-secret configuration file path."""

DEFAULT_CONFIGURATION_PATH = Path("config/configuration.json")
"""Working-directory-relative configuration path used as the final fallback."""


class CliUsageError(ValueError):
    """Report invalid command-line input without retaining raw arguments."""

    error_category = "configuration"

    def __init__(self) -> None:
        """Initialize a fixed safe message."""
        super().__init__("Command-line arguments are invalid.")


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise CliUsageError


def _aware_datetime(value: str) -> datetime:
    """Parse one operator-supplied aware ISO-8601 timestamp safely."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError(
            "An aware ISO-8601 time is required."
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("An aware ISO-8601 time is required.")
    return parsed


def resolve_configuration_path(
    cli_path: str | None,
    *,
    environ: Mapping[str, str],
) -> Path:
    """Resolve CLI, environment, then default configuration-path precedence."""
    raw_path = (
        cli_path
        if cli_path is not None
        else environ.get(CONFIG_PATH_ENVIRONMENT_VARIABLE)
    )
    if raw_path is None:
        return DEFAULT_CONFIGURATION_PATH
    if not raw_path or raw_path.isspace():
        raise FoundationConfigurationError
    return Path(raw_path)


async def run_foundation_application(
    application: FoundationApplication,
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
) -> FoundationExitCode:
    """Start and immediately stop the worker-free foundation lifecycle."""
    try:
        await application.start(configuration_path, environ=environ)
        await application.shutdown()
    except asyncio.CancelledError as cancellation:
        try:
            await application.shutdown()
        except asyncio.CancelledError:
            cancellation.add_note("Additional cancellation arrived during shutdown.")
        except FoundationStartupError:
            cancellation.add_note("Foundation shutdown failed safely.")
        raise
    except FoundationStartupError as error:
        return error.exit_code
    return FoundationExitCode.SUCCESS


def _parser() -> _SafeArgumentParser:
    parser = _SafeArgumentParser(
        prog="python -m telegram_assist_bot",
        description=("Run the foundation check or explicit Telegram session login."),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=(
            "check",
            "login",
            "ingest",
            "ingest-text",
            "media-cleanup",
            "media-cleanup-worker",
            "schedule-worker",
            "approval-bot",
            "runtime",
            "publication-queue",
            "publication-cancel",
            "publication-recover-presend",
            "publication-recover-immediate",
            "approval-queue",
            "approval-retry",
            "approval-recover-documents",
            "deployment-preflight",
            "render-instance-config",
            "operator-config",
        ),
        default="check",
        help=(
            "Use 'login' for explicit authentication, 'ingest' (or the compatible "
            "'ingest-text' alias) for full ingestion, 'media-cleanup' for one "
            "cleanup batch, 'media-cleanup-worker' for periodic bounded cleanup, "
            "'schedule-worker' for a fail-closed legacy notice, "
            "'approval-bot' for Bot API polling, or 'runtime' for the single-owner "
            "ingestion and publication process; publication queue commands never "
            "open Telegram sessions; approval queue commands safely inspect or "
            "explicitly retry one approval proposal."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=(
            "Non-secret configuration path; overrides TAB_CONFIG_PATH and the "
            "default config/configuration.json path."
        ),
    )
    parser.add_argument(
        "--status",
        choices=("pending", "retry", "permanent-failed", "completed"),
        default="pending",
    )
    parser.add_argument("--job-id", metavar="ID")
    parser.add_argument("--approval-post-id", metavar="ID")
    parser.add_argument("--from-time", type=_aware_datetime, metavar="ISO_TIME")
    parser.add_argument("--to-time", type=_aware_datetime, metavar="ISO_TIME")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--requeue", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--instance")
    parser.add_argument("--retention-days", type=int, default=2)
    parser.add_argument("--approval-chat-id", type=int)
    parser.add_argument("--admin-user-id", type=int)
    parser.add_argument("--admin-user-ids")
    parser.add_argument("--source-username")
    parser.add_argument("--source-usernames")
    parser.add_argument("--destination-name")
    parser.add_argument("--destination-id", type=int)
    parser.add_argument("--destination-username")
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--kernel-version")
    parser.add_argument("--mongodb-image")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--operation",
        choices=(
            "admin-add",
            "admin-remove",
            "admin-enable",
            "admin-disable",
            "source-add",
            "source-remove",
            "source-enable",
            "source-disable",
            "destination-add",
            "destination-remove",
            "destination-enable",
            "destination-disable",
            "retention-set",
            "logging-set",
            "timezone-set",
            "preview-set",
            "cleanup-interval-set",
            "approval-chat-set",
        ),
    )
    parser.add_argument("--value")
    parser.add_argument("--destinations")
    return parser


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _report_cli_failure(
    *,
    sink: JsonLineEventSink,
    redactor: Redactor,
    error: BaseException,
) -> None:
    logger = StructuredLogger(
        sink=sink,
        clock=_utc_now,
        redactor=redactor,
        minimum_level=LogLevel.DEBUG,
    )
    context = CorrelationContext(correlation_id=uuid4().hex)
    with bind_log_context(context):
        logger.emit(level=LogLevel.INFO, event_name="startup_begun")
        logger.emit(
            level=LogLevel.ERROR,
            event_name="configuration_validation_failed",
            error=error,
        )
        logger.emit(
            level=LogLevel.ERROR,
            event_name="startup_failed",
            error=error,
        )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    output: BinaryEventStream | None = None,
) -> int:
    """Run the safe one-shot foundation CLI and return its stable exit code."""
    environment_snapshot = dict(os.environ if environ is None else environ)
    stream = sys.stderr.buffer if output is None else output
    redactor = Redactor()
    sink = JsonLineEventSink(stream, redactor=redactor)

    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "deployment-preflight":
            if arguments.kernel_version is None or arguments.mongodb_image is None:
                raise CliUsageError
            report = require_mongodb_compatibility(
                kernel_release=arguments.kernel_version,
                mongodb_image=arguments.mongodb_image,
            )
            sys.stdout.write(f"{report}\n")
            return int(FoundationExitCode.SUCCESS)
        if arguments.command == "render-instance-config":
            admin_values = (
                parse_admin_user_ids(arguments.admin_user_ids)
                if arguments.admin_user_ids is not None
                else (
                    (arguments.admin_user_id,)
                    if arguments.admin_user_id is not None
                    else None
                )
            )
            source_values = (
                parse_source_usernames(arguments.source_usernames)
                if arguments.source_usernames is not None
                else (
                    parse_source_usernames(arguments.source_username)
                    if arguments.source_username is not None
                    else None
                )
            )
            required = (
                arguments.template,
                arguments.output,
                arguments.instance,
                arguments.approval_chat_id,
                admin_values,
                source_values,
                arguments.destination_name,
                arguments.destination_id,
            )
            if any(value is None for value in required):
                raise CliUsageError
            render_instance_configuration(
                template_path=arguments.template,
                output_path=arguments.output,
                instance=arguments.instance,
                retention_days=arguments.retention_days,
                approval_chat_id=arguments.approval_chat_id,
                admin_user_ids=admin_values,
                source_usernames=source_values,
                destination_name=arguments.destination_name,
                destination_id=arguments.destination_id,
                destination_username=arguments.destination_username,
                timezone=arguments.timezone,
                force=arguments.force,
            )
            return int(FoundationExitCode.SUCCESS)
        if arguments.command == "operator-config":
            if (
                arguments.config is None
                or arguments.operation is None
                or arguments.value is None
            ):
                raise CliUsageError
            destinations = tuple(
                item.strip()
                for item in (arguments.destinations or "").split(",")
                if item.strip()
            )
            operation = arguments.operation
            if operation == "admin-add":
                mutator = add_administrators(
                    arguments.value, allowed_destinations=destinations
                )
            elif operation == "admin-remove":
                mutator = remove_administrator(int(arguments.value))
            elif operation in {"admin-enable", "admin-disable"}:
                mutator = set_administrator_active(
                    int(arguments.value), active=operation.endswith("enable")
                )
            elif operation == "source-add":
                mutator = add_sources(
                    arguments.value, allowed_destinations=destinations
                )
            elif operation == "source-remove":
                mutator = remove_source(arguments.value)
            elif operation in {"source-enable", "source-disable"}:
                mutator = set_source_active(
                    arguments.value, active=operation.endswith("enable")
                )
            elif operation == "destination-add":
                destination = json.loads(arguments.value)
                mutator = add_destination(
                    name=destination["name"],
                    telegram_channel_id=destination["telegram_channel_id"],
                    username=destination.get("username"),
                    enabled=destination.get("enabled", True),
                )
            elif operation == "destination-remove":
                mutator = remove_destination(arguments.value)
            elif operation in {"destination-enable", "destination-disable"}:
                mutator = set_destination_active(
                    arguments.value, active=operation.endswith("enable")
                )
            elif operation == "retention-set":
                mutator = set_media_retention(int(arguments.value))
            elif operation == "timezone-set":
                mutator = set_timezone(arguments.value)
            elif operation == "preview-set":
                if arguments.value not in ("true", "false"):
                    raise CliUsageError
                mutator = set_media_preview_enabled(arguments.value == "true")
            elif operation == "cleanup-interval-set":
                mutator = set_media_cleanup_interval(int(arguments.value))
            elif operation == "approval-chat-set":
                mutator = set_approval_chat_id(int(arguments.value))
            else:
                mutator = set_logging_level(arguments.value)
            config_path = Path(arguments.config)
            backup_path = mutate_configuration_transaction(
                config_path=config_path,
                backup_directory=config_path.parent.parent / "backups" / "config",
                environ=environment_snapshot,
                mutator=mutator,
                affected_services=(
                    "runtime",
                    "approval-bot",
                    "media-cleanup-worker",
                ),
            )
            sys.stdout.write(f"configuration_backup={backup_path}\n")
            if operation == "admin-add":
                sys.stdout.write(
                    "reminder=each new administrator must send /start to the bot\n"
                )
            if operation == "source-add":
                sys.stdout.write(
                    "warning=enabled sources may trigger the current-day "
                    "history crawl\n"
                )
            return int(FoundationExitCode.SUCCESS)
        configuration_path = resolve_configuration_path(
            arguments.config,
            environ=environment_snapshot,
        )
    except (
        CliUsageError,
        FoundationConfigurationError,
        InstanceConfigurationError,
        DeploymentCompatibilityError,
        ConfigMutationConflictError,
        ConfigTransactionError,
        ValueError,
    ) as error:
        with suppress(Exception):
            _report_cli_failure(sink=sink, redactor=redactor, error=error)
        return int(FoundationExitCode.CONFIGURATION_ERROR)

    if arguments.command == "login":
        exit_code = asyncio.run(
            run_telegram_login(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
            )
        )
    elif arguments.command in {"ingest", "ingest-text"}:
        ingestion_application = create_text_ingestion_application(sink=sink)
        exit_code = asyncio.run(
            run_text_ingestion_application(
                ingestion_application,
                configuration_path,
                environ=environment_snapshot,
            )
        )
    elif arguments.command == "media-cleanup":
        exit_code = asyncio.run(
            run_media_cleanup(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
            )
        )
    elif arguments.command == "media-cleanup-worker":
        exit_code = asyncio.run(
            run_media_cleanup_worker(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
            )
        )
    elif arguments.command == "schedule-worker":
        logger = StructuredLogger(
            sink=sink,
            clock=_utc_now,
            redactor=redactor,
            minimum_level=LogLevel.DEBUG,
        )
        logger.emit(
            level=LogLevel.ERROR,
            event_name="legacy_schedule_worker_disabled",
            fields={"safe_reason": "native_scheduling_requires_runtime"},
        )
        exit_code = FoundationExitCode.INFRASTRUCTURE_ERROR
    elif arguments.command == "approval-bot":
        approval_application = create_approval_bot_application(sink=sink)
        exit_code = asyncio.run(
            run_approval_bot_application(
                approval_application,
                configuration_path,
                environ=environment_snapshot,
            )
        )
    elif arguments.command == "runtime":
        runtime_application = create_operational_runtime_application(sink=sink)
        exit_code = asyncio.run(
            run_text_ingestion_application(
                runtime_application,
                configuration_path,
                environ=environment_snapshot,
            )
        )
    elif arguments.command == "publication-queue":
        rows = asyncio.run(
            inspect_publication_queue(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
                status=arguments.status,
            )
        )
        for row in rows:
            sys.stdout.write(f"{row}\n")
        exit_code = FoundationExitCode.SUCCESS
    elif arguments.command == "publication-cancel":
        if not arguments.job_id:
            return int(FoundationExitCode.CONFIGURATION_ERROR)
        result = asyncio.run(
            cancel_publication_job(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
                job_id=arguments.job_id,
            )
        )
        sys.stdout.write(f"cancellation_result={result.value}\n")
        exit_code = FoundationExitCode.SUCCESS
    elif arguments.command == "publication-recover-presend":
        if not arguments.approval_post_id:
            return int(FoundationExitCode.CONFIGURATION_ERROR)
        recovery_result = asyncio.run(
            recover_pre_send_publication(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
                approval_post_id=arguments.approval_post_id,
            )
        )
        sys.stdout.write(f"recovery_result={recovery_result.value}\n")
        exit_code = FoundationExitCode.SUCCESS
    elif arguments.command == "publication-recover-immediate":
        if not arguments.approval_post_id:
            return int(FoundationExitCode.CONFIGURATION_ERROR)
        recovery_result = asyncio.run(
            recover_failed_immediate_selection(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
                approval_post_id=arguments.approval_post_id,
                dry_run=arguments.dry_run,
                requeue=arguments.requeue,
            )
        )
        sys.stdout.write(f"recovery_result={recovery_result.value}\n")
        exit_code = FoundationExitCode.SUCCESS
    elif arguments.command == "approval-queue":
        rows = asyncio.run(
            inspect_approval_queue(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
                status=arguments.status,
            )
        )
        for row in rows:
            sys.stdout.write(f"{row}\n")
        exit_code = FoundationExitCode.SUCCESS
    elif arguments.command == "approval-retry":
        if not arguments.approval_post_id:
            return int(FoundationExitCode.CONFIGURATION_ERROR)
        retried = asyncio.run(
            retry_approval_delivery(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
                approval_post_id=arguments.approval_post_id,
            )
        )
        sys.stdout.write(f"approval_retry_queued={str(retried).lower()}\n")
        exit_code = FoundationExitCode.SUCCESS
    elif arguments.command == "approval-recover-documents":
        exact = bool(arguments.approval_post_id)
        bounded_range = (
            arguments.from_time is not None and arguments.to_time is not None
        )
        if exact == bounded_range or not 1 <= arguments.limit <= 100:
            return int(FoundationExitCode.CONFIGURATION_ERROR)
        document_recovery_result = asyncio.run(
            recover_rejected_document_deliveries(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
                approval_post_id=arguments.approval_post_id,
                started_at=arguments.from_time,
                ended_at=arguments.to_time,
                dry_run=arguments.dry_run,
                limit=arguments.limit,
            )
        )
        sys.stdout.write(
            "approval_document_recovery_mode="
            f"{'dry-run' if arguments.dry_run else 'execute'}\n"
        )
        sys.stdout.write(
            f"matching_count={len(document_recovery_result.matching_post_ids)}\n"
        )
        sys.stdout.write(
            f"requeued_count={len(document_recovery_result.requeued_post_ids)}\n"
        )
        for post_id in document_recovery_result.matching_post_ids:
            sys.stdout.write(f"approval_post_id={post_id[:12]}\n")
        exit_code = FoundationExitCode.SUCCESS
    else:
        foundation_application = create_foundation_application(sink=sink)
        exit_code = asyncio.run(
            run_foundation_application(
                foundation_application,
                configuration_path,
                environ=environment_snapshot,
            )
        )
    return int(exit_code)


__all__ = (
    "CONFIG_PATH_ENVIRONMENT_VARIABLE",
    "DEFAULT_CONFIGURATION_PATH",
    "CliUsageError",
    "main",
    "resolve_configuration_path",
    "run_foundation_application",
)
