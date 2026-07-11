"""Verify post aggregate identity and immutable original-content contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from telegram_assist_bot.domain.posts import (
    InvalidPostIdentifierError,
    InvalidPostVersionError,
    InvalidSourceMessageIdentityError,
    OriginalContentMutationError,
    OriginalPostContent,
    Post,
    PostId,
    PostInvariantError,
    SourceMessageIdentity,
    TelegramEntity,
)


class _ExternalString(str):
    """Simulate a string subclass carrying mutable provider-owned state."""

    payload: list[str]

    def __new__(cls, value: str) -> _ExternalString:
        instance = super().__new__(cls, value)
        instance.payload = ["provider-owned"]
        return instance


def _make_post(
    *,
    post_id: PostId,
    source_identity: SourceMessageIdentity,
    original_content: OriginalPostContent | None = None,
) -> Post:
    return Post(
        post_id=post_id,
        source_identity=source_identity,
        source_channel_username="source_channel",
        source_channel_display_name="کانال منبع",
        original_content=original_content
        or OriginalPostContent(text="متن اصلی", caption=None),
        source_published_at=datetime(2026, 7, 1, 7, 30, tzinfo=UTC),
        received_at=datetime(2026, 7, 1, 7, 31, tzinfo=UTC),
    )


def test_source_identity_equality_hash_and_key_use_both_components() -> None:
    identity = SourceMessageIdentity(
        source_channel_id=-1001234567890,
        source_message_id=7,
    )
    same = SourceMessageIdentity(source_channel_id=-1001234567890, source_message_id=7)
    different_channel = SourceMessageIdentity(
        source_channel_id=-1001234567891,
        source_message_id=7,
    )
    different_message = SourceMessageIdentity(
        source_channel_id=-1001234567890,
        source_message_id=8,
    )

    assert identity == same
    assert hash(identity) == hash(same)
    assert identity.as_tuple == (-1001234567890, 7)
    assert {identity, same, different_channel, different_message} == {
        identity,
        different_channel,
        different_message,
    }


@pytest.mark.parametrize(
    ("source_channel_id", "source_message_id"),
    [
        (0, 1),
        (True, 1),
        (False, 1),
        (-100.0, 1),
        ("-100", 1),
        (None, 1),
        (-100, 0),
        (-100, -1),
        (-100, True),
        (-100, 1.0),
        (-100, "1"),
        (-100, None),
    ],
)
def test_source_identity_rejects_invalid_identifier_shapes(
    source_channel_id: object,
    source_message_id: object,
) -> None:
    with pytest.raises(InvalidSourceMessageIdentityError):
        SourceMessageIdentity(
            source_channel_id=cast("int", source_channel_id),
            source_message_id=cast("int", source_message_id),
        )


def test_source_identity_accepts_nonzero_signed_channel_ids() -> None:
    assert SourceMessageIdentity(100, 1).source_channel_id == 100
    assert SourceMessageIdentity(-100, 1).source_channel_id == -100


def test_post_id_is_an_opaque_stable_value_object() -> None:
    identifier = PostId("post:01JZZ8V1KTRN8T0A73JY4WT3BA")
    same = PostId("post:01JZZ8V1KTRN8T0A73JY4WT3BA")

    assert identifier == same
    assert hash(identifier) == hash(same)
    assert str(identifier) == "post:01JZZ8V1KTRN8T0A73JY4WT3BA"


@pytest.mark.parametrize(
    "value",
    ["", " ", "\t\n", "p" * 129, 1, True, None],
)
def test_post_id_rejects_blank_oversized_and_non_string_values(value: object) -> None:
    with pytest.raises(InvalidPostIdentifierError):
        PostId(cast("str", value))


def test_post_aggregate_identity_is_separate_from_ingestion_idempotency() -> None:
    shared_post_id = PostId("post-1")
    first_source = SourceMessageIdentity(-1001, 10)
    second_source = SourceMessageIdentity(-1002, 20)

    first = _make_post(post_id=shared_post_id, source_identity=first_source)
    same_aggregate_other_snapshot = _make_post(
        post_id=shared_post_id,
        source_identity=second_source,
    )
    same_source_different_aggregate = _make_post(
        post_id=PostId("post-2"),
        source_identity=first_source,
    )

    assert first == same_aggregate_other_snapshot
    assert hash(first) == hash(same_aggregate_other_snapshot)
    assert (
        first.idempotency_identity != same_aggregate_other_snapshot.idempotency_identity
    )

    assert first != same_source_different_aggregate
    assert (
        first.idempotency_identity
        == same_source_different_aggregate.idempotency_identity
    )
    assert first.idempotency_identity.as_tuple == (-1001, 10)


def test_original_content_defensively_freezes_entity_sequences() -> None:
    text_entity = TelegramEntity(offset_utf16=0, length_utf16=4, entity_type="bold")
    caption_entity = TelegramEntity(
        offset_utf16=5,
        length_utf16=2,
        entity_type="custom_emoji",
        custom_emoji_id="5368324170671202286",
    )
    mutable_text_entities = [text_entity]
    mutable_caption_entities = [caption_entity]

    content = OriginalPostContent(
        text="متن اصلی",
        caption="شرح 😀",
        text_entities=cast("tuple[TelegramEntity, ...]", mutable_text_entities),
        caption_entities=cast("tuple[TelegramEntity, ...]", mutable_caption_entities),
    )
    mutable_text_entities.clear()
    mutable_caption_entities.append(text_entity)

    assert content.text_entities == (text_entity,)
    assert content.caption_entities == (caption_entity,)
    assert isinstance(content.text_entities, tuple)
    assert isinstance(content.caption_entities, tuple)


def test_original_content_rejects_unordered_or_non_entity_collections() -> None:
    entity = TelegramEntity(offset_utf16=0, length_utf16=1, entity_type="bold")

    with pytest.raises(PostInvariantError):
        OriginalPostContent(
            text="text",
            caption=None,
            text_entities=cast("tuple[TelegramEntity, ...]", {entity}),
        )
    with pytest.raises(PostInvariantError):
        OriginalPostContent(
            text="text",
            caption=None,
            text_entities=cast("tuple[TelegramEntity, ...]", ("bold",)),
        )


@pytest.mark.parametrize(
    ("text", "caption"),
    [
        (1, None),
        (None, object()),
        (_ExternalString("text"), None),
        (None, _ExternalString("caption")),
    ],
)
def test_original_content_rejects_non_builtin_strings(
    text: object,
    caption: object,
) -> None:
    with pytest.raises(PostInvariantError):
        OriginalPostContent(
            text=cast("str | None", text),
            caption=cast("str | None", caption),
        )


def test_original_content_rejects_entities_without_corresponding_content() -> None:
    entity = TelegramEntity(offset_utf16=0, length_utf16=1, entity_type="bold")

    with pytest.raises(PostInvariantError):
        OriginalPostContent(text=None, caption=None, text_entities=(entity,))
    with pytest.raises(PostInvariantError):
        OriginalPostContent(text=None, caption=None, caption_entities=(entity,))


def test_original_content_and_nested_entities_are_immutable() -> None:
    entity = TelegramEntity(offset_utf16=0, length_utf16=2, entity_type="italic")
    content = OriginalPostContent(
        text="سلام",
        caption=None,
        text_entities=(entity,),
    )

    content_attribute = "text"
    entity_attribute = "offset_utf16"
    with pytest.raises(FrozenInstanceError):
        setattr(content, content_attribute, "تغییر یافته")
    with pytest.raises(FrozenInstanceError):
        setattr(entity, entity_attribute, 1)


def test_post_exposes_exact_original_content_without_normalization() -> None:
    text = "سلام\n😀 پیام\u200cویژه"  # noqa: RUF001
    caption = "شرح فارسی\nخط دوم 🟢"
    custom_emoji = TelegramEntity(
        offset_utf16=5,
        length_utf16=2,
        entity_type="custom_emoji",
        custom_emoji_id="5368324170671202286",
    )
    content = OriginalPostContent(
        text=text,
        caption=caption,
        text_entities=(custom_emoji,),
        caption_entities=(TelegramEntity(0, 4, "bold"),),
    )
    post = _make_post(
        post_id=PostId("post-persian"),
        source_identity=SourceMessageIdentity(-1001, 44),
        original_content=content,
    )

    assert post.original_text == text
    assert post.original_caption == caption
    assert post.original_text_entities == (custom_emoji,)
    assert post.original_caption_entities == (TelegramEntity(0, 4, "bold"),)
    assert post.original_text is not None
    assert post.original_text.encode("utf-8") == text.encode("utf-8")
    assert "\u200c" in post.original_text


def test_post_accepts_identical_content_and_rejects_a_changed_source_version() -> None:
    original = OriginalPostContent(text="اصل پیام 😀", caption=None)
    post = _make_post(
        post_id=PostId("post-content-version"),
        source_identity=SourceMessageIdentity(-1001, 99),
        original_content=original,
    )

    post.assert_original_content_matches(original)
    post.assert_original_content_matches(
        OriginalPostContent(text="اصل پیام 😀", caption=None)
    )
    with pytest.raises(OriginalContentMutationError):
        post.assert_original_content_matches(
            OriginalPostContent(text="نسخه تغییر یافته", caption=None)
        )


@pytest.mark.parametrize(
    ("field_name", "value", "expected_error"),
    [
        ("post_id", "post-id", InvalidPostIdentifierError),
        ("source_identity", (-1001, 1), InvalidSourceMessageIdentityError),
        ("original_content", "text", PostInvariantError),
        ("source_channel_username", "", PostInvariantError),
        ("source_channel_username", "x" * 129, PostInvariantError),
        ("source_channel_display_name", " ", PostInvariantError),
        ("source_channel_display_name", "x" * 257, PostInvariantError),
        ("status", "Discovered", PostInvariantError),
        ("version", True, InvalidPostVersionError),
        ("version", -1, InvalidPostVersionError),
    ],
)
def test_post_rejects_invalid_aggregate_field_shapes(
    field_name: str,
    value: object,
    expected_error: type[Exception],
) -> None:
    post = _make_post(
        post_id=PostId("post-fields"),
        source_identity=SourceMessageIdentity(-1001, 1),
    )
    changes: dict[str, Any] = {field_name: value}

    with pytest.raises(expected_error):
        replace(post, **changes)
