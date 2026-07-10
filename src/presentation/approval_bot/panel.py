"""Callback-driven management panel hosted by the approval bot."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.application.management_service import ManagementService
from src.domain.entities import DestinationChannel
from src.domain.enums import ChannelKind
from src.domain.interfaces import AdminRepository
from src.shared.config import RecurringForwardConfig
from src.shared.errors import ConfigurationError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)
_PREFIX = "pnl"
_PAGE_SIZE = 8


class PanelStates(StatesGroup):
    """Conversation states used by management-panel wizards."""

    source_identifier = State()
    destination_chat_id = State()
    destination_title = State()
    destination_public_id = State()
    destination_kind = State()
    campaign_id = State()
    campaign_url = State()
    campaign_destinations = State()
    campaign_times = State()
    campaign_header = State()


@dataclass
class _DestinationDraft:
    """Temporary destination wizard values stored in FSM data."""

    chat_id: int
    title: str = ""
    public_id: str = ""


def _keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Build an inline keyboard from compact text/callback tuples."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def _main_keyboard() -> InlineKeyboardMarkup:
    """Return the root panel keyboard."""
    return _keyboard(
        [
            [("📥 کانال‌های مبدا", f"{_PREFIX}:src:list:0")],
            [("📤 کانال‌های مقصد", f"{_PREFIX}:dst:list:0")],
            [("📢 تبلیغات روزانه", f"{_PREFIX}:cmp:list:0")],
            [("📊 وضعیت سیستم", f"{_PREFIX}:status")],
            [("✖️ بستن", f"{_PREFIX}:close")],
        ]
    )


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Edit the current panel message, falling back to a new response."""
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            await callback.message.answer(text, reply_markup=markup)


