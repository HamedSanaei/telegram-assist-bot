"""Thin worker trigger for bounded media cleanup."""

from datetime import datetime

from telegram_assist_bot.application.cleanup_expired_media import CleanupExpiredMedia


async def cleanup_media_once(use_case: CleanupExpiredMedia, *, now: datetime) -> int:
    """Run one bounded cleanup batch."""
    return await use_case.execute(now=now)
