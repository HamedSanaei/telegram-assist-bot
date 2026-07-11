"""Application-owned persistence contract for original posts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from telegram_assist_bot.domain.posts import (
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
)

if TYPE_CHECKING:
    from datetime import datetime

__all__ = (
    "InsertPostOutcome",
    "InsertPostResult",
    "InvalidPostRepositoryRequestError",
    "PostConcurrencyConflictError",
    "PostNotFoundError",
    "PostRepository",
    "PostRepositoryDataError",
    "PostRepositoryError",
    "PostRepositoryUnavailableError",
    "PostTransitionRequest",
)


class PostRepositoryError(Exception):
    """Base class for safe failures exposed by a post repository."""


class PostRepositoryUnavailableError(PostRepositoryError):
    """Report that post persistence is temporarily unavailable."""

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

    def __init__(self) -> None:
        """Initialize a driver-independent concurrency error."""
        super().__init__("The post changed before the transition was persisted.")


class InvalidPostRepositoryRequestError(PostRepositoryError):
    """Report a malformed application request without retaining raw input."""

    def __init__(self) -> None:
        """Initialize a safe request-validation error."""
        super().__init__("The post repository request is invalid.")


class InsertPostOutcome(StrEnum):
    """Describe whether an idempotent insert created a new post."""

    CREATED = "Created"
    ALREADY_EXISTS = "AlreadyExists"


@dataclass(frozen=True, slots=True)
class InsertPostResult:
    """Return the deterministic outcome of one idempotent insert attempt."""

    outcome: InsertPostOutcome

    def __post_init__(self) -> None:
        """Require the application-owned outcome enum without coercion."""
        if type(self.outcome) is not InsertPostOutcome:
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
