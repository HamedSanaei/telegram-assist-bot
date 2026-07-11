"""Immutable retry and external-operation timeout policies."""

from __future__ import annotations

import math
from dataclasses import dataclass

_MAX_ATTEMPTS = 10


def _strict_integer(value: object, *, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    return value


def _finite_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class ExternalOperationPolicy:
    """Require an explicit positive timeout for an external adapter operation."""

    timeout_seconds: float

    def __post_init__(self) -> None:
        """Reject absent, non-finite, or non-positive timeout values."""
        timeout = _finite_number(self.timeout_seconds, name="timeout_seconds")
        if timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        object.__setattr__(self, "timeout_seconds", timeout)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Define bounded attempts, exponential backoff, a cap, and jitter."""

    max_attempts: int
    initial_delay_seconds: float
    max_delay_seconds: float
    backoff_multiplier: float = 2.0
    jitter_ratio: float = 0.0

    def __post_init__(self) -> None:
        """Validate strict finite policy inputs and the hard attempt bound."""
        max_attempts = _strict_integer(self.max_attempts, name="max_attempts")
        if not 1 <= max_attempts <= _MAX_ATTEMPTS:
            raise ValueError(f"max_attempts must be between 1 and {_MAX_ATTEMPTS}")

        initial_delay = _finite_number(
            self.initial_delay_seconds, name="initial_delay_seconds"
        )
        maximum_delay = _finite_number(self.max_delay_seconds, name="max_delay_seconds")
        multiplier = _finite_number(self.backoff_multiplier, name="backoff_multiplier")
        jitter_ratio = _finite_number(self.jitter_ratio, name="jitter_ratio")
        if initial_delay < 0 or maximum_delay < 0:
            raise ValueError("retry delays must not be negative")
        if multiplier < 1:
            raise ValueError("backoff_multiplier must be at least one")
        if not 0 <= jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between zero and one")

        object.__setattr__(self, "initial_delay_seconds", initial_delay)
        object.__setattr__(self, "max_delay_seconds", maximum_delay)
        object.__setattr__(self, "backoff_multiplier", multiplier)
        object.__setattr__(self, "jitter_ratio", jitter_ratio)

    def delay_for_retry(self, retry_number: int, *, random_value: float) -> float:
        """Calculate one capped delay; retry numbers start at one."""
        retry_number = _strict_integer(retry_number, name="retry_number")
        if not 1 <= retry_number < self.max_attempts:
            raise ValueError("retry_number is outside this policy's retry range")
        random_value = _finite_number(random_value, name="random_value")
        if not 0 <= random_value <= 1:
            raise ValueError("random_value must be between zero and one")

        try:
            exponential = self.initial_delay_seconds * (
                self.backoff_multiplier ** (retry_number - 1)
            )
        except OverflowError:
            exponential = self.max_delay_seconds
        base_delay = min(exponential, self.max_delay_seconds)
        jitter_multiplier = (
            1 - self.jitter_ratio + (2 * self.jitter_ratio * random_value)
        )
        return min(base_delay * jitter_multiplier, self.max_delay_seconds)


__all__ = ("ExternalOperationPolicy", "RetryPolicy")
