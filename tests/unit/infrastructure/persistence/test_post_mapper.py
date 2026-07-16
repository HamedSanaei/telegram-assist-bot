# ruff: noqa: RUF001

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest
from bson.int64 import Int64

from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    StatusTransition,
    TelegramEntity,
    TransitionActorCategory,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.post_mapper import (
    POST_DOCUMENT_SCHEMA_VERSION,
    InvalidPostDocumentError,
    post_from_document,
    post_to_document,
    status_transition_to_document,
)


def _make_post() -> Post:
    received_at = datetime(2026, 3, 20, 8, 9, 10, 789123, tzinfo=UTC)
    post = Post(
        post_id=PostId("post-فارسی-42"),
        source_identity=SourceMessageIdentity(-1001234567890, 321),
        source_channel_username="Exact_ChannelName",
        source_channel_display_name="کانال نمونه ✅",
        original_content=OriginalPostContent(
            text="سلام\nخطِ دوم با نیم‌فاصله 👨‍👩‍👧‍👦 و ایموجی ویژه ✨",
            caption="کپشن اصلی\nبدون تغییر 🧿",
            text_entities=(
                TelegramEntity(0, 4, "bold"),
                TelegramEntity(33, 2, "custom_emoji", "5368324170671202286"),
            ),
            caption_entities=(TelegramEntity(0, 5, "italic"),),
        ),
        source_published_at=datetime(
            2026,
            3,
            20,
            8,
            0,
            0,
            123456,
            tzinfo=UTC,
        ),
        received_at=received_at,
    )
    stored = post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=received_at + timedelta(seconds=1, microseconds=555321),
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted_without_normalization",
        correlation_id="corr-۰۱",
    )
    return stored.transition_to(
        PostStatus.EXPIRED,
        expected_version=1,
        occurred_at=stored.expires_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="retention_elapsed",
    )


def _assert_post_fields_equal(actual: Post, expected: Post) -> None:
    assert actual.post_id == expected.post_id
    assert actual.source_identity == expected.source_identity
    assert actual.source_channel_username == expected.source_channel_username
    assert actual.source_channel_display_name == expected.source_channel_display_name
    assert actual.original_content == expected.original_content
    assert actual.source_published_at == expected.source_published_at
    assert actual.received_at == expected.received_at
    assert actual.expires_at == expected.expires_at
    assert actual.status is expected.status
    assert actual.version == expected.version
    assert actual.transition_history == expected.transition_history


def test_post_round_trip_preserves_every_current_domain_field_exactly() -> None:
    post = _make_post()

    restored = post_from_document(post_to_document(post))

    _assert_post_fields_equal(restored, post)
    assert restored.original_text == post.original_text
    assert restored.original_caption == post.original_caption
    assert restored.original_text_entities == post.original_text_entities
    assert restored.original_caption_entities == post.original_caption_entities


def test_document_uses_stable_version_one_schema_and_indexable_identity_fields() -> (
    None
):
    document = post_to_document(_make_post())

    assert document["schema_version"] == POST_DOCUMENT_SCHEMA_VERSION == 1
    assert document["_id"] == "post-فارسی-42"
    assert document["source_channel_id"] == -1001234567890
    assert document["source_message_id"] == 321
    assert document["status"] == "Expired"
    assert document["version"] == 2
    assert list(cast("dict[str, object]", document["original_content"])) == [
        "text",
        "caption",
        "text_entities",
        "caption_entities",
    ]


def test_pre_t011_version_one_document_defaults_missing_claim_marker() -> None:
    post = _make_post()
    document = post_to_document(post)
    del document["next_stage_claimed_at"]
    del document["next_stage_claim_correlation_id"]

    restored = post_from_document(document)

    _assert_post_fields_equal(restored, post)


def test_mapper_preserves_entity_order_text_line_breaks_emoji_and_zwnj() -> None:
    post = _make_post()
    document = post_to_document(post)
    content = cast("dict[str, object]", document["original_content"])

    assert content["text"] == "سلام\nخطِ دوم با نیم‌فاصله 👨‍👩‍👧‍👦 و ایموجی ویژه ✨"
    assert content["caption"] == "کپشن اصلی\nبدون تغییر 🧿"
    assert content["text_entities"] == [
        {
            "offset_utf16": 0,
            "length_utf16": 4,
            "entity_type": "bold",
            "custom_emoji_id": None,
            "url": None,
        },
        {
            "offset_utf16": 33,
            "length_utf16": 2,
            "entity_type": "custom_emoji",
            "custom_emoji_id": "5368324170671202286",
            "url": None,
        },
    ]


