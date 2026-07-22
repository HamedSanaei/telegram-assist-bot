"""Domain models and value objects for scheduled advertisement campaigns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

_CAMPAIGN_ID_REGEX: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_-]+$")
_PUBLIC_POST_URL_REGEX: Final[re.Pattern[str]] = re.compile(
    r"^https://t\.me/([a-zA-Z0-9_]{5,32})/([1-9]\d*)$"
)


class Weekday(StrEnum):
    """Canonical lower-case weekday values in campaign timezone."""

    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class AdvertisementPublicationMode(StrEnum):
    """Approved publication modes for scheduled advertisement campaigns."""

    COPY = "copy"


class AdvertisementErrorPolicy(StrEnum):
    """Approved error handling policies for scheduled advertisement campaigns."""

    RETRY_THEN_FAIL = "retry_then_fail"


@dataclass(frozen=True, slots=True)
class SourceAdvertisementPost:
    """Immutable representation of a validated public Telegram source post."""

    url: str
    channel_username: str
    message_id: int

    def __post_init__(self) -> None:
        """Validate invariant attributes of the source advertisement post."""
        if not isinstance(self.url, str):
            raise ValueError("source_post_url must be a string")
        match = _PUBLIC_POST_URL_REGEX.match(self.url)
        if not match:
            raise ValueError(
                "source_post_url must be a valid public HTTPS t.me post URL"
            )
        url_username = match.group(1)
        url_msg_id = int(match.group(2))
        if (
            self.channel_username.strip().lower() != url_username.lower()
        ):
            raise ValueError("source_channel_username does not match source_post_url")
        if self.message_id != url_msg_id:
            raise ValueError("message_id does not match source_post_url")


@dataclass(frozen=True, slots=True)
class AdvertisementCampaign:
    """Immutable domain representation of a scheduled advertisement campaign."""

    campaign_id: str
    name: str
    enabled: bool
    source_post: SourceAdvertisementPost
    destination_names: tuple[str, ...]
    weekdays: tuple[Weekday, ...]
    times: tuple[time, ...]
    start_date: date
    end_date: date
    timezone: ZoneInfo
    publication_mode: AdvertisementPublicationMode
    priority: int
    minimum_gap_seconds: int
    error_policy: AdvertisementErrorPolicy
    max_retries: int

    def __post_init__(self) -> None:
        """Enforce strict invariant rules for advertisement campaigns."""
        if (
            not _CAMPAIGN_ID_REGEX.match(self.campaign_id)
            or len(self.campaign_id) > 64
        ):
            raise ValueError(
                "campaign_id must be a valid non-empty ASCII slug "
                "(letters, digits, _, -)"
            )
        if not self.name or self.name.isspace():
            raise ValueError("name must be a non-blank string")
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        if not isinstance(self.source_post, SourceAdvertisementPost):
            raise ValueError("source_post must be a SourceAdvertisementPost instance")
        if not self.destination_names:
            raise ValueError(
                "destination_names must be a non-empty tuple of destination names"
            )
        if len(self.destination_names) != len(set(self.destination_names)):
            raise ValueError("destination_names must be unique")
        if not self.weekdays:
            raise ValueError("weekdays must be a non-empty tuple of Weekday values")
        if len(self.weekdays) != len(set(self.weekdays)):
            raise ValueError("weekdays must be unique")
        if not self.times:
            raise ValueError("times must be a non-empty tuple of datetime.time values")
        if len(self.times) != len(set(self.times)):
            raise ValueError("times must be unique")
        if not isinstance(self.start_date, date):
            raise ValueError("start_date must be a datetime.date instance")
        if not isinstance(self.end_date, date):
            raise ValueError("end_date must be a datetime.date instance")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be before or equal to end_date")
        if not isinstance(self.timezone, ZoneInfo):
            raise ValueError("timezone must be a ZoneInfo instance")
        if not isinstance(self.publication_mode, AdvertisementPublicationMode):
            raise ValueError(
                "publication_mode must be an AdvertisementPublicationMode enum"
            )
        if type(self.priority) is not int or self.priority < 0:
            raise ValueError("priority must be a non-negative integer")
        if type(self.minimum_gap_seconds) is not int or self.minimum_gap_seconds <= 0:
            raise ValueError("minimum_gap_seconds must be a positive integer")
        if not isinstance(self.error_policy, AdvertisementErrorPolicy):
            raise ValueError("error_policy must be an AdvertisementErrorPolicy enum")
        if type(self.max_retries) is not int or not (0 <= self.max_retries <= 10):
            raise ValueError("max_retries must be an integer between 0 and 10")
