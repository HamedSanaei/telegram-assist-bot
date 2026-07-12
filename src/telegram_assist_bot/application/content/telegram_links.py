"""Linear-time discovery of Telegram username and link spans."""

from __future__ import annotations

import re
from dataclasses import dataclass

_REFERENCE = re.compile(
    r"(?<![\w@])(?:@(?P<at>[A-Za-z0-9_]{5,32})|(?:(?:https?://)?(?:t\.me|telegram\.me)/)(?P<link>[A-Za-z0-9_]{5,32})(?:/[^\s]*)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TelegramReferenceSpan:
    """Describe one detected reference without network resolution."""

    start: int
    end: int
    username: str


def telegram_reference_spans(text: str) -> tuple[TelegramReferenceSpan, ...]:
    """Return bounded-regex Telegram references in source order."""
    return tuple(
        TelegramReferenceSpan(
            match.start(), match.end(), match.group("at") or match.group("link")
        )
        for match in _REFERENCE.finditer(text)
    )
