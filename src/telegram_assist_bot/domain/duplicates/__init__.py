"""Public exact-duplicate domain contracts."""

from .models import (
    SEMANTIC_DUPLICATE_MANUAL_REVIEW_REASON,
    DuplicateCheckResult,
    InvalidSemanticDuplicateTransitionError,
    SemanticDuplicateFailure,
    SemanticDuplicateFailurePolicy,
    SemanticDuplicatePolicy,
    SemanticDuplicateResult,
    SemanticDuplicateState,
)

__all__ = (
    "SEMANTIC_DUPLICATE_MANUAL_REVIEW_REASON",
    "DuplicateCheckResult",
    "InvalidSemanticDuplicateTransitionError",
    "SemanticDuplicateFailure",
    "SemanticDuplicateFailurePolicy",
    "SemanticDuplicatePolicy",
    "SemanticDuplicateResult",
    "SemanticDuplicateState",
)
