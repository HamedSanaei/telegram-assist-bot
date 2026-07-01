"""Unit tests for text normalization, hashing, and UTF-8 safety."""

from __future__ import annotations

from src.domain.services.text_normalizer import content_hash, normalize_for_hash


class TestNormalizeForHash:
    """Tests for :func:`normalize_for_hash`."""

    def test_collapses_whitespace(self) -> None:
        assert normalize_for_hash("سلام   دنیا") == "سلام دنیا"

    def test_preserves_persian_letters(self) -> None:
        assert normalize_for_hash("متن کاربر") == "متن کاربر"

    def test_removes_zero_width_characters(self) -> None:
        with_zwnj = "می‌شود"
        assert normalize_for_hash(with_zwnj) == "میشود"

    def test_lowercases_latin(self) -> None:
        assert normalize_for_hash("Breaking NEWS") == "breaking news"


class TestContentHash:
    """Tests for :func:`content_hash`."""

    def test_stable_across_whitespace_variants(self) -> None:
        assert content_hash("سلام  دنیا") == content_hash("سلام دنیا\n")

    def test_differs_for_different_text(self) -> None:
        assert content_hash("خبر اول") != content_hash("خبر دوم")

    def test_no_mojibake_roundtrip(self, tmp_path) -> None:
        """Persian text written and read as UTF-8 hashes identically."""
        text = "سلام متن کاربر"
        file = tmp_path / "message.txt"
        file.write_text(text, encoding="utf-8")
        restored = file.read_text(encoding="utf-8")
        assert restored == text
        assert content_hash(restored) == content_hash(text)
