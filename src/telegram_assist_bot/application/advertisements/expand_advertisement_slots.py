"""Expand configured campaign dates into durable advertisement slots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from telegram_assist_bot.domain.advertisement_slot import (
    AdvertisementSlot,
    AdvertisementSlotAudit,
    advertisement_slot_identity,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from telegram_assist_bot.application.ports import Clock
    from telegram_assist_bot.application.ports.advertisement_repository import (
        AdvertisementSlotRepository,
    )
    from telegram_assist_bot.domain.advertisement_source import (
        AdvertisementSourceSnapshot,
    )
    from telegram_assist_bot.domain.advertisements import AdvertisementCampaign

_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True, slots=True)
class ExpandAdvertisementSlotsResult:
    """Summarize one deterministic campaign expansion."""

    slots: tuple[AdvertisementSlot, ...]
    skipped_nonexistent_times: int


def _campaign_fingerprint(campaign: AdvertisementCampaign) -> str:
    payload = {
        "campaign_id": campaign.campaign_id,
        "destinations": list(campaign.destination_names),
        "weekdays": [str(item) for item in campaign.weekdays],
        "times": [item.isoformat(timespec="minutes") for item in campaign.times],
        "start_date": campaign.start_date.isoformat(),
        "end_date": campaign.end_date.isoformat(),
        "timezone": campaign.timezone.key,
        "priority": campaign.priority,
        "minimum_gap_seconds": campaign.minimum_gap_seconds,
        "max_retries": campaign.max_retries,
        "source_identity": campaign.source_post.url,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_local_instant(
    local_naive: datetime, campaign: AdvertisementCampaign
) -> datetime | None:
    timezone = campaign.timezone
    first = local_naive.replace(tzinfo=timezone, fold=0)
    first_round_trip = first.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
    if first_round_trip != local_naive:
        return None
    second = local_naive.replace(tzinfo=timezone, fold=1)
    second_round_trip = second.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
    if second_round_trip == local_naive and first.utcoffset() != second.utcoffset():
        return first
    return first


class ExpandAdvertisementSlots:
    """Generate and reconcile every inclusive campaign slot without publication."""

    def __init__(self, repository: AdvertisementSlotRepository, clock: Clock) -> None:
        """Store durable repository and deterministic UTC clock dependencies."""
        self._repository = repository
        self._clock = clock

    async def execute(
        self,
        campaign: AdvertisementCampaign,
        snapshot: AdvertisementSourceSnapshot,
        destinations: Mapping[str, int],
    ) -> ExpandAdvertisementSlotsResult:
        """Expand a finite configured date range and reconcile future slots."""
        if not campaign.enabled:
            return ExpandAdvertisementSlotsResult((), 0)
        if snapshot.campaign_id != campaign.campaign_id or not snapshot.is_current:
            raise ValueError("slot expansion requires the current campaign snapshot")
        resolved_destinations: list[tuple[str, int]] = []
        for name in campaign.destination_names:
            destination_id = destinations.get(name)
            if type(destination_id) is not int or destination_id == 0:
                raise ValueError("campaign destination is missing or invalid")
            resolved_destinations.append((name, destination_id))

        now = self._clock.utc_now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware instant")
        now = now.astimezone(UTC)
        config_fingerprint = _campaign_fingerprint(campaign)
        slots: list[AdvertisementSlot] = []
        audits: list[AdvertisementSlotAudit] = []
        day = campaign.start_date
        weekdays = {_WEEKDAY_INDEX[str(item)] for item in campaign.weekdays}
        while day <= campaign.end_date:
            if day.weekday() in weekdays:
                for configured_time in campaign.times:
                    local_naive = datetime.combine(day, configured_time)
                    local_aware = _resolve_local_instant(local_naive, campaign)
                    if local_aware is None:
                        audits.append(
                            AdvertisementSlotAudit(
                                campaign_id=campaign.campaign_id,
                                local_scheduled_value=local_naive.isoformat(
                                    timespec="minutes"
                                ),
                                timezone_name=campaign.timezone.key,
                                reason="nonexistent_local_time",
                                recorded_at=now,
                            )
                        )
                        continue
                    due_at = local_aware.astimezone(UTC)
                    for destination_name, destination_id in resolved_destinations:
                        slots.append(
                            AdvertisementSlot(
                                slot_id=advertisement_slot_identity(
                                    campaign.campaign_id, destination_id, due_at
                                ),
                                campaign_id=campaign.campaign_id,
                                destination_name=destination_name,
                                destination_id=destination_id,
                                due_at=due_at,
                                local_scheduled_at=local_aware,
                                timezone_name=campaign.timezone.key,
                                source_snapshot_id=snapshot.snapshot_id,
                                source_snapshot_version=snapshot.snapshot_version,
                                config_fingerprint=config_fingerprint,
                                priority=campaign.priority,
                                minimum_gap_seconds=campaign.minimum_gap_seconds,
                                max_retries=campaign.max_retries,
                                created_at=now,
                                updated_at=now,
                            )
                        )
            day += timedelta(days=1)

        ordered = tuple(
            sorted(slots, key=lambda item: (item.due_at, item.destination_id))
        )
        persisted = await self._repository.reconcile_campaign_slots(
            campaign.campaign_id,
            ordered,
            tuple(audits),
            now=now,
        )
        return ExpandAdvertisementSlotsResult(
            slots=persisted,
            skipped_nonexistent_times=len(audits),
        )


__all__ = ("ExpandAdvertisementSlots", "ExpandAdvertisementSlotsResult")
