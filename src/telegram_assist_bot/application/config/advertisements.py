"""Application-owned mapping for advertisement campaign configuration."""

from __future__ import annotations

import re
from datetime import date, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementSourceFetchPolicy,
)
from telegram_assist_bot.domain.advertisements import (
    AdvertisementCampaign,
    AdvertisementErrorPolicy,
    AdvertisementPublicationMode,
    SourceAdvertisementPost,
    SourceCachePolicy,
    SourceUnavailablePolicy,
    Weekday,
)

if TYPE_CHECKING:
    from telegram_assist_bot.shared.config.models import (
        AdvertisementCampaignConfig,
        AdvertisementConfig,
    )

_PUBLIC_POST_URL_REGEX = re.compile(r"^https://t\.me/([a-zA-Z0-9_]{5,32})/([1-9]\d*)$")


def map_advertisement_campaign(
    campaign_config: AdvertisementCampaignConfig,
) -> AdvertisementCampaign:
    """Map one validated configuration campaign model to a domain campaign entity."""
    match = _PUBLIC_POST_URL_REGEX.match(campaign_config.source_post_url)
    if not match:
        raise ValueError("source_post_url must be a valid public HTTPS t.me post URL")
    message_id = int(match.group(2))

    source_post = SourceAdvertisementPost(
        url=campaign_config.source_post_url,
        channel_username=campaign_config.source_channel_username,
        message_id=message_id,
    )

    parsed_times = tuple(
        time.fromisoformat(time_str) for time_str in campaign_config.times
    )
    parsed_weekdays = tuple(
        Weekday(weekday_str) for weekday_str in campaign_config.weekdays
    )
    start_date = date.fromisoformat(campaign_config.start_date)
    end_date = date.fromisoformat(campaign_config.end_date)
    tz = ZoneInfo(campaign_config.timezone)
    mode = AdvertisementPublicationMode(campaign_config.publication_mode)
    policy = AdvertisementErrorPolicy(campaign_config.error_policy)

    cache_policy = (
        SourceCachePolicy(campaign_config.source_cache_policy)
        if campaign_config.source_cache_policy is not None
        else None
    )
    unavailable_policy = (
        SourceUnavailablePolicy(campaign_config.source_unavailable_policy)
        if campaign_config.source_unavailable_policy is not None
        else None
    )
    interval_sec = campaign_config.refresh_interval_seconds

    return AdvertisementCampaign(
        campaign_id=campaign_config.campaign_id,
        name=campaign_config.name,
        enabled=campaign_config.enabled,
        source_post=source_post,
        destination_names=tuple(campaign_config.destination_names),
        weekdays=parsed_weekdays,
        times=parsed_times,
        start_date=start_date,
        end_date=end_date,
        timezone=tz,
        publication_mode=mode,
        priority=campaign_config.priority,
        minimum_gap_seconds=campaign_config.minimum_gap_seconds,
        error_policy=policy,
        max_retries=campaign_config.max_retries,
        source_cache_policy=cache_policy,
        source_unavailable_policy=unavailable_policy,
        snapshot_retention_days=campaign_config.snapshot_retention_days,
        refresh_interval_seconds=interval_sec,
    )


def map_advertisement_campaigns(
    config: AdvertisementConfig,
) -> tuple[AdvertisementCampaign, ...]:
    """Map all configured advertisement campaigns deterministically."""
    return tuple(
        map_advertisement_campaign(campaign_config)
        for campaign_config in config.campaigns
    )


def map_advertisement_source_fetch_policy(
    config: AdvertisementConfig,
) -> AdvertisementSourceFetchPolicy | None:
    """Map the explicit global source-fetch policy, preserving disabled absence."""
    source_fetch = config.source_fetch
    if source_fetch is None:
        if any(campaign.enabled for campaign in config.campaigns):
            raise ValueError(
                "enabled campaigns require explicit source_fetch configuration"
            )
        return None
    return AdvertisementSourceFetchPolicy(
        timeout_seconds=source_fetch.timeout_seconds,
        max_attempts=source_fetch.max_attempts,
        initial_backoff_seconds=source_fetch.initial_backoff_seconds,
    )
