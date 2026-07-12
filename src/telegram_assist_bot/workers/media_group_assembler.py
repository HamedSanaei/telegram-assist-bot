"""Thin worker trigger for durable media-group finalization."""

from datetime import datetime

from telegram_assist_bot.application.assemble_media_group import AssembleMediaGroup


async def finalize_media_group_once(
    use_case: AssembleMediaGroup, group_key: str, *, now: datetime
) -> bool:
    """Attempt one atomic due-group finalization."""
    return await use_case.finalize_if_due(group_key, now=now)
