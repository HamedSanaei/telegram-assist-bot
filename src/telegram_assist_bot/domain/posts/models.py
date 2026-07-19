"""Immutable aggregate and value objects for original Telegram posts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Final, Self, cast

from telegram_assist_bot.domain.advertisement import (
    ADVERTISEMENT_MANUAL_REVIEW_REASON,
    AdvertisementCheckFailure,
    AdvertisementCheckResult,
    AdvertisementFailurePolicy,
    AdvertisementProcessingState,
    InvalidAdvertisementTransitionError,
)
from telegram_assist_bot.domain.categories import (
    CategorizationCheckFailure,
    CategorizationMethod,
    CategorizationResult,
    CategorizationState,
)
from telegram_assist_bot.domain.duplicates.models import (
    SEMANTIC_DUPLICATE_MANUAL_REVIEW_REASON,
    InvalidSemanticDuplicateTransitionError,
    SemanticDuplicateFailure,
    SemanticDuplicateFailurePolicy,
    SemanticDuplicatePolicy,
    SemanticDuplicateResult,
    SemanticDuplicateState,
)
from telegram_assist_bot.domain.posts.entities import TelegramEntity
from telegram_assist_bot.domain.posts.errors import (
    InvalidPostIdentifierError,
    InvalidPostTransitionError,
    InvalidPostVersionError,
    InvalidSourceMessageIdentityError,
    OriginalContentMutationError,
    PostInvariantError,
    PostVersionConflictError,
)
from telegram_assist_bot.domain.posts.status import (
    PostStatus,
    StatusTransition,
    TransitionActorCategory,
    _canonical_utc,
    is_post_status_transition_allowed,
)
from telegram_assist_bot.domain.scoring import (
    ScoringFailure,
    ScoringFailurePolicy,
    ScoringResult,
    ScoringState,
)

POST_RETENTION_PERIOD: Final[timedelta] = timedelta(days=14)
"""The fixed Milestone 0 retention period for a received post."""

_MAX_POST_ID_LENGTH: Final[int] = 128
_MAX_SOURCE_USERNAME_LENGTH: Final[int] = 128
_MAX_SOURCE_DISPLAY_NAME_LENGTH: Final[int] = 256


def _is_bounded_non_blank_string(value: object, maximum_length: int) -> bool:
    """Return whether text is non-blank without trimming or normalizing it."""
    return (
        type(value) is str
        and bool(value)
        and not value.isspace()
        and len(value) <= maximum_length
    )


def _freeze_entities(value: object) -> tuple[TelegramEntity, ...]:
    """Defensively copy and validate an original entity sequence."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PostInvariantError
    entities: tuple[object, ...] = tuple(value)
    if any(type(entity) is not TelegramEntity for entity in entities):
        raise PostInvariantError
    return cast("tuple[TelegramEntity, ...]", entities)


