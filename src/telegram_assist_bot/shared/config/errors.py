"""Safe exception types for configuration loading and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

type ConfigurationPathSegment = str | int
type ConfigurationPath = tuple[ConfigurationPathSegment, ...]

_SAFE_PATH_SEGMENT: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
)
_REDACTED_FIELD_SEGMENT: Final[str] = "<field>"
_REDACTED_INDEX_SEGMENT: Final[str] = "<index>"


def _sanitize_path_segment(
    segment: ConfigurationPathSegment,
) -> ConfigurationPathSegment:
    """Return a bounded field/index segment safe for an error message."""
    if isinstance(segment, bool):
        return _REDACTED_INDEX_SEGMENT
    if isinstance(segment, int):
        return segment if segment >= 0 else _REDACTED_INDEX_SEGMENT
    if _SAFE_PATH_SEGMENT.fullmatch(segment) is not None:
        return segment
    return _REDACTED_FIELD_SEGMENT


def sanitize_configuration_path(
    path: Iterable[ConfigurationPathSegment],
) -> ConfigurationPath:
    """Sanitize an untrusted validator location without exposing key contents."""
    return tuple(_sanitize_path_segment(segment) for segment in path)


def format_configuration_path(path: ConfigurationPath) -> str:
    """Render a safe validator path using dotted fields and bracketed indices."""
    safe_path = sanitize_configuration_path(path)
    if not safe_path:
        return "<root>"
    rendered = ""
    for segment in safe_path:
        if isinstance(segment, int):
            rendered += f"[{segment}]"
        else:
            separator = "." if rendered else ""
            rendered += f"{separator}{segment}"
    return rendered


@dataclass(frozen=True, slots=True)
class ConfigurationIssue:
    """Describe one immutable, secret-safe configuration validation issue."""

    path: ConfigurationPath
    message: str
    code: str = "invalid_value"

    def __post_init__(self) -> None:
        """Sanitize paths and reject unsafe empty issue metadata."""
        object.__setattr__(self, "path", sanitize_configuration_path(self.path))
        if not self.message or self.message.isspace():
            raise ValueError("configuration issue message must not be blank")
        if _SAFE_PATH_SEGMENT.fullmatch(self.code) is None:
            raise ValueError("configuration issue code must be a safe identifier")

    @property
    def formatted_path(self) -> str:
        """Return the safe human-readable form of the issue path."""
        return format_configuration_path(self.path)


class ConfigurationError(Exception):
    """Base class for expected configuration startup failures."""

    error_category: ClassVar[str] = "configuration"


class ConfigurationFileNotFoundError(ConfigurationError):
    """Report that the requested configuration file does not exist."""

    def __init__(self, path: Path) -> None:
        """Initialize the error without reading or exposing file contents."""
        self.path = path
        super().__init__(f"Configuration file was not found: {path}")


class ConfigurationReadError(ConfigurationError):
    """Report a configuration filesystem read failure without OS details."""

    def __init__(self, path: Path) -> None:
        """Initialize the error with only the attempted local path."""
        self.path = path
        super().__init__(f"Configuration file could not be read: {path}")


class ConfigurationEncodingError(ConfigurationError):
    """Report invalid UTF-8 without displaying undecodable bytes."""

    def __init__(self, path: Path, byte_offset: int) -> None:
        """Initialize the error with a safe numeric failure offset."""
        self.path = path
        self.byte_offset = byte_offset
        super().__init__(
            f"Configuration file contains invalid UTF-8 at byte offset "
            f"{byte_offset}: {path}"
        )


class ConfigurationJsonError(ConfigurationError):
    """Report malformed JSON without echoing source text or parser details."""

    def __init__(self, path: Path, line: int, column: int) -> None:
        """Initialize the error with safe line and column coordinates."""
        self.path = path
        self.line = line
        self.column = column
        super().__init__(
            f"Configuration file contains invalid JSON at line {line}, "
            f"column {column}: {path}"
        )


class ConfigurationRootError(ConfigurationError):
    """Report that the JSON document root is not an object."""

    def __init__(self, path: Path) -> None:
        """Initialize the error without exposing the received root value."""
        self.path = path
        super().__init__(f"Configuration JSON root must be an object: {path}")


class UnsupportedConfigurationSchemaVersionError(ConfigurationError):
    """Report an unsupported schema without echoing the received value."""

    def __init__(self, supported_version: int) -> None:
        """Initialize the error with only the safe supported version."""
        self.supported_version = supported_version
        super().__init__(
            "Configuration schema version is unsupported; "
            f"supported version: {supported_version}"
        )


class ConfigurationValidationError(ConfigurationError):
    """Aggregate all independent typed configuration validation issues."""

    __slots__ = ("_initialized", "_issues")
    _initialized: bool
    _issues: tuple[ConfigurationIssue, ...]

    def __init__(self, issues: Iterable[ConfigurationIssue]) -> None:
        """Store issues as an immutable tuple and build a secret-safe message."""
        immutable_issues = tuple(issues)
        if not immutable_issues:
            raise ValueError("configuration validation requires at least one issue")
        rendered_issues = "\n".join(
            f"- {issue.formatted_path}: {issue.message} ({issue.code})"
            for issue in immutable_issues
        )
        super().__init__(
            f"Configuration validation failed with {len(immutable_issues)} "
            f"issue(s):\n{rendered_issues}"
        )
        super().__setattr__("_issues", immutable_issues)
        super().__setattr__("_initialized", True)

    def __setattr__(self, name: str, value: object) -> None:
        """Prevent mutation after construction, including mutation of ``args``."""
        if getattr(self, "_initialized", False):
            raise AttributeError("configuration validation errors are immutable")
        super().__setattr__(name, value)

    @property
    def issues(self) -> tuple[ConfigurationIssue, ...]:
        """Return the complete immutable validation issue sequence."""
        return self._issues


__all__ = [
    "ConfigurationEncodingError",
    "ConfigurationError",
    "ConfigurationFileNotFoundError",
    "ConfigurationIssue",
    "ConfigurationJsonError",
    "ConfigurationPath",
    "ConfigurationPathSegment",
    "ConfigurationReadError",
    "ConfigurationRootError",
    "ConfigurationValidationError",
    "UnsupportedConfigurationSchemaVersionError",
    "format_configuration_path",
    "sanitize_configuration_path",
]
