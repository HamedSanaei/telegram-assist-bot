"""Verify application retry behavior through an integration-level boundary."""

from tests.unit.application.publication import test_publication_retry_policy


def test_transient_attempts_are_bounded() -> None:
    """Reuse the deterministic scripted publisher at the integration boundary."""
    test_publication_retry_policy.test_bounded_exponential_retry_stops_at_max_attempts()


def test_ambiguous_attempt_is_not_retried() -> None:
    """Prove an uncertain external outcome remains terminal."""
    test_publication_retry_policy.test_ambiguous_attempt_is_terminal_without_retry()
