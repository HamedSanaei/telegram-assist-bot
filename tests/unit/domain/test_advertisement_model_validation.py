# ruff: noqa: DTZ001, PT011
# mypy: disable-error-code="arg-type"
"""Strict validation tests for persisted advertisement domain contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from telegram_assist_bot.domain.advertisement_slot import (
    AdvertisementCollisionAudit,
    AdvertisementSlot,
    AdvertisementSlotAudit,
    advertisement_slot_identity,
)
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementMediaReference,
    AdvertisementSourceFetchPolicy,
    AdvertisementSourceIdentity,
    AdvertisementSourceSnapshot,
)
from telegram_assist_bot.domain.advertisements.campaign import (
    AdvertisementCampaign,
    AdvertisementErrorPolicy,
    AdvertisementPublicationMode,
    SourceAdvertisementPost,
    SourceCachePolicy,
    SourceUnavailablePolicy,
    Weekday,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.publication_collision import CollisionResolutionState

NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)


def _campaign() -> AdvertisementCampaign:
    return AdvertisementCampaign(
        campaign_id="campaign-1",
        name="Campaign",
        enabled=True,
        source_post=SourceAdvertisementPost(
            "https://t.me/source_channel/42", "source_channel", 42
        ),
        destination_names=("destination",),
        weekdays=(Weekday.MONDAY,),
        times=(time(9),),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        timezone=ZoneInfo("UTC"),
        publication_mode=AdvertisementPublicationMode.COPY,
        priority=1,
        minimum_gap_seconds=60,
        error_policy=AdvertisementErrorPolicy.RETRY_THEN_FAIL,
        max_retries=2,
        source_cache_policy=SourceCachePolicy.CACHED,
        source_unavailable_policy=SourceUnavailablePolicy.FAIL_CLOSED,
        snapshot_retention_days=7,
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("campaign_id", "bad id"),
        ("name", " "),
        ("enabled", 1),
        ("source_post", object()),
        ("destination_names", ()),
        ("destination_names", ("a", "a")),
        ("weekdays", ()),
        ("weekdays", (Weekday.MONDAY, Weekday.MONDAY)),
        ("times", ()),
        ("times", (time(9), time(9))),
        ("start_date", "2026-01-01"),
        ("end_date", "2026-12-31"),
        ("end_date", date(2025, 1, 1)),
        ("timezone", "UTC"),
        ("publication_mode", "copy"),
        ("priority", -1),
        ("minimum_gap_seconds", 0),
        ("error_policy", "retry"),
        ("max_retries", 11),
        ("source_cache_policy", None),
        ("source_unavailable_policy", None),
        ("snapshot_retention_days", 0),
        ("source_cache_policy", "cached"),
        ("source_unavailable_policy", "fail_closed"),
        ("refresh_interval_seconds", 60),
    ],
)
def test_campaign_rejects_invalid_configuration(field_name: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(_campaign(), **{field_name: value})


def test_campaign_periodic_refresh_accepts_only_bounded_interval() -> None:
    periodic = replace(
        _campaign(),
        source_cache_policy=SourceCachePolicy.PERIODIC_REFRESH,
        refresh_interval_seconds=60,
    )
    assert periodic.refresh_interval_seconds == 60
    with pytest.raises(ValueError):
        replace(periodic, refresh_interval_seconds=59)


@pytest.mark.parametrize(
    "changes",
    [
        {"url": 1},
        {"url": "http://t.me/source_channel/42"},
        {"channel_username": "other_name"},
        {"message_id": 41},
    ],
)
def test_source_post_url_components_must_match(changes: dict[str, object]) -> None:
    source = SourceAdvertisementPost(
        "https://t.me/source_channel/42", "source_channel", 42
    )
    with pytest.raises(ValueError):
        replace(source, **changes)


def _slot() -> AdvertisementSlot:
    slot_id = advertisement_slot_identity("campaign-1", -1001, NOW)
    return AdvertisementSlot(
        slot_id=slot_id,
        campaign_id="campaign-1",
        destination_name="destination",
        destination_id=-1001,
        due_at=NOW,
        local_scheduled_at=NOW,
        timezone_name="UTC",
        source_snapshot_id="snapshot-1",
        source_snapshot_version=1,
        config_fingerprint="fingerprint",
        priority=1,
        minimum_gap_seconds=60,
        max_retries=2,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("campaign_id", " "),
        ("destination_name", ""),
        ("destination_id", 0),
        ("source_snapshot_version", 0),
        ("minimum_gap_seconds", 0),
        ("priority", -1),
        ("max_retries", -1),
        ("version", -1),
        ("claim_count", -1),
        ("publication_attempt_count", -1),
        ("due_at", datetime(2026, 1, 1)),
        ("lease_until", datetime(2026, 1, 1)),
        ("execution_delay_seconds", -0.1),
        ("status", "scheduled"),
        ("collision_state", "unresolved"),
    ],
)
def test_slot_rejects_invalid_persistence_fields(
    field_name: str, value: object
) -> None:
    with pytest.raises(ValueError):
        replace(_slot(), **{field_name: value})


@pytest.mark.parametrize(
    ("campaign_id", "destination_id", "due_at"),
    [
        (" ", -1, NOW),
        ("campaign", 0, NOW),
        ("campaign", -1, datetime(2026, 1, 1)),
    ],
)
def test_slot_identity_rejects_ambiguous_inputs(
    campaign_id: str, destination_id: int, due_at: datetime
) -> None:
    with pytest.raises(ValueError):
        advertisement_slot_identity(campaign_id, destination_id, due_at)


def test_slot_audit_models_reject_unknown_policy_and_naive_time() -> None:
    with pytest.raises(ValueError):
        AdvertisementSlotAudit("campaign", "time", "UTC", "unknown", NOW)
    with pytest.raises(ValueError):
        AdvertisementSlotAudit(
            "campaign", "time", "UTC", "nonexistent_local_time", datetime(2026, 1, 1)
        )
    with pytest.raises(ValueError):
        AdvertisementCollisionAudit(NOW, NOW, 1, "unknown", NOW)
    with pytest.raises(ValueError):
        AdvertisementCollisionAudit(
            NOW, NOW, 2, "advertisement_priority_minimum_gap", NOW
        )
    with pytest.raises(ValueError):
        AdvertisementCollisionAudit(
            datetime(2026, 1, 1),
            NOW,
            1,
            "advertisement_priority_minimum_gap",
            NOW,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("timeout_seconds", 0),
        ("max_attempts", 0),
        ("initial_backoff_seconds", -1),
    ],
)
def test_source_fetch_policy_is_bounded(field_name: str, value: object) -> None:
    policy = AdvertisementSourceFetchPolicy(10, 2, 1)
    with pytest.raises(ValueError):
        replace(policy, **{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("media_type", "photo"),
        ("item_index", -1),
        ("size_bytes", -1),
        ("storage_path", " "),
    ],
)
def test_source_media_reference_rejects_invalid_metadata(
    field_name: str, value: object
) -> None:
    media = AdvertisementMediaReference(
        MediaType.PHOTO, 0, 1, "image/jpeg", "x.jpg", "sha256/x"
    )
    with pytest.raises(ValueError):
        replace(media, **{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("campaign_id", " "),
        ("source_channel_username", ""),
        ("source_message_id", 0),
        ("source_identity_fingerprint", " "),
    ],
)
def test_source_identity_rejects_invalid_values(field_name: str, value: object) -> None:
    identity = AdvertisementSourceIdentity.create("campaign", "Source_Name", 42)
    with pytest.raises(ValueError):
        replace(identity, **{field_name: value})
    assert identity.source_channel_username == "Source_Name"


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("snapshot_id", " "),
        ("campaign_id", ""),
        ("source_identity", object()),
        ("snapshot_version", 0),
        ("content_hash", " "),
    ],
)
def test_source_snapshot_rejects_invalid_identity_fields(
    field_name: str, value: object
) -> None:
    identity = AdvertisementSourceIdentity.create("campaign", "source", 42)
    snapshot = AdvertisementSourceSnapshot(
        snapshot_id="snapshot",
        campaign_id="campaign",
        source_identity=identity,
        snapshot_version=1,
        snapshot_contract_version="1.0.0",
        content_hash="hash",
        text="text",
        caption=None,
        text_entities=(),
        caption_entities=(),
        media_group_id=None,
        media_references=(),
        source_published_at=NOW,
        source_edited_at=None,
        fetched_at=NOW,
        last_successful_fetch_at=NOW,
    )
    with pytest.raises(ValueError):
        replace(snapshot, **{field_name: value})


def test_slot_optional_times_are_canonicalized() -> None:
    slot = replace(
        _slot(),
        lease_until=NOW,
        next_attempt_at=NOW,
        published_at=NOW,
        effective_due_at=NOW,
        collision_state=CollisionResolutionState.RESOLVED,
    )
    assert slot.effective_due_at == NOW
