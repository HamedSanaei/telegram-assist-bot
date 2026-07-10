"""Approval request sender (implements :class:`ApprovalNotifier`)."""

from __future__ import annotations

import asyncio
from html import escape
from pathlib import Path
from datetime import timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import FSInputFile

from src.domain.entities import (
    ApprovalMessageRef,
    ApprovalPreviewRefreshResult,
    DestinationChannel,
    Post,
)
from src.domain.enums import MediaKind, PostCategory, QualityScoreStatus
from src.domain.interfaces import (
    AdminRepository,
    ApprovalMessageRepository,
    ChannelRepository,
)
from src.presentation.approval_bot.keyboards import build_channel_keyboard
from src.shared.errors import TelegramPublishError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

_PREVIEW_TEXT_LIMIT = 3000
_CAPTION_LIMIT = 1024
_ADMIN_SEND_DELAY_SECONDS = 0.25
_DEFAULT_TIMEZONE = "Asia/Tehran"

_CATEGORY_LABELS: dict[PostCategory, str] = {
    PostCategory.GENERAL_NEWS: "خبر عمومی",
    PostCategory.BREAKING_NEWS: "خبر فوری",
    PostCategory.TECHNOLOGY: "تکنولوژی",
    PostCategory.VPN: "وی‌پی‌ان",
    PostCategory.VPN_CONFIG: "کانفیگ وی‌پی‌ان",
    PostCategory.IRRELEVANT: "نامرتبط",
}


def build_preview_text(
    post: Post,
    source_label: str | None = None,
    timezone_name: str = _DEFAULT_TIMEZONE,
    body_limit: int = _PREVIEW_TEXT_LIMIT,
) -> str:
    """
    Build the Persian preview message shown to admins for approval.

    Args:
        post: The post awaiting approval.
        source_label: Optional readable source channel label.

    Returns:
        The formatted UTF-8 preview text.
    """
    category = _CATEGORY_LABELS.get(post.category, "نامشخص") if post.category else "نامشخص"
    header_lines = [
        "🆕 پست جدید در انتظار تایید",
        f"🏷 دسته‌بندی: {category}",
        f"📡 منبع: {source_label or post.source_chat_id}",
    ]
    published_at = post.source_metrics.source_published_at
    if published_at is not None:
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        local_time = published_at.astimezone(ZoneInfo(timezone_name))
        header_lines.append(f"🕒 انتشار مبدا: {local_time.strftime('%Y-%m-%d %H:%M')}")
    if post.quality_score is not None:
        header_lines.append(
            "⭐ امتیاز پیشنهادی: "
            f"{post.quality_score.score:.0f}/100 — {post.quality_score.reason}"
        )
    elif post.quality_score_status == QualityScoreStatus.PENDING:
        header_lines.append("⭐ امتیاز: در انتظار آمار ۲۰ دقیقه‌ای")
    elif post.quality_score_status == QualityScoreStatus.UNAVAILABLE:
        header_lines.append("⭐ امتیاز: در دسترس نیست")
    if post.media:
        header_lines.append(f"📎 دارای {len(post.media)} پیوست رسانه‌ای")
    if post.vpn_configs:
        working = sum(1 for c in post.vpn_configs if c.test_status.value == "working")
        unsupported = sum(
            1 for c in post.vpn_configs if c.test_status.value == "unsupported"
        )
        suffix = f"، تست پشتیبانی‌نشده: {unsupported}" if unsupported else ""
        header_lines.append(
            f"🔐 کانفیگ‌ها: {len(post.vpn_configs)} (سالم از ایران: {working}{suffix})"
        )
    header = "\n".join(f"<i>{escape(line)}</i>" for line in header_lines)
    body = escape(post.text[:body_limit] if post.text else "(بدون متن)")
    return f"{header}\n\n{body}"


def _first_existing_media(post: Post) -> tuple[MediaKind, Path] | None:
    """
    Return the first downloaded media path for an approval preview.

    Args:
        post: The post awaiting approval.

    Returns:
        The first existing local media kind and path, or ``None`` when the
        post has no downloaded media file.
    """
    preferred_order = (MediaKind.PHOTO, MediaKind.VIDEO, MediaKind.DOCUMENT)
    by_kind = {kind: [] for kind in preferred_order}
    for media in post.media:
        if media.kind in by_kind and media.file_path:
            by_kind[media.kind].append(Path(media.file_path))
    for kind in preferred_order:
        for path in by_kind[kind]:
            if path.exists():
                return kind, path
    return None


