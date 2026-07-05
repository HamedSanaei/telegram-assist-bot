"""Cheap local text similarity helpers for AI-token reduction.

These helpers are deterministic and dependency-free. They are not a
replacement for semantic AI duplicate detection; they only reduce the
candidate set sent to AI and catch near-identical reposts locally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.domain.services.text_normalizer import normalize_for_hash

_LINK_RE = re.compile(r"https?://\S+|t\.me/\S+|telegram\.me/\S+", re.IGNORECASE)
_MENTION_RE = re.compile(r"@\w+")
_TOKEN_RE = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)


@dataclass(frozen=True)
class SimilarTextCandidate:
    """
    Local similarity result for one stored post text.

    Attributes:
        text: Original existing text.
        score: Jaccard similarity over normalized word shingles.
    """

    text: str
    score: float


def normalized_content_text(text: str) -> str:
    """
    Normalize text for cheap content comparison.

    Args:
        text: Raw Telegram post text.

    Returns:
        A normalized string with common channel/footer noise removed.
    """
    clean = normalize_for_hash(text)
    clean = _LINK_RE.sub(" ", clean)
    clean = _MENTION_RE.sub(" ", clean)
    return " ".join(_TOKEN_RE.findall(clean))


def similarity_score(left: str, right: str) -> float:
    """
    Return a local similarity score between two post texts.

    Args:
        left: First raw text.
        right: Second raw text.

    Returns:
        Float in the ``0.0`` to ``1.0`` range.
    """
    left_features = _features(left)
    right_features = _features(right)
    if not left_features or not right_features:
        return 0.0
    overlap = len(left_features & right_features)
    union = len(left_features | right_features)
    return overlap / union if union else 0.0


def rank_similar_texts(
    new_text: str,
    existing_texts: list[str],
    limit: int = 5,
    minimum_score: float = 0.28,
) -> list[SimilarTextCandidate]:
    """
    Rank existing texts by cheap local similarity.

    Args:
        new_text: New post text.
        existing_texts: Stored post texts from recent source posts.
        limit: Maximum number of candidates to return.
        minimum_score: Minimum local score required to keep a candidate.

    Returns:
        Similar candidates sorted by descending score.
    """
    ranked = [
        SimilarTextCandidate(text=text, score=similarity_score(new_text, text))
        for text in existing_texts
        if text.strip()
    ]
    return [
        candidate
        for candidate in sorted(ranked, key=lambda item: item.score, reverse=True)
        if candidate.score >= minimum_score
    ][:limit]


def _features(text: str) -> set[str]:
    """Return unigram and bigram features for one normalized text."""
    tokens = normalized_content_text(text).split()
    if not tokens:
        return set()
    features = set(tokens)
    features.update(
        f"{tokens[index]} {tokens[index + 1]}"
        for index in range(len(tokens) - 1)
    )
    return features
