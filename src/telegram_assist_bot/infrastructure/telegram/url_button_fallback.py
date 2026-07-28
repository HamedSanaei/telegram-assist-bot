"""Materialize portable source URL buttons for Telegram User API publication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from telegram_assist_bot.domain.posts import TelegramEntity

if TYPE_CHECKING:
    from telegram_assist_bot.domain.posts import TelegramUrlButton

_MESSAGE_TEXT_LIMIT_UTF16: Final[int] = 4096
_MEDIA_CAPTION_LIMIT_UTF16: Final[int] = 1024


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


@dataclass(frozen=True, slots=True)
class MaterializedUrlContent:
    """Hold User API text and explicit clickable URL entities."""

    text: str | None
    entities: tuple[TelegramEntity, ...]


def materialize_url_buttons(
    text: str | None,
    entities: tuple[TelegramEntity, ...],
    keyboard: tuple[tuple[TelegramUrlButton, ...], ...],
    *,
    has_media: bool,
) -> MaterializedUrlContent:
    """Append ordered URL buttons as visible text with clickable URL entities.

    Telegram User API sessions cannot publish reply markup. Converting only the
    already-validated portable URL-button model preserves the destination action
    without interpreting source callback data.
    """
    if not keyboard:
        return MaterializedUrlContent(text, entities)
    parts: list[str] = []
    url_entities: list[TelegramEntity] = []
    offset = 0

    def append(value: str) -> None:
        nonlocal offset
        parts.append(value)
        offset += _utf16_length(value)

    if text:
        append(text)
        append("\n\n")
    for row_index, row in enumerate(keyboard):
        if row_index:
            append("\n\n")
        for button_index, button in enumerate(row):
            if button_index:
                append("\n")
            append(f"{button.label}: ")
            url_offset = offset
            append(button.url)
            url_entities.append(
                TelegramEntity(url_offset, _utf16_length(button.url), "url")
            )
    result = "".join(parts)
    limit = _MEDIA_CAPTION_LIMIT_UTF16 if has_media else _MESSAGE_TEXT_LIMIT_UTF16
    if _utf16_length(result) > limit:
        kind = "caption" if has_media else "message"
        raise ValueError(f"Portable URL fallback exceeds Telegram {kind} limit.")
    return MaterializedUrlContent(result, (*entities, *url_entities))


__all__ = ("MaterializedUrlContent", "materialize_url_buttons")
