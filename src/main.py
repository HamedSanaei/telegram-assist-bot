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
from src.application.vpn_test_service import VpnTestService
from src.composition import (
    create_ai_service,
    create_mongo,
    create_price_source,
    create_repositories,
    create_sqlite,
    sync_config_to_sqlite,
)
from src.domain.entities import QueueItem
from src.domain.enums import QueueItemType, QueueStatus
from src.infrastructure.telegram.telethon_publish import (
    TelethonDestinationPublisher,
    TelethonSourceMetadataRefresher,
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
    load_configuration,
    log_startup_summary,
    validate_main_app_config,
)
from src.shared.errors import ApprovalStateError
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


async def run(configure_logging: bool = True) -> None:
    """
    Build the dependency graph and run all main-process services.

    Raises:
        ConfigurationError: When required configuration is missing.
    """
    config = load_configuration()
    if configure_logging:
        setup_logging(
            config.logging.level,
            config.logging.file,
            color_console=config.logging.color_console,
            entrypoint_name="main",
        )
    log_startup_summary(config)
    validate_main_app_config(config)

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
    metadata_refresher: TelethonSourceMetadataRefresher
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
    metadata_refresher = TelethonSourceMetadataRefresher(scheduler_client)
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
        metadata_refresher=metadata_refresher,
        vpn_testing_enabled=config.vpn_testing.iran_worker_enabled,
    )

    async def handle_quality_score(item: QueueItem) -> QueueStatus:
        """Process a legacy score job without losing its approval preview."""
        post_id = str(item.payload["post_id"])
        await approval.request_approval(post_id)
        await quality_scores.score_post(post_id)
        updated = await posts.get(post_id)
        if updated is not None:
            await notifier.refresh_post(updated)
        return QueueStatus.COMPLETED

    async def handle_quality_score_update(item: QueueItem) -> QueueStatus:
        """Refresh metrics, score, and edit existing approval previews."""
        post_id = str(item.payload["post_id"])
        await quality_scores.score_post(post_id)
        updated = await posts.get(post_id)
        if updated is not None:
            await notifier.refresh_post(updated)
        return QueueStatus.COMPLETED

    async def handle_vpn_test(item: QueueItem) -> QueueStatus:
        """Test a post's configs; queue approval when eligible."""
        post_id = str(item.payload["post_id"])
        eligible = await vpn_tests.test_post_configs(post_id)
        updated = await posts.get(post_id)
        if updated is not None:
            await notifier.refresh_post(updated)
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
        await refresh_all_approval_keyboards(approval_bot, approval)

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

    scheduler.start()
    await approval.repair_orphaned_approval_requests()
    logger.info("Main application started")
    try:
        await asyncio.gather(
            approval_dispatcher.start_polling(approval_bot),
            main_dispatcher.start_polling(publisher_bot),
            worker.run(),
            config_sync.run(),
            recurring_forwards.run(),
        )
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
    asyncio.run(run())


if __name__ == "__main__":
    main()
