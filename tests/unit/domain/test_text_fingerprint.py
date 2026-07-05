"""Unit tests for cheap local text similarity helpers."""

from __future__ import annotations

from src.domain.services.text_fingerprint import rank_similar_texts, similarity_score


class TestTextFingerprint:
    """Tests for local duplicate-candidate filtering."""

    def test_near_identical_repost_scores_high(self) -> None:
        score = similarity_score(
            "خبر فوری درباره بازار ارز @source https://t.me/source",
            "خبر فوری درباره بازار ارز @other",
        )
        assert score >= 0.9

    def test_unrelated_texts_are_not_candidates(self) -> None:
        candidates = rank_similar_texts(
            "خبر فوری درباره بازار ارز",
            ["آموزش نصب برنامه روی ویندوز"],
        )
        assert candidates == []
