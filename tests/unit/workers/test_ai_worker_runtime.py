# mypy: disable-error-code="arg-type,call-arg"
"""Behavioral coverage for the durable AI worker orchestration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.provider_guard import (
    AllProvidersTemporarilyUnavailableError,
)
from telegram_assist_bot.application.ai.schemas import ScoringContext
from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (
    AllProvidersFailedError,
)
from telegram_assist_bot.application.ports import SemanticDuplicateCandidate
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.domain.posts import PostId
from telegram_assist_bot.shared.config import (
    AiTaskFailureAction,
    SemanticDuplicatePolicy,
)
from telegram_assist_bot.workers.ai_worker import AIWorker

NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)


class Clock:
    def utc_now(self) -> datetime:
        return NOW


class Jobs:
    def __init__(self, job: AIJob | None) -> None:
        self.job = job
        self.updated: list[AIJob] = []
        self.enqueued: list[AIJob] = []

    async def claim_next_due(self, **_: object) -> AIJob | None:
        job, self.job = self.job, None
        return job

    async def update(self, job: AIJob) -> AIJob:
        self.updated.append(job)
        return job

    async def enqueue(self, job: AIJob) -> object:
        self.enqueued.append(job)
        return SimpleNamespace()


class Posts:
    def __init__(self, post: object | None) -> None:
        self.post = post

    async def get_by_id(self, *_: object, **__: object) -> object | None:
        return self.post


class Prompts:
    def get_prompt(self, task: AITaskType, *_: object) -> object:
        bodies = {
            AITaskType.ADVERTISEMENT_DETECTION: "{text}",
            AITaskType.SEMANTIC_DUPLICATE: "{text} {compare_text}",
            AITaskType.CATEGORIZATION: "{allowed_categories} {text}",
            AITaskType.SCORING: "{text}",
        }
        return SimpleNamespace(body=bodies[task])


class Handler:
    def __init__(self, score_context: ScoringContext | None = None) -> None:
        self.completed: list[dict[str, object]] = []
        self.failed: list[dict[str, object]] = []
        self.score_context = score_context

    async def complete(self, **kwargs: object) -> None:
        self.completed.append(kwargs)

    async def fail(self, **kwargs: object) -> None:
        self.failed.append(kwargs)

    async def prepare_claimed(self, **_: object) -> ScoringContext | None:
        return self.score_context


class Executor:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, object]] = []

    async def execute(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Logger:
    def __init__(self) -> None:
        self.events: list[str] = []

    def emit(self, *, event_name: str, **_: object) -> None:
        self.events.append(event_name)


class Candidates:
    def __init__(self, values: tuple[SemanticDuplicateCandidate, ...]) -> None:
        self.values = values

    async def list_candidates(
        self, **_: object
    ) -> tuple[SemanticDuplicateCandidate, ...]:
        return self.values


def _job(task: AITaskType) -> AIJob:
    return AIJob.create(
        f"job-{task.value}",
        "post-1",
        task.value,
        "1",
        "1",
        20,
        created_at=NOW,
    ).claim("worker", 300, NOW)


def _post(text: str | None) -> object:
    return SimpleNamespace(
        post_id=PostId("post-1"),
        original_content=SimpleNamespace(text=text, caption=None),
    )


def _config() -> object:
    return SimpleNamespace(
        ai=SimpleNamespace(
            queue=SimpleNamespace(
                lease_duration_seconds=60,
                next_run_delay_seconds=5,
            ),
            failure_policies=(),
        ),
        semantic_duplicate=SimpleNamespace(
            threshold=0.8,
            duplicate_policy=SemanticDuplicatePolicy.MANUAL_REVIEW,
        ),
        categorization=SimpleNamespace(
            categories=(
                SimpleNamespace(category_id="general", active=True),
                SimpleNamespace(category_id="off", active=False),
            )
        ),
    )


def _result(task: AITaskType, result: dict[str, object]) -> AIResult:
    return AIResult(
        success=True,
        task_type=task,
        provider_name="fake",
        model_name="fake",
        result=result,
        prompt_version="1",
        schema_version="1",
        attempt_number=1,
        fallback_count=0,
        created_at=NOW,
    )


def _worker(
    job: AIJob | None,
    *,
    post: object | None,
    executor: Executor,
    handlers: dict[AITaskType, Handler],
    candidates: Candidates | None = None,
) -> tuple[AIWorker, Jobs]:
    jobs = Jobs(job)
    worker = AIWorker(
        "worker",
        cast("Any", jobs),
        cast("Any", Posts(post)),
        cast("Any", executor),
        cast("Any", Prompts()),
        Clock(),
        cast("Any", _config()),
        handlers,
        semantic_candidates=cast("Any", candidates),
        poll_seconds=0.01,
    )
    return worker, jobs


def test_execute_once_idle_missing_post_and_unknown_handler() -> None:
    async def scenario() -> None:
        worker, _ = _worker(None, post=None, executor=Executor([]), handlers={})
        assert not await worker.execute_once()

        missing, missing_jobs = _worker(
            _job(AITaskType.ADVERTISEMENT_DETECTION),
            post=None,
            executor=Executor([]),
            handlers={AITaskType.ADVERTISEMENT_DETECTION: Handler()},
        )
        assert await missing.execute_once()
        assert missing_jobs.updated[-1].status is AIJobStatus.EXPIRED

        unknown, unknown_jobs = _worker(
            _job(AITaskType.CATEGORIZATION),
            post=_post("text"),
            executor=Executor([]),
            handlers={},
        )
        assert await unknown.execute_once()
        assert unknown_jobs.updated[-1].status is AIJobStatus.PENDING

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "task",
    [
        AITaskType.ADVERTISEMENT_DETECTION,
        AITaskType.CATEGORIZATION,
    ],
)
def test_text_tasks_cover_empty_success_and_provider_failure(task: AITaskType) -> None:
    async def scenario() -> None:
        handler = Handler()
        empty, empty_jobs = _worker(
            _job(task),
            post=_post(None),
            executor=Executor([]),
            handlers={task: handler},
        )
        await empty.execute_once()
        assert empty_jobs.updated[-1].status is AIJobStatus.EXPIRED

        success, _ = _worker(
            _job(task),
            post=_post("متن"),
            executor=Executor([object()]),
            handlers={task: handler},
        )
        await success.execute_once()
        assert handler.completed

        failed, _ = _worker(
            _job(task),
            post=_post("متن"),
            executor=Executor(
                [
                    AllProvidersFailedError(
                        AiTaskFailureAction.MANUAL_REVIEW, "synthetic"
                    )
                ]
            ),
            handlers={task: handler},
        )
        await failed.execute_once()
        assert handler.failed

    asyncio.run(scenario())


def test_scoring_skips_stale_and_maps_success_and_failure() -> None:
    async def scenario() -> None:
        stale = Handler(None)
        stale_worker, _ = _worker(
            _job(AITaskType.SCORING),
            post=_post("text"),
            executor=Executor([]),
            handlers={AITaskType.SCORING: stale},
        )
        await stale_worker.execute_once()
        assert not stale.completed

        handler = Handler(ScoringContext(text="score me"))
        success, _ = _worker(
            _job(AITaskType.SCORING),
            post=_post("text"),
            executor=Executor([object()]),
            handlers={AITaskType.SCORING: handler},
        )
        await success.execute_once()
        assert handler.completed

        failed, _ = _worker(
            _job(AITaskType.SCORING),
            post=_post("text"),
            executor=Executor(
                [AllProvidersFailedError(AiTaskFailureAction.RETRY_LATER, "synthetic")]
            ),
            handlers={AITaskType.SCORING: handler},
        )
        await failed.execute_once()
        assert handler.failed

    asyncio.run(scenario())


def test_semantic_duplicate_no_candidates_success_and_failure() -> None:
    async def scenario() -> None:
        handler = Handler()
        no_candidates, jobs = _worker(
            _job(AITaskType.SEMANTIC_DUPLICATE),
            post=_post("متن مشابه"),
            executor=Executor([]),
            handlers={AITaskType.SEMANTIC_DUPLICATE: handler},
            candidates=Candidates(()),
        )
        await no_candidates.execute_once()
        assert jobs.updated[-1].status is AIJobStatus.COMPLETED

        candidate = SemanticDuplicateCandidate(
            PostId("candidate"),
            "متن قدیمی",
            NOW - timedelta(days=1),
            NOW + timedelta(days=13),
        )
        result = _result(
            AITaskType.SEMANTIC_DUPLICATE,
            {
                "is_duplicate": True,
                "similarity": 0.9,
                "confidence": 0.8,
                "reason": "similar",
            },
        )
        success, success_jobs = _worker(
            _job(AITaskType.SEMANTIC_DUPLICATE),
            post=_post("متن مشابه"),
            executor=Executor([result]),
            handlers={AITaskType.SEMANTIC_DUPLICATE: handler},
            candidates=Candidates((candidate,)),
        )
        await success.execute_once()
        assert success_jobs.updated[-1].status is AIJobStatus.COMPLETED
        assert success_jobs.enqueued

        failure, failure_jobs = _worker(
            _job(AITaskType.SEMANTIC_DUPLICATE),
            post=_post("متن مشابه"),
            executor=Executor(
                [
                    AllProvidersFailedError(
                        AiTaskFailureAction.MANUAL_REVIEW, "synthetic"
                    )
                ]
            ),
            handlers={AITaskType.SEMANTIC_DUPLICATE: handler},
            candidates=Candidates((candidate,)),
        )
        await failure.execute_once()
        assert failure_jobs.updated[-1].status in {
            AIJobStatus.WAITING_FOR_RETRY,
            AIJobStatus.ALL_PROVIDERS_FAILED,
        }
        assert handler.failed

    asyncio.run(scenario())


def test_temporary_provider_unavailability_releases_at_next_eligible_time() -> None:
    class ExplodingWorker(AIWorker):
        async def _process_job(self, job: AIJob) -> None:
            del job
            raise AllProvidersTemporarilyUnavailableError(NOW + timedelta(seconds=30))

    async def scenario() -> None:
        base, jobs = _worker(
            _job(AITaskType.CATEGORIZATION),
            post=_post("text"),
            executor=Executor([]),
            handlers={AITaskType.CATEGORIZATION: Handler()},
        )
        worker = ExplodingWorker(
            base.owner,
            base.ai_job_repository,
            base.post_repository,
            base.execute_ai_with_fallback,
            base.prompt_registry,
            base.clock,
            base.config,
            base.task_handlers,
        )
        assert await worker.execute_once()
        assert jobs.updated[-1].status is AIJobStatus.PENDING
        assert jobs.updated[-1].next_run_at == NOW + timedelta(seconds=30)

    asyncio.run(scenario())


def test_worker_loop_logs_idle_cancellation_and_iteration_failure() -> None:
    """The supervisor must delay idle/error iterations and propagate cancellation."""

    class CancelledWorker(AIWorker):
        async def execute_once(self) -> bool:
            raise asyncio.CancelledError

    async def scenario() -> None:
        logger = Logger()
        sleeps = 0

        async def cancel_after_two_sleeps(_: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps == 1:
                raise RuntimeError("synthetic sleeper failure")
            raise asyncio.CancelledError

        base, _ = _worker(
            None,
            post=None,
            executor=Executor([]),
            handlers={},
        )
        base.logger = cast("Any", logger)
        base.sleeper = cancel_after_two_sleeps

        with pytest.raises(asyncio.CancelledError):
            await base.run()

        assert logger.events == ["ai_worker_started", "ai_worker_iteration_failed"]

        cancelled_logger = Logger()
        cancelled = CancelledWorker(
            base.owner,
            base.ai_job_repository,
            base.post_repository,
            base.execute_ai_with_fallback,
            base.prompt_registry,
            base.clock,
            base.config,
            base.task_handlers,
            logger=cast("Any", cancelled_logger),
        )
        with pytest.raises(asyncio.CancelledError):
            await cancelled.run()
        assert cancelled_logger.events == [
            "ai_worker_started",
            "ai_worker_cancelled",
        ]

    asyncio.run(scenario())


def test_claimed_job_cancellation_and_unexpected_failure_release_lease() -> None:
    """Both cancellation and unexpected work failures return a durable lease."""

    class FailingWorker(AIWorker):
        def __init__(
            self, *args: object, error: BaseException, **kwargs: object
        ) -> None:
            super().__init__(*args, **kwargs)
            self.error = error

        async def _process_job(self, job: AIJob) -> None:
            del job
            raise self.error

    async def scenario() -> None:
        for error in (asyncio.CancelledError(), RuntimeError("synthetic")):
            base, jobs = _worker(
                _job(AITaskType.CATEGORIZATION),
                post=_post("text"),
                executor=Executor([]),
                handlers={AITaskType.CATEGORIZATION: Handler()},
            )
            worker = FailingWorker(
                base.owner,
                base.ai_job_repository,
                base.post_repository,
                base.execute_ai_with_fallback,
                base.prompt_registry,
                base.clock,
                base.config,
                base.task_handlers,
                error=error,
            )
            if isinstance(error, asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    await worker.execute_once()
            else:
                assert await worker.execute_once()
            assert jobs.updated[-1].status is AIJobStatus.PENDING

    asyncio.run(scenario())


def test_semantic_duplicate_rejects_missing_dependencies_and_empty_source() -> None:
    """Invalid semantic worker wiring is explicit while blank content expires."""

    async def scenario() -> None:
        handler = Handler()
        empty, empty_jobs = _worker(
            _job(AITaskType.SEMANTIC_DUPLICATE),
            post=_post("   "),
            executor=Executor([]),
            handlers={AITaskType.SEMANTIC_DUPLICATE: handler},
        )
        await empty.execute_once()
        assert empty_jobs.updated[-1].status is AIJobStatus.EXPIRED

        missing, missing_jobs = _worker(
            _job(AITaskType.SEMANTIC_DUPLICATE),
            post=_post("text"),
            executor=Executor([]),
            handlers={AITaskType.SEMANTIC_DUPLICATE: handler},
        )
        await missing.execute_once()
        assert missing_jobs.updated[-1].status is AIJobStatus.PENDING

    asyncio.run(scenario())
