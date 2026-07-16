"""UTF-16-aware span editing and Telegram entity rebasing."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from telegram_assist_bot.domain.posts import TelegramEntity


@dataclass(frozen=True, slots=True)
class TextEdit:
    """Replace one Python-character span with text."""

    start: int
    end: int
    replacement: str


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _python_index(value: str, offset: int) -> int:
    consumed = 0
    for index, character in enumerate(value):
        if consumed == offset:
            return index
        consumed += _utf16_length(character)
        if consumed > offset:
            raise ValueError("Entity offset splits a UTF-16 surrogate pair.")
    if consumed == offset:
        return len(value)
    raise ValueError("Entity offset is outside text.")


def apply_edits(
    text: str, entities: tuple[TelegramEntity, ...], edits: tuple[TextEdit, ...]
) -> tuple[str, tuple[TelegramEntity, ...]]:
    """Apply non-overlapping edits and preserve only non-intersecting entities."""
    ordered = tuple(sorted(edits, key=lambda item: item.start))
    if any(
        edit.start < 0 or edit.end < edit.start or edit.end > len(text)
        for edit in ordered
    ):
        raise ValueError("Text edit is outside text.")
    if any(left.end > right.start for left, right in itertools.pairwise(ordered)):
        raise ValueError("Text edits overlap.")
    pieces: list[str] = []
    cursor = 0
    for edit in ordered:
        pieces.extend((text[cursor : edit.start], edit.replacement))
        cursor = edit.end
    pieces.append(text[cursor:])
    transformed = "".join(pieces)
    rebased: list[TelegramEntity] = []
    for entity in entities:
        start = _python_index(text, entity.offset_utf16)
        end = _python_index(text, entity.offset_utf16 + entity.length_utf16)
        if any(start < edit.end and end > edit.start for edit in ordered):
            continue
        shift = sum(
            len(edit.replacement) - (edit.end - edit.start)
            for edit in ordered
            if edit.end <= start
        )
        new_start = start + shift
        new_end = end + shift
        rebased.append(
            TelegramEntity(
                offset_utf16=_utf16_length(transformed[:new_start]),
                length_utf16=_utf16_length(transformed[new_start:new_end]),
                entity_type=entity.entity_type,
                custom_emoji_id=entity.custom_emoji_id,
                url=entity.url,
            )
        )
    return transformed, tuple(rebased)
