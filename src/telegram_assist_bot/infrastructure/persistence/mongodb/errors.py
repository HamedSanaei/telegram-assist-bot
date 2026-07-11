"""Redacted infrastructure errors for MongoDB persistence boundaries."""

from __future__ import annotations


class MongoPersistenceError(RuntimeError):
    """Base class for safe MongoDB adapter failures."""


class MongoConnectionError(MongoPersistenceError):
    """Report an unavailable or unsupported MongoDB deployment safely."""

    def __init__(self) -> None:
        """Initialize a fixed message that cannot contain a connection URI."""
        super().__init__("MongoDB connection verification failed.")


class MongoIndexInitializationError(MongoPersistenceError):
    """Report missing, incompatible, or unavailable MongoDB indexes safely."""

    def __init__(self) -> None:
        """Initialize a fixed message without server or document details."""
        super().__init__("MongoDB post index initialization failed.")


class InvalidPostDocumentError(MongoPersistenceError):
    """Report an invalid versioned post document without retaining its values."""

    def __init__(self, rule: str = "invalid_document") -> None:
        """Retain only an allowlisted code-owned validation rule."""
        safe_rules = {
            "invalid_document",
            "invalid_expiration",
            "invalid_schema_version",
            "invalid_timestamp",
            "missing_field",
        }
        self.rule = rule if rule in safe_rules else "invalid_document"
        super().__init__(f"MongoDB post document violates {self.rule!r}.")


__all__ = (
    "InvalidPostDocumentError",
    "MongoConnectionError",
    "MongoIndexInitializationError",
    "MongoPersistenceError",
)
