# ruff: noqa: RUF001

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest
from bson.int64 import Int64

from telegram_assist_bot.domain.advertisement import (
    AdvertisementCheckFailure,
    AdvertisementCheckResult,
    AdvertisementFailurePolicy,
    AdvertisementProcessingState,
)
from telegram_assist_bot.domain.categories import (
    CategorizationCheckFailure,
    CategorizationMethod,
    CategorizationResult,
)
from telegram_assist_bot.domain.duplicates import (
    SemanticDuplicateFailure,
    SemanticDuplicateFailurePolicy,
    SemanticDuplicatePolicy,
    SemanticDuplicateResult,
)
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    StatusTransition,
    TelegramEntity,
    TelegramUrlButton,
    TransitionActorCategory,
)
from telegram_assist_bot.domain.scoring import (
    ScoringFailure,
    ScoringFailurePolicy,
    ScoringResult,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    post_mapper as post_mapper_module,
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
            inline_keyboard=(
                (
                    TelegramUrlButton(
                        "اتصال 🚀",
                        "https://t.me/proxy?server=example.invalid&port=443&secret=safe",
                    ),
                ),
            ),
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
    assert actual.advertisement_state is expected.advertisement_state
    assert (
        actual.advertisement_processing_version
        == expected.advertisement_processing_version
    )
    assert actual.advertisement_result == expected.advertisement_result
    assert actual.advertisement_failure == expected.advertisement_failure


def test_post_round_trip_preserves_every_current_domain_field_exactly() -> None:
    post = _make_post()

    restored = post_from_document(post_to_document(post))

    _assert_post_fields_equal(restored, post)
    assert restored.original_text == post.original_text
    assert restored.original_caption == post.original_caption
    assert restored.original_text_entities == post.original_text_entities
    assert restored.original_caption_entities == post.original_caption_entities


def test_legacy_post_without_advertisement_processing_uses_safe_defaults() -> None:
    """Pre-T042 documents remain readable without inventing a classification."""
    document = post_to_document(_make_post())
    del document["advertisement_processing"]

    restored = post_from_document(document)

    assert restored.advertisement_state is AdvertisementProcessingState.NOT_REQUESTED
    assert restored.advertisement_processing_version == 0
    assert restored.advertisement_result is None
    assert restored.advertisement_failure is None


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
        "inline_keyboard",
    ]


def test_inline_url_keyboard_round_trip_and_legacy_default() -> None:
    document = post_to_document(_make_post())

    restored = post_from_document(document)

    assert restored.original_content.inline_keyboard[0][0].label == "اتصال 🚀"
    assert restored.original_content.inline_keyboard[0][0].url.startswith(
        "https://t.me/proxy?"
    )
    content = cast("dict[str, object]", document["original_content"])
    del content["inline_keyboard"]
    assert post_from_document(document).original_content.inline_keyboard == ()


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


