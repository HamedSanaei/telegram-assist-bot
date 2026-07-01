"""APScheduler-based scheduled jobs (USD price publishing, cleanup)."""

from __future__ import annotations

from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.shared.config import SchedulerConfig
from src.shared.errors import ConfigurationError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

AsyncJob = Callable[[], Awaitable[object]]


def _parse_time(value: str) -> tuple[int, int]:
    """
    Parse an ``HH:MM`` string into hour and minute.

    Raises:
        ConfigurationError: When the value is not valid ``HH:MM``.
    """
    try:
        hour_raw, minute_raw = value.split(":")
        hour, minute = int(hour_raw), int(minute_raw)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid scheduler time '{value}', expected HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigurationError(f"Scheduler time out of range: '{value}'")
    return hour, minute


def create_scheduler(
    config: SchedulerConfig,
    price_job: AsyncJob,
    cleanup_job: AsyncJob,
) -> AsyncIOScheduler:
    """
    Build the scheduler with all configured cron jobs.

    Args:
        config: Scheduler section of the configuration (times are in
            the configured timezone, ``Asia/Tehran`` by default).
        price_job: Coroutine factory publishing the USD price.
        cleanup_job: Coroutine factory removing expired data.

    Returns:
        A configured (not yet started) :class:`AsyncIOScheduler`.

    Raises:
        ConfigurationError: When a configured time is invalid.

    Example:
        scheduler = create_scheduler(config.scheduler, price.publish_usd_price, cleanup.run)
        scheduler.start()
    """
    timezone = ZoneInfo(config.timezone)
    scheduler = AsyncIOScheduler(timezone=timezone)
    for time_value in config.usd_price_publish_times:
        hour, minute = _parse_time(time_value)
        scheduler.add_job(
            price_job,
            CronTrigger(hour=hour, minute=minute, timezone=timezone),
            id=f"usd_price_{time_value}",
            misfire_grace_time=600,
        )
    hour, minute = _parse_time(config.cleanup_time)
    scheduler.add_job(
        cleanup_job,
        CronTrigger(hour=hour, minute=minute, timezone=timezone),
        id="cleanup",
        misfire_grace_time=3600,
    )
    logger.info(
        "Scheduler configured price_times=%s cleanup=%s tz=%s",
        config.usd_price_publish_times,
        config.cleanup_time,
        config.timezone,
    )
    return scheduler
