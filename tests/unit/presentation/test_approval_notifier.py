"""Unit tests for approval preview formatting."""

from __future__ import annotations

import pytest

from datetime import datetime, timezone
from pathlib import Path

from src.domain.entities import ApprovalMessageRef, DestinationChannel, MediaItem
from src.domain.entities import Post, PostQualityScore
from src.domain.entities import PostSourceMetrics
from src.domain.enums import MediaKind, PostCategory
from src.presentation.approval_bot.notifier import AiogramApprovalNotifier, build_preview_text
from src.shared.errors import TelegramPublishError


class FakeAdminRepository:
    """Test double that returns configured admin ids."""

    def __init__(self, user_ids: list[int]) -> None:
        """Args: user_ids: Admin user ids returned by the fake."""
        self._user_ids = user_ids

    async def list_user_ids(self) -> list[int]:
        """Return configured admin user ids."""
        return list(self._user_ids)


class FailingApprovalNotifier(AiogramApprovalNotifier):
    """Notifier test double whose Telegram sends always fail."""

    async def _send_preview(
        self, admin_id: int, post: Post, text: str, keyboard: object
    ) -> tuple[int, str]:
        """Raise as if Telegram refused the approval message."""
        raise RuntimeError("bot was blocked")


class SuccessfulApprovalNotifier(AiogramApprovalNotifier):
    """Notifier test double whose Telegram sends return fake message ids."""

    async def _send_preview(
        self, admin_id: int, post: Post, text: str, keyboard: object
    ) -> tuple[int, str]:
        """Return a deterministic fake message id."""
        return admin_id + 1000, "text"


class FakeMessage:
    """Minimal aiogram-like message."""

    def __init__(self, message_id: int) -> None:
        """Args: message_id: Telegram message id."""
        self.message_id = message_id


class VideoFailingBot:
    """Bot fake whose video send fails but document fallback succeeds."""

    def __init__(self) -> None:
        """Initialize send call counters."""
        self.video_calls = 0
        self.document_calls = 0

    async def send_video(self, *args: object, **kwargs: object) -> FakeMessage:
        """Fail like Telegram rejected the video preview."""
        self.video_calls += 1
        raise RuntimeError("video upload failed")

    async def send_document(self, *args: object, **kwargs: object) -> FakeMessage:
        """Return a successful document preview message."""
        self.document_calls += 1
        return FakeMessage(777)


class PreviewEditBot:
    """Bot fake recording approval preview text/caption edits."""

    def __init__(self) -> None:
        self.text_edits: list[dict[str, object]] = []
        self.caption_edits: list[dict[str, object]] = []

    async def edit_message_text(self, **kwargs: object) -> None:
        self.text_edits.append(kwargs)

    async def edit_message_caption(self, **kwargs: object) -> None:
        self.caption_edits.append(kwargs)


class FailingPreviewEditBot(PreviewEditBot):
    """Preview bot fake that raises one scripted edit error."""

    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error

    async def edit_message_text(self, **kwargs: object) -> None:
        """Raise the configured Telegram-like edit error."""
        raise RuntimeError(self.error)


class FakeApprovalMessageRepository:
    """Minimal active approval-message repository for edit tests."""

    def __init__(self, refs: list[ApprovalMessageRef]) -> None:
        self.refs = refs
        self.deactivated: list[int] = []

    async def list_active(self, post_id: str) -> list[ApprovalMessageRef]:
        return [ref for ref in self.refs if ref.post_id == post_id and ref.active]

    async def deactivate(self, message_ref_id: int) -> None:
        self.deactivated.append(message_ref_id)

    async def activate(self, message_ref_id: int) -> None:
        """Record reactivation of a repaired reference."""
        if message_ref_id in self.deactivated:
            self.deactivated.remove(message_ref_id)