def test_text_url_round_trip_and_legacy_document_compatibility() -> None:
    post = _make_post()
    document = post_to_document(post)
    content = cast("dict[str, object]", document["original_content"])
    content["text_entities"] = [
        {
            "offset_utf16": 8,
            "length_utf16": 4,
            "entity_type": "text_url",
            "custom_emoji_id": None,
            "url": "https://example.invalid/path",
        }
    ]

    restored = post_from_document(document)

    assert restored.original_text_entities[0].url == "https://example.invalid/path"
    del cast("list[dict[str, object]]", content["text_entities"])[0]["url"]
    assert post_from_document(document).original_text_entities[0].url is None


def test_timestamp_encoding_preserves_microseconds_and_ceilings_expiry() -> None:
    post = _make_post()
    document = post_to_document(post)

    assert document["source_published_at"] == datetime(
        2026, 3, 20, 8, 0, 0, 123000, tzinfo=UTC
    )
    assert document["source_published_at_microsecond_remainder"] == 456
    assert document["received_at"] == datetime(
        2026, 3, 20, 8, 9, 10, 789000, tzinfo=UTC
    )
    assert document["received_at_microsecond_remainder"] == 123
    assert document["expires_at"] == datetime(2026, 4, 3, 8, 9, 10, 790000, tzinfo=UTC)
    assert document["expires_at_microsecond_remainder"] == 123
    assert post_from_document(document).expires_at == post.expires_at


def test_exact_millisecond_expiry_is_not_advanced() -> None:
    post = Post(
        post_id=PostId("exact-ms"),
        source_identity=SourceMessageIdentity(-1001, 1),
        source_channel_username=None,
        source_channel_display_name="Source",
        original_content=OriginalPostContent("سلام", None),
        source_published_at=datetime(2026, 1, 1, tzinfo=UTC),
        received_at=datetime(2026, 1, 1, 0, 0, 0, 123000, tzinfo=UTC),
    )

    document = post_to_document(post)

    assert document["expires_at"] == post.expires_at
    assert document["expires_at_microsecond_remainder"] == 0
    _assert_post_fields_equal(post_from_document(document), post)


def test_status_transition_helper_matches_history_schema_for_atomic_push() -> None:
    transition = _make_post().transition_history[0]

    document = status_transition_to_document(transition)

    assert document == {
        "previous_status": "Discovered",
        "new_status": "Stored",
        "occurred_at": datetime(2026, 3, 20, 8, 9, 12, 344000, tzinfo=UTC),
        "occurred_at_microsecond_remainder": 444,
        "actor_category": "service",
        "reason": "persisted_without_normalization",
        "correlation_id": "corr-۰۱",
    }


def test_aware_non_utc_document_datetimes_are_canonicalized() -> None:
    post = _make_post()
    document = post_to_document(post)
    plus_three_thirty = timezone(timedelta(hours=3, minutes=30))
    datetime_fields = ("source_published_at", "received_at", "expires_at")
    for field_name in datetime_fields:
        persisted = cast("datetime", document[field_name])
        document[field_name] = persisted.astimezone(plus_three_thirty)
    history = cast("list[dict[str, object]]", document["transition_history"])
    for transition in history:
        occurred_at = cast("datetime", transition["occurred_at"])
        transition["occurred_at"] = occurred_at.astimezone(plus_three_thirty)

    restored = post_from_document(document)

    _assert_post_fields_equal(restored, post)
    assert restored.received_at.tzinfo is UTC
    assert all(item.occurred_at.tzinfo is UTC for item in restored.transition_history)


def test_bson_int64_values_are_converted_to_builtin_domain_integers() -> None:
    post = _make_post()
    document = post_to_document(post)
    document["source_channel_id"] = Int64(post.source_identity.source_channel_id)
    document["source_message_id"] = Int64(post.source_identity.source_message_id)
    document["version"] = Int64(post.version)
    document["received_at_microsecond_remainder"] = Int64(123)

    restored = post_from_document(document)

    _assert_post_fields_equal(restored, post)
    assert type(restored.source_identity.source_channel_id) is int
    assert type(restored.version) is int


@pytest.mark.parametrize(
    "field_path",
    [
        ("source_published_at",),
        ("received_at",),
        ("expires_at",),
        ("transition_history", 0, "occurred_at"),
    ],
)
def test_naive_document_datetimes_are_rejected(
    field_path: tuple[str | int, ...],
) -> None:
    document = post_to_document(_make_post())
    target: object = document
    for part in field_path[:-1]:
        if isinstance(part, str):
            target = cast("dict[str, object]", target)[part]
        else:
            target = cast("list[object]", target)[part]
    final = field_path[-1]
    if isinstance(final, str):
        value = cast("dict[str, object]", target)[final]
        cast("dict[str, object]", target)[final] = cast("datetime", value).replace(
            tzinfo=None
        )

    with pytest.raises(InvalidPostDocumentError) as error:
        post_from_document(document)

    assert error.value.rule in {"invalid_timestamp", "invalid_document"}