def _freeze_history(value: object) -> tuple[StatusTransition, ...]:
    """Defensively copy and validate a lifecycle history sequence."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PostInvariantError
    history: tuple[object, ...] = tuple(value)
    if any(type(item) is not StatusTransition for item in history):
        raise PostInvariantError
    return cast("tuple[StatusTransition, ...]", history)


@dataclass(frozen=True, slots=True, order=True)
class PostId:
    """Represent an opaque internal post identifier without database semantics."""

    value: str

    def __post_init__(self) -> None:
        """Reject blank or oversized identifiers without retaining bad input."""
        if not _is_bounded_non_blank_string(self.value, _MAX_POST_ID_LENGTH):
            raise InvalidPostIdentifierError

    def __str__(self) -> str:
        """Return the unchanged opaque identifier value."""
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class SourceMessageIdentity:
    """Identify one Telegram source message for idempotent ingestion."""

    source_channel_id: int
    source_message_id: int

    def __post_init__(self) -> None:
        """Require a non-zero signed channel ID and positive message ID."""
        if (
            type(self.source_channel_id) is not int
            or self.source_channel_id == 0
            or type(self.source_message_id) is not int
            or self.source_message_id <= 0
        ):
            raise InvalidSourceMessageIdentityError

    @property
    def as_tuple(self) -> tuple[int, int]:
        """Return the stable two-part idempotency key."""
        return (self.source_channel_id, self.source_message_id)


@dataclass(frozen=True, slots=True)
class OriginalPostContent:
    """Preserve original text, caption, and entity sequences exactly."""

    text: str | None = field(repr=False)
    caption: str | None = field(repr=False)
    text_entities: tuple[TelegramEntity, ...] = ()
    caption_entities: tuple[TelegramEntity, ...] = ()

    def __post_init__(self) -> None:
        """Freeze entity inputs without changing any Telegram source content."""
        if self.text is not None and type(self.text) is not str:
            raise PostInvariantError
        if self.caption is not None and type(self.caption) is not str:
            raise PostInvariantError
        text_entities = _freeze_entities(self.text_entities)
        caption_entities = _freeze_entities(self.caption_entities)
        if self.text is None and text_entities:
            raise PostInvariantError
        if self.caption is None and caption_entities:
            raise PostInvariantError
        object.__setattr__(self, "text_entities", text_entities)
        object.__setattr__(self, "caption_entities", caption_entities)


@dataclass(frozen=True, slots=True, eq=False)
class Post:
    """Represent one immutable post aggregate and its lifecycle snapshot.

    Equality and hashing use only ``post_id`` so successive lifecycle snapshots
    keep stable aggregate identity. Ingestion idempotency is deliberately exposed
    separately through ``source_identity``.
    """

    post_id: PostId
    source_identity: SourceMessageIdentity
    source_channel_username: str | None
    source_channel_display_name: str
    original_content: OriginalPostContent = field(repr=False)
    source_published_at: datetime
    received_at: datetime
    status: PostStatus = PostStatus.DISCOVERED
    version: int = 0
    transition_history: tuple[StatusTransition, ...] = field(
        default=(),
        repr=False,
    )
    advertisement_state: AdvertisementProcessingState = (
        AdvertisementProcessingState.NOT_REQUESTED
    )
    advertisement_processing_version: int = 0
    advertisement_job_id: str | None = None
    advertisement_result: AdvertisementCheckResult | None = field(
        default=None,
        repr=False,
    )
    advertisement_failure: AdvertisementCheckFailure | None = field(
        default=None,
        repr=False,
    )
    semantic_duplicate_state: SemanticDuplicateState = (
        SemanticDuplicateState.NOT_REQUESTED
    )
    semantic_duplicate_version: int = 0
    semantic_duplicate_job_id: str | None = None
    semantic_duplicate_result: SemanticDuplicateResult | None = field(
        default=None, repr=False
    )
    semantic_duplicate_failure: SemanticDuplicateFailure | None = field(
        default=None, repr=False
    )
    categorization_state: CategorizationState = CategorizationState.NOT_REQUESTED
    categorization_processing_version: int = 0
    categorization_job_id: str | None = None
    categorization_result: CategorizationResult | None = field(default=None, repr=False)
    categorization_failure: CategorizationCheckFailure | None = field(
        default=None, repr=False
    )
    scoring_state: ScoringState = ScoringState.NOT_REQUESTED
    scoring_processing_version: int = 0
    scoring_job_id: str | None = None
    scoring_due_at: datetime | None = None
    scoring_result: ScoringResult | None = field(default=None, repr=False)
    scoring_failure: ScoringFailure | None = field(default=None, repr=False)
    expires_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        """Canonicalize time and enforce all aggregate rehydration invariants."""
        if type(self.post_id) is not PostId:
            raise InvalidPostIdentifierError
        if type(self.source_identity) is not SourceMessageIdentity:
            raise InvalidSourceMessageIdentityError
        if type(self.original_content) is not OriginalPostContent:
            raise PostInvariantError
        if self.source_channel_username is not None and not (
            _is_bounded_non_blank_string(
                self.source_channel_username,
                _MAX_SOURCE_USERNAME_LENGTH,
            )
        ):
            raise PostInvariantError
        if not _is_bounded_non_blank_string(
            self.source_channel_display_name,
            _MAX_SOURCE_DISPLAY_NAME_LENGTH,
        ):
            raise PostInvariantError
        if not isinstance(self.status, PostStatus):
            raise PostInvariantError
        if type(self.version) is not int or self.version < 0:
            raise InvalidPostVersionError
        if type(self.advertisement_state) is not AdvertisementProcessingState:
            raise PostInvariantError
        if (
            type(self.advertisement_processing_version) is not int
            or self.advertisement_processing_version < 0
        ):
            raise PostInvariantError
        if self.advertisement_job_id is not None and (
            type(self.advertisement_job_id) is not str
            or not self.advertisement_job_id
            or self.advertisement_job_id.isspace()
            or len(self.advertisement_job_id) > 128
        ):
            raise PostInvariantError
        if self.advertisement_result is not None and (
            type(self.advertisement_result) is not AdvertisementCheckResult
        ):
            raise PostInvariantError
        if self.advertisement_failure is not None and (
            type(self.advertisement_failure) is not AdvertisementCheckFailure
        ):
            raise PostInvariantError
        if type(self.semantic_duplicate_state) is not SemanticDuplicateState:
            raise PostInvariantError
        if (
            type(self.semantic_duplicate_version) is not int
            or self.semantic_duplicate_version < 0
        ):
            raise PostInvariantError
        if (
            self.semantic_duplicate_job_id is not None
            and not _is_bounded_non_blank_string(
                self.semantic_duplicate_job_id, _MAX_POST_ID_LENGTH
            )
        ):
            raise PostInvariantError
        if (
            self.semantic_duplicate_result is not None
            and type(self.semantic_duplicate_result) is not SemanticDuplicateResult
        ):
            raise PostInvariantError
        if (
            self.semantic_duplicate_failure is not None
            and type(self.semantic_duplicate_failure) is not SemanticDuplicateFailure
        ):
            raise PostInvariantError

        if type(self.categorization_state) is not CategorizationState:
            raise PostInvariantError
        if (
            type(self.categorization_processing_version) is not int
            or self.categorization_processing_version < 0
        ):
            raise PostInvariantError
        if self.categorization_job_id is not None and not _is_bounded_non_blank_string(
            self.categorization_job_id, _MAX_POST_ID_LENGTH
        ):
            raise PostInvariantError
        if (
            self.categorization_result is not None
            and type(self.categorization_result) is not CategorizationResult
        ):
            raise PostInvariantError
        if (
            self.categorization_failure is not None
            and type(self.categorization_failure) is not CategorizationCheckFailure
        ):
            raise PostInvariantError
        if type(self.scoring_state) is not ScoringState:
            raise PostInvariantError
        if (
            type(self.scoring_processing_version) is not int
            or self.scoring_processing_version < 0
        ):
            raise PostInvariantError
        if self.scoring_job_id is not None and not _is_bounded_non_blank_string(
            self.scoring_job_id, _MAX_POST_ID_LENGTH
        ):
            raise PostInvariantError
        if (
            self.scoring_result is not None
            and type(self.scoring_result) is not ScoringResult
        ):
            raise PostInvariantError
        if (
            self.scoring_failure is not None
            and type(self.scoring_failure) is not ScoringFailure
        ):
            raise PostInvariantError
        if self.scoring_due_at is not None:
            object.__setattr__(
                self, "scoring_due_at", _canonical_utc(self.scoring_due_at)
            )

        source_published_at = _canonical_utc(self.source_published_at)
        received_at = _canonical_utc(self.received_at)
        history = _freeze_history(self.transition_history)
        object.__setattr__(self, "source_published_at", source_published_at)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "transition_history", history)
        try:
            expires_at = received_at + POST_RETENTION_PERIOD
        except OverflowError:
            raise PostInvariantError from None
        object.__setattr__(self, "expires_at", expires_at)
        self._validate_lifecycle_history()
        self._validate_advertisement_state()
        self._validate_semantic_duplicate_state()
        self._validate_categorization_state()
        self._validate_scoring_state()

    def __eq__(self, other: object) -> bool:
        """Compare stable aggregate identity rather than mutable lifecycle state."""
        return isinstance(other, Post) and self.post_id == other.post_id

    def __hash__(self) -> int:
        """Hash the stable internal aggregate identifier."""
        return hash(self.post_id)

    @property
    def original_text(self) -> str | None:
        """Return the exact original Telegram message text."""
        return self.original_content.text

    @property
    def original_caption(self) -> str | None:
        """Return the exact original Telegram media caption."""
        return self.original_content.caption

    @property
    def original_text_entities(self) -> tuple[TelegramEntity, ...]:
        """Return entities associated with original message text."""
        return self.original_content.text_entities

    @property
    def original_caption_entities(self) -> tuple[TelegramEntity, ...]:
        """Return entities associated with the original media caption."""
        return self.original_content.caption_entities

    @property
    def idempotency_identity(self) -> SourceMessageIdentity:
        """Return the source identity used to deduplicate ingestion."""
        return self.source_identity

    def is_expired_at(self, occurred_at: datetime) -> bool:
        """Return whether the fixed retention boundary has been reached."""
        return _canonical_utc(occurred_at) >= self.expires_at

    def assert_original_content_matches(
        self,
        candidate: OriginalPostContent,
    ) -> None:
        """Reject a conflicting source-content version during idempotent ingest."""
        if type(candidate) is not OriginalPostContent:
            raise OriginalContentMutationError
        if candidate != self.original_content:
            raise OriginalContentMutationError

    def transition_to(
        self,
        new_status: PostStatus,
        *,
        expected_version: int,
        occurred_at: datetime,
        actor_category: TransitionActorCategory,
        reason: str,
        correlation_id: str | None = None,
    ) -> Self:
        """Return a new snapshot after one validated optimistic transition."""
        if type(expected_version) is not int or expected_version < 0:
            raise InvalidPostVersionError
        if expected_version != self.version:
            raise PostVersionConflictError(expected_version, self.version)
        if not is_post_status_transition_allowed(self.status, new_status):
            raise InvalidPostTransitionError

        normalized_time = _canonical_utc(occurred_at)
        previous_time = (
            self.transition_history[-1].occurred_at
            if self.transition_history
            else self.received_at
        )
        if normalized_time < previous_time:
            raise InvalidPostTransitionError
        self._validate_transition_timing(new_status, normalized_time)
        transition = StatusTransition(
            previous_status=self.status,
            new_status=new_status,
            occurred_at=normalized_time,
            actor_category=actor_category,
            reason=reason,
            correlation_id=correlation_id,
        )
        return replace(
            self,
            status=new_status,
            version=self.version + 1,
            transition_history=(*self.transition_history, transition),
        )

    @property
    def advertisement_allows_next_stage(self) -> bool:
        """Return whether the next normal content stage may run exactly once."""
        return self.advertisement_state.allows_next_pipeline_stage

    @property
    def advertisement_requires_manual_review(self) -> bool:
        """Expose a typed handoff consumable by the approval application layer."""
        return self.advertisement_state.approval_review_eligible

    @property
    def advertisement_manual_review_reason(self) -> str | None:
        """Return the stable approval handoff reason only for manual review."""
        if not self.advertisement_requires_manual_review:
            return None
        return ADVERTISEMENT_MANUAL_REVIEW_REASON

    @property
    def semantic_duplicate_allows_next_stage(self) -> bool:
        """Return whether the next required pipeline stage may run."""
        return self.semantic_duplicate_state.allows_next_pipeline_stage

    @property
    def semantic_duplicate_requires_manual_review(self) -> bool:
        """Expose the application-owned review handoff."""
        return self.semantic_duplicate_state.requires_manual_review

    @property
    def semantic_duplicate_manual_review_reason(self) -> str | None:
        """Return a stable reason for a valid duplicate manual-review handoff."""
        if (
            self.semantic_duplicate_state
            is not SemanticDuplicateState.DUPLICATE_MANUAL_REVIEW
        ):
            return None
        return SEMANTIC_DUPLICATE_MANUAL_REVIEW_REASON

    def start_semantic_duplicate_check(
        self,
        *,
        job_id: str,
        expected_processing_version: int,
        requested_at: datetime,
    ) -> Self:
        """Enter semantic processing only after advertisement permits continuation."""
        self._assert_semantic_transition_context(
            expected_processing_version, requested_at
        )
        if (
            self.semantic_duplicate_state is SemanticDuplicateState.PENDING
            and self.semantic_duplicate_job_id == job_id
        ):
            return self
        if (
            self.semantic_duplicate_state is not SemanticDuplicateState.NOT_REQUESTED
            or not self.advertisement_allows_next_stage
            or not _is_bounded_non_blank_string(job_id, _MAX_POST_ID_LENGTH)
        ):
            raise InvalidSemanticDuplicateTransitionError
        return replace(
            self,
            semantic_duplicate_state=SemanticDuplicateState.PENDING,
            semantic_duplicate_version=self.semantic_duplicate_version + 1,
            semantic_duplicate_job_id=job_id,
            semantic_duplicate_result=None,
            semantic_duplicate_failure=None,
        )

    def apply_semantic_duplicate_result(
        self,
        result: SemanticDuplicateResult,
        *,
        policy: SemanticDuplicatePolicy,
        job_id: str,
        expected_processing_version: int,
    ) -> Self:
        """Apply one validated semantic decision and explicit duplicate policy."""
        self._assert_semantic_transition_context(
            expected_processing_version, result.checked_at
        )
        if (
            self.semantic_duplicate_state
            not in {
                SemanticDuplicateState.PENDING,
                SemanticDuplicateState.RETRY_PENDING,
            }
            or self.semantic_duplicate_job_id != job_id
            or type(policy) is not SemanticDuplicatePolicy
        ):
            raise InvalidSemanticDuplicateTransitionError
        if not result.is_duplicate:
            target = SemanticDuplicateState.PASSED
        else:
            target = {
                SemanticDuplicatePolicy.REJECT: (
                    SemanticDuplicateState.DUPLICATE_REJECTED
                ),
                SemanticDuplicatePolicy.MANUAL_REVIEW: (
                    SemanticDuplicateState.DUPLICATE_MANUAL_REVIEW
                ),
                SemanticDuplicatePolicy.CONTINUE_PROCESSING: (
                    SemanticDuplicateState.DUPLICATE_ALLOWED
                ),
            }[policy]
        return replace(
            self,
            semantic_duplicate_state=target,
            semantic_duplicate_version=self.semantic_duplicate_version + 1,
            semantic_duplicate_result=result,
            semantic_duplicate_failure=None,
        )

    def apply_semantic_duplicate_failure(
        self,
        failure: SemanticDuplicateFailure,
        *,
        job_id: str,
        expected_processing_version: int,
    ) -> Self:
        """Apply an AI failure policy without fabricating a duplicate result."""
        self._assert_semantic_transition_context(
            expected_processing_version, failure.failed_at
        )
        if (
            self.semantic_duplicate_state
            not in {
                SemanticDuplicateState.PENDING,
                SemanticDuplicateState.RETRY_PENDING,
            }
            or self.semantic_duplicate_job_id != job_id
        ):
            raise InvalidSemanticDuplicateTransitionError
        target = {
            SemanticDuplicateFailurePolicy.CONTINUE_PROCESSING: (
                SemanticDuplicateState.FAILURE_CONTINUE
            ),
            SemanticDuplicateFailurePolicy.STOP_PROCESSING: (
                SemanticDuplicateState.PROCESSING_STOPPED
            ),
            SemanticDuplicateFailurePolicy.RETRY_LATER: (
                SemanticDuplicateState.RETRY_PENDING
            ),
            SemanticDuplicateFailurePolicy.MANUAL_REVIEW: (
                SemanticDuplicateState.FAILURE_MANUAL_REVIEW
            ),
        }[failure.policy]
        return replace(
            self,
            semantic_duplicate_state=target,
            semantic_duplicate_version=self.semantic_duplicate_version + 1,
            semantic_duplicate_result=None,
            semantic_duplicate_failure=failure,
        )

    def _assert_semantic_transition_context(
        self, expected_processing_version: int, occurred_at: datetime
    ) -> None:
        if (
            type(expected_processing_version) is not int
            or expected_processing_version != self.semantic_duplicate_version
            or self.status is not PostStatus.STORED
            or self.is_expired_at(occurred_at)
        ):
            raise InvalidSemanticDuplicateTransitionError

    def start_advertisement_check(
        self,
        *,
        job_id: str,
        expected_processing_version: int,
        requested_at: datetime,
    ) -> Self:
        """Bind one canonical AI Job and enter the pending processing state."""
        self._assert_advertisement_transition_context(
            expected_processing_version,
            requested_at,
        )
        if (
            self.advertisement_state is AdvertisementProcessingState.PENDING
            and self.advertisement_job_id == job_id
        ):
            return self
        if self.advertisement_state is not AdvertisementProcessingState.NOT_REQUESTED:
            raise InvalidAdvertisementTransitionError
        if (
            type(job_id) is not str
            or not job_id
            or job_id.isspace()
            or len(job_id) > 128
        ):
            raise InvalidAdvertisementTransitionError
        return replace(
            self,
            advertisement_state=AdvertisementProcessingState.PENDING,
            advertisement_processing_version=self.advertisement_processing_version + 1,
            advertisement_job_id=job_id,
            advertisement_result=None,
            advertisement_failure=None,
        )

    def apply_advertisement_result(
        self,
        result: AdvertisementCheckResult,
        *,
        job_id: str,
        expected_processing_version: int,
    ) -> Self:
        """Apply one validated result without a confidence-threshold policy."""
        if type(result) is not AdvertisementCheckResult:
            raise InvalidAdvertisementTransitionError
        self._assert_advertisement_transition_context(
            expected_processing_version,
            result.checked_at,
        )
        target = (
            AdvertisementProcessingState.REJECTED_AS_ADVERTISEMENT
            if result.is_advertisement
            else AdvertisementProcessingState.PASSED
        )
        if (
            self.advertisement_state is target
            and self.advertisement_job_id == job_id
            and self.advertisement_result == result
        ):
            return self
        if (
            self.advertisement_state
            not in {
                AdvertisementProcessingState.PENDING,
                AdvertisementProcessingState.RETRY_PENDING,
            }
            or self.advertisement_job_id != job_id
        ):
            raise InvalidAdvertisementTransitionError
        return replace(
            self,
            advertisement_state=target,
            advertisement_processing_version=self.advertisement_processing_version + 1,
            advertisement_result=result,
            advertisement_failure=None,
        )

    def apply_advertisement_failure(
        self,
        failure: AdvertisementCheckFailure,
        *,
        job_id: str,
        expected_processing_version: int,
    ) -> Self:
        """Apply one approved failure policy without fabricating classification."""
        if type(failure) is not AdvertisementCheckFailure:
            raise InvalidAdvertisementTransitionError
        self._assert_advertisement_transition_context(
            expected_processing_version,
            failure.failed_at,
        )
        target_by_policy = {
            AdvertisementFailurePolicy.CONTINUE_PROCESSING: (
                AdvertisementProcessingState.FAILED_CONTINUE
            ),
            AdvertisementFailurePolicy.STOP_PROCESSING: (
                AdvertisementProcessingState.PROCESSING_STOPPED
            ),
            AdvertisementFailurePolicy.RETRY_LATER: (
                AdvertisementProcessingState.RETRY_PENDING
            ),
            AdvertisementFailurePolicy.MANUAL_REVIEW: (
                AdvertisementProcessingState.MANUAL_REVIEW_REQUIRED
            ),
        }
        target = target_by_policy[failure.policy]
        if (
            self.advertisement_state is target
            and self.advertisement_job_id == job_id
            and self.advertisement_failure == failure
        ):
            return self
        if (
            self.advertisement_state
            not in {
                AdvertisementProcessingState.PENDING,
                AdvertisementProcessingState.RETRY_PENDING,
            }
            or self.advertisement_job_id != job_id
        ):
            raise InvalidAdvertisementTransitionError
        if (
            failure.policy is AdvertisementFailurePolicy.RETRY_LATER
            and failure.next_retry_at is None
        ):
            raise InvalidAdvertisementTransitionError
        return replace(
            self,
            advertisement_state=target,
            advertisement_processing_version=self.advertisement_processing_version + 1,
            advertisement_result=None,
            advertisement_failure=failure,
        )

    def _assert_advertisement_transition_context(
        self,
        expected_processing_version: int,
        occurred_at: datetime,
    ) -> None:
        """Reject stale or lifecycle-ineligible advertisement state updates."""
        if (
            type(expected_processing_version) is not int
            or expected_processing_version != self.advertisement_processing_version
            or self.status is not PostStatus.STORED
            or self.is_expired_at(occurred_at)
        ):
            raise InvalidAdvertisementTransitionError

    def _validate_transition_timing(
        self,
        new_status: PostStatus,
        occurred_at: datetime,
    ) -> None:
        """Enforce receipt ordering and the exact expiration boundary."""
        if new_status is PostStatus.EXPIRED:
            if occurred_at < self.expires_at:
                raise InvalidPostTransitionError
        elif occurred_at >= self.expires_at:
            raise InvalidPostTransitionError

    def _validate_lifecycle_history(self) -> None:
        """Reject malformed rehydrated lifecycle chains and versions."""
        if self.version != len(self.transition_history):
            raise InvalidPostVersionError
        if not self.transition_history:
            if self.status is not PostStatus.DISCOVERED:
                raise PostInvariantError
            return

        expected_previous = PostStatus.DISCOVERED
        previous_time = self.received_at
        for transition in self.transition_history:
            if transition.previous_status is not expected_previous:
                raise PostInvariantError
            if transition.occurred_at < previous_time:
                raise PostInvariantError
            try:
                self._validate_transition_timing(
                    transition.new_status,
                    transition.occurred_at,
                )
            except InvalidPostTransitionError:
                raise PostInvariantError from None
            expected_previous = transition.new_status
            previous_time = transition.occurred_at
        if self.status is not expected_previous:
            raise PostInvariantError

    def _validate_advertisement_state(self) -> None:
        """Validate additive processing state independently from lifecycle history."""
        state = self.advertisement_state
        result = self.advertisement_result
        failure = self.advertisement_failure
        job_id = self.advertisement_job_id
        if state is AdvertisementProcessingState.NOT_REQUESTED:
            if (
                self.advertisement_processing_version != 0
                or job_id is not None
                or result is not None
                or failure is not None
            ):
                raise PostInvariantError
            return
        if job_id is None or self.advertisement_processing_version < 1:
            raise PostInvariantError
        if state is AdvertisementProcessingState.PENDING:
            if result is not None or failure is not None:
                raise PostInvariantError
            return
        if state is AdvertisementProcessingState.RETRY_PENDING:
            if (
                result is not None
                or failure is None
                or failure.policy is not AdvertisementFailurePolicy.RETRY_LATER
                or failure.next_retry_at is None
            ):
                raise PostInvariantError
            return
        if state in {
            AdvertisementProcessingState.PASSED,
            AdvertisementProcessingState.REJECTED_AS_ADVERTISEMENT,
        }:
            if result is None or failure is not None:
                raise PostInvariantError
            if (
                state is AdvertisementProcessingState.PASSED
            ) is result.is_advertisement:
                raise PostInvariantError
            return
        expected_policy = {
            AdvertisementProcessingState.FAILED_CONTINUE: (
                AdvertisementFailurePolicy.CONTINUE_PROCESSING
            ),
            AdvertisementProcessingState.PROCESSING_STOPPED: (
                AdvertisementFailurePolicy.STOP_PROCESSING
            ),
            AdvertisementProcessingState.MANUAL_REVIEW_REQUIRED: (
                AdvertisementFailurePolicy.MANUAL_REVIEW
            ),
        }.get(state)
        if (
            result is not None
            or failure is None
            or failure.policy is not expected_policy
        ):
            raise PostInvariantError

    def _validate_semantic_duplicate_state(self) -> None:
        """Validate additive semantic state and legacy-safe defaults."""
        state = self.semantic_duplicate_state
        result = self.semantic_duplicate_result
        failure = self.semantic_duplicate_failure
        job_id = self.semantic_duplicate_job_id
        if state is SemanticDuplicateState.NOT_REQUESTED:
            if self.semantic_duplicate_version != 0 or any(
                value is not None for value in (job_id, result, failure)
            ):
                raise PostInvariantError
            return
        if job_id is None or self.semantic_duplicate_version < 1:
            raise PostInvariantError
        if state is SemanticDuplicateState.PENDING:
            if result is not None or failure is not None:
                raise PostInvariantError
            return
        if state is SemanticDuplicateState.RETRY_PENDING:
            if result is not None or failure is None or failure.next_retry_at is None:
                raise PostInvariantError
            return
        if state in {
            SemanticDuplicateState.PASSED,
            SemanticDuplicateState.DUPLICATE_REJECTED,
            SemanticDuplicateState.DUPLICATE_MANUAL_REVIEW,
            SemanticDuplicateState.DUPLICATE_ALLOWED,
        }:
            if result is None or failure is not None:
                raise PostInvariantError
            if (state is SemanticDuplicateState.PASSED) is result.is_duplicate:
                raise PostInvariantError
            return
        if result is not None or failure is None:
            raise PostInvariantError

    def _validate_categorization_state(self) -> None:
        """Validate additive categorization state and legacy-safe defaults."""
        state = self.categorization_state
        result = self.categorization_result
        failure = self.categorization_failure
        job_id = self.categorization_job_id
        if state is CategorizationState.NOT_REQUESTED:
            if self.categorization_processing_version != 0 or any(
                value is not None for value in (job_id, result, failure)
            ):
                raise PostInvariantError
            return
        if self.categorization_processing_version < 1:
            raise PostInvariantError
        if state is CategorizationState.PENDING:
            if job_id is None or result is not None or failure is not None:
                raise PostInvariantError
            return
        if state is CategorizationState.RETRY_PENDING:
            if (
                job_id is None
                or result is not None
                or failure is None
                or failure.next_retry_at is None
            ):
                raise PostInvariantError
            return
        if state in {
            CategorizationState.AI_ASSIGNED,
            CategorizationState.KEYWORD_FALLBACK,
            CategorizationState.SOURCE_DEFAULT_FALLBACK,
            CategorizationState.SUPERSEDED_MANUAL,
        }:
            if result is None or failure is not None:
                raise PostInvariantError
            return
        if state is CategorizationState.PROCESSING_STOPPED:
            if job_id is None or result is not None or failure is None:
                raise PostInvariantError
            return

    def enqueue_categorization(self, job_id: str) -> Post:
        """Enter pending categorization state with a claimed job ID."""
        if not _is_bounded_non_blank_string(job_id, _MAX_POST_ID_LENGTH):
            raise PostInvariantError
        state = self.categorization_state
        if state is CategorizationState.PENDING:
            if self.categorization_job_id != job_id:
                raise InvalidPostTransitionError
            return self
        if state not in {
            CategorizationState.NOT_REQUESTED,
            CategorizationState.RETRY_PENDING,
        }:
            raise InvalidPostTransitionError
        return replace(
            self,
            categorization_state=CategorizationState.PENDING,
            categorization_job_id=job_id,
            categorization_processing_version=self.categorization_processing_version
            + 1,
        )

    def apply_categorization_result(
        self,
        result: CategorizationResult,
        job_id: str | None,
        expected_processing_version: int,
    ) -> Post:
        """Persist a categorization result using CAS version check."""
        if type(result) is not CategorizationResult:
            raise PostInvariantError
        if self.categorization_processing_version != expected_processing_version:
            raise PostVersionConflictError(
                expected_processing_version,
                self.categorization_processing_version,
            )

        # Check if manual override exists:
        if (
            self.categorization_result is not None
            and self.categorization_result.method is CategorizationMethod.MANUAL
            and result.method is not CategorizationMethod.MANUAL
        ):
            return self

        state_map = {
            CategorizationMethod.AI: CategorizationState.AI_ASSIGNED,
            CategorizationMethod.KEYWORD: CategorizationState.KEYWORD_FALLBACK,
            CategorizationMethod.SOURCE_DEFAULT: (
                CategorizationState.SOURCE_DEFAULT_FALLBACK
            ),
            CategorizationMethod.MANUAL: CategorizationState.SUPERSEDED_MANUAL,
        }
        target_state = state_map[result.method]

        return replace(
            self,
            categorization_state=target_state,
            categorization_job_id=job_id or self.categorization_job_id,
            categorization_result=result,
            categorization_failure=None,
            categorization_processing_version=expected_processing_version + 1,
        )

    def apply_categorization_failure(
        self,
        failure: CategorizationCheckFailure,
        job_id: str,
        expected_processing_version: int,
    ) -> Post:
        """Persist categorization failure or retry state."""
        if type(failure) is not CategorizationCheckFailure:
            raise PostInvariantError
        if self.categorization_processing_version != expected_processing_version:
            raise PostVersionConflictError(
                expected_processing_version,
                self.categorization_processing_version,
            )

        if failure.policy == "retry_later":
            target_state = CategorizationState.RETRY_PENDING
        else:
            target_state = CategorizationState.PROCESSING_STOPPED

        return replace(
            self,
            categorization_state=target_state,
            categorization_job_id=job_id,
            categorization_failure=failure,
            categorization_processing_version=expected_processing_version + 1,
        )

    @property
    def scoring_is_eligible(self) -> bool:
        """Return whether all required content stages permit delayed scoring."""
        return (
            self.status is PostStatus.STORED
            and self.advertisement_allows_next_stage
            and self.semantic_duplicate_allows_next_stage
            and self.categorization_state
            in {
                CategorizationState.AI_ASSIGNED,
                CategorizationState.KEYWORD_FALLBACK,
                CategorizationState.SOURCE_DEFAULT_FALLBACK,
                CategorizationState.SUPERSEDED_MANUAL,
            }
        )

    def schedule_scoring(
        self,
        *,
        job_id: str,
        due_at: datetime,
        expected_processing_version: int,
    ) -> Post:
        """Persist one durable scoring schedule using processing CAS."""
        if self.scoring_processing_version != expected_processing_version:
            raise PostVersionConflictError(
                expected_processing_version, self.scoring_processing_version
            )
        canonical_due = _canonical_utc(due_at)
        if self.scoring_state is ScoringState.SCHEDULED:
            if self.scoring_job_id == job_id and self.scoring_due_at == canonical_due:
                return self
            raise InvalidPostTransitionError
        if (
            self.scoring_state is not ScoringState.NOT_REQUESTED
            or not self.scoring_is_eligible
            or not _is_bounded_non_blank_string(job_id, _MAX_POST_ID_LENGTH)
        ):
            raise InvalidPostTransitionError
        return replace(
            self,
            scoring_state=ScoringState.SCHEDULED,
            scoring_processing_version=expected_processing_version + 1,
            scoring_job_id=job_id,
            scoring_due_at=canonical_due,
        )

    def mark_scoring_pending(self, *, expected_processing_version: int) -> Post:
        """Mark an already due claimed scoring Job as pending."""
        if (
            self.scoring_processing_version != expected_processing_version
            or self.scoring_state
            not in {ScoringState.SCHEDULED, ScoringState.RETRY_PENDING}
        ):
            raise InvalidPostTransitionError
        return replace(
            self,
            scoring_state=ScoringState.PENDING,
            scoring_failure=None,
            scoring_processing_version=expected_processing_version + 1,
        )

    def apply_scoring_result(
        self,
        result: ScoringResult,
        *,
        job_id: str,
        expected_processing_version: int,
    ) -> Post:
        """Persist the first valid score even after approval or publication."""
        if (
            type(result) is not ScoringResult
            or self.scoring_processing_version != expected_processing_version
            or self.scoring_job_id != job_id
            or self.scoring_state
            not in {
                ScoringState.SCHEDULED,
                ScoringState.PENDING,
                ScoringState.RETRY_PENDING,
            }
        ):
            raise InvalidPostTransitionError
        return replace(
            self,
            scoring_state=ScoringState.COMPLETED,
            scoring_result=result,
            scoring_failure=None,
            scoring_processing_version=expected_processing_version + 1,
        )

    def apply_scoring_failure(
        self,
        failure: ScoringFailure,
        *,
        job_id: str,
        expected_processing_version: int,
    ) -> Post:
        """Persist retry-pending or terminal unavailable scoring state."""
        if (
            type(failure) is not ScoringFailure
            or self.scoring_processing_version != expected_processing_version
            or self.scoring_job_id != job_id
            or self.scoring_state
            not in {
                ScoringState.SCHEDULED,
                ScoringState.PENDING,
                ScoringState.RETRY_PENDING,
            }
        ):
            raise InvalidPostTransitionError
        target = (
            ScoringState.RETRY_PENDING
            if failure.policy is ScoringFailurePolicy.RETRY_LATER
            else ScoringState.UNAVAILABLE
        )
        return replace(
            self,
            scoring_state=target,
            scoring_failure=failure,
            scoring_result=None,
            scoring_processing_version=expected_processing_version + 1,
        )

    def mark_scoring_stale(self, *, expected_processing_version: int) -> Post:
        """Resolve an expired/deleted scoring request without a fabricated result."""
        if (
            self.scoring_processing_version != expected_processing_version
            or self.scoring_state
            not in {
                ScoringState.SCHEDULED,
                ScoringState.PENDING,
                ScoringState.RETRY_PENDING,
            }
        ):
            raise InvalidPostTransitionError
        return replace(
            self,
            scoring_state=ScoringState.STALE_OR_EXPIRED,
            scoring_processing_version=expected_processing_version + 1,
        )

    def _validate_scoring_state(self) -> None:
        """Validate additive scoring state and legacy-safe defaults."""
        state = self.scoring_state
        values = (self.scoring_job_id, self.scoring_due_at)
        if state is ScoringState.NOT_REQUESTED:
            if self.scoring_processing_version != 0 or any(
                value is not None
                for value in (*values, self.scoring_result, self.scoring_failure)
            ):
                raise PostInvariantError
            return
        if self.scoring_processing_version < 1 or any(
            value is None for value in values
        ):
            raise PostInvariantError
        if state in {ScoringState.SCHEDULED, ScoringState.PENDING}:
            if self.scoring_result is not None or self.scoring_failure is not None:
                raise PostInvariantError
        elif state is ScoringState.RETRY_PENDING:
            if (
                self.scoring_result is not None
                or self.scoring_failure is None
                or self.scoring_failure.next_retry_at is None
            ):
                raise PostInvariantError
        elif state is ScoringState.COMPLETED:
            if self.scoring_result is None or self.scoring_failure is not None:
                raise PostInvariantError
        elif state is ScoringState.UNAVAILABLE:
            if self.scoring_result is not None or self.scoring_failure is None:
                raise PostInvariantError
        elif state is ScoringState.STALE_OR_EXPIRED and self.scoring_result is not None:
            raise PostInvariantError


__all__ = [
    "POST_RETENTION_PERIOD",
    "OriginalPostContent",
    "Post",
    "PostId",
    "SourceMessageIdentity",
]
