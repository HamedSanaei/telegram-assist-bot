"""Command handlers for the main management bot.

Admins manage source channels, destination channels, and the per-channel
scheduled-publishing interval here. Runtime command edits are stored in
SQLite, but ``configuration.json`` hot reload is authoritative for channel
and admin lists and may overwrite command-made channel changes.
"""

from __future__ import annotations

from dataclasses import replace

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.domain.entities import DestinationChannel
from src.domain.enums import ChannelKind
from src.domain.interfaces import AdminRepository, ChannelRepository
from src.shared.config import AppConfig
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

_HELP_TEXT = (
    "🤖 ربات مدیریت پست‌یاب فعال است.\n\n"
    "گزارش‌ها:\n"
    "/status - وضعیت کلی سیستم\n"
    "/sources - کانال‌های منبع\n"
    "/destinations - کانال‌های مقصد\n\n"
    "مدیریت منبع‌ها:\n"
    "/addsource <@username یا chat_id> - افزودن کانال منبع\n"
    "/delsource <@username یا chat_id> - حذف کانال منبع\n\n"
    "مدیریت مقصدها:\n"
    "/adddest <chat_id> <عنوان> - افزودن کانال مقصد\n"
    "/deldest <chat_id> - غیرفعال کردن کانال مقصد\n"
    "/setdest <chat_id> <فیلد> <مقدار> - تغییر تنظیمات کانال مقصد\n"
    "    فیلدها: title | public_id | kind (news/breaking/technology/vpn)\n"
    "    | usd (on/off) | enabled (on/off) | interval (دقیقه بین پست‌ها)\n"
    "/setinterval <chat_id> <دقیقه> - میان‌بر تنظیم فاصله زمان‌بندی"
)

_TRUTHY = {"on", "1", "true", "yes"}
_FALSY = {"off", "0", "false", "no"}


def _args(message: Message, maxsplit: int) -> list[str]:
    """
    Return command arguments split from the message text.

    Args:
        message: The incoming command message.
        maxsplit: Maximum number of splits (command itself included).

    Returns:
        The argument list, excluding the command word itself.
    """
    return (message.text or "").split(maxsplit=maxsplit)[1:]


