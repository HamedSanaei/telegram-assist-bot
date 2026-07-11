from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from itertools import product
from typing import cast

import pytest

from telegram_assist_bot.domain.posts import (
    ALLOWED_POST_STATUS_TRANSITIONS,
    InvalidPostTransitionError,
    InvalidPostVersionError,
    OriginalContentMutationError,
    OriginalPostContent,
    Post,
    PostId,
    PostInvariantError,
    PostStatus,
    PostVersionConflictError,
    SourceMessageIdentity,
    StatusTransition,
    TelegramEntity,
    TransitionActorCategory,
    is_post_status_transition_allowed,
)

_RECEIVED_AT = datetime(2026, 1, 10, 8, 30, tzinfo=UTC)


class _ExternalString(str):
    """Simulate serializable-looking metadata with mutable external state."""

    payload: list[str]

    def __new__(cls, value: str) -> _ExternalString:
        instance = super().__new__(cls, value)
        instance.payload = ["provider-owned"]
        return instance


def _make_post() -> Post:
    return Post(
        post_id=PostId("post-lifecycle-1"),
        source_identity=SourceMessageIdentity(-1001234567890, 42),
        source_channel_username="source_channel",
        source_channel_display_name="Source channel",
        original_content=OriginalPostContent(
            text="Original text",
            caption="Original caption",
            text_entities=(TelegramEntity(0, 8, "bold"),),
            caption_entities=(TelegramEntity(0, 8, "italic"),),
        ),
        source_published_at=_RECEIVED_AT - timedelta(minutes=5),
        received_at=_RECEIVED_AT,
    )


def _snapshot_with_status(status: PostStatus) -> Post:
    post = _make_post()
    if status is PostStatus.DISCOVERED:
        return post
    if status is PostStatus.STORED:
        return post.transition_to(
            PostStatus.STORED,
            expected_version=0,
            occurred_at=post.received_at + timedelta(hours=1),
            actor_category=TransitionActorCategory.SERVICE,
            reason="stored",
        )
    return post.transition_to(
        PostStatus.EXPIRED,
        expected_version=0,
        occurred_at=post.expires_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="retention elapsed",
    )


def test_new_post_starts_in_discovered_state_without_history() -> None:
    post = _make_post()

    assert post.status is PostStatus.DISCOVERED
    assert post.version == 0
    assert post.transition_history == ()


def test_milestone_zero_transition_table_is_exact_and_immutable() -> None:
    expected = {
        PostStatus.DISCOVERED: frozenset({PostStatus.STORED, PostStatus.EXPIRED}),
        PostStatus.STORED: frozenset({PostStatus.EXPIRED}),
        PostStatus.EXPIRED: frozenset(),
    }

    assert dict(ALLOWED_POST_STATUS_TRANSITIONS) == expected
    assert set(PostStatus) == set(expected)
    for previous_status, new_status in product(PostStatus, repeat=2):
        assert is_post_status_transition_allowed(previous_status, new_status) is (
            new_status in expected[previous_status]
        )

    with pytest.raises(TypeError):
        cast(
            "dict[PostStatus, frozenset[PostStatus]]",
            ALLOWED_POST_STATUS_TRANSITIONS,
        )[PostStatus.EXPIRED] = frozenset({PostStatus.DISCOVERED})


@pytest.mark.parametrize(
    ("initial_status", "new_status"),
    [
        (PostStatus.DISCOVERED, PostStatus.DISCOVERED),
        (PostStatus.STORED, PostStatus.DISCOVERED),
        (PostStatus.STORED, PostStatus.STORED),
        (PostStatus.EXPIRED, PostStatus.DISCOVERED),
        (PostStatus.EXPIRED, PostStatus.STORED),
        (PostStatus.EXPIRED, PostStatus.EXPIRED),
    ],
)
def test_every_forbidden_transition_raises_domain_error(
    initial_status: PostStatus,
    new_status: PostStatus,
) -> None:
    post = _snapshot_with_status(initial_status)
    occurred_at = (
        post.expires_at
        if new_status is PostStatus.EXPIRED
        else post.received_at + timedelta(hours=2)
    )

    with pytest.raises(InvalidPostTransitionError):
        post.transition_to(
            new_status,
            expected_version=post.version,
            occurred_at=occurred_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason="forbidden edge",
        )


