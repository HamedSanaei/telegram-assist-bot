"""Provider-independent canonical AI task taxonomy."""

from enum import StrEnum


class AITaskType(StrEnum):
    """Supported canonical AI task types for the isolated AI pipeline."""

    ADVERTISEMENT_DETECTION = "advertisement_detection"
    SEMANTIC_DUPLICATE = "semantic_duplicate"
    CATEGORIZATION = "categorization"
    SCORING = "scoring"


__all__ = ("AITaskType",)