def _build_caption_text(
    post: Post,
    source_label: str | None,
    timezone_name: str,
) -> str:
    """Build valid HTML that always fits Telegram's caption limit."""
    low, high = 0, 500
    best = build_preview_text(post, source_label, timezone_name, body_limit=0)
    while low <= high:
        body_limit = (low + high) // 2
        candidate = build_preview_text(
            post,
            source_label,
            timezone_name,
            body_limit=body_limit,
        )
        if len(candidate) <= _CAPTION_LIMIT:
            best = candidate
            low = body_limit + 1
        else:
            high = body_limit - 1
    return best


class AiogramApprovalNotifier:
    """
    Sends approval request messages with channel buttons to every
    configured admin via the approval bot.

    Example:
        notifier = AiogramApprovalNotifier(approval_bot, admin_repo)
        await notifier.send_approval_request(post, channels)
    """

    def __init__(
        self,
        bot: Bot,
        admins: AdminRepository,
        channels: ChannelRepository | None = None,
        approval_messages: ApprovalMessageRepository | None = None,
        timezone_name: str = _DEFAULT_TIMEZONE,
    ) -> None:
        """
        Args:
            bot: aiogram bot created with the approval bot token.
            admins: Admin repository providing recipient user ids.
            channels: Optional channel repository used to display source
                channel names instead of raw numeric chat ids.
            approval_messages: Optional message-reference store used to edit
                already delivered previews after background scoring.
            timezone_name: IANA timezone used for source publish time display.
        """
        self._bot = bot
        self._admins = admins
        self._channels = channels
        self._approval_messages = approval_messages
        self._timezone_name = timezone_name

    async def send_approval_request(
        self, post: Post, channels: list[DestinationChannel]
    ) -> list[ApprovalMessageRef]:
        """
        Send the approval message to all admins.

        Args:
            post: The post awaiting approval.
            channels: Enabled destination channels for the buttons.

        Returns:
            References to delivered Telegram messages that carry the inline
            keyboard.

        Raises:
            TelegramPublishError: When no admin receives the approval
                message, so the queue can retry instead of marking the post
                as waiting for approval.
        """
        text = build_preview_text(
            post,
            await self._source_label(post),
            timezone_name=self._timezone_name,
        )
        keyboard = build_channel_keyboard(post.post_id, channels, published_chat_ids=set())
        success_count = 0
        failure_count = 0
        delivered: list[ApprovalMessageRef] = []
        for admin_id in await self._admins.list_user_ids():
            try:
                message_id, preview_kind = await self._send_preview(
                    admin_id, post, text, keyboard
                )
                delivered.append(
                    ApprovalMessageRef(
                        post_id=post.post_id,
                        admin_user_id=admin_id,
                        chat_id=admin_id,
                        message_id=message_id,
                        preview_kind=preview_kind,
                    )
                )
                success_count += 1
                await asyncio.sleep(_ADMIN_SEND_DELAY_SECONDS)
            except Exception as exc:
                failure_count += 1
                logger.error(
                    "Approval message failed admin=%s post=%s error=%s "
                    "(hint: the admin must open the approval bot and press "
                    "Start once before the bot can message them)",
                    admin_id,
                    post.post_id,
                    exc,
                )
        if success_count == 0:
            raise TelegramPublishError(
                "Approval message failed for all admins "
                f"post={post.post_id} failures={failure_count}"
            )
        return delivered

    async def _send_preview(
        self, admin_id: int, post: Post, text: str, keyboard: object
    ) -> tuple[int, str]:
        """
        Send the approval preview, including the first media file when present.

        Args:
            admin_id: Telegram user id of the admin recipient.
            post: The post awaiting approval.
            text: Preview text built by :func:`build_preview_text`.
            keyboard: Inline keyboard with destination channel buttons.

        Returns:
            Telegram message id that owns the inline keyboard.
        """
        media = _first_existing_media(post)
        if media is None:
            sent = await self._send_with_retry(
                self._bot.send_message,
                admin_id,
                text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            return self._message_id(sent), "text"

        media_kind, path = media
        input_file = FSInputFile(str(path))
        sender = self._media_sender(media_kind)
        if len(text) <= _CAPTION_LIMIT:
            sent = await self._send_media_with_fallback(
                sender=sender,
                admin_id=admin_id,
                input_file=input_file,
                media_kind=media_kind,
                post_id=post.post_id,
                caption=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            return self._message_id(sent), "caption"

        await self._send_media_with_fallback(
            sender=sender,
            admin_id=admin_id,
            input_file=input_file,
            media_kind=media_kind,
            post_id=post.post_id,
        )
        sent = await self._send_with_retry(
            self._bot.send_message,
            admin_id,
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return self._message_id(sent), "text"

    async def refresh_approval_request(
        self,
        post: Post,
        channels: list[DestinationChannel],
        published_chat_ids: set[int],
        scheduled_chat_ids: set[int],
        has_delivery_history: bool,
        refs: list[ApprovalMessageRef] | None = None,
    ) -> ApprovalPreviewRefreshResult:
        """
        Edit all active approval preview headers after background enrichment.

        Args:
            post: Updated MongoDB post.

        Returns:
            Counts of updated, retryable-failed, and permanently failed refs.
        """
        if self._approval_messages is None:
            return ApprovalPreviewRefreshResult()
        text = build_preview_text(
            post,
            await self._source_label(post),
            timezone_name=self._timezone_name,
        )
        caption_text = _build_caption_text(
            post,
            await self._source_label(post),
            self._timezone_name,
        )
        keyboard = build_channel_keyboard(
            post.post_id,
            channels,
            published_chat_ids,
            scheduled_chat_ids,
            has_delivery_history=has_delivery_history,
        )
        updated = 0
        retryable_failures = 0
        permanent_failures = 0
        target_refs = refs
        if target_refs is None:
            target_refs = await self._approval_messages.list_active(post.post_id)
        for ref in target_refs:
            try:
                actual_kind = await self._edit_reference(
                    ref,
                    post,
                    text,
                    caption_text,
                    keyboard,
                )
                updated += 1
                if ref.id is not None and ref.preview_kind != actual_kind:
                    await self._approval_messages.set_preview_kind(ref.id, actual_kind)
                    ref.preview_kind = actual_kind
                if not ref.active and ref.id is not None:
                    await self._approval_messages.activate(ref.id)
            except Exception as exc:
                logger.warning(
                    "Approval preview update failed post=%s chat=%s message=%s error=%s",
                    post.post_id,
                    ref.chat_id,
                    ref.message_id,
                    exc,
                )
                permanent = self._is_permanent_edit_error(exc)
                if permanent:
                    permanent_failures += 1
                else:
                    retryable_failures += 1
                if permanent and ref.active and ref.id is not None:
                    await self._approval_messages.deactivate(ref.id)
        if updated:
            logger.info("Approval previews updated post=%s count=%d", post.post_id, updated)
        return ApprovalPreviewRefreshResult(
            updated=updated,
            retryable_failures=retryable_failures,
            permanent_failures=permanent_failures,
        )

    async def _edit_reference(
        self,
        ref: ApprovalMessageRef,
        post: Post,
        text: str,
        caption_text: str,
        keyboard: object,
    ) -> str:
        """
        Edit one tracked preview and recover legacy text/caption mismatches.

        Args:
            ref: Stored approval-message reference.
            post: Current post used to infer legacy preview type.
            text: Full text-message preview.
            caption_text: Caption-sized preview.
            keyboard: Current callback keyboard.

        Returns:
            The actual Telegram body type successfully edited.

        Raises:
            Exception: The final Telegram edit error when neither body type
                can be edited.
        """
        preferred = self._preferred_preview_kind(ref, post, text)
        try:
            await self._edit_as(
                preferred,
                ref,
                text,
                caption_text,
                keyboard,
            )
            return preferred
        except Exception as exc:
            if self._is_not_modified_error(exc):
                return preferred
            if not self._is_kind_mismatch_error(preferred, exc):
                raise
            alternate = "caption" if preferred == "text" else "text"
            logger.info(
                "Correcting legacy approval preview kind post=%s chat=%s "
                "message=%s from=%s to=%s",
                post.post_id,
                ref.chat_id,
                ref.message_id,
                preferred,
                alternate,
            )
            try:
                await self._edit_as(
                    alternate,
                    ref,
                    text,
                    caption_text,
                    keyboard,
                )
            except Exception as alternate_exc:
                if self._is_not_modified_error(alternate_exc):
                    return alternate
                raise
            return alternate

    async def _edit_as(
        self,
        preview_kind: str,
        ref: ApprovalMessageRef,
        text: str,
        caption_text: str,
        keyboard: object,
    ) -> None:
        """Edit one Telegram message using the requested body type."""
        if preview_kind == "caption":
            await self._send_with_retry(
                self._bot.edit_message_caption,
                chat_id=ref.chat_id,
                message_id=ref.message_id,
                caption=caption_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return
        await self._send_with_retry(
            self._bot.edit_message_text,
            chat_id=ref.chat_id,
            message_id=ref.message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    @staticmethod
    def _preferred_preview_kind(
        ref: ApprovalMessageRef, post: Post, text: str
    ) -> str:
        """Return the stored or inferred Telegram preview body type."""
        if ref.preview_kind in {"text", "caption"}:
            return ref.preview_kind
        if _first_existing_media(post) is not None and len(text) <= _CAPTION_LIMIT:
            return "caption"
        return "text"

    @staticmethod
    def _is_not_modified_error(exc: Exception) -> bool:
        """Return whether Telegram reports that the desired state already exists."""
        return "message is not modified" in str(exc).lower()

    @staticmethod
    def _is_kind_mismatch_error(preview_kind: str, exc: Exception) -> bool:
        """Return whether Telegram rejected an edit because its body type is wrong."""
        message = str(exc).lower()
        if preview_kind == "text":
            return "there is no text in the message to edit" in message
        return any(
            marker in message
            for marker in (
                "there is no caption in the message to edit",
                "message has no caption",
                "message is not a media message",
            )
        )

    async def refresh_post(self, post: Post) -> int:
        """Backward-compatible refresh helper for older callers and tests."""
        channels = await self._channels.list_destinations() if self._channels else []
        result = await self.refresh_approval_request(
            post,
            channels,
            set(),
            set(),
            False,
        )
        return result.updated

    @staticmethod
    def _is_permanent_edit_error(exc: Exception) -> bool:
        """Return whether Telegram says the tracked message is permanently gone."""
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "message to edit not found",
                "message not found",
                "message_id_invalid",
                "message can't be edited",
                "chat not found",
                "bot was blocked",
                "user is deactivated",
            )
        )

    @staticmethod
    def _message_id(message: object) -> int:
        """Return the Telegram message id from an aiogram message object."""
        message_id = getattr(message, "message_id", None)
        if not isinstance(message_id, int):
            raise TelegramPublishError("Telegram send returned no message_id")
        return message_id

    def _media_sender(self, kind: MediaKind) -> object:
        """Return the Bot API send method for the media kind."""
        if kind == MediaKind.PHOTO:
            return self._bot.send_photo
        if kind == MediaKind.VIDEO:
            return self._bot.send_video
        return self._bot.send_document

    async def _send_media_with_fallback(
        self,
        sender: object,
        admin_id: int,
        input_file: FSInputFile,
        media_kind: MediaKind,
        post_id: str,
        **kwargs: object,
    ) -> object:
        """
        Send preview media and fall back to document for problematic videos.

        Args:
            sender: Primary aiogram media send method.
            admin_id: Admin chat id.
            input_file: Local media file.
            media_kind: Stored media kind.
            post_id: Post id for structured logs.
            **kwargs: Bot API send arguments such as caption and keyboard.

        Returns:
            The aiogram message returned by Telegram.

        Raises:
            Exception: Re-raises non-video failures and document fallback
            failures so the approval queue retries normally.
        """
        try:
            return await self._send_with_retry(sender, admin_id, input_file, **kwargs)
        except Exception as exc:
            if media_kind != MediaKind.VIDEO:
                raise
            logger.warning(
                "Approval video preview failed; retrying as document "
                "admin=%s post=%s error=%s",
                admin_id,
                post_id,
                exc,
            )
            return await self._send_with_retry(
                self._bot.send_document,
                admin_id,
                input_file,
                **kwargs,
            )

    async def _source_label(self, post: Post) -> str | None:
        """Return a readable source channel label for the preview."""
        if post.source_label.strip():
            return post.source_label.strip()
        if self._channels is None:
            return None
        return await self._channels.get_source_label(post.source_chat_id)

    async def _send_with_retry(self, sender: object, *args: object, **kwargs: object) -> object:
        """
        Execute one Bot API send and obey Telegram flood-wait responses.

        Args:
            sender: Bound aiogram send method.
            *args: Positional arguments for the send method.
            **kwargs: Keyword arguments for the send method.

        Returns:
            The aiogram message returned by the sender.

        Raises:
            Exception: Re-raises the final send error after one retry.
        """
        try:
            return await sender(*args, **kwargs)
        except TelegramRetryAfter as exc:
            delay = int(getattr(exc, "retry_after", 1)) + 1
            logger.warning("Telegram rate limit hit; retrying after %ss", delay)
            await asyncio.sleep(delay)
            return await sender(*args, **kwargs)