def test_discovered_can_transition_to_stored_before_expiration() -> None:
    post = _make_post()
    occurred_at = post.received_at + timedelta(seconds=1)

    stored = post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=occurred_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
        correlation_id="collect-42",
    )

    assert stored.status is PostStatus.STORED
    assert stored.version == 1
    assert stored.transition_history == (
        StatusTransition(
            previous_status=PostStatus.DISCOVERED,
            new_status=PostStatus.STORED,
            occurred_at=occurred_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason="persisted",
            correlation_id="collect-42",
        ),
    )


def test_discovered_can_transition_directly_to_expired_at_boundary() -> None:
    post = _make_post()

    expired = post.transition_to(
        PostStatus.EXPIRED,
        expected_version=0,
        occurred_at=post.expires_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="retention elapsed",
    )

    assert expired.status is PostStatus.EXPIRED
    assert expired.version == 1
    assert expired.transition_history[-1].previous_status is PostStatus.DISCOVERED
    assert expired.transition_history[-1].new_status is PostStatus.EXPIRED


def test_stored_can_transition_to_expired_and_preserves_full_history() -> None:
    post = _make_post()
    stored_at = post.received_at + timedelta(minutes=2)
    stored = post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=stored_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
        correlation_id="collect-42",
    )

    expired = stored.transition_to(
        PostStatus.EXPIRED,
        expected_version=1,
        occurred_at=stored.expires_at,
        actor_category=TransitionActorCategory.ADMINISTRATOR,
        reason="manual retention cleanup",
        correlation_id="cleanup-7",
    )

    assert expired.status is PostStatus.EXPIRED
    assert expired.version == 2
    assert expired.transition_history[:-1] == stored.transition_history
    assert expired.transition_history == (
        StatusTransition(
            previous_status=PostStatus.DISCOVERED,
            new_status=PostStatus.STORED,
            occurred_at=stored_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason="persisted",
            correlation_id="collect-42",
        ),
        StatusTransition(
            previous_status=PostStatus.STORED,
            new_status=PostStatus.EXPIRED,
            occurred_at=stored.expires_at,
            actor_category=TransitionActorCategory.ADMINISTRATOR,
            reason="manual retention cleanup",
            correlation_id="cleanup-7",
        ),
    )


def test_transition_returns_a_new_immutable_snapshot() -> None:
    post = _make_post()

    stored = post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=post.received_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
    )

    assert stored is not post
    assert post.status is PostStatus.DISCOVERED
    assert post.version == 0
    assert post.transition_history == ()
    with pytest.raises(FrozenInstanceError):
        post.status = PostStatus.STORED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        post.original_content.text = "replacement"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        post.transition_history.append(stored.transition_history[0])  # type: ignore[attr-defined]


@pytest.mark.parametrize("expected_version", [-1, True])
def test_transition_rejects_malformed_expected_version(
    expected_version: object,
) -> None:
    post = _make_post()

    with pytest.raises(InvalidPostVersionError):
        post.transition_to(
            PostStatus.STORED,
            expected_version=cast("int", expected_version),
            occurred_at=post.received_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason="persisted",
        )


@pytest.mark.parametrize("expected_version", [0, 2])
def test_transition_rejects_stale_and_future_versions(
    expected_version: int,
) -> None:
    stored = _snapshot_with_status(PostStatus.STORED)

    with pytest.raises(PostVersionConflictError) as error:
        stored.transition_to(
            PostStatus.EXPIRED,
            expected_version=expected_version,
            occurred_at=stored.expires_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason="retention elapsed",
        )

    assert error.value.expected_version == expected_version
    assert error.value.current_version == 1


def test_invalid_status_object_is_not_treated_as_a_transition() -> None:
    post = _make_post()

    assert not is_post_status_transition_allowed(PostStatus.DISCOVERED, "Stored")
    assert not is_post_status_transition_allowed("Discovered", PostStatus.STORED)
    with pytest.raises(InvalidPostTransitionError):
        post.transition_to(
            cast("PostStatus", "Stored"),
            expected_version=0,
            occurred_at=post.received_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason="persisted",
        )


