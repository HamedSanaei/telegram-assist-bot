"""Safe domain exceptions for post identity, content, and lifecycle rules."""

from __future__ import annotations

from typing import Final

__all__ = [
    "InvalidPostIdentifierError",
    "InvalidPostTransitionError",
    "InvalidPostVersionError",
    "InvalidSourceMessageIdentityError",
    "InvalidTelegramEntityError",
    "NaiveDatetimeError",
    "OriginalContentMutationError",
    "PostDomainError",
    "PostInvariantError",
    "PostVersionConflictError",
]

_SAFE_ENTITY_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "custom_emoji_id",
        "entity_type",
        "length_utf16",
        "offset_utf16",
    }
)
_SAFE_ENTITY_RULES: Final[frozenset[str]] = frozenset(
    {
        "allowed_only_for_custom_emoji",
        "must_be_non_blank_string_at_most_64_characters",
        "must_be_non_negative_strict_integer",
        "must_be_positive_strict_integer",
        "required_non_blank_string_at_most_256_characters",
    }
)


class PostDomainError(Exception):
    """Base class for expected violations of post domain rules."""


class PostInvariantError(PostDomainError):
    """Report an inconsistent post value without retaining its raw input."""

    def __init__(self) -> None:
        """Initialize an input-safe invariant error."""
        super().__init__("Post data violates a domain invariant.")


class InvalidPostIdentifierError(PostInvariantError):
    """Report an invalid internal post identifier."""

    def __init__(self) -> None:
        """Initialize an input-safe identifier error."""
        PostDomainError.__init__(self, "The internal post identifier is invalid.")


class InvalidSourceMessageIdentityError(PostInvariantError):
    """Report an invalid source-channel and source-message identity."""

    def __init__(self) -> None:
        """Initialize an input-safe source identity error."""
        PostDomainError.__init__(self, "The source message identity is invalid.")


class InvalidTelegramEntityError(PostInvariantError):
    """Report a Telegram entity rule failure without retaining entity content."""

    def __init__(self, field_name: str, rule: str) -> None:
        """Initialize the error from allowlisted, code-owned diagnostic labels."""
        safe_field_name = (
            field_name if field_name in _SAFE_ENTITY_FIELD_NAMES else "entity"
        )
        safe_rule = rule if rule in _SAFE_ENTITY_RULES else "invalid_value"
        self.field_name: str = safe_field_name
        self.rule: str = safe_rule
        PostDomainError.__init__(
            self,
            f"Telegram entity field {safe_field_name!r} violates {safe_rule!r}.",
        )


class NaiveDatetimeError(PostInvariantError):
    """Report a datetime that has no usable UTC offset."""

    def __init__(self) -> None:
        """Initialize an input-safe timezone-awareness error."""
        PostDomainError.__init__(self, "Post datetimes must be timezone-aware.")


class InvalidPostTransitionError(PostDomainError):
    """Report a transition not permitted by the post lifecycle."""

    def __init__(self) -> None:
        """Initialize an input-safe transition error."""
        super().__init__("The requested post status transition is not allowed.")


class InvalidPostVersionError(PostInvariantError):
    """Report a malformed optimistic-concurrency version."""

    def __init__(self) -> None:
        """Initialize an input-safe version error."""
        PostDomainError.__init__(self, "The post version is invalid.")


class PostVersionConflictError(PostDomainError):
    """Report a safe optimistic-concurrency mismatch."""

    def __init__(self, expected_version: int, current_version: int) -> None:
        """Retain only the non-sensitive numeric versions involved in the conflict."""
        self.expected_version: int = expected_version
        self.current_version: int = current_version
        super().__init__("The post version does not match the expected version.")


class OriginalContentMutationError(PostDomainError):
    """Report an attempt to replace immutable original Telegram content."""

    def __init__(self) -> None:
        """Initialize an input-safe original-content mutation error."""
        super().__init__("Original post content cannot be changed.")