def create_panel_router(
    management: ManagementService,
    admins: AdminRepository,
) -> Router:
    """
    Create the admin-only ``/panel`` router.

    Args:
        management: Application service persisting panel actions.
        admins: Admin repository used on every message and callback.

    Returns:
        Configured aiogram router.
    """
    router = Router(name="approval-management-panel")

    def callback_page(callback: CallbackQuery) -> int:
        """Return a non-negative page suffix from callback data."""
        try:
            return max(0, int((callback.data or "").rsplit(":", maxsplit=1)[-1]))
        except ValueError:
            return 0

    async def is_admin(user_id: int) -> bool:
        """Return whether the user may use panel operations."""
        allowed = await admins.is_admin(user_id)
        if not allowed:
            logger.warning("Unauthorized management panel access user=%s", user_id)
        return allowed

    async def require_callback_admin(callback: CallbackQuery) -> bool:
        """Validate a callback sender and answer unauthorized attempts."""
        if await is_admin(callback.from_user.id):
            return True
        await callback.answer("⛔️ دسترسی مجاز نیست.", show_alert=True)
        return False

    async def require_message_admin(message: Message) -> bool:
        """Validate a message sender and reply to unauthorized attempts."""
        user_id = message.from_user.id if message.from_user else 0
        if await is_admin(user_id):
            return True
        await message.answer("⛔️ دسترسی مجاز نیست.")
        return False

    @router.message(Command("panel"))
    async def show_panel(message: Message, state: FSMContext) -> None:
        """Open the management panel root menu."""
        if not await require_message_admin(message):
            return
        await state.clear()
        await message.answer("⚙️ پنل مدیریت", reply_markup=_main_keyboard())

    @router.callback_query(F.data == f"{_PREFIX}:home")
    async def panel_home(callback: CallbackQuery, state: FSMContext) -> None:
        """Return to the root panel menu."""
        if not await require_callback_admin(callback):
            return
        await state.clear()
        await _edit(callback, "⚙️ پنل مدیریت", _main_keyboard())
        await callback.answer()

    @router.callback_query(F.data == f"{_PREFIX}:close")
    async def panel_close(callback: CallbackQuery, state: FSMContext) -> None:
        """Close the current panel message."""
        if not await require_callback_admin(callback):
            return
        await state.clear()
        if callback.message:
            await callback.message.delete()
        await callback.answer()

    @router.callback_query(F.data == f"{_PREFIX}:status")
    async def panel_status(callback: CallbackQuery) -> None:
        """Show a compact live management status."""
        if not await require_callback_admin(callback):
            return
        sources = await management.list_sources()
        destinations = await management.list_destinations()
        campaigns = await management.list_campaigns()
        text = (
            "📊 وضعیت سیستم\n\n"
            f"📥 مبداها: {len(sources)}\n"
            f"📤 مقصدها: {len(destinations)}\n"
            f"📢 تبلیغات: {len(campaigns)} "
            f"(فعال: {sum(1 for item in campaigns if item.enabled)})"
        )
        await _edit(callback, text, _keyboard([[("↩️ بازگشت", f"{_PREFIX}:home")]]))
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{_PREFIX}:src:list"))
    async def list_sources(
        callback: CallbackQuery,
        answer: bool = True,
        page_override: int | None = None,
    ) -> None:
        """List sources with add and remove actions."""
        if not await require_callback_admin(callback):
            return
        sources = await management.list_sources()
        page = callback_page(callback) if page_override is None else page_override
        start = page * _PAGE_SIZE
        visible = sources[start : start + _PAGE_SIZE]
        rows = [
            [(f"🗑 {source[:42]}", f"{_PREFIX}:src:del:{index}")]
            for index, source in enumerate(visible, start=start)
        ]
        navigation: list[tuple[str, str]] = []
        if page > 0:
            navigation.append(("◀️ قبلی", f"{_PREFIX}:src:list:{page - 1}"))
        if start + _PAGE_SIZE < len(sources):
            navigation.append(("بعدی ▶️", f"{_PREFIX}:src:list:{page + 1}"))
        if navigation:
            rows.append(navigation)
        rows.extend(
            [[("➕ افزودن مبدا", f"{_PREFIX}:src:add")], [("↩️ بازگشت", f"{_PREFIX}:home")]]
        )
        text = "📥 کانال‌های مبدا\n\n" + (
            "\n".join(f"{index + 1}. {source}" for index, source in enumerate(sources))
            if sources
            else "موردی ثبت نشده است."
        )
        await _edit(callback, text[:3900], _keyboard(rows))
        if answer:
            await callback.answer()

    @router.callback_query(F.data == f"{_PREFIX}:src:add")
    async def begin_add_source(callback: CallbackQuery, state: FSMContext) -> None:
        """Start the source-add wizard."""
        if not await require_callback_admin(callback):
            return
        await state.set_state(PanelStates.source_identifier)
        await callback.message.answer("شناسه مبدا را بفرستید: @username، لینک یا chat_id")
        await callback.answer()

    @router.message(PanelStates.source_identifier)
    async def finish_add_source(message: Message, state: FSMContext) -> None:
        """Persist a source identifier entered by an admin."""
        if not await require_message_admin(message):
            return
        identifier = (message.text or "").strip()
        if not identifier:
            await message.answer("شناسه خالی است. دوباره ارسال کنید.")
            return
        await management.add_source(identifier)
        await state.clear()
        await message.answer(
            f"✅ مبدا {identifier} ذخیره شد.", reply_markup=_main_keyboard()
        )

    @router.callback_query(F.data.startswith(f"{_PREFIX}:src:del:"))
    async def remove_source(callback: CallbackQuery) -> None:
        """Remove a source selected by list index."""
        if not await require_callback_admin(callback):
            return
        sources = await management.list_sources()
        index = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        if index >= len(sources):
            await callback.answer("فهرست تغییر کرده است؛ دوباره باز کنید.", show_alert=True)
            return
        await management.remove_source(sources[index])
        await list_sources(callback, answer=False, page_override=0)
        await callback.answer("✅ حذف شد.")

    @router.callback_query(F.data.startswith(f"{_PREFIX}:dst:list"))
    async def list_destinations(
        callback: CallbackQuery,
        answer: bool = True,
        page_override: int | None = None,
    ) -> None:
        """List destinations with add and remove actions."""
        if not await require_callback_admin(callback):
            return
        destinations = await management.list_destinations()
        page = callback_page(callback) if page_override is None else page_override
        start = page * _PAGE_SIZE
        visible = destinations[start : start + _PAGE_SIZE]
        rows = [
            [(f"🗑 {item.title[:38]}", f"{_PREFIX}:dst:del:{item.chat_id}")]
            for item in visible
        ]
        navigation = []
        if page > 0:
            navigation.append(("◀️ قبلی", f"{_PREFIX}:dst:list:{page - 1}"))
        if start + _PAGE_SIZE < len(destinations):
            navigation.append(("بعدی ▶️", f"{_PREFIX}:dst:list:{page + 1}"))
        if navigation:
            rows.append(navigation)
        rows.extend(
            [[("➕ افزودن مقصد", f"{_PREFIX}:dst:add")], [("↩️ بازگشت", f"{_PREFIX}:home")]]
        )
        text = "📤 کانال‌های مقصد\n\n" + (
            "\n".join(
                f"{item.title}: {item.chat_id} | {item.kind.value} | {item.public_id or '-'}"
                for item in destinations
            )
            if destinations
            else "موردی ثبت نشده است."
        )
        await _edit(callback, text[:3900], _keyboard(rows))
        if answer:
            await callback.answer()

    @router.callback_query(F.data == f"{_PREFIX}:dst:add")
    async def begin_add_destination(callback: CallbackQuery, state: FSMContext) -> None:
        """Start the destination-add wizard."""
        if not await require_callback_admin(callback):
            return
        await state.set_state(PanelStates.destination_chat_id)
        await callback.message.answer("chat_id عددی مقصد را بفرستید (با -100).")
        await callback.answer()

    @router.message(PanelStates.destination_chat_id)
    async def destination_chat_id(message: Message, state: FSMContext) -> None:
        """Capture destination chat id."""
        if not await require_message_admin(message):
            return
        try:
            chat_id = int((message.text or "").strip())
        except ValueError:
            await message.answer("chat_id باید عددی باشد.")
            return
        await state.update_data(destination={"chat_id": chat_id})
        await state.set_state(PanelStates.destination_title)
        await message.answer("عنوان نمایشی کانال را بفرستید.")

    @router.message(PanelStates.destination_title)
    async def destination_title(message: Message, state: FSMContext) -> None:
        """Capture destination title."""
        if not await require_message_admin(message):
            return
        data = await state.get_data()
        draft = dict(data["destination"])
        draft["title"] = (message.text or "").strip()
        await state.update_data(destination=draft)
        await state.set_state(PanelStates.destination_public_id)
        await message.answer("public_id مقصد را بفرستید، مثل @mychannel؛ برای خالی بودن - بفرستید.")

    @router.message(PanelStates.destination_public_id)
    async def destination_public_id(message: Message, state: FSMContext) -> None:
        """Capture destination public id and ask for channel kind."""
        if not await require_message_admin(message):
            return
        data = await state.get_data()
        draft = dict(data["destination"])
        raw = (message.text or "").strip()
        draft["public_id"] = "" if raw == "-" else raw
        await state.update_data(destination=draft)
        await state.set_state(PanelStates.destination_kind)
        await message.answer(
            "نوع کانال را انتخاب کنید.",
            reply_markup=_keyboard(
                [
                    [("خبر", f"{_PREFIX}:dst:kind:news"), ("فوری", f"{_PREFIX}:dst:kind:breaking")],
                    [("تکنولوژی", f"{_PREFIX}:dst:kind:technology"), ("VPN", f"{_PREFIX}:dst:kind:vpn")],
                ]
            ),
        )

    @router.callback_query(PanelStates.destination_kind, F.data.startswith(f"{_PREFIX}:dst:kind:"))
    async def finish_add_destination(callback: CallbackQuery, state: FSMContext) -> None:
        """Persist the completed destination wizard."""
        if not await require_callback_admin(callback):
            return
        kind = ChannelKind((callback.data or "").rsplit(":", maxsplit=1)[-1])
        data = await state.get_data()
        draft = _DestinationDraft(**dict(data["destination"]))
        channel = DestinationChannel(
            chat_id=draft.chat_id,
            title=draft.title or str(draft.chat_id),
            public_id=draft.public_id,
            kind=kind,
        )
        await management.upsert_destination(channel)
        await state.clear()
        await callback.message.answer(
            f"✅ مقصد {channel.title} ذخیره شد.", reply_markup=_main_keyboard()
        )
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{_PREFIX}:dst:del:"))
    async def remove_destination(callback: CallbackQuery) -> None:
        """Remove a destination selected by chat id."""
        if not await require_callback_admin(callback):
            return
        chat_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        await management.remove_destination(chat_id)
        await list_destinations(callback, answer=False, page_override=0)
        await callback.answer("✅ حذف شد.")

    @router.callback_query(F.data.startswith(f"{_PREFIX}:cmp:list"))
    async def list_campaigns(
        callback: CallbackQuery,
        answer: bool = True,
        page_override: int | None = None,
    ) -> None:
        """List recurring campaigns and management actions."""
        if not await require_callback_admin(callback):
            return
        campaigns = await management.list_campaigns()
        page = callback_page(callback) if page_override is None else page_override
        start = page * _PAGE_SIZE
        visible = campaigns[start : start + _PAGE_SIZE]
        rows: list[list[tuple[str, str]]] = []
        for index, item in enumerate(visible, start=start):
            rows.append(
                [
                    (("⏸" if item.enabled else "▶️") + f" {item.id[:22]}", f"{_PREFIX}:cmp:toggle:{index}"),
                    ("✏️", f"{_PREFIX}:cmp:edit:{index}"),
                    ("🗑", f"{_PREFIX}:cmp:del:{index}"),
                ]
            )
        navigation = []
        if page > 0:
            navigation.append(("◀️ قبلی", f"{_PREFIX}:cmp:list:{page - 1}"))
        if start + _PAGE_SIZE < len(campaigns):
            navigation.append(("بعدی ▶️", f"{_PREFIX}:cmp:list:{page + 1}"))
        if navigation:
            rows.append(navigation)
        rows.extend(
            [[("➕ افزودن تبلیغ", f"{_PREFIX}:cmp:add")], [("↩️ بازگشت", f"{_PREFIX}:home")]]
        )
        text = "📢 تبلیغات روزانه\n\n" + (
            "\n".join(
                f"{'✅' if item.enabled else '⏸'} {item.id} | {', '.join(item.times)} | {len(item.destination_chat_ids)} مقصد"
                for item in campaigns
            )
            if campaigns
            else "موردی ثبت نشده است."
        )
        await _edit(callback, text[:3900], _keyboard(rows))
        if answer:
            await callback.answer()

    @router.callback_query(F.data == f"{_PREFIX}:cmp:add")
    async def begin_add_campaign(callback: CallbackQuery, state: FSMContext) -> None:
        """Start the recurring-campaign wizard."""
        if not await require_callback_admin(callback):
            return
        await state.set_state(PanelStates.campaign_id)
        await callback.message.answer("یک شناسه کوتاه انگلیسی برای تبلیغ بفرستید، مثل daily_ad_1")
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{_PREFIX}:cmp:edit:"))
    async def begin_edit_campaign(callback: CallbackQuery, state: FSMContext) -> None:
        """Load an existing campaign and restart the wizard from its URL."""
        if not await require_callback_admin(callback):
            return
        campaigns = await management.list_campaigns()
        index = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        if index >= len(campaigns):
            await callback.answer("فهرست تغییر کرده است.", show_alert=True)
            return
        campaign = campaigns[index]
        await state.update_data(campaign={"id": campaign.id, "enabled": campaign.enabled})
        await state.set_state(PanelStates.campaign_url)
        await callback.message.answer(
            f"لینک جدید را بفرستید. لینک فعلی:\n{campaign.source_post_url}"
        )
        await callback.answer()

    @router.message(PanelStates.campaign_id)
    async def campaign_id(message: Message, state: FSMContext) -> None:
        """Capture a recurring campaign id."""
        if not await require_message_admin(message):
            return
        value = (message.text or "").strip()
        if not value or len(value) > 40 or not all(c.isalnum() or c in "_-" for c in value):
            await message.answer("شناسه فقط حروف انگلیسی، عدد، _ و - و حداکثر ۴۰ کاراکتر باشد.")
            return
        await state.update_data(campaign={"id": value})
        await state.set_state(PanelStates.campaign_url)
        await message.answer("لینک پست مبدا را بفرستید.")

    @router.message(PanelStates.campaign_url)
    async def campaign_url(message: Message, state: FSMContext) -> None:
        """Capture the source post URL."""
        if not await require_message_admin(message):
            return
        value = (message.text or "").strip()
        if not value.startswith(("https://t.me/", "http://t.me/")):
            await message.answer("لینک باید با https://t.me/ شروع شود.")
            return
        data = await state.get_data()
        draft = dict(data["campaign"])
        draft["source_post_url"] = value
        await state.update_data(campaign=draft)
        await state.set_state(PanelStates.campaign_destinations)
        await message.answer("chat_id مقصدها را با کاما بفرستید.")

    @router.message(PanelStates.campaign_destinations)
    async def campaign_destinations(message: Message, state: FSMContext) -> None:
        """Capture destination ids for a recurring campaign."""
        if not await require_message_admin(message):
            return
        try:
            values = [int(value.strip()) for value in (message.text or "").split(",") if value.strip()]
        except ValueError:
            await message.answer("همه مقصدها باید chat_id عددی باشند.")
            return
        known = {item.chat_id for item in await management.list_destinations()}
        if not values or any(value not in known for value in values):
            await message.answer("حداقل یک مقصد ثبت‌شده و معتبر وارد کنید.")
            return
        data = await state.get_data()
        draft = dict(data["campaign"])
        draft["destination_chat_ids"] = list(dict.fromkeys(values))
        await state.update_data(campaign=draft)
        await state.set_state(PanelStates.campaign_times)
        await message.answer("ساعت‌های تهران را با کاما بفرستید، مثل 09:00,15:00,21:00")

    @router.message(PanelStates.campaign_times)
    async def campaign_times(message: Message, state: FSMContext) -> None:
        """Capture and validate daily campaign times."""
        if not await require_message_admin(message):
            return
        values = [value.strip() for value in (message.text or "").split(",") if value.strip()]
        try:
            for value in values:
                hour, minute = (int(part) for part in value.split(":"))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError
        except (ValueError, TypeError):
            await message.answer("فرمت ساعت معتبر نیست. نمونه: 09:00,21:30")
            return
        data = await state.get_data()
        draft = dict(data["campaign"])
        draft["times"] = list(dict.fromkeys(values))
        await state.update_data(campaign=draft)
        await state.set_state(PanelStates.campaign_header)
        await message.answer(
            "هدر Forwarded from نمایش داده شود؟",
            reply_markup=_keyboard(
                [[("نمایش هدر", f"{_PREFIX}:cmp:header:yes"), ("بدون هدر", f"{_PREFIX}:cmp:header:no")]]
            ),
        )

    @router.callback_query(PanelStates.campaign_header, F.data.startswith(f"{_PREFIX}:cmp:header:"))
    async def finish_campaign(callback: CallbackQuery, state: FSMContext) -> None:
        """Persist the completed recurring campaign."""
        if not await require_callback_admin(callback):
            return
        data = await state.get_data()
        draft = dict(data["campaign"])
        campaign = RecurringForwardConfig(
            id=str(draft["id"]),
            source_post_url=str(draft["source_post_url"]),
            destination_chat_ids=[int(value) for value in draft["destination_chat_ids"]],
            times=[str(value) for value in draft["times"]],
            enabled=bool(draft.get("enabled", True)),
            show_forward_header=(callback.data or "").endswith(":yes"),
        )
        try:
            await management.upsert_campaign(campaign)
        except ConfigurationError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await state.clear()
        await callback.message.answer(
            f"✅ تبلیغ {campaign.id} ذخیره شد.", reply_markup=_main_keyboard()
        )
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{_PREFIX}:cmp:toggle:"))
    async def toggle_campaign(callback: CallbackQuery) -> None:
        """Toggle one recurring campaign enabled state."""
        if not await require_callback_admin(callback):
            return
        campaigns = await management.list_campaigns()
        index = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        if index >= len(campaigns):
            await callback.answer("فهرست تغییر کرده است.", show_alert=True)
            return
        campaign = campaigns[index]
        await management.set_campaign_enabled(campaign.id, not campaign.enabled)
        await list_campaigns(callback, answer=False, page_override=0)
        await callback.answer("✅ وضعیت تغییر کرد.")

    @router.callback_query(F.data.startswith(f"{_PREFIX}:cmp:del:"))
    async def delete_campaign(callback: CallbackQuery) -> None:
        """Delete one recurring campaign."""
        if not await require_callback_admin(callback):
            return
        campaigns = await management.list_campaigns()
        index = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        if index >= len(campaigns):
            await callback.answer("فهرست تغییر کرده است.", show_alert=True)
            return
        await management.delete_campaign(campaigns[index].id)
        await list_campaigns(callback, answer=False, page_override=0)
        await callback.answer("✅ حذف شد.")

    @router.message(Command("cancel"))
    async def cancel_panel_state(message: Message, state: FSMContext) -> None:
        """Cancel any active panel wizard."""
        if not await require_message_admin(message):
            return
        await state.clear()
        await message.answer("عملیات لغو شد.", reply_markup=_main_keyboard())

    return router
