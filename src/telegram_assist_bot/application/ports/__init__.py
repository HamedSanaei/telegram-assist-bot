"""Application-owned ports for external system interactions."""

from telegram_assist_bot.application.ports.post_repository import (
    InsertPostOutcome,
    InsertPostResult,
    InvalidPostRepositoryRequestError,
    PostConcurrencyConflictError,
    PostNotFoundError,
    PostRepository,
    PostRepositoryDataError,
    PostRepositoryError,
    PostRepositoryUnavailableError,
    PostTransitionRequest,
)

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