def test_rich_processing_results_round_trip_without_losing_audit_metadata() -> None:
    """Exercise every additive processing result through the strict BSON mapper."""
    now = datetime(2026, 3, 20, 8, 10, 0, 654321, tzinfo=UTC)
    post = Post(
        post_id=PostId("rich-processing-post"),
        source_identity=SourceMessageIdentity(-1001, 808),
        source_channel_username="source",
        source_channel_display_name="Source",
        original_content=OriginalPostContent("rich processing", None),
        source_published_at=now - timedelta(minutes=1),
        received_at=now,
    ).transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=now,
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )
    post = post.start_advertisement_check(
        job_id="ad-job",
        expected_processing_version=0,
        requested_at=now,
    ).apply_advertisement_result(
        AdvertisementCheckResult(
            is_advertisement=False,
            confidence=0.75,
            reason="not an advertisement",
            provider_name="provider",
            model_name="model",
            checked_at=now,
            prompt_version="1",
            schema_version="1",
            attempt_number=2,
            fallback_count=1,
            cache_hit=True,
            cache_age_seconds=4.5,
        ),
        job_id="ad-job",
        expected_processing_version=1,
    )
    post = post.start_semantic_duplicate_check(
        job_id="semantic-job",
        expected_processing_version=0,
        requested_at=now,
    ).apply_semantic_duplicate_result(
        SemanticDuplicateResult(
            is_duplicate=False,
            similarity=0.25,
            confidence=0.9,
            matched_post_id=None,
            reason="different",
            provider_name="provider",
            model_name="model",
            checked_at=now,
            prompt_version="1",
            schema_version="1",
            attempt_number=1,
            fallback_count=0,
            cache_hit=True,
            cache_age_seconds=2.5,
        ),
        policy=SemanticDuplicatePolicy.REJECT,
        job_id="semantic-job",
        expected_processing_version=1,
    )
    post = post.enqueue_categorization("category-job").apply_categorization_result(
        CategorizationResult(
            category_id="news",
            method=CategorizationMethod.AI,
            policy_version=2,
            assigned_at=now,
            reason="news",
            confidence=0.8,
            provider_name="provider",
            model_name="model",
            prompt_version="2.0.0",
            schema_version="2",
            cache_hit=True,
            cache_age=1.5,
            attempt_number=2,
            fallback_count=1,
        ),
        "category-job",
        expected_processing_version=1,
    )
    post = post.schedule_scoring(
        job_id="score-job",
        due_at=now + timedelta(minutes=1),
        expected_processing_version=0,
    ).apply_scoring_result(
        ScoringResult(
            score=81,
            confidence=0.88,
            reason="useful",
            provider_name="provider",
            model_name="model",
            scored_at=now,
            prompt_version="1",
            schema_version="1",
            attractiveness_probability=0.7,
            engagement_probability=0.6,
            headline_quality=80,
            freshness=90,
            news_value=75,
            writing_quality=79,
            cache_hit=True,
            cache_age_seconds=3.0,
            attempt_number=2,
            fallback_count=1,
        ),
        job_id="score-job",
        expected_processing_version=1,
    )

    restored = post_from_document(post_to_document(post))

    assert restored.advertisement_result == post.advertisement_result
    assert restored.semantic_duplicate_result == post.semantic_duplicate_result
    assert restored.categorization_result == post.categorization_result
    assert restored.scoring_result == post.scoring_result


