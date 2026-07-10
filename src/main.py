"""Main application entrypoint.

Runs, in one asyncio event loop:
  * the approval bot (aiogram long polling),
  * the SQLite queue worker (VPN tests + approval dispatch),
  * the scheduler (USD price publishing twice a day + daily cleanup).

The collector runs as a separate process (``python -m src.workers.collector``),
and the Iran VPN worker runs on the Iran server
(``python -m src.workers.iran_vpn_worker``).
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from telethon import TelegramClient

from src.application.approval_service import ApprovalService
from src.application.cleanup_service import CleanupService
from src.application.management_service import ManagementService
from src.application.price_service import UsdPriceService
from src.application.quality_score_service import QualityScoreService
from src.application.runtime_lease_service import RuntimeLeaseService
from src.application.vpn_test_service import VpnTestService
from src.composition import (
    create_ai_service,
    create_mongo,
    create_price_source,
    create_repositories,
    create_runtime_lease_store,
    create_sqlite,
    sync_config_to_sqlite,
)
from src.domain.entities import QueueItem
from src.domain.enums import QueueItemType, QueueStatus, SourceMetricsStatus
from src.infrastructure.telegram.telethon_publish import (
    TelethonDestinationPublisher,
)
from src.infrastructure.telegram.publisher import AiogramMessagePublisher
from src.infrastructure.configuration.atomic_editor import AtomicConfigurationEditor
from src.infrastructure.vpn.worker_client import IranWorkerVpnTester
from src.presentation.approval_bot.handlers import create_approval_router
from src.presentation.approval_bot.notifier import AiogramApprovalNotifier
from src.presentation.approval_bot.panel import create_panel_router
from src.presentation.approval_bot.propagation import refresh_all_approval_keyboards
from src.presentation.main_bot.handlers import create_main_router
from src.shared.config import (
    AppConfig,
    load_configuration,
    log_startup_summary,
    validate_main_app_config,
)
from src.shared.errors import (
    ApplicationAlreadyRunningError,
    ApprovalStateError,
    RuntimeLeaseLostError,
    TelegramPublishError,
)
from src.shared.logging_setup import get_logger, setup_logging
from src.workers.queue_worker import QueueWorker
from src.workers.recurring_forward import RecurringForwardWorker
from src.workers.config_sync import ConfigSyncWorker
from src.workers.scheduler import create_scheduler

logger = get_logger(__name__)


def _prompt_scheduler_phone() -> str:
    """
    Prompt for the scheduler Telegram account phone number.

    Returns:
        The phone number typed in the terminal.

    Side effects:
        Blocks startup until the operator enters a non-empty phone number.
        Telethon then asks for the login code and 2FA password when needed.
    """
    while True:
        phone = input(
            "Enter telegram.scheduler_phone for the native scheduler user "
            "(example: +989121234567): "
        ).strip()
        if phone:
            return phone
        print("telegram.scheduler_phone cannot be empty for first scheduler login.")


async def run(
    configure_logging: bool = True,
    runtime_lease: RuntimeLeaseService | None = None,
    config: AppConfig | None = None,
) -> None:
    """
    Configure and run all main-process services under a runtime lease.

    Args:
        configure_logging: Whether this standalone entrypoint configures the
            process-wide logger.
        runtime_lease: Lease already acquired and heartbeated by
            :mod:`src.run_all`. When omitted, this entrypoint owns its own
            bot-polling lease.
        config: Optional configuration preloaded by :mod:`src.run_all`.

    Raises:
        ConfigurationError: When required configuration is missing.
        ApplicationAlreadyRunningError: Another process owns the same bot
            polling identity.
        RuntimeLeaseLostError: Lease renewal becomes unsafe while running.
    """
    config = config or load_configuration()
    if configure_logging:
        setup_logging(
            config.logging.level,
            config.logging.file,
            color_console=config.logging.color_console,
            entrypoint_name="main",
        )
    log_startup_summary(config)
    validate_main_app_config(config)

    if runtime_lease is not None:
        if not runtime_lease.is_acquired:
            raise RuntimeError("Externally managed bot-polling lease is not acquired")
        await _run_application(config)
        return

    lease_client, lease_repository = create_runtime_lease_store(config)
    owned_lease = RuntimeLeaseService(
        lease_repository,
        "bot-polling",
        (
            config.telegram.bot_token,
            config.telegram.approval_bot_token,
        ),
    )
    try:
        await owned_lease.acquire()
        await owned_lease.run_with_heartbeat(_run_application(config))
    finally:
        await owned_lease.release()
        lease_client.close()


async def _run_application(config: AppConfig) -> None:
    """
    Build the dependency graph and run the main process after lease acquisition.

    Args:
        config: Validated application configuration.
    """

    db = await create_sqlite(config)
    await sync_config_to_sqlite(config, db)
    repos = create_repositories(db)
    mongo_client, posts = create_mongo(config)
    await posts.ensure_indexes()
    ai = create_ai_service(config)

    publisher_bot = Bot(config.telegram.bot_token)
    approval_bot = Bot(config.telegram.approval_bot_token)
    price_publisher = AiogramMessagePublisher(publisher_bot)
    scheduler_client: TelegramClient | None = TelegramClient(
        config.telegram.scheduler_session,
        int(config.telegram.api_id),
        config.telegram.api_hash,
    )
    destination_publisher: TelethonDestinationPublisher
    await scheduler_client.connect()
    if await scheduler_client.is_user_authorized():
        logger.info(
            "Telegram native scheduler session ready session=%s",
            config.telegram.scheduler_session,
        )
    else:
        phone = config.telegram.scheduler_phone or _prompt_scheduler_phone()
        await scheduler_client.start(phone=phone)
        logger.info(
            "Telegram native scheduler session logged in session=%s",
            config.telegram.scheduler_session,
        )
    destination_publisher = TelethonDestinationPublisher(scheduler_client)
    notifier = AiogramApprovalNotifier(
        approval_bot,
        repos["admins"],
        repos["channels"],
        approval_messages=repos["approval_messages"],
        timezone_name=config.scheduler.timezone,
    )

    approval = ApprovalService(
        posts=posts,
        publish_log=repos["publish_log"],
        channels=repos["channels"],
        admins=repos["admins"],
        publisher=destination_publisher,
        notifier=notifier,
        source_identifiers=config.telegram.source_channels,
        queue=repos["queue"],
        approval_requests=repos["approval_requests"],
        approval_messages=repos["approval_messages"],
        scheduled_publisher=destination_publisher,
    )
    vpn_tester = IranWorkerVpnTester(
        api_url=config.vpn_testing.worker_api_url,
        api_token=config.vpn_testing.worker_api_token,
        timeout_seconds=config.vpn_testing.test_timeout_seconds,
    )
    vpn_tests = VpnTestService(vpn_tester, posts)
    quality_scores = QualityScoreService(
        posts=posts,
        ai=ai,
        vpn_testing_enabled=config.vpn_testing.iran_worker_enabled,
    )

    async def metrics_are_ready(post_id: str) -> bool:
        """Reroute legacy score jobs through the collector metrics worker."""
        post = await posts.get(post_id)
        if post is None:
            raise ApprovalStateError(f"Post {post_id} not found for scoring")
        if post.source_metrics_status != SourceMetricsStatus.PENDING:
            return True
        await repos["queue"].enqueue_if_missing_post_item(
            QueueItemType.SOURCE_METRICS_REFRESH,
            post_id,
            {"post_id": post_id, "legacy_score_job": True},
        )
        logger.info("Deferred legacy score until collector metrics refresh post=%s", post_id)
        return False

    async def handle_quality_score(item: QueueItem) -> QueueStatus:
        """Process a legacy score job without losing its approval preview."""
        post_id = str(item.payload["post_id"])
        await approval.request_approval(post_id)
        if not await metrics_are_ready(post_id):
            return QueueStatus.SKIPPED
        await quality_scores.score_post(post_id)
        result = await approval.refresh_approval_previews(post_id)
        if result.retryable_failures:
            raise TelegramPublishError(
                f"Approval preview refresh has {result.retryable_failures} retryable failures"
            )
        return QueueStatus.COMPLETED

    async def handle_quality_score_update(item: QueueItem) -> QueueStatus:
        """Refresh metrics, score, and edit existing approval previews."""
        post_id = str(item.payload["post_id"])
        if not await metrics_are_ready(post_id):
            return QueueStatus.SKIPPED
        await quality_scores.score_post(post_id)
        result = await approval.refresh_approval_previews(post_id)
        if result.retryable_failures:
            raise TelegramPublishError(
                f"Approval preview refresh has {result.retryable_failures} retryable failures"
            )
        return QueueStatus.COMPLETED

    async def handle_vpn_test(item: QueueItem) -> QueueStatus:
        """Test a post's configs; queue approval when eligible."""
        post_id = str(item.payload["post_id"])
        eligible = await vpn_tests.test_post_configs(post_id)
        result = await approval.refresh_approval_previews(post_id)
        if result.retryable_failures:
            raise TelegramPublishError(
                f"Approval preview refresh has {result.retryable_failures} retryable failures"
            )
        logger.info("Background VPN test complete post=%s eligible=%s", post_id, eligible)
        return QueueStatus.COMPLETED

    async def handle_approval_request(item: QueueItem) -> QueueStatus:
        """Send the approval message for a post to all admins."""
        post_id = str(item.payload["post_id"])
        await approval.request_approval(post_id)
        return QueueStatus.WAITING_APPROVAL

    async def handle_scheduled_publish(item: QueueItem) -> QueueStatus:
        """Publish a queued post when its per-channel slot is due."""
        post_id = str(item.payload["post_id"])
        chat_id = int(item.payload["chat_id"])
        admin_id = int(item.payload["admin_user_id"])
        try:
            await approval.publish(post_id, chat_id, admin_id)
        except ApprovalStateError as exc:
            logger.warning(
                "Scheduled publish skipped post=%s chat=%s: %s", post_id, chat_id, exc
            )
            return QueueStatus.SKIPPED
        return QueueStatus.PUBLISHED

    worker = QueueWorker(
        queue=repos["queue"],
        handlers={
            QueueItemType.QUALITY_SCORE: handle_quality_score,
            QueueItemType.QUALITY_SCORE_UPDATE: handle_quality_score_update,
            QueueItemType.VPN_TEST: handle_vpn_test,
            QueueItemType.APPROVAL_REQUEST: handle_approval_request,
            QueueItemType.SCHEDULED_PUBLISH: handle_scheduled_publish,
        },
    )

    price_source = create_price_source(config)
    price_service = UsdPriceService(
        source=price_source,
        history=repos["price_history"],
        channels=repos["channels"],
        publisher=price_publisher,
    )
    cleanup_service = CleanupService(
        posts=posts, queue=repos["queue"], retention_days=config.storage.retention_days
    )
    scheduler = create_scheduler(
        config.scheduler, price_service.publish_usd_price, cleanup_service.run
    )
    async def refresh_tracked_approval_messages() -> None:
        """Refresh approval keyboards after runtime channel/admin config changes."""
        await refresh_all_approval_keyboards(
            approval_bot,
            approval,
            max_posts=25,
            delay_seconds=1.05,
        )

    config_sync = ConfigSyncWorker(db, on_applied=refresh_tracked_approval_messages)
    management = ManagementService(
        repos["channels"],
        AtomicConfigurationEditor(),
        repos["recurring_campaigns"],
    )
    recurring_forwards = RecurringForwardWorker(
        repos["recurring_forwards"], destination_publisher
    )

    approval_dispatcher = Dispatcher()
    approval_dispatcher.include_router(
        create_approval_router(approval, timezone_name=config.scheduler.timezone)
    )
    approval_dispatcher.include_router(
        create_panel_router(management, repos["admins"])
    )
    main_dispatcher = Dispatcher()
    main_dispatcher.include_router(
        create_main_router(config, repos["admins"], repos["channels"])
    )

    async def repair_recent_previews_in_background() -> None:
        """Repair existing approval keyboards without delaying bot startup."""
        try:
            await approval.repair_recent_approval_previews(
                limit=40,
                delay_seconds=1.05,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background approval preview repair failed")

    scheduler.start()
    logger.info("Main application started")
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(approval_dispatcher.start_polling(approval_bot))
            tasks.create_task(main_dispatcher.start_polling(publisher_bot))
            tasks.create_task(worker.run())
            tasks.create_task(config_sync.run())
            tasks.create_task(recurring_forwards.run())
            tasks.create_task(repair_recent_previews_in_background())
    finally:
        worker.stop()
        config_sync.stop()
        recurring_forwards.stop()
        scheduler.shutdown(wait=False)
        await publisher_bot.session.close()
        await approval_bot.session.close()
        if scheduler_client is not None:
            await scheduler_client.disconnect()
        mongo_client.close()
        await db.close()


def main() -> None:
    """Synchronous entrypoint for ``python -m src.main``."""
    try:
        asyncio.run(run())
    except ApplicationAlreadyRunningError as exc:
        logger.error("Startup refused: %s", exc)
    except RuntimeLeaseLostError as exc:
        logger.error("Runtime stopped after lease loss: %s", exc)


if __name__ == "__main__":
    main()
