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

from src.application.approval_service import ApprovalService
from src.application.cleanup_service import CleanupService
from src.application.price_service import UsdPriceService
from src.application.vpn_test_service import VpnTestService
from src.composition import (
    create_mongo,
    create_price_source,
    create_repositories,
    create_sqlite,
    sync_config_to_sqlite,
)
from src.domain.entities import QueueItem
from src.domain.enums import QueueItemType, QueueStatus
from src.infrastructure.telegram.publisher import AiogramMessagePublisher
from src.infrastructure.vpn.worker_client import IranWorkerVpnTester
from src.presentation.approval_bot.handlers import create_approval_router
from src.presentation.approval_bot.notifier import AiogramApprovalNotifier
from src.presentation.main_bot.handlers import create_main_router
from src.shared.config import (
    load_configuration,
    log_startup_summary,
    validate_main_app_config,
)
from src.shared.errors import ApprovalStateError
from src.shared.logging_setup import get_logger, setup_logging
from src.workers.queue_worker import QueueWorker
from src.workers.scheduler import create_scheduler

logger = get_logger(__name__)


async def run() -> None:
    """
    Build the dependency graph and run all main-process services.

    Raises:
        ConfigurationError: When required configuration is missing.
    """
    config = load_configuration()
    setup_logging(config.logging.level, config.logging.file)
    log_startup_summary(config)
    validate_main_app_config(config)

    db = await create_sqlite(config)
    await sync_config_to_sqlite(config, db)
    repos = create_repositories(db)
    mongo_client, posts = create_mongo(config)
    await posts.ensure_indexes()

    publisher_bot = Bot(config.telegram.bot_token)
    approval_bot = Bot(config.telegram.approval_bot_token)
    publisher = AiogramMessagePublisher(publisher_bot)
    notifier = AiogramApprovalNotifier(
        approval_bot,
        repos["admins"],
        repos["channels"],
    )

    approval = ApprovalService(
        posts=posts,
        publish_log=repos["publish_log"],
        channels=repos["channels"],
        admins=repos["admins"],
        publisher=publisher,
        notifier=notifier,
        source_identifiers=config.telegram.source_channels,
        queue=repos["queue"],
    )
    vpn_tester = IranWorkerVpnTester(
        api_url=config.vpn_testing.worker_api_url,
        api_token=config.vpn_testing.worker_api_token,
        timeout_seconds=config.vpn_testing.test_timeout_seconds,
    )
    vpn_tests = VpnTestService(vpn_tester, posts)

    async def handle_vpn_test(item: QueueItem) -> QueueStatus:
        """Test a post's configs; queue approval when eligible."""
        post_id = str(item.payload["post_id"])
        eligible = await vpn_tests.test_post_configs(post_id)
        if not eligible:
            logger.info("Post not eligible for VPN channels post=%s", post_id)
            return QueueStatus.SKIPPED
        await repos["queue"].enqueue(QueueItemType.APPROVAL_REQUEST, {"post_id": post_id})
        return QueueStatus.COMPLETED

    async def handle_approval_request(item: QueueItem) -> QueueStatus:
        """Send the approval message for a post to all admins."""
        await approval.request_approval(str(item.payload["post_id"]))
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
        publisher=publisher,
    )
    cleanup_service = CleanupService(
        posts=posts, queue=repos["queue"], retention_days=config.storage.retention_days
    )
    scheduler = create_scheduler(
        config.scheduler, price_service.publish_usd_price, cleanup_service.run
    )

    approval_dispatcher = Dispatcher()
    approval_dispatcher.include_router(
        create_approval_router(approval, timezone_name=config.scheduler.timezone)
    )
    main_dispatcher = Dispatcher()
    main_dispatcher.include_router(
        create_main_router(config, repos["admins"], repos["channels"])
    )

    scheduler.start()
    logger.info("Main application started")
    try:
        await asyncio.gather(
            approval_dispatcher.start_polling(approval_bot),
            main_dispatcher.start_polling(publisher_bot),
            worker.run(),
        )
    finally:
        worker.stop()
        scheduler.shutdown(wait=False)
        await publisher_bot.session.close()
        await approval_bot.session.close()
        mongo_client.close()
        await db.close()


def main() -> None:
    """Synchronous entrypoint for ``python -m src.main``."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
