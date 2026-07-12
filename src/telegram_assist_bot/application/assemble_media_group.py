"""Durable deterministic Telegram media-group assembly."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from telegram_assist_bot.application.ports import (
    ContentPreparationRepository,
    MediaGroup,
    MediaGroupMember,
)


def media_group_key(source_channel_id: int, telegram_group_id: str) -> str:
    """Build the canonical source-scoped group identity."""
    if source_channel_id == 0 or not telegram_group_id:
        raise ValueError("Media group identity is invalid.")
    return f"{source_channel_id}:{telegram_group_id}"


class AssembleMediaGroup:
    """Add replay-safe members and finalize after durable bounded windows."""

    def __init__(
        self,
        repository: ContentPreparationRepository,
        *,
        quiet_window: timedelta,
        maximum_wait: timedelta,
    ) -> None:
        """Initialize durable group windows and repository."""
        if quiet_window <= timedelta(0) or maximum_wait < quiet_window:
            raise ValueError("Media group windows are invalid.")
        self._repository = repository
        self._quiet = quiet_window
        self._maximum = maximum_wait

    async def add_member(
        self,
        *,
        source_channel_id: int,
        telegram_group_id: str,
        member: MediaGroupMember,
    ) -> MediaGroup:
        """Persist an out-of-order member without duplicating replays."""
        key = media_group_key(source_channel_id, telegram_group_id)
        existing = await self._repository.get_group(key)
        if existing is not None and existing.finalized_at is not None:
            # Finalized groups deterministically ignore late members.
            return existing
        if existing is None:
            group = MediaGroup(
                group_key=key,
                source_channel_id=source_channel_id,
                telegram_group_id=telegram_group_id,
                members=(),
                first_member_at=member.source_date,
                last_member_at=member.source_date,
                finalize_after=member.source_date + self._quiet,
                maximum_wait_until=member.source_date + self._maximum,
            )
        else:
            last = max(existing.last_member_at, member.source_date)
            group = replace(
                existing,
                last_member_at=last,
                finalize_after=min(last + self._quiet, existing.maximum_wait_until),
            )
        return await self._repository.add_group_member(group, member)

    async def finalize_if_due(self, group_key: str, *, now: datetime) -> bool:
        """Atomically let only one worker finalize a due group."""
        group = await self._repository.get_group(group_key)
        if group is None or group.finalized_at is not None:
            return False
        if now < min(group.finalize_after, group.maximum_wait_until):
            return False
        return await self._repository.finalize_group(group_key, at=now)
