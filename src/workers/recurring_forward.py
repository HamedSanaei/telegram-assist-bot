"""Rolling worker that fills Telegram's native schedule with daily campaigns."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.domain.entities import RecurringForwardOccurrence
from src.domain.interfaces import (
    RecurringForwardOccurrenceRepository,
    RecurringForwardPublisher,
)
from src.shared.config import CONFIG_PATH_ENV_VAR, DEFAULT_CONFIG_PATH, load_configuration
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class RecurringForwardWorker:
    """Reconcile configured daily campaigns into Telegram native schedules."""

    def __init__(
        self,
        occurrences: RecurringForwardOccurrenceRepository,
        publisher: RecurringForwardPublisher,
        config_path: str | None = None,
        poll_seconds: float = 30.0,
    ) -> None:
        """
        Args:
            occurrences: Idempotent SQLite occurrence store.
            publisher: Telethon recurring-forward publisher.
            config_path: Optional configuration path.
            poll_seconds: Reconciliation interval.
        """
        import os

        self._occurrences = occurrences
        self._publisher = publisher
        self._config_path = config_path or os.environ.get(
            CONFIG_PATH_ENV_VAR, DEFAULT_CONFIG_PATH
        )
        self._poll_seconds = max(5.0, poll_seconds)
        self._stop = asyncio.Event()

    def stop(self) -> None:
        """Request worker shutdown."""
        self._stop.set()

    async def run(self) -> None:
        """Reconcile schedules until stopped or cancelled."""
        while not self._stop.is_set():
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Recurring forward reconciliation failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def reconcile(self, now: datetime | None = None) -> int:
        """Schedule missing occurrences and cancel disabled campaign messages."""
        config = load_configuration(self._config_path)
        zone = ZoneInfo(config.scheduler.timezone)
        now_utc = self._as_utc(now or datetime.now(timezone.utc))
        horizon = now_utc + timedelta(
            hours=config.scheduler.recurring_forward_lookahead_hours
        )
        active = [item for item in config.scheduler.recurring_forwards if item.enabled]
        scheduled_count = 0
        desired: set[tuple[str, int, str]] = set()
        for campaign in active:
            for local_time in self._occurrence_times(
                campaign.times, now_utc, horizon, zone
            ):
                scheduled_at = local_time.astimezone(timezone.utc)
                for destination_chat_id in campaign.destination_chat_ids:
                    desired.add(
                        (campaign.id, destination_chat_id, scheduled_at.isoformat())
                    )
                    occurrence = RecurringForwardOccurrence(
                        campaign_id=campaign.id,
                        destination_chat_id=destination_chat_id,
                        source_post_url=campaign.source_post_url,
                        show_forward_header=campaign.show_forward_header,
                        scheduled_at=scheduled_at,
                    )
                    occurrence_id = await self._occurrences.reserve(occurrence)
                    if occurrence_id is None:
                        continue
                    try:
                        message_ids = await self._publisher.schedule_from_url(
                            campaign.source_post_url,
                            destination_chat_id,
                            campaign.show_forward_header,
                            scheduled_at,
                        )
                        if not message_ids:
                            raise RuntimeError("Telegram returned no scheduled message ids")
                        await self._occurrences.mark_scheduled(
                            occurrence_id, message_ids
                        )
                        scheduled_count += 1
                        logger.info(
                            "Recurring forward scheduled campaign=%s chat=%s at=%s messages=%d",
                            campaign.id,
                            destination_chat_id,
                            scheduled_at.isoformat(),
                            len(message_ids),
                        )
                    except Exception as exc:
                        await self._occurrences.mark_failed(occurrence_id, str(exc))
                        logger.error(
                            "Recurring forward failed campaign=%s chat=%s at=%s error=%s",
                            campaign.id,
                            destination_chat_id,
                            scheduled_at.isoformat(),
                            exc,
                        )
        for occurrence in await self._occurrences.list_future_scheduled(now_utc):
            occurrence_key = (
                occurrence.campaign_id,
                occurrence.destination_chat_id,
                occurrence.scheduled_at.isoformat(),
            )
            if occurrence_key in desired:
                continue
            try:
                await self._publisher.delete_scheduled_messages(
                    occurrence.destination_chat_id,
                    list(occurrence.message_ids),
                )
                if occurrence.id is not None:
                    await self._occurrences.mark_cancelled(occurrence.id)
                logger.info(
                    "Recurring forward cancelled campaign=%s chat=%s at=%s",
                    occurrence.campaign_id,
                    occurrence.destination_chat_id,
                    occurrence.scheduled_at.isoformat(),
                )
            except Exception as exc:
                logger.error(
                    "Recurring forward cancellation failed campaign=%s chat=%s error=%s",
                    occurrence.campaign_id,
                    occurrence.destination_chat_id,
                    exc,
                )
        return scheduled_count

    @staticmethod
    def _occurrence_times(
        times: list[str],
        now_utc: datetime,
        horizon_utc: datetime,
        zone: ZoneInfo,
    ) -> list[datetime]:
        """Return future local campaign datetimes inside the rolling horizon."""
        start_date = now_utc.astimezone(zone).date()
        end_date = horizon_utc.astimezone(zone).date()
        values: list[datetime] = []
        current_date = start_date
        while current_date <= end_date:
            for value in times:
                hour, minute = (int(part) for part in value.split(":"))
                candidate = datetime(
                    current_date.year,
                    current_date.month,
                    current_date.day,
                    hour,
                    minute,
                    tzinfo=zone,
                )
                candidate_utc = candidate.astimezone(timezone.utc)
                if now_utc < candidate_utc <= horizon_utc:
                    values.append(candidate)
            current_date += timedelta(days=1)
        return sorted(values)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Return one timezone-aware UTC datetime."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
