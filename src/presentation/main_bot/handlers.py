"""Command handlers for the main management bot."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.domain.interfaces import AdminRepository, ChannelRepository
from src.shared.config import AppConfig
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


def create_main_router(
    config: AppConfig,
    admins: AdminRepository,
    channels: ChannelRepository,
) -> Router:
    """
    Create the router for ``telegram.bot_token`` management commands.

    Args:
        config: Loaded application configuration.
        admins: Admin repository used to restrict commands.
        channels: Channel repository used for status reports.

    Returns:
        A configured aiogram :class:`Router`.

    Example:
        dispatcher.include_router(create_main_router(config, admins, channels))
    """
    router = Router(name="main-management")

    async def _is_admin(message: Message) -> bool:
        """Return whether the sender is an allowed admin."""
        user_id = message.from_user.id if message.from_user else 0
        if await admins.is_admin(user_id):
            return True
        logger.warning("Non-admin user tried main bot command user=%s", user_id)
        await message.answer(
            "⛔️ دسترسی شما مجاز نیست.\n"
            f"شناسه شما: {user_id}\n"
            "این شناسه باید در telegram.admin_user_ids تنظیم شود."
        )
        return False

    @router.message(Command("start", "help"))
    async def on_start(message: Message) -> None:
        """Show the main bot command list."""
        if not await _is_admin(message):
            return
        await message.answer(
            "🤖 ربات مدیریت پست‌یاب فعال است.\n\n"
            "دستورهای فعلی:\n"
            "/status - وضعیت کلی سیستم\n"
            "/sources - کانال‌های منبع\n"
            "/destinations - کانال‌های مقصد"
        )

    @router.message(Command("status"))
    async def on_status(message: Message) -> None:
        """Show a compact runtime configuration summary."""
        if not await _is_admin(message):
            return
        destinations = await channels.list_destinations()
        sources = await channels.list_sources()
        await message.answer(
            "📊 وضعیت سیستم\n"
            f"منبع‌ها: {len(sources)}\n"
            f"مقصدها: {len(destinations)}\n"
            f"ادمین‌ها: {len(config.telegram.admin_user_ids)}\n"
            f"MongoDB: {config.database.mongodb_database}\n"
            f"AI: {config.ai.primary_provider} → {config.ai.fallback_provider or 'none'}\n"
            "Backfill: امروز میلادی "
            f"(تا {config.telegram.collector_daily_backfill_max_messages} پیام هر منبع)"
        )

    @router.message(Command("sources"))
    async def on_sources(message: Message) -> None:
        """List configured source channels."""
        if not await _is_admin(message):
            return
        sources = await channels.list_sources()
        text = "📥 کانال‌های منبع:\n" + "\n".join(f"- {source}" for source in sources)
        await message.answer(text if sources else "هیچ کانال منبعی تنظیم نشده است.")

    @router.message(Command("destinations"))
    async def on_destinations(message: Message) -> None:
        """List configured destination channels."""
        if not await _is_admin(message):
            return
        destinations = await channels.list_destinations()
        if not destinations:
            await message.answer("هیچ کانال مقصدی تنظیم نشده است.")
            return
        lines = ["📤 کانال‌های مقصد:"]
        for channel in destinations:
            public_id = channel.public_id or "(public_id تنظیم نشده)"
            lines.append(
                f"- {channel.title}: {channel.chat_id} | {public_id} | {channel.kind.value}"
            )
        await message.answer("\n".join(lines))

    return router