@pytest.mark.parametrize("reason", ["", "   ", "x" * 1025])
def test_transition_rejects_invalid_reason(reason: str) -> None:
    post = _make_post()

    with pytest.raises(PostInvariantError):
        post.transition_to(
            PostStatus.STORED,
            expected_version=0,
            occurred_at=post.received_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason=reason,
        )


@pytest.mark.parametrize("correlation_id", ["", "   ", "x" * 129])
def test_transition_rejects_invalid_correlation_id(correlation_id: str) -> None:
    post = _make_post()

    with pytest.raises(PostInvariantError):
        post.transition_to(
            PostStatus.STORED,
            expected_version=0,
            occurred_at=post.received_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason="persisted",
            correlation_id=correlation_id,
        )


def test_transition_rejects_actor_outside_owned_categories() -> None:
    post = _make_post()

    with pytest.raises(PostInvariantError):
        post.transition_to(
            PostStatus.STORED,
            expected_version=0,
            occurred_at=post.received_at,
            actor_category=cast("TransitionActorCategory", "worker"),
            reason="persisted",
        )


@pytest.mark.parametrize(
    ("reason", "correlation_id"),
    [
        (_ExternalString("persisted"), None),
        ("persisted", _ExternalString("collect-42")),
        (cast("str", object()), None),
        ("persisted", cast("str | None", object())),
    ],
)
def test_transition_rejects_non_builtin_metadata_strings(
    reason: str,
    correlation_id: str | None,
) -> None:
    post = _make_post()

    with pytest.raises(PostInvariantError):
        post.transition_to(
            PostStatus.STORED,
            expected_version=0,
            occurred_at=post.received_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason=reason,
            correlation_id=correlation_id,
        )


def test_transition_repr_does_not_expose_content_or_transition_metadata() -> None:
    post = replace(
        _make_post(),
        original_content=OriginalPostContent(
            text="private-source-content-marker",
            caption="private-caption-marker",
        ),
    )
    stored = post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=post.received_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="private-reason-marker",
        correlation_id="private-correlation-marker",
    )

    rendered_post = repr(stored)
    rendered_transition = repr(stored.transition_history[-1])
    assert "private-source-content-marker" not in rendered_post
    assert "private-caption-marker" not in rendered_post
    assert "private-reason-marker" not in rendered_transition
    assert "private-correlation-marker" not in rendered_transition


def test_status_transition_rejects_an_invalid_edge_when_built_directly() -> None:
    with pytest.raises(InvalidPostTransitionError):
        StatusTransition(
            previous_status=PostStatus.EXPIRED,
            new_status=PostStatus.STORED,
            occurred_at=_RECEIVED_AT,
            actor_category=TransitionActorCategory.SERVICE,
            reason="invalid edge",
        )


def test_original_content_match_is_idempotent_but_mutation_is_rejected() -> None:
    post = _make_post()
    equal_content = OriginalPostContent(
        text=post.original_text,
        caption=post.original_caption,
        text_entities=post.original_text_entities,
        caption_entities=post.original_caption_entities,
    )

    post.assert_original_content_matches(equal_content)
    with pytest.raises(OriginalContentMutationError):
        post.assert_original_content_matches(
            replace(equal_content, text="changed original text")
        )
    with pytest.raises(OriginalContentMutationError):
        post.assert_original_content_matches(cast("OriginalPostContent", object()))


def test_rehydration_rejects_nonzero_version_without_history() -> None:
    post = _make_post()

    with pytest.raises(InvalidPostVersionError):
        replace(post, version=1)


def test_rehydration_rejects_noninitial_status_without_history() -> None:
    post = _make_post()

    with pytest.raises(PostInvariantError):
        replace(post, status=PostStatus.STORED)


def test_rehydration_rejects_history_that_starts_from_wrong_status() -> None:
    post = _make_post()
    transition = StatusTransition(
        previous_status=PostStatus.STORED,
        new_status=PostStatus.EXPIRED,
        occurred_at=post.expires_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="retention elapsed",
    )

    with pytest.raises(PostInvariantError):
        replace(
            post,
            status=PostStatus.EXPIRED,
            version=1,
            transition_history=(transition,),
        )


def test_rehydration_rejects_status_that_disagrees_with_history_tail() -> None:
    post = _make_post()
    transition = StatusTransition(
        previous_status=PostStatus.DISCOVERED,
        new_status=PostStatus.STORED,
        occurred_at=post.received_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
    )

    with pytest.raises(PostInvariantError):
        replace(
            post,
            status=PostStatus.EXPIRED,
            version=1,
            transition_history=(transition,),
        )


