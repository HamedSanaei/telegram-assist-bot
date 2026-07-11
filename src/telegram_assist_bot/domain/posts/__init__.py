"""Pure domain contracts for original posts and their lifecycle."""

from telegram_assist_bot.domain.posts.entities import TelegramEntity
from telegram_assist_bot.domain.posts.errors import (
    InvalidPostIdentifierError,
    InvalidPostTransitionError,
    InvalidPostVersionError,
    InvalidSourceMessageIdentityError,
    InvalidTelegramEntityError,
    NaiveDatetimeError,
    OriginalContentMutationError,
    PostDomainError,
    PostInvariantError,
    PostVersionConflictError,
)
from telegram_assist_bot.domain.posts.models import (
    POST_RETENTION_PERIOD,
    OriginalPostContent,
    Post,
    PostId,
    SourceMessageIdentity,
)
from telegram_assist_bot.domain.posts.status import (
    ALLOWED_POST_STATUS_TRANSITIONS,
    PostStatus,
    StatusTransition,
    TransitionActorCategory,
    is_post_status_transition_allowed,
)

__all__ = (
    "ALLOWED_POST_STATUS_TRANSITIONS",
    "POST_RETENTION_PERIOD",
    "InvalidPostIdentifierError",
    "InvalidPostTransitionError",
    "InvalidPostVersionError",
    "InvalidSourceMessageIdentityError",
    "InvalidTelegramEntityError",
    "NaiveDatetimeError",
    "OriginalContentMutationError",
    "OriginalPostContent",
    "Post",
    "PostDomainError",
    "PostId",
    "PostInvariantError",
    "PostStatus",
    "PostVersionConflictError",
    "SourceMessageIdentity",
    "StatusTransition",
    "TelegramEntity",
    "TransitionActorCategory",
    "is_post_status_transition_allowed",
)
