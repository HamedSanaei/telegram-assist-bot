"""Unit tests for scheduler login prompting."""

from __future__ import annotations

import builtins

from src.main import _prompt_scheduler_phone


def test_prompt_scheduler_phone_retries_until_non_empty(
    monkeypatch: object,
) -> None:
    """
    The first native scheduler login must collect a phone number.

    Empty input is rejected so Telethon never receives ``None`` and raises
    ``ValueError: No phone number or bot token provided`` again.
    """
    answers = iter(["", "+989121234567"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))
    monkeypatch.setattr(builtins, "print", lambda _message: None)

    assert _prompt_scheduler_phone() == "+989121234567"