def _parse_bool(value: str) -> bool | None:
    """Parse an on/off argument; return ``None`` when unrecognized."""
    lowered = value.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    return None


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
        channels: Channel repository for reading and editing channels.

    Returns:
        A configured aiogram :class:`Router`.

    Example:
        dispatcher.include_router(create_main_router(config, admins, channels))
    """
    router = Router(name="main-management")

    def _ai_provider_status() -> str:
        """Return a compact non-secret AI provider chain summary."""
        active = [
            provider.name
            for provider in config.ai.providers
            if provider.enabled
            and provider.api_key
            and provider.base_url
            and provider.model
        ]
        enabled = [provider.name for provider in config.ai.providers if provider.enabled]
        names = active or enabled
        return " → ".join(names) if names else "none"

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

    async def _load_destination(
        message: Message, chat_id_raw: str
    ) -> DestinationChannel | None:
        """Parse a chat id and load its destination row, replying on errors."""
        try:
            chat_id = int(chat_id_raw)
        except ValueError:
            await message.answer("chat_id باید عددی باشد (مثال: -1001234567890).")
            return None
        channel = await channels.get_destination(chat_id)
        if channel is None:
            await message.answer(f"کانال مقصدی با شناسه {chat_id} ثبت نشده است.")
            return None
        return channel

    @router.message(Command("start", "help"))
    async def on_start(message: Message) -> None:
        """Show the main bot command list."""
        if not await _is_admin(message):
            return
        await message.answer(_HELP_TEXT)

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
            f"AI: {_ai_provider_status()}\n"
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
        """List configured destination channels with their settings."""
        if not await _is_admin(message):
            return
        destinations = await channels.list_destinations()
        if not destinations:
            await message.answer("هیچ کانال مقصدی تنظیم نشده است.")
            return
        lines = ["📤 کانال‌های مقصد:"]
        for channel in destinations:
            public_id = channel.public_id or "(public_id تنظیم نشده)"
            usd = "💵" if channel.publish_usd_price else ""
            lines.append(
                f"- {channel.title}: {channel.chat_id} | {public_id} | "
                f"{channel.kind.value} | اسکجول: هر ۵ دقیقه {usd}"
            )
        await message.answer("\n".join(lines))

    @router.message(Command("addsource"))
    async def on_add_source(message: Message) -> None:
        """Add a source channel; the collector picks it up automatically."""
        if not await _is_admin(message):
            return
        args = _args(message, maxsplit=1)
        if not args:
            await message.answer("استفاده: /addsource <@username یا chat_id>")
            return
        identifier = args[0].strip()
        await channels.upsert_source(identifier)
        logger.info("Source channel added via bot identifier=%s", identifier)
        await message.answer(
            f"✅ کانال منبع «{identifier}» اضافه شد.\n"
            f"کالکتور حداکثر تا {config.telegram.source_refresh_seconds} ثانیه دیگر "
            "آن را زیر نظر می‌گیرد."
        )

    @router.message(Command("delsource"))
    async def on_del_source(message: Message) -> None:
        """Disable a source channel."""
        if not await _is_admin(message):
            return
        args = _args(message, maxsplit=1)
        if not args:
            await message.answer("استفاده: /delsource <@username یا chat_id>")
            return
        identifier = args[0].strip()
        if await channels.disable_source(identifier):
            logger.info("Source channel disabled via bot identifier=%s", identifier)
            await message.answer(f"✅ کانال منبع «{identifier}» حذف (غیرفعال) شد.")
        else:
            await message.answer(f"کانال منبعی با شناسه «{identifier}» پیدا نشد.")

    @router.message(Command("adddest"))
    async def on_add_destination(message: Message) -> None:
        """Add a destination channel with default settings."""
        if not await _is_admin(message):
            return
        args = _args(message, maxsplit=2)
        if len(args) < 2:
            await message.answer("استفاده: /adddest <chat_id> <عنوان>")
            return
        try:
            chat_id = int(args[0])
        except ValueError:
            await message.answer("chat_id باید عددی باشد (مثال: -1001234567890).")
            return
        title = args[1].strip()
        existing = await channels.get_destination(chat_id)
        if existing is not None and existing.enabled:
            await message.answer(f"کانال {chat_id} از قبل ثبت شده است ({existing.title}).")
            return
        channel = (
            replace(existing, title=title, enabled=True)
            if existing is not None
            else DestinationChannel(chat_id=chat_id, title=title)
        )
        await channels.upsert_destination(channel)
        logger.info("Destination channel added via bot chat=%s title=%s", chat_id, title)
        await message.answer(
            f"✅ کانال مقصد «{title}» ({chat_id}) اضافه شد.\n"
            f"نوع: {channel.kind.value} | اسکجول تلگرام: هر ۵ دقیقه\n"
            "با /setdest می‌توانید نوع، public_id و بقیه تنظیمات را تغییر دهید.\n"
            "یادآوری: ربات اصلی برای ارسال فوری و اکانت scheduler برای اسکجول "
            "باید ادمین این کانال باشند."
        )

    @router.message(Command("deldest"))
    async def on_del_destination(message: Message) -> None:
        """Disable a destination channel."""
        if not await _is_admin(message):
            return
        args = _args(message, maxsplit=1)
        if not args:
            await message.answer("استفاده: /deldest <chat_id>")
            return
        channel = await _load_destination(message, args[0])
        if channel is None:
            return
        await channels.upsert_destination(replace(channel, enabled=False))
        logger.info("Destination channel disabled via bot chat=%s", channel.chat_id)
        await message.answer(f"✅ کانال مقصد «{channel.title}» غیرفعال شد.")

    async def _set_interval(message: Message, channel: DestinationChannel, value: str) -> None:
        """Validate and store a new scheduling interval for a channel."""
        try:
            minutes = int(value)
        except ValueError:
            minutes = -1
        if minutes < 0:
            await message.answer("فاصله باید یک عدد صحیح (دقیقه، ۰ یا بیشتر) باشد.")
            return
        await channels.upsert_destination(replace(channel, post_interval_minutes=minutes))
        logger.info(
            "Destination interval changed via bot chat=%s minutes=%d",
            channel.chat_id,
            minutes,
        )
        await message.answer(
            f"✅ مقدار legacy interval برای «{channel.title}» ذخیره شد؛ "
            "اسکجول واقعی تلگرام فعلاً طبق سیاست جدید هر ۵ دقیقه چیده می‌شود."
        )

    @router.message(Command("setinterval"))
    async def on_set_interval(message: Message) -> None:
        """Shortcut for setting a channel's scheduling interval."""
        if not await _is_admin(message):
            return
        args = _args(message, maxsplit=2)
        if len(args) < 2:
            await message.answer("استفاده: /setinterval <chat_id> <دقیقه>")
            return
        channel = await _load_destination(message, args[0])
        if channel is None:
            return
        await _set_interval(message, channel, args[1])

    @router.message(Command("setdest"))
    async def on_set_destination(message: Message) -> None:
        """Change one setting of a destination channel."""
        if not await _is_admin(message):
            return
        args = _args(message, maxsplit=3)
        if len(args) < 3:
            await message.answer(
                "استفاده: /setdest <chat_id> <فیلد> <مقدار>\n"
                "فیلدها: title | public_id | kind | usd | enabled | interval"
            )
            return
        channel = await _load_destination(message, args[0])
        if channel is None:
            return
        field_name, value = args[1].strip().lower(), args[2].strip()

        if field_name == "interval":
            await _set_interval(message, channel, value)
            return
        if field_name == "title":
            updated = replace(channel, title=value)
        elif field_name == "public_id":
            updated = replace(channel, public_id=value)
        elif field_name == "kind":
            try:
                updated = replace(channel, kind=ChannelKind(value.lower()))
            except ValueError:
                kinds = ", ".join(kind.value for kind in ChannelKind)
                await message.answer(f"نوع نامعتبر است. مقادیر مجاز: {kinds}")
                return
        elif field_name in ("usd", "enabled"):
            flag = _parse_bool(value)
            if flag is None:
                await message.answer("مقدار باید on یا off باشد.")
                return
            if field_name == "usd":
                updated = replace(channel, publish_usd_price=flag)
            else:
                updated = replace(channel, enabled=flag)
        else:
            await message.answer(
                "فیلد ناشناخته است. فیلدها: title | public_id | kind | usd | enabled | interval"
            )
            return

        await channels.upsert_destination(updated)
        logger.info(
            "Destination setting changed via bot chat=%s field=%s",
            channel.chat_id,
            field_name,
        )
        await message.answer(f"✅ تنظیم «{field_name}» کانال «{updated.title}» ذخیره شد.")

    return router