def test_rehydration_rejects_transition_before_receipt() -> None:
    post = _make_post()
    transition = StatusTransition(
        previous_status=PostStatus.DISCOVERED,
        new_status=PostStatus.STORED,
        occurred_at=post.received_at - timedelta(microseconds=1),
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
    )

    with pytest.raises(PostInvariantError):
        replace(
            post,
            status=PostStatus.STORED,
            version=1,
            transition_history=(transition,),
        )


def test_rehydration_rejects_premature_expiration_history() -> None:
    post = _make_post()
    transition = StatusTransition(
        previous_status=PostStatus.DISCOVERED,
        new_status=PostStatus.EXPIRED,
        occurred_at=post.expires_at - timedelta(microseconds=1),
        actor_category=TransitionActorCategory.SERVICE,
        reason="retention elapsed",
    )

    with pytest.raises(PostInvariantError):
        replace(
            post,
            status=PostStatus.EXPIRED,
            version=1,
            transition_history=(transition,),
        )


def test_rehydration_rejects_non_transition_history_member() -> None:
    post = _make_post()

    with pytest.raises(PostInvariantError):
        replace(
            post,
            transition_history=cast(
                "tuple[StatusTransition, ...]", ("not-a-transition",)
            ),
        )


def test_rehydration_rejects_unordered_history_input() -> None:
    post = _make_post()
    transition = StatusTransition(
        previous_status=PostStatus.DISCOVERED,
        new_status=PostStatus.STORED,
        occurred_at=post.received_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
    )

    with pytest.raises(PostInvariantError):
        replace(
            post,
            transition_history=cast(
                "tuple[StatusTransition, ...]",
                {transition},
            ),
        )


def test_rehydration_defensively_copies_an_ordered_history_sequence() -> None:
    post = _make_post()
    transition = StatusTransition(
        previous_status=PostStatus.DISCOVERED,
        new_status=PostStatus.STORED,
        occurred_at=post.received_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
    )
    mutable_history = [transition]

    stored = replace(
        post,
        status=PostStatus.STORED,
        version=1,
        transition_history=cast(
            "tuple[StatusTransition, ...]",
            mutable_history,
        ),
    )
    mutable_history.clear()

    assert stored.transition_history == (transition,)


def test_rehydration_rejects_a_discontinuous_history_chain() -> None:
    post = _make_post()
    stored = StatusTransition(
        previous_status=PostStatus.DISCOVERED,
        new_status=PostStatus.STORED,
        occurred_at=post.received_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
    )
    expired_from_wrong_previous = StatusTransition(
        previous_status=PostStatus.DISCOVERED,
        new_status=PostStatus.EXPIRED,
        occurred_at=post.expires_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="retention elapsed",
    )

    with pytest.raises(PostInvariantError):
        replace(
            post,
            status=PostStatus.EXPIRED,
            version=2,
            transition_history=(stored, expired_from_wrong_previous),
        )


def test_rehydration_rejects_descending_history_timestamps() -> None:
    post = _make_post()
    stored = StatusTransition(
        previous_status=PostStatus.DISCOVERED,
        new_status=PostStatus.STORED,
        occurred_at=post.received_at + timedelta(minutes=2),
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
    )
    earlier_expiration = StatusTransition(
        previous_status=PostStatus.STORED,
        new_status=PostStatus.EXPIRED,
        occurred_at=post.received_at + timedelta(minutes=1),
        actor_category=TransitionActorCategory.SERVICE,
        reason="invalid clock order",
    )

    with pytest.raises(PostInvariantError):
        replace(
            post,
            status=PostStatus.EXPIRED,
            version=2,
            transition_history=(stored, earlier_expiration),
        )


def test_rehydration_rejects_stored_transition_at_expiration_boundary() -> None:
    post = _make_post()
    stored_too_late = StatusTransition(
        previous_status=PostStatus.DISCOVERED,
        new_status=PostStatus.STORED,
        occurred_at=post.expires_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted too late",
    )

    with pytest.raises(PostInvariantError):
        replace(
            post,
            status=PostStatus.STORED,
            version=1,
            transition_history=(stored_too_late,),
        )