def test_processing_failures_round_trip_without_fabricating_results() -> None:
    """Every persisted failure policy remains distinct from a provider result."""
    now = datetime(2026, 3, 20, 8, 10, tzinfo=UTC)
    base = Post(
        post_id=PostId("failure-processing-post"),
        source_identity=SourceMessageIdentity(-1001, 809),
        source_channel_username="source",
        source_channel_display_name="Source",
        original_content=OriginalPostContent("failure processing", None),
        source_published_at=now - timedelta(minutes=1),
        received_at=now,
    ).transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=now,
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )
    ad_failed = base.start_advertisement_check(
        job_id="ad-job",
        expected_processing_version=0,
        requested_at=now,
    ).apply_advertisement_failure(
        AdvertisementCheckFailure(
            policy=AdvertisementFailurePolicy.CONTINUE_PROCESSING,
            failure_category="timeout",
            failure_type="all_providers_failed",
            failed_at=now,
            attempted_candidates_count=2,
            retry_count=1,
            fallback_count=1,
        ),
        job_id="ad-job",
        expected_processing_version=1,
    )
    semantic_failed = ad_failed.start_semantic_duplicate_check(
        job_id="semantic-job",
        expected_processing_version=0,
        requested_at=now,
    ).apply_semantic_duplicate_failure(
        SemanticDuplicateFailure(
            SemanticDuplicateFailurePolicy.CONTINUE_PROCESSING,
            "timeout",
            now,
        ),
        job_id="semantic-job",
        expected_processing_version=1,
    )
    categorization_failed = semantic_failed.enqueue_categorization(
        "category-job"
    ).apply_categorization_failure(
        CategorizationCheckFailure(
            policy="stop_processing",
            failure_category="invalid_response",
            failed_at=now,
            attempted_candidates_count=2,
            retry_count=1,
            fallback_count=1,
        ),
        "category-job",
        expected_processing_version=1,
    )
    restored_category = post_from_document(post_to_document(categorization_failed))
    assert restored_category.categorization_result is None
    assert (
        restored_category.categorization_failure
        == categorization_failed.categorization_failure
    )

    score_base = semantic_failed.apply_categorization_result(
        CategorizationResult(
            "news",
            CategorizationMethod.SOURCE_DEFAULT,
            1,
            now,
        ),
        None,
        0,
    ).schedule_scoring(
        job_id="score-job",
        due_at=now + timedelta(minutes=1),
        expected_processing_version=0,
    )
    scoring_failed = score_base.apply_scoring_failure(
        ScoringFailure(
            ScoringFailurePolicy.MARK_UNAVAILABLE,
            "provider_failure",
            now,
        ),
        job_id="score-job",
        expected_processing_version=1,
    )
    restored_score = post_from_document(post_to_document(scoring_failed))
    assert restored_score.scoring_result is None
    assert restored_score.scoring_failure == scoring_failed.scoring_failure


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("original_content",), []),
        (("original_content", "text"), 1),
        (("original_content", "text_entities"), {}),
        (("original_content", "text_entities", 0), []),
        (("original_content", "text_entities", 0, "offset_utf16"), True),
        (("original_content", "inline_keyboard"), {}),
        (("original_content", "inline_keyboard", 0), {}),
        (("original_content", "inline_keyboard", 0, 0), []),
        (("transition_history",), {}),
        (("transition_history", 0), []),
        (("transition_history", 0, "actor_category"), "unknown"),
        (("received_at",), "2026-01-01"),
        (("next_stage_claimed_at",), datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
def test_nested_document_type_confusion_is_rejected(
    path: tuple[str | int, ...],
    invalid_value: object,
) -> None:
    """The public mapper rejects malformed nested BSON without coercion."""
    document = post_to_document(_make_post())
    target: object = document
    for segment in path[:-1]:
        target = (
            cast("dict[str, object]", target)[segment]
            if isinstance(segment, str)
            else cast("list[object]", target)[segment]
        )
    final = path[-1]
    if isinstance(final, str):
        cast("dict[str, object]", target)[final] = invalid_value
    else:
        cast("list[object]", target)[final] = invalid_value

    with pytest.raises(InvalidPostDocumentError):
        post_from_document(document)


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        (post_mapper_module._require_mapping, ([1],)),
        (post_mapper_module._require_mapping, ({1: "value"},)),
        (post_mapper_module._require_string, (1,)),
        (post_mapper_module._require_boolean, (1,)),
        (post_mapper_module._require_float, (1,)),
        (post_mapper_module._entities_from_document, ({},)),
        (post_mapper_module.inline_keyboard_from_document, ({},)),
        (post_mapper_module.status_transition_to_document, ({},)),
        (post_mapper_module.post_to_document, ({},)),
    ],
)
def test_mapper_scalar_helpers_reject_type_confusion(
    operation: object,
    args: tuple[object, ...],
) -> None:
    """All BSON helper boundaries reject coercion before constructing Domain data."""
    kwargs = (
        {"rule": "invalid_document"}
        if operation is post_mapper_module._require_mapping
        else {}
    )
    with pytest.raises(InvalidPostDocumentError):
        cast("Any", operation)(*args, **kwargs)


def test_mapper_timestamp_overflow_is_reported_as_safe_document_error() -> None:
    with pytest.raises(InvalidPostDocumentError) as ceiling:
        post_mapper_module._ceil_to_millisecond(datetime.max.replace(tzinfo=UTC))
    assert ceiling.value.rule == "invalid_timestamp"
    with pytest.raises(InvalidPostDocumentError) as restore:
        post_mapper_module._restore_ceil_datetime(
            datetime.min.replace(tzinfo=UTC),
            1,
        )
    assert restore.value.rule == "invalid_timestamp"