@pytest.mark.parametrize("schema_version", [0, 2, True, "1", None])
def test_unknown_or_non_integer_schema_version_is_rejected(
    schema_version: object,
) -> None:
    document = post_to_document(_make_post())
    document["schema_version"] = schema_version

    with pytest.raises(InvalidPostDocumentError) as error:
        post_from_document(document)

    assert error.value.rule == "invalid_schema_version"


@pytest.mark.parametrize("removed_field", ["_id", "source_message_id", "status"])
def test_missing_required_root_field_is_rejected(removed_field: str) -> None:
    document = post_to_document(_make_post())
    del document[removed_field]

    with pytest.raises(InvalidPostDocumentError) as error:
        post_from_document(document)

    assert error.value.rule == "missing_field"


def test_unknown_root_field_is_rejected_under_version_one_schema() -> None:
    document = post_to_document(_make_post())
    document["future_field"] = "must increment schema version"

    with pytest.raises(InvalidPostDocumentError) as error:
        post_from_document(document)

    assert error.value.rule == "invalid_document"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("source_channel_id", True),
        ("source_message_id", "321"),
        ("version", 2.0),
        ("status", "Ready"),
    ],
)
def test_invalid_scalar_or_enum_field_is_rejected(
    field_name: str,
    invalid_value: object,
) -> None:
    document = post_to_document(_make_post())
    document[field_name] = invalid_value

    with pytest.raises(InvalidPostDocumentError):
        post_from_document(document)


@pytest.mark.parametrize("invalid_remainder", [-1, 1000, True, 1.5, "123"])
def test_invalid_precision_remainder_is_rejected(invalid_remainder: object) -> None:
    document = post_to_document(_make_post())
    document["received_at_microsecond_remainder"] = invalid_remainder

    with pytest.raises(InvalidPostDocumentError):
        post_from_document(document)


def test_non_millisecond_aligned_bson_datetime_is_rejected() -> None:
    document = post_to_document(_make_post())
    document["received_at"] = datetime(2026, 3, 20, 8, 9, 10, 789001, tzinfo=UTC)

    with pytest.raises(InvalidPostDocumentError) as error:
        post_from_document(document)

    assert error.value.rule == "invalid_timestamp"


def test_persisted_expiry_must_match_domain_computed_expiry() -> None:
    document = post_to_document(_make_post())
    document["expires_at"] = cast("datetime", document["expires_at"]) + timedelta(
        milliseconds=1
    )

    with pytest.raises(InvalidPostDocumentError) as error:
        post_from_document(document)

    assert error.value.rule == "invalid_expiration"


def test_invalid_nested_entity_and_history_documents_are_rejected() -> None:
    invalid_entity_document = post_to_document(_make_post())
    content = cast("dict[str, object]", invalid_entity_document["original_content"])
    entities = cast("list[dict[str, object]]", content["text_entities"])
    entities[0]["unexpected"] = True

    with pytest.raises(InvalidPostDocumentError) as entity_error:
        post_from_document(invalid_entity_document)
    assert entity_error.value.rule == "invalid_document"

    invalid_history_document = post_to_document(_make_post())
    history = cast(
        "list[dict[str, object]]", invalid_history_document["transition_history"]
    )
    history[0]["new_status"] = "Expired"

    with pytest.raises(InvalidPostDocumentError) as history_error:
        post_from_document(invalid_history_document)
    assert history_error.value.rule == "invalid_document"


def test_mapping_does_not_alias_mutable_document_data() -> None:
    post = _make_post()
    document = post_to_document(post)
    pristine = deepcopy(document)

    restored = post_from_document(document)
    content = cast("dict[str, object]", document["original_content"])
    content["text"] = "دادهٔ دست‌کاری‌شده"
    history = cast("list[dict[str, object]]", document["transition_history"])
    history[0]["reason"] = "changed"

    _assert_post_fields_equal(restored, post)
    assert post_to_document(restored) == pristine


def test_invalid_document_error_never_retains_raw_document_content() -> None:
    sensitive_value = "document-content-must-not-appear-9375"
    document = post_to_document(_make_post())
    document["status"] = sensitive_value

    with pytest.raises(InvalidPostDocumentError) as error:
        post_from_document(document)

    assert sensitive_value not in str(error.value)
    assert sensitive_value not in repr(error.value)
    assert not hasattr(error.value, "document")


def test_status_transition_helper_rejects_non_transition_values() -> None:
    with pytest.raises(InvalidPostDocumentError) as error:
        status_transition_to_document(
            cast("StatusTransition", {"new_status": "Stored"})
        )

    assert error.value.rule == "invalid_document"
