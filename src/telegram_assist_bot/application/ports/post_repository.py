"""Application-owned persistence contract for original posts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from telegram_assist_bot.domain.advertisement import AdvertisementProcessingState
from telegram_assist_bot.domain.categories import CategorizationState
from telegram_assist_bot.domain.duplicates import SemanticDuplicateState
from telegram_assist_bot.domain.posts import (
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
)

if TYPE_CHECKING:
    from datetime import datetime

__all__ = (
    "AdvertisementPostRepository",
    "AdvertisementPostUpdateRequest",
    "CategorizationPostRepository",
    "CategorizationPostUpdateRequest",
    "InsertPostOutcome",
    "InsertPostResult",
    "InvalidPostRepositoryRequestError",
    "PostClaimOutcome",
    "PostClaimRequest",
    "PostClaimResult",
    "PostConcurrencyConflictError",
    "PostNotFoundError",
    "PostRepository",
    "PostRepositoryDataError",
    "PostRepositoryError",
    "PostRepositoryUnavailableError",
    "PostTransitionRequest",
    "SemanticDuplicatePostRepository",
    "SemanticDuplicatePostUpdateRequest",
)


class PostRepositoryError(Exception):
    """Base class for safe failures exposed by a post repository."""

    error_category: ClassVar[str] = "permanent"


class PostRepositoryUnavailableError(PostRepositoryError):
    """Report that post persistence is temporarily unavailable."""

    error_category = "transient"

    def __init__(self) -> None:
        """Initialize an adapter-independent availability error."""
        super().__init__("Post persistence is temporarily unavailable.")


class PostRepositoryDataError(PostRepositoryError):
    """Report persisted post data that cannot satisfy the domain contract."""

    def __init__(self) -> None:
        """Initialize an input-safe persisted-data error."""
        super().__init__("Persisted post data is invalid or unsupported.")


class PostNotFoundError(PostRepositoryError):
    """Report that a post targeted by a write no longer exists."""

    def __init__(self) -> None:
        """Initialize an identifier-free not-found error."""
        super().__init__("The requested post does not exist.")


class PostConcurrencyConflictError(PostRepositoryError):
    """Report a stale optimistic-concurrency transition request."""

    error_category = "concurrency_conflict"

    def __init__(self) -> None:
        """Initialize a driver-independent concurrency error."""
        super().__init__("The post changed before the transition was persisted.")


class InvalidPostRepositoryRequestError(PostRepositoryError):
    """Report a malformed application request without retaining raw input."""

    error_category = "validation"

    def __init__(self) -> None:
        """Initialize a safe request-validation error."""
        super().__init__("The post repository request is invalid.")


class InsertPostOutcome(StrEnum):
    """Describe whether an idempotent insert created a new post."""

    CREATED = "Created"
    ALREADY_EXISTS = "AlreadyExists"
    CONFLICT = "Conflict"


@dataclass(frozen=True, slots=True)
class InsertPostResult:
    """Return the deterministic outcome of one idempotent insert attempt."""

    outcome: InsertPostOutcome
    post_id: PostId

    def __post_init__(self) -> None:
        """Require the application-owned outcome enum without coercion."""
        if type(self.outcome) is not InsertPostOutcome:
            raise InvalidPostRepositoryRequestError
        if type(self.post_id) is not PostId:
            raise InvalidPostRepositoryRequestError


class PostClaimOutcome(StrEnum):
    """Describe whether one caller atomically won the next-stage marker."""

    CLAIMED = "Claimed"
    ALREADY_CLAIMED = "AlreadyClaimed"
    CONFLICT = "Conflict"


@dataclass(frozen=True, slots=True)
class PostClaimRequest:
    """Request one durable next-stage marker for a canonical stored post."""

    post_id: PostId
    source_identity: SourceMessageIdentity
    claimed_at: datetime
    correlation_id: str

    def __post_init__(self) -> None:
        """Validate safe application-owned claim inputs."""
        if type(self.post_id) is not PostId:
            raise InvalidPostRepositoryRequestError
        if type(self.source_identity) is not SourceMessageIdentity:
            raise InvalidPostRepositoryRequestError
        if self.claimed_at.tzinfo is None or self.claimed_at.utcoffset() is None:
            raise InvalidPostRepositoryRequestError
        if (
            type(self.correlation_id) is not str
            or not self.correlation_id
            or self.correlation_id.isspace()
            or len(self.correlation_id) > 128
        ):
            raise InvalidPostRepositoryRequestError


@dataclass(frozen=True, slots=True)
class PostClaimResult:
    """Return one canonical post identity and atomic marker outcome."""

    outcome: PostClaimOutcome
    post_id: PostId

    def __post_init__(self) -> None:
        """Require exact application-owned result types."""
        if (
            type(self.outcome) is not PostClaimOutcome
            or type(self.post_id) is not PostId
        ):
            raise InvalidPostRepositoryRequestError


@dataclass(frozen=True, slots=True)
class PostTransitionRequest:
    """Carry one domain-validated snapshot for an atomic persistence transition.

    The domain creates ``post`` before this request reaches persistence. The
    repository only compares the prior version and status atomically and writes
    the supplied next snapshot; it does not duplicate lifecycle policy.
    """

    post: Post
    expected_version: int
    expected_status: PostStatus

    def __post_init__(self) -> None:
        """Reject requests that are not exactly one coherent lifecycle step."""
        if type(self.post) is not Post:
            raise InvalidPostRepositoryRequestError
        if type(self.expected_version) is not int or self.expected_version < 0:
            raise InvalidPostRepositoryRequestError
        if type(self.expected_status) is not PostStatus:
            raise InvalidPostRepositoryRequestError
        if self.post.version != self.expected_version + 1:
            raise InvalidPostRepositoryRequestError
        if not self.post.transition_history:
            raise InvalidPostRepositoryRequestError

        latest_transition = self.post.transition_history[-1]
        if (
            latest_transition.previous_status is not self.expected_status
            or latest_transition.new_status is not self.post.status
        ):
            raise InvalidPostRepositoryRequestError


@dataclass(frozen=True, slots=True)
class AdvertisementPostUpdateRequest:
    """Carry one validated advertisement-processing CAS update."""

    post: Post
    expected_processing_version: int
    expected_processing_state: AdvertisementProcessingState

    def __post_init__(self) -> None:
        """Require exactly one additive processing-state step."""
        if (
            type(self.post) is not Post
            or type(self.expected_processing_version) is not int
            or self.expected_processing_version < 0
            or self.post.advertisement_processing_version
            != self.expected_processing_version + 1
            or type(self.expected_processing_state) is not AdvertisementProcessingState
        ):
            raise InvalidPostRepositoryRequestError


@dataclass(frozen=True, slots=True)
class SemanticDuplicatePostUpdateRequest:
    """Carry one validated semantic-processing CAS update."""

    post: Post
    expected_processing_version: int
    expected_processing_state: SemanticDuplicateState

    def __post_init__(self) -> None:
        """Require one coherent additive semantic transition."""
        if (
            type(self.post) is not Post
            or type(self.expected_processing_version) is not int
            or self.expected_processing_version < 0
            or self.post.semantic_duplicate_version
            != self.expected_processing_version + 1
            or type(self.expected_processing_state) is not SemanticDuplicateState
        ):
            raise InvalidPostRepositoryRequestError


@runtime_checkable
class PostRepository(Protocol):
    """Persist posts without exposing storage-specific objects or exceptions."""

    async def insert_idempotently(self, post: Post) -> InsertPostResult:
        """Insert a post once according to its source-message identity."""
        ...

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        """Return a non-expired post by internal identity at ``as_of``."""
        ...

    async def get_by_source_identity(
        self,
        source_identity: SourceMessageIdentity,
        *,
        as_of: datetime,
    ) -> Post | None:
        """Return a non-expired post by its source idempotency identity."""
        ...

    async def list_unexpired(
        self,
        *,
        as_of: datetime,
        limit: int,
    ) -> tuple[Post, ...]:
        """Return at most ``limit`` non-expired posts in repository order."""
        ...

    async def transition(self, request: PostTransitionRequest) -> Post:
        """Atomically persist and return one domain-validated next snapshot."""
        ...

    async def claim_for_next_stage(self, request: PostClaimRequest) -> PostClaimResult:
        """Atomically create one durable next-stage marker for a stored post."""
        ...


@runtime_checkable
class AdvertisementPostRepository(Protocol):
    """Expose only the Post reads and CAS needed by advertisement detection."""

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        """Return a non-expired canonical Post."""
        ...

    async def update_advertisement(
        self,
        request: AdvertisementPostUpdateRequest,
    ) -> Post:
        """Atomically persist one advertisement result/failure state transition."""
        ...


@runtime_checkable
class SemanticDuplicatePostRepository(Protocol):
    """Expose only canonical reads and semantic CAS writes."""

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        """Return one non-expired canonical Post."""
        ...

    async def update_semantic_duplicate(
        self, request: SemanticDuplicatePostUpdateRequest
    ) -> Post:
        """Atomically persist one semantic result/failure transition."""
        ...


@dataclass(frozen=True, slots=True)
class CategorizationPostUpdateRequest:
    """Carry one validated categorization-processing CAS update."""

    post: Post
    expected_processing_version: int
    expected_processing_state: CategorizationState

    def __post_init__(self) -> None:
        """Require one coherent additive categorization transition."""
        if (
            type(self.post) is not Post
            or type(self.expected_processing_version) is not int
            or self.expected_processing_version < 0
            or self.post.categorization_processing_version
            != self.expected_processing_version + 1
            or type(self.expected_processing_state) is not CategorizationState
        ):
            raise InvalidPostRepositoryRequestError


@runtime_checkable
class CategorizationPostRepository(Protocol):
    """Expose only canonical reads and categorization CAS writes."""

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        """Return one non-expired canonical Post."""
        ...

    async def update_categorization(
        self, request: CategorizationPostUpdateRequest
    ) -> Post:
        """Atomically persist one categorization result/failure transition."""
        ...
