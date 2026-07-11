"""Recursive, deterministic redaction for structured observability values."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

REDACTION_MARKER: Final[str] = "[REDACTED]"
"""The fixed marker used for every removed secret or unsafe recursive value."""

type RedactedValue = (
    None | bool | int | float | str | list[RedactedValue] | dict[str, RedactedValue]
)

_NORMALIZE_KEY_PATTERN = re.compile(r"[^a-z0-9]+", re.IGNORECASE)
_URL_PATTERN = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s<>\[\]{}\"'؛،]+")
_URL_SECRET_PARAMETER_PATTERN = re.compile(
    r"(?i)[?&#](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|key|"
    r"password|passwd|secret|credential|signature|sig)="
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)\b(authorization|proxy-authorization)\s*([:=])\s*"
    r"[\"']?(?:(?:bearer|basic)\s+)?[^\"'\s,;؛،]+[\"']?"
)
_AUTH_SCHEME_PATTERN = re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]+")
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"\b\d{5,}:[a-z0-9_-]{20,}\b", re.I)
_COOKIE_NAME = r"[!#$%&'*+\-.^_`|~0-9a-z]+"
_COOKIE_VALUE = r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^;\s,؛،]+)"
_COOKIE_PAIR = rf"{_COOKIE_NAME}\s*=\s*{_COOKIE_VALUE}"
_COOKIE_ATTRIBUTE = rf"(?:{_COOKIE_PAIR}|secure|httponly)"
_COOKIE_HEADER_PATTERN = re.compile(
    rf"(?i)\b(cookie|set-cookie)\s*([:=])\s*"
    rf"(?:\"[^\"\r\n]+\"|'[^'\r\n]+'|"
    rf"{_COOKIE_PAIR}(?:\s*;\s*{_COOKIE_ATTRIBUTE})*)"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?(?:key|hash|id)|access[_-]?token|refresh[_-]?token|token|"
    r"password|passwd|secret|credential|session[_-]?(?:id|string|data|path)?|"
    r"phone[_-]?number|mongodb[_-]?uri|database[_-]?url)\s*"
    r"([:=])\s*"
    r"[^\s,;&؛،]+"
)

_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "authorizationheader",
        "authheader",
        "proxyauthorization",
        "proxyauthorizationheader",
        "apikey",
        "xapikey",
        "apihash",
        "apiid",
        "apitoken",
        "accesstoken",
        "refreshtoken",
        "token",
        "bottoken",
        "password",
        "passwd",
        "pwd",
        "secret",
        "clientsecret",
        "credential",
        "credentials",
        "cookie",
        "setcookie",
        "session",
        "sessiondata",
        "sessionstring",
        "sessionpath",
        "phonenumber",
        "mongodburi",
        "databaseurl",
        "privatekey",
    }
)
_CONTENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "text",
        "caption",
        "content",
        "posttext",
        "postcontent",
        "originaltext",
        "originalcontent",
        "telegramtext",
        "telegrammessage",
        "telegramcontent",
        "message",
        "body",
        "rawmessage",
        "rawtext",
        "rawcaption",
        "messagepayload",
        "payload",
        "telegrampayload",
        "messagebody",
    }
)
_SENSITIVE_KEY_SUFFIXES: Final[tuple[str, ...]] = (
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "credentials",
    "apikey",
    "apihash",
    "apiid",
    "authorization",
    "authorizationheader",
    "session",
    "sessionpath",
    "phonenumber",
)


def _normalized_key(value: str) -> str:
    return _NORMALIZE_KEY_PATTERN.sub("", value.casefold())


def _is_sensitive_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return (
        normalized in _SENSITIVE_KEYS
        or normalized in _CONTENT_KEYS
        or normalized.endswith(_SENSITIVE_KEY_SUFFIXES)
    )


class Redactor:
    """Copy JSON-like values while removing secrets and unsafe object details."""

    __slots__ = ("_max_depth", "_secret_values")

    def __init__(
        self,
        *,
        secret_values: Iterable[str] = (),
        max_depth: int = 16,
    ) -> None:
        """Copy injected secret values and configure a bounded recursion depth."""
        if type(max_depth) is not int or not 1 <= max_depth <= 64:
            raise ValueError("Redaction max_depth must be between 1 and 64.")
        copied_values: set[str] = set()
        for value in secret_values:
            if type(value) is not str or not value:
                raise ValueError("Redaction secret values must be non-empty strings.")
            copied_values.add(value)
        self._secret_values = tuple(
            sorted(copied_values, key=lambda item: (-len(item), item))
        )
        self._max_depth = max_depth

    def __repr__(self) -> str:
        """Describe policy shape without exposing injected secret values."""
        return (
            f"{type(self).__name__}(secret_values={REDACTION_MARKER}, "
            f"max_depth={self._max_depth})"
        )

    def redact(self, value: object) -> RedactedValue:
        """Return a detached, recursively redacted JSON-compatible value."""
        return self._redact(value, depth=0, active_containers=set())

    def _redact_string(self, value: str) -> str:
        def redact_url(match: re.Match[str]) -> str:
            url = match.group(0)
            remainder = url.split("://", 1)[1]
            authority = re.split(r"[/\?#]", remainder, maxsplit=1)[0]
            path = remainder[len(authority) :]
            if (
                "@" in authority
                or _URL_SECRET_PARAMETER_PATTERN.search(url)
                or (
                    authority.casefold() == "api.telegram.org"
                    and path.casefold().startswith("/bot")
                )
            ):
                return REDACTION_MARKER
            return url

        redacted = _URL_PATTERN.sub(redact_url, value)
        for secret in self._secret_values:
            redacted = redacted.replace(secret, REDACTION_MARKER)
        redacted = _AUTHORIZATION_PATTERN.sub(
            lambda match: f"{match.group(1)}{match.group(2)} {REDACTION_MARKER}",
            redacted,
        )
        redacted = _AUTH_SCHEME_PATTERN.sub(
            lambda match: f"{match.group(1)} {REDACTION_MARKER}",
            redacted,
        )
        redacted = _TELEGRAM_BOT_TOKEN_PATTERN.sub(REDACTION_MARKER, redacted)
        redacted = _COOKIE_HEADER_PATTERN.sub(
            lambda match: f"{match.group(1)}{match.group(2)} {REDACTION_MARKER}",
            redacted,
        )
        return _SECRET_ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{REDACTION_MARKER}",
            redacted,
        )

    def _redact(
        self,
        value: object,
        *,
        depth: int,
        active_containers: set[int],
    ) -> RedactedValue:
        if depth >= self._max_depth:
            return REDACTION_MARKER
        if value is None or type(value) is bool or type(value) is int:
            return value
        if type(value) is float:
            return value if math.isfinite(value) else None
        if type(value) is str:
            return self._redact_string(value)
        if isinstance(value, BaseException):
            return {
                "type": type(value).__name__,
                "message": self._redact_string(str(value)),
            }
        if isinstance(value, Mapping):
            return self._redact_mapping(
                value,
                depth=depth,
                active_containers=active_containers,
            )
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return self._redact_sequence(
                value,
                depth=depth,
                active_containers=active_containers,
            )
        return f"<{type(value).__name__}>"

    def _redact_mapping(
        self,
        value: Mapping[object, object],
        *,
        depth: int,
        active_containers: set[int],
    ) -> RedactedValue:
        identity = id(value)
        if identity in active_containers:
            return REDACTION_MARKER
        active_containers.add(identity)
        try:
            result: dict[str, RedactedValue] = {}
            for raw_key, item in value.items():
                key = (
                    self._redact_string(raw_key)
                    if type(raw_key) is str
                    else f"<{type(raw_key).__name__}>"
                )
                result[key] = (
                    REDACTION_MARKER
                    if type(raw_key) is str and _is_sensitive_key(raw_key)
                    else self._redact(
                        item,
                        depth=depth + 1,
                        active_containers=active_containers,
                    )
                )
            return result
        finally:
            active_containers.remove(identity)

    def _redact_sequence(
        self,
        value: Sequence[object],
        *,
        depth: int,
        active_containers: set[int],
    ) -> RedactedValue:
        identity = id(value)
        if identity in active_containers:
            return REDACTION_MARKER
        active_containers.add(identity)
        try:
            return [
                self._redact(
                    item,
                    depth=depth + 1,
                    active_containers=active_containers,
                )
                for item in value
            ]
        finally:
            active_containers.remove(identity)


__all__ = ("REDACTION_MARKER", "RedactedValue", "Redactor")
