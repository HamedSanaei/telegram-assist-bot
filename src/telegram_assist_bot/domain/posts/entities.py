"""SDK-independent representations of Telegram text entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .errors import InvalidTelegramEntityError

_CUSTOM_EMOJI_ENTITY_TYPE: Final[str] = "custom_emoji"
_TEXT_URL_ENTITY_TYPE: Final[str] = "text_url"
_MAX_ENTITY_TYPE_LENGTH: Final[int] = 64
_MAX_CUSTOM_EMOJI_ID_LENGTH: Final[int] = 256
_MAX_TEXT_URL_LENGTH: Final[int] = 4096


def _is_strict_integer(value: object) -> bool:
    """Return whether a value is an integer but not a boolean."""
    return type(value) is int


def _is_bounded_non_blank_string(value: object, *, maximum_length: int) -> bool:
    """Return whether text is non-blank and within its unchanged size limit."""
    return (
        type(value) is str
        and bool(value)
        and not value.isspace()
        and len(value) <= maximum_length
    )


@dataclass(frozen=True, slots=True)
class TelegramEntity:
    """Represent one original Telegram entity without an SDK dependency.

    ``offset_utf16`` and ``length_utf16`` are measured in UTF-16 code units.
    Adapters are responsible for converting provider-specific entity objects to
    this representation. Entity ordering, overlap, and source-text bounds are
    intentionally preserved for validation at the appropriate boundary.

    Attributes:
        offset_utf16: Zero-based entity offset in UTF-16 code units.
        length_utf16: Positive entity length in UTF-16 code units.
        entity_type: Exact non-blank Telegram entity type identifier.
        custom_emoji_id: Stable identifier required only for custom emoji.
        url: Optional target metadata used only by text URL entities.
    """

    offset_utf16: int
    length_utf16: int
    entity_type: str
    custom_emoji_id: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        """Validate scalar shape without normalizing source entity data."""
        if not _is_strict_integer(self.offset_utf16) or self.offset_utf16 < 0:
            raise InvalidTelegramEntityError(
                "offset_utf16",
                "must_be_non_negative_strict_integer",
            )
        if not _is_strict_integer(self.length_utf16) or self.length_utf16 <= 0:
            raise InvalidTelegramEntityError(
                "length_utf16",
                "must_be_positive_strict_integer",
            )
        if not _is_bounded_non_blank_string(
            self.entity_type,
            maximum_length=_MAX_ENTITY_TYPE_LENGTH,
        ):
            raise InvalidTelegramEntityError(
                "entity_type",
                "must_be_non_blank_string_at_most_64_characters",
            )

        has_valid_custom_emoji_id = _is_bounded_non_blank_string(
            self.custom_emoji_id,
            maximum_length=_MAX_CUSTOM_EMOJI_ID_LENGTH,
        )
        if self.entity_type == _CUSTOM_EMOJI_ENTITY_TYPE:
            if not has_valid_custom_emoji_id:
                raise InvalidTelegramEntityError(
                    "custom_emoji_id",
                    "required_non_blank_string_at_most_256_characters",
                )
        elif self.custom_emoji_id is not None:
            raise InvalidTelegramEntityError(
                "custom_emoji_id",
                "allowed_only_for_custom_emoji",
            )

        has_valid_url = _is_bounded_non_blank_string(
            self.url,
            maximum_length=_MAX_TEXT_URL_LENGTH,
        )
        if self.entity_type == _TEXT_URL_ENTITY_TYPE:
            if self.url is not None and not has_valid_url:
                raise InvalidTelegramEntityError(
                    "url",
                    "must_be_non_blank_string_at_most_4096_characters",
                )
        elif self.url is not None:
            raise InvalidTelegramEntityError(
                "url",
                "allowed_only_for_text_url",
            )


__all__ = ["TelegramEntity"]