class TestApprovalPreview:
    """Tests for admin-facing approval preview text."""

    def test_preview_uses_source_label_when_available(self) -> None:
        post = Post(
            post_id="p1",
            source_chat_id=-100123,
            source_message_id=10,
            text="متن خبر",
            content_hash="hash",
            category=PostCategory.GENERAL_NEWS,
        )

        text = build_preview_text(post, source_label="کانال منبع (@source)")

        assert "📡 منبع: کانال منبع (@source)" in text
        assert "-100123" not in text

    def test_preview_includes_quality_score(self) -> None:
        post = Post(
            post_id="p1",
            source_chat_id=-100123,
            source_message_id=10,
            text="متن خبر",
            content_hash="hash",
            quality_score=PostQualityScore(
                score=83,
                reason="ارزش بازنشر دارد",
                provider="groq",
            ),
        )

        text = build_preview_text(post)

        assert "⭐ امتیاز پیشنهادی: 83/100 — ارزش بازنشر دارد" in text

    def test_preview_header_is_italic_and_body_is_escaped(self) -> None:
        post = Post(
            post_id="p1",
            source_chat_id=-100123,
            source_message_id=10,
            text="خبر <مهم> & فوری",
            content_hash="hash",
            source_metrics=PostSourceMetrics(
                source_published_at=datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
            ),
        )

        text = build_preview_text(post, timezone_name="Asia/Tehran")

        assert text.startswith("<i>🆕")
        assert "🕒 انتشار مبدا: 2026-07-03 11:30" in text
        assert "خبر &lt;مهم&gt; &amp; فوری" in text

    async def test_notifier_raises_when_all_admin_sends_fail(self) -> None:
        post = Post(
            post_id="p1",
            source_chat_id=-100123,
            source_message_id=10,
            text="متن خبر",
            content_hash="hash",
            category=PostCategory.GENERAL_NEWS,
        )
        channel = DestinationChannel(chat_id=-100456, title="News")
        notifier = FailingApprovalNotifier(
            bot=object(),
            admins=FakeAdminRepository([123, 456]),
        )

        with pytest.raises(TelegramPublishError):
            await notifier.send_approval_request(post, [channel])

    async def test_notifier_returns_delivered_message_refs(self) -> None:
        post = Post(
            post_id="p1",
            source_chat_id=-100123,
            source_message_id=10,
            text="متن خبر",
            content_hash="hash",
            category=PostCategory.GENERAL_NEWS,
        )
        channel = DestinationChannel(chat_id=-100456, title="News")
        notifier = SuccessfulApprovalNotifier(
            bot=object(),
            admins=FakeAdminRepository([123, 456]),
        )

        refs = await notifier.send_approval_request(post, [channel])

        assert [(ref.admin_user_id, ref.message_id) for ref in refs] == [
            (123, 1123),
            (456, 1456),
        ]

    async def test_video_preview_falls_back_to_document(
        self, tmp_path: Path
    ) -> None:
        """Video preview failures are retried as documents for admins."""
        video_path = tmp_path / "preview.mp4"
        video_path.write_bytes(b"video")
        post = Post(
            post_id="p1",
            source_chat_id=-100123,
            source_message_id=10,
            text="متن خبر",
            content_hash="hash",
            media=[
                MediaItem(
                    kind=MediaKind.VIDEO,
                    file_path=str(video_path),
                    mime_type="video/mp4",
                )
            ],
            category=PostCategory.GENERAL_NEWS,
        )
        channel = DestinationChannel(chat_id=-100456, title="News")
        bot = VideoFailingBot()
        notifier = AiogramApprovalNotifier(
            bot=bot,
            admins=FakeAdminRepository([123]),
        )

        refs = await notifier.send_approval_request(post, [channel])

        assert refs[0].message_id == 777
        assert bot.video_calls == 1
        assert bot.document_calls == 1

    async def test_score_refresh_edits_existing_admin_preview(self) -> None:
        """Background scoring updates the tracked message instead of resending."""
        post = Post(
            post_id="p1",
            source_chat_id=-100,
            source_message_id=1,
            text="خبر",
            content_hash="hash",
            quality_score=PostQualityScore(
                score=88,
                reason="تعامل مناسب",
                provider="groq",
            ),
        )
        refs = [
            ApprovalMessageRef(
                id=1,
                post_id="p1",
                admin_user_id=42,
                chat_id=42,
                message_id=100,
                preview_kind="text",
            )
        ]
        bot = PreviewEditBot()
        notifier = AiogramApprovalNotifier(
            bot=bot,
            admins=FakeAdminRepository([42]),
            approval_messages=FakeApprovalMessageRepository(refs),
        )

        assert await notifier.refresh_post(post) == 1
        assert "88/100" in str(bot.text_edits[0]["text"])
        assert bot.text_edits[0]["reply_markup"] is not None
        assert bot.caption_edits == []

    async def test_score_refresh_preserves_caption_keyboard(self) -> None:
        """Caption refresh defines caption text and keeps callback buttons."""
        post = Post(
            post_id="p1",
            source_chat_id=-100,
            source_message_id=1,
            text="خبر تصویری",
            content_hash="hash",
            quality_score=PostQualityScore(score=90, reason="خوب", provider="groq"),
        )
        refs = [
            ApprovalMessageRef(
                id=1,
                post_id="p1",
                admin_user_id=42,
                chat_id=42,
                message_id=100,
                preview_kind="caption",
            )
        ]
        bot = PreviewEditBot()
        notifier = AiogramApprovalNotifier(
            bot=bot,
            admins=FakeAdminRepository([42]),
            approval_messages=FakeApprovalMessageRepository(refs),
        )

        assert await notifier.refresh_post(post) == 1
        assert "90/100" in str(bot.caption_edits[0]["caption"])
        assert bot.caption_edits[0]["reply_markup"] is not None
        assert len(str(bot.caption_edits[0]["caption"])) <= 1024

    async def test_transient_preview_error_keeps_reference_active(self) -> None:
        """A disconnect is retryable and must not discard the message ref."""
        post = Post("p1", -100, 1, "خبر", "hash")
        refs = [ApprovalMessageRef("p1", 42, 42, 100, id=1)]
        repository = FakeApprovalMessageRepository(refs)
        notifier = AiogramApprovalNotifier(
            bot=FailingPreviewEditBot("Server disconnected"),
            admins=FakeAdminRepository([42]),
            approval_messages=repository,
        )

        result = await notifier.refresh_approval_request(
            post, [], set(), set(), False
        )

        assert result.retryable_failures == 1
        assert repository.deactivated == []

    async def test_permanent_preview_error_deactivates_reference(self) -> None:
        """A deleted Telegram message is excluded from later refresh loops."""
        post = Post("p1", -100, 1, "خبر", "hash")
        refs = [ApprovalMessageRef("p1", 42, 42, 100, id=1)]
        repository = FakeApprovalMessageRepository(refs)
        notifier = AiogramApprovalNotifier(
            bot=FailingPreviewEditBot("Bad Request: message to edit not found"),
            admins=FakeAdminRepository([42]),
            approval_messages=repository,
        )

        result = await notifier.refresh_approval_request(
            post, [], set(), set(), False
        )

        assert result.permanent_failures == 1
        assert repository.deactivated == [1]
