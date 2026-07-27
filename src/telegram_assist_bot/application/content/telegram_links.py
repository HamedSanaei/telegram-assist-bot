"""Linear-time discovery of Telegram username and link spans."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl

_REFERENCE = re.compile(
    r"(?<![\w@])(?:@(?P<at>[A-Za-z0-9_]{5,32})|(?:(?:https?://)?(?:t\.me|telegram\.me)/)(?P<link>[A-Za-z0-9_]{5,32})(?P<suffix>(?:[/?#][^\s]*)?))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TelegramReferenceSpan:
    """Describe one detected reference without network resolution."""

    start: int
    end: int
    username: str


def telegram_reference_spans(text: str) -> tuple[TelegramReferenceSpan, ...]:
    """Return removable Telegram references while preserving proxy links."""
    return tuple(
        TelegramReferenceSpan(
            match.start(), match.end(), match.group("at") or match.group("link")
        )
        for match in _REFERENCE.finditer(text)
        if not _is_proxy_configuration(match)
    )


def _is_proxy_configuration(match: re.Match[str]) -> bool:
    """Recognize functional Telegram MTProto/SOCKS links, not channel aliases."""
    if match.group("at") is not None:
        return False
    route = (match.group("link") or "").casefold()
    suffix = match.group("suffix") or ""
    if route not in {"proxy", "socks"} or not suffix.startswith("?"):
        return False
    try:
        parameters = {
            key.casefold(): value
            for key, value in parse_qsl(
                suffix[1:], keep_blank_values=True, strict_parsing=False
            )
        }
    except ValueError:
        return False
    required = {"server", "port", "secret"} if route == "proxy" else {"server", "port"}
    return all(parameters.get(name) for name in required)
