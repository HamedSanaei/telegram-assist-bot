from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from telegram_assist_bot.application.approvals import AuthorizeAdminAction
from telegram_assist_bot.application.ports import (
    ApprovalContent,
    ApprovalDeliveryRateLimitError,
    ApprovalDeliveryRejectedError,
    ApprovalDeliveryTransientError,
    ApprovalDeliveryUnavailableError,
    ApprovalMedia,
    ApprovalMediaPathError,
    ApprovalMediaRejectedError,
    ApprovalMediaRejectionReason,
    ApprovalMediaUploadTimeoutError,
    BotEditOutcome,
    InlineKeyboard,
)
from telegram_assist_bot.domain import Administrator, AdminPermission
from telegram_assist_bot.domain.posts import TelegramEntity
from telegram_assist_bot.infrastructure.telegram.bot import AiogramAdminMessagingGateway
from telegram_assist_bot.presentation.bot import (
    ProtectedCallbackHandler,
    map_aiogram_update,
)

if TYPE_CHECKING:
    from pathlib import Path


class FakeSession:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class Result:
    message_id = 7


class FakeBot:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.answers: list[str] = []
        self.media_count = 0
        self.media_calls: list[tuple[str, object, dict[str, object]]] = []

    async def send_message(self, *_args: object, **_kwargs: object) -> Result:
        return Result()

    async def send_document(self, *_args: object, **_kwargs: object) -> Result:
        self.media_calls.append(("document", _args, _kwargs))
        return Result()

    async def send_photo(self, *_args: object, **_kwargs: object) -> Result:
        self.media_calls.append(("photo", _args, _kwargs))
        return Result()

    async def send_video(self, *_args: object, **_kwargs: object) -> Result:
        self.media_calls.append(("video", _args, _kwargs))
        return Result()

    async def send_animation(self, *_args: object, **_kwargs: object) -> Result:
        self.media_calls.append(("animation", _args, _kwargs))
        return Result()

    async def send_media_group(
        self, *_args: object, **_kwargs: object
    ) -> tuple[Result, Result]:
        self.media_count += 1
        return (Result(), Result())

    async def edit_message_text(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def answer_callback_query(self, query_id: str, **_kwargs: object) -> None:
        self.answers.append(query_id)


def update(actor: int = 1001, *, chat_type: str = "private") -> Update:
    chat_id = actor if chat_type == "private" else -100
    return Update(
        update_id=1,
        callback_query=CallbackQuery(
            id="query",
            from_user=User(id=actor, is_bot=False, first_name="مدیر"),
            chat_instance="synthetic",
            data="c1_synthetic",
            message=Message(
                message_id=3,
                date=datetime(2026, 7, 12, tzinfo=UTC),
                chat=Chat(id=chat_id, type=chat_type),
            ),
        ),
    )


def test_typed_update_mapping_uses_authenticated_actor() -> None:
    mapped = map_aiogram_update(update())
    assert mapped is not None
    assert mapped.actor_id == 1001
    assert mapped.chat_id == 1001
    assert mapped.callback_data == "c1_synthetic"


def test_handler_rejects_before_dispatch_and_adapter_closes_once(
    tmp_path: Path,
) -> None:
    (tmp_path / "first.bin").write_bytes(b"one")
    (tmp_path / "second.bin").write_bytes(b"two")

    async def scenario() -> None:
        fake = FakeBot()
        gateway = AiogramAdminMessagingGateway(
            cast("Any", fake), timeout_seconds=1, media_root=tmp_path
        )
        administrator = Administrator(
            1001, True, "admin", frozenset({AdminPermission.TOGGLE}), frozenset({-2001})
        )
        handler = ProtectedCallbackHandler(
            AuthorizeAdminAction((administrator,)), gateway
        )
        calls = 0

        async def dispatch(*_args: object) -> None:
            nonlocal calls
            calls += 1

        assert not await handler.handle(
            update(999), dispatch, permission=AdminPermission.TOGGLE
        )
        assert calls == 0
        assert fake.answers == ["query"]
        assert await gateway.send_header(1001, "هدر", InlineKeyboard(())) == 7
        assert await gateway.send_content(1001, ApprovalContent("متن", None)) == (7,)
        media = ApprovalContent(
            None,
            "کپشن‌فارسی 😀",
            caption_entities=(TelegramEntity(0, 6, "bold"),),
            media_paths=("first.bin", "second.bin"),
        )
        assert await gateway.send_content(1001, media) == (7,)
        assert fake.media_calls[-1][0] == "document"
        assert (
            await gateway.edit_header(1001, 7, "هدر", InlineKeyboard(()))
            is BotEditOutcome.UPDATED
        )
        await gateway.close()
        await gateway.close()
        assert fake.session.closed == 1

    asyncio.run(scenario())


def test_aiogram_gateway_uses_real_media_types_and_album_photo_preview(
    tmp_path: Path,
) -> None:
    for name in ("photo.jpg", "video.mp4", "animation.gif", "document.pdf"):
        (tmp_path / name).write_bytes(name.encode())

    async def scenario() -> None:
        fake = FakeBot()
        gateway = AiogramAdminMessagingGateway(
            cast("Any", fake),
            timeout_seconds=1,
            media_root=tmp_path,
            upload_timeout_seconds=2,
        )
        entities = (TelegramEntity(0, 4, "bold"),)
        media = (
            ApprovalMedia("photo", "photo.jpg", "image/jpeg", "عکس.jpg"),
            ApprovalMedia("video", "video.mp4", "video/mp4", "video.mp4"),
            ApprovalMedia("animation", "animation.gif", "image/gif", "anim.gif"),
            ApprovalMedia(
                "document", "document.pdf", "application/pdf", "document.pdf"
            ),
        )
        for expected, item in zip(
            ("photo", "video", "animation", "document"), media, strict=True
        ):
            await gateway.send_content(
                7,
                ApprovalContent(
                    None,
                    "کپشن",
                    caption_entities=entities,
                    media=(item,),
                ),
            )
            kind, _args, kwargs = fake.media_calls[-1]
            assert kind == expected
            assert kwargs["caption"] == "کپشن"
            mapped = cast("list[object]", kwargs["caption_entities"])
            assert cast("Any", mapped[0]).offset == 0
            assert cast("Any", mapped[0]).length == 4

        await gateway.send_content(
            7,
            ApprovalContent(None, "آلبوم", media=(media[3], media[0], media[1])),
        )
        assert fake.media_calls[-1][0] == "photo"

        with pytest.raises(ApprovalMediaPathError):
            await gateway.send_content(
                7,
                ApprovalContent(
                    None,
                    "x",
                    media=(ApprovalMedia("photo", "../outside.jpg"),),
                ),
            )

    asyncio.run(scenario())


def test_document_npvt_preserves_filename_caption_and_supported_entities(
    tmp_path: Path,
) -> None:
    (tmp_path / "stored-hash").write_bytes(b"synthetic-npvt")

    async def scenario() -> None:
        fake = FakeBot()
        gateway = AiogramAdminMessagingGateway(
            cast("Any", fake), timeout_seconds=1, media_root=tmp_path
        )
        await gateway.send_content(
            7,
            ApprovalContent(
                None,
                None,
                media=(
                    ApprovalMedia(
                        "document",
                        "stored-hash",
                        "application/octet-stream",
                        "config.npvt",
                    ),
                ),
            ),
        )
        kind, _args, kwargs = fake.media_calls[-1]
        assert kind == "document"
        assert cast("Any", kwargs["document"]).filename == "config.npvt"
        assert kwargs["caption"] is None
        assert kwargs["caption_entities"] == []

        caption = "سلام ایران"
        await gateway.send_content(
            7,
            ApprovalContent(
                None,
                caption,
                caption_entities=(
                    TelegramEntity(0, 4, "bold"),
                    TelegramEntity(5, 5, "text_url"),
                    TelegramEntity(5, 2, "custom_emoji", "123"),
                ),
                media=(
                    ApprovalMedia(
                        "document",
                        "stored-hash",
                        "application/octet-stream",
                        "کانفیگ ویژه.npvt",
                    ),
                ),
            ),
        )
        _kind, _args, kwargs = fake.media_calls[-1]
        assert cast("Any", kwargs["document"]).filename == "کانفیگ ویژه.npvt"
        assert kwargs["caption"] == caption
        mapped = cast("list[Any]", kwargs["caption_entities"])
        assert [(item.type, item.offset, item.length) for item in mapped] == [
            ("bold", 0, 4)
        ]

    asyncio.run(scenario())


def test_document_entity_bad_request_retries_once_without_entities(
    tmp_path: Path,
) -> None:
    (tmp_path / "document.npvt").write_bytes(b"synthetic-npvt")

    class EntityRejectingBot(FakeBot):
        async def send_document(self, *_args: object, **kwargs: object) -> Result:
            self.media_calls.append(("document", _args, kwargs))
            if len(self.media_calls) == 1:
                raise TelegramBadRequest(
                    cast("Any", object()), "can't parse entities: invalid entity"
                )
            return Result()

    async def scenario() -> None:
        fake = EntityRejectingBot()
        gateway = AiogramAdminMessagingGateway(
            cast("Any", fake), timeout_seconds=1, media_root=tmp_path
        )
        assert await gateway.send_content(
            7,
            ApprovalContent(
                None,
                "سلام",
                caption_entities=(TelegramEntity(0, 4, "bold"),),
                media=(ApprovalMedia("document", "document.npvt"),),
            ),
        ) == (7,)
        assert len(fake.media_calls) == 2
        assert cast("list[Any]", fake.media_calls[0][2]["caption_entities"])
        assert fake.media_calls[1][2]["caption_entities"] == []

    asyncio.run(scenario())


def test_document_ambiguous_bad_request_is_not_retried(tmp_path: Path) -> None:
    (tmp_path / "document.npvt").write_bytes(b"synthetic-npvt")

    class AmbiguousRejectingBot(FakeBot):
        async def send_document(self, *_args: object, **kwargs: object) -> Result:
            self.media_calls.append(("document", _args, kwargs))
            raise TelegramBadRequest(cast("Any", object()), "bad document request")

    async def scenario() -> None:
        fake = AmbiguousRejectingBot()
        gateway = AiogramAdminMessagingGateway(
            cast("Any", fake), timeout_seconds=1, media_root=tmp_path
        )
        with pytest.raises(ApprovalMediaRejectedError) as rejected:
            await gateway.send_content(
                7,
                ApprovalContent(
                    None,
                    "سلام",
                    caption_entities=(TelegramEntity(0, 4, "bold"),),
                    media=(ApprovalMedia("document", "document.npvt"),),
                ),
            )
        assert rejected.value.reason is (
            ApprovalMediaRejectionReason.GENERIC_BAD_REQUEST
        )
        assert len(fake.media_calls) == 1

    asyncio.run(scenario())


def test_document_invalid_entity_and_files_have_safe_explicit_reasons(
    tmp_path: Path,
) -> None:
    (tmp_path / "valid.npvt").write_bytes(b"synthetic")
    (tmp_path / "empty.npvt").write_bytes(b"")

    async def scenario() -> None:
        gateway = AiogramAdminMessagingGateway(
            cast("Any", FakeBot()), timeout_seconds=1, media_root=tmp_path
        )
        with pytest.raises(ApprovalMediaRejectedError) as invalid_entity:
            await gateway.send_content(
                7,
                ApprovalContent(
                    None,
                    "سلام",
                    caption_entities=(TelegramEntity(3, 4, "bold"),),
                    media=(ApprovalMedia("document", "valid.npvt"),),
                ),
            )
        assert invalid_entity.value.reason is (
            ApprovalMediaRejectionReason.ENTITY_BOUNDS_INVALID
        )

        with pytest.raises(ApprovalMediaPathError) as empty:
            await gateway.send_content(
                7,
                ApprovalContent(
                    None,
                    "",
                    media=(ApprovalMedia("document", "empty.npvt"),),
                ),
            )
        assert empty.value.reason is ApprovalMediaRejectionReason.FILE_EMPTY

        with pytest.raises(ApprovalMediaPathError) as missing:
            await gateway.send_content(
                7,
                ApprovalContent(
                    None,
                    "",
                    media=(ApprovalMedia("document", "missing.npvt"),),
                ),
            )
        assert missing.value.reason is ApprovalMediaRejectionReason.FILE_MISSING

    asyncio.run(scenario())


def test_aiogram_gateway_recovers_legacy_image_preview_without_hash_filename(
    tmp_path: Path,
) -> None:
    jpeg_hash = "f59ac19d7a626ce9f36fa6cbb32930ed753c8b2cb367c8a"
    png_hash = "a" * 64
    gif_hash = "b" * 64
    document_hash = "c" * 64
    (tmp_path / jpeg_hash).write_bytes(b"\xff\xd8\xff\xe0legacy-jpeg")
    (tmp_path / png_hash).write_bytes(b"\x89PNG\r\n\x1a\nlegacy-png")
    (tmp_path / gif_hash).write_bytes(b"GIF89alegacy-gif")
    (tmp_path / document_hash).write_bytes(b"%PDF-1.7 document")

    async def scenario() -> None:
        fake = FakeBot()
        gateway = AiogramAdminMessagingGateway(
            cast("Any", fake), timeout_seconds=1, media_root=tmp_path
        )
        entities = (TelegramEntity(0, 4, "bold"),)

        await gateway.send_content(
            7,
            ApprovalContent(
                None,
                "کپشن",
                caption_entities=entities,
                media=(ApprovalMedia("Document", jpeg_hash, "image/jpeg"),),
            ),
        )
        kind, _args, kwargs = fake.media_calls[-1]
        assert kind == "photo"
        upload = cast("Any", kwargs["photo"])
        assert upload.filename == "approval-photo.jpg"
        assert jpeg_hash not in upload.filename
        assert kwargs["caption"] == "کپشن"
        assert cast("Any", kwargs["caption_entities"])[0].length == 4

        await gateway.send_content(
            7,
            ApprovalContent(
                None,
                "png",
                media=(ApprovalMedia("document", png_hash),),
            ),
        )
        assert fake.media_calls[-1][0] == "photo"
        assert cast("Any", fake.media_calls[-1][2]["photo"]).filename == (
            "approval-photo.png"
        )

        await gateway.send_content(
            7,
            ApprovalContent(
                None,
                "gif",
                media=(ApprovalMedia("document", gif_hash),),
            ),
        )
        assert fake.media_calls[-1][0] == "animation"
        assert cast("Any", fake.media_calls[-1][2]["animation"]).filename == (
            "approval-animation.gif"
        )

        await gateway.send_content(
            7,
            ApprovalContent(
                None,
                "pdf",
                media=(ApprovalMedia("document", document_hash),),
            ),
        )
        assert fake.media_calls[-1][0] == "document"
        assert cast("Any", fake.media_calls[-1][2]["document"]).filename == (
            "approval-document.pdf"
        )

    asyncio.run(scenario())


def test_video_control_card_falls_back_when_reply_association_is_rejected(
    tmp_path: Path,
) -> None:
    (tmp_path / "video.mp4").write_bytes(b"synthetic-video")

    class ReplyRejectingBot(FakeBot):
        def __init__(self) -> None:
            super().__init__()
            self.header_calls: list[dict[str, object]] = []

        async def send_message(self, *_args: object, **kwargs: object) -> Result:
            self.header_calls.append(dict(kwargs))
            if kwargs.get("reply_to_message_id") is not None:
                raise TelegramBadRequest(cast("Any", object()), "reply rejected")
            return Result()

    async def scenario() -> None:
        fake = ReplyRejectingBot()
        gateway = AiogramAdminMessagingGateway(
            cast("Any", fake), timeout_seconds=1, media_root=tmp_path
        )
        content_ids = await gateway.send_content(
            7,
            ApprovalContent(
                None,
                "کپشن ویدئو",
                media=(
                    ApprovalMedia(
                        "video", "video.mp4", "video/mp4", "source-video.mp4"
                    ),
                ),
            ),
        )
        keyboard = InlineKeyboard(())
        control_id = await gateway.send_header(
            7,
            "کارت کنترل",
            keyboard,
            reply_to_message_id=content_ids[0],
        )
        assert control_id == 7
        assert len(fake.header_calls) == 2
        assert fake.header_calls[0]["reply_to_message_id"] == 7
        assert "reply_to_message_id" not in fake.header_calls[1]
        assert fake.header_calls[1]["reply_markup"] is not None

    asyncio.run(scenario())


def test_aiogram_gateway_has_a_separate_media_upload_timeout(tmp_path: Path) -> None:
    (tmp_path / "slow.jpg").write_bytes(b"slow")

    class SlowBot(FakeBot):
        async def send_photo(self, *_args: object, **_kwargs: object) -> Result:
            await asyncio.sleep(0.05)
            return Result()

    async def scenario() -> None:
        gateway = AiogramAdminMessagingGateway(
            cast("Any", SlowBot()),
            timeout_seconds=1,
            media_root=tmp_path,
            upload_timeout_seconds=0.001,
        )
        with pytest.raises(ApprovalMediaUploadTimeoutError):
            await gateway.send_content(
                7,
                ApprovalContent(
                    None,
                    "slow",
                    media=(ApprovalMedia("photo", "slow.jpg"),),
                ),
            )

    asyncio.run(scenario())


def test_aiogram_gateway_maps_delivery_media_and_edit_boundaries(
    tmp_path: Path,
) -> None:
    (tmp_path / "single.bin").write_bytes(b"single")

    class FailingBot(FakeBot):
        def __init__(self) -> None:
            super().__init__()
            self.send_error: Exception | None = None
            self.edit_error: Exception | None = None

        async def send_message(self, *_args: object, **_kwargs: object) -> Result:
            if self.send_error is not None:
                raise self.send_error
            return Result()

        async def edit_message_text(self, *_args: object, **_kwargs: object) -> None:
            if self.edit_error is not None:
                raise self.edit_error

    async def scenario() -> None:
        fake = FailingBot()
        gateway = AiogramAdminMessagingGateway(
            cast("Any", fake), timeout_seconds=1, media_root=tmp_path
        )
        method = cast("Any", object())

        assert gateway.bot is cast("Any", fake)
        assert isinstance(
            gateway._delivery_error(TelegramForbiddenError(method, "forbidden")),
            ApprovalDeliveryUnavailableError,
        )
        rate_limit = gateway._delivery_error(
            TelegramRetryAfter(method, "retry", retry_after=12)
        )
        assert isinstance(rate_limit, ApprovalDeliveryRateLimitError)
        assert rate_limit.retry_after_seconds == 12
        assert isinstance(
            gateway._delivery_error(TelegramNetworkError(method, "network")),
            ApprovalDeliveryTransientError,
        )
        assert isinstance(
            gateway._delivery_error(TelegramServerError(method, "server")),
            ApprovalDeliveryTransientError,
        )
        assert isinstance(
            gateway._delivery_error(TelegramBadRequest(method, "bad")),
            ApprovalDeliveryRejectedError,
        )

        single_media = ApprovalContent(
            None,
            "کپشن",
            caption_entities=(TelegramEntity(0, 4, "bold"),),
            media_paths=("single.bin",),
        )
        assert await gateway.send_content(1, single_media) == (7,)
        text = ApprovalContent(
            "متن",
            None,
            text_entities=(TelegramEntity(0, 3, "italic"),),
        )
        assert await gateway.send_content(1, text) == (7,)

        fake.send_error = TelegramForbiddenError(method, "forbidden")
        with pytest.raises(ApprovalDeliveryUnavailableError):
            await gateway.send_header(1, "header")
        fake.send_error = TelegramBadRequest(method, "bad")
        with pytest.raises(ApprovalDeliveryRejectedError):
            await gateway.send_content(1, text)
        fake.send_error = TelegramServerError(method, "server")
        with pytest.raises(ApprovalDeliveryTransientError):
            await gateway.send_header(1, "header")
        fake.send_error = None

        fake.edit_error = TelegramBadRequest(method, "message is not modified")
        assert (
            await gateway.edit_header(1, 7, "header", InlineKeyboard(()))
            is BotEditOutcome.NOT_MODIFIED
        )
        fake.edit_error = TelegramBadRequest(method, "message to edit not found")
        assert (
            await gateway.edit_header(1, 7, "header", InlineKeyboard(()))
            is BotEditOutcome.DELETED
        )
        fake.edit_error = TelegramBadRequest(method, "other")
        with pytest.raises(RuntimeError, match="rejected"):
            await gateway.edit_header(1, 7, "header", InlineKeyboard(()))
        fake.edit_error = TelegramNetworkError(method, "network")
        with pytest.raises(TimeoutError, match="Temporary"):
            await gateway.edit_header(1, 7, "header", InlineKeyboard(()))
        fake.edit_error = TelegramServerError(method, "server")
        with pytest.raises(TimeoutError, match="Temporary"):
            await gateway.edit_header(1, 7, "header", InlineKeyboard(()))

    asyncio.run(scenario())
