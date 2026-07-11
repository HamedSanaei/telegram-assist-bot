"""Application-owned clock contract for deterministic time boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


class Clock(Protocol):
    """Return the current timezone-aware UTC instant."""

    def utc_now(self) -> datetime:
        """Return one current UTC instant without side effects."""
        ...


__all__ = ("Clock",)
