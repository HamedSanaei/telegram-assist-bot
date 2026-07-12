"""Composition root and lifecycle for the persistent publication worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from telegram_assist_bot.application.publication import (
    PublishImmediately,
    PublishRequest,
)
from telegram_assist_bot.application.scheduling import RunDuePublication
from telegram_assist_bot.bootstrap.runtime import (
    FoundationExitCode,
    FoundationStartupError,
    create_foundation_application,
)
from telegram_assist_bot.bootstrap.telegram_validation import validate_telegram_startup
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoPublicationPayloadLoader,
    MongoPublicationRepository,
    MongoScheduleRepository,
    initialize_publication_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
    initialize_content_preparation_indexes,
)
from telegram_assist_bot.infrastructure.telegram.user import TelethonSessionAdapter
from telegram_assist_bot.infrastructure.telegram.user_publisher import (
    TelethonPublisherClient,
    TelethonPublisherGateway,
)
from telegram_assist_bot.workers import ScheduledPublicationWorker

if TYPE_CHECKING:
    from collections.abc import Mapping

    from telegram_assist_bot.application.ports import PublicationPayloadLoader
    from telegram_assist_bot.bootstrap.runtime import FoundationApplication
    from telegram_assist_bot.shared.observability import EventSink


class ScheduleWorkerStartupError(RuntimeError):
    """Report a safe operational worker startup failure."""

    def __init__(self, *, cause: BaseException | None = None) -> None:
        """Retain only the exception chain, never provider text."""
        super().__init__("Scheduled publication worker could not be started.")
        if cause is not None:
            self.__cause__ = cause


class ScheduleWorkerApplication:
    """Own foundation, User API session, repositories, and one worker loop."""

    def __init__(self, foundation: FoundationApplication) -> None:
        """Create an inert lifecycle without connections or session access."""
        self._foundation = foundation
        self._session: TelethonSessionAdapter | None = None
        self._worker: ScheduledPublicationWorker | None = None
        self._started = False
        self._closed = False

    async def start(
        self, configuration_path: Path, *, environ: Mapping[str, str]
    ) -> None:
        """Validate all dependencies before making the persistent worker ready."""
        try:
            await self._foundation.start(configuration_path, environ=environ)
            loaded = self._foundation.configuration
            settings = loaded.settings
            user = settings.telegram.user
            operation_timeout = settings.publishing.operation_timeout_seconds
            session = TelethonSessionAdapter(
                session_path=user.session_path,
                runtime_root=Path("var/sessions"),
                api_id=int(loaded.secrets.get(user.api_id).get_secret_value()),
                api_hash=loaded.secrets.get(user.api_hash).get_secret_value(),
                timeout_seconds=float(operation_timeout),
            )
            self._session = session
            report = await validate_telegram_startup(settings, session)
            destination_ids = {
                item.channel.channel_id
                for item in report.channels
                if item.role.value == "Destination"
            }
            client = await session.open_authorized_client()
            database = self._foundation.mongodb_client[settings.mongodb.database_name]
            publications = database["publications"]
            schedules = database["scheduled_publications"]
            queues = database["schedule_queues"]
            media = database["media_items"]
            groups = database["media_groups"]
            preparations = database["content_preparations"]
            await initialize_publication_indexes(publications, schedules, queues)
            await initialize_content_preparation_indexes(media, groups, preparations)
            content_repository = MongoContentPreparationRepository(
                media, groups, preparations
            )
            destination_names = {
                value.telegram_channel_id: value.name
                for value in settings.destination_channels
                if value.enabled and value.telegram_channel_id in destination_ids
            }
            loader: PublicationPayloadLoader = MongoPublicationPayloadLoader(
                content_repository,
                database["posts"],
                media,
                groups,
                destination_names=destination_names,
            )
            settings.media.root.mkdir(parents=True, exist_ok=True)
            publisher = TelethonPublisherGateway(
                cast("TelethonPublisherClient", client), media_root=settings.media.root
            )
            publication = PublishImmediately(
                MongoPublicationRepository(publications),
                publisher,
                clock=_utc_now,
                timeout_seconds=float(operation_timeout),
                lease_seconds=float(settings.publishing.publication_lease_seconds),
                max_attempts=settings.publishing.publication_max_attempts,
                initial_delay_seconds=float(
                    settings.publishing.retry_initial_delay_seconds
                ),
                maximum_delay_seconds=float(
                    settings.publishing.retry_maximum_delay_seconds
                ),
            )
            owner = f"schedule-worker-{uuid4().hex}"

            async def build_request(
                post_id: str, destination_id: int
            ) -> PublishRequest:
                payload = await loader.load(post_id, destination_id)
                return PublishRequest(
                    post_id,
                    destination_id,
                    payload,
                    owner,
                    uuid4().hex,
                    True,
                    True,
                    True,
                    True,
                    True,
                    destination_id in destination_ids,
                    action="scheduled",
                )

            runner = RunDuePublication(
                MongoScheduleRepository(schedules, queues),
                owner=owner,
                clock=_utc_now,
                lease_seconds=float(settings.publishing.publication_lease_seconds),
                max_attempts=settings.publishing.publication_max_attempts,
                retry_delay_seconds=float(
                    settings.publishing.retry_initial_delay_seconds
                ),
                build_request=build_request,
                publish=publication.execute,
            )
            self._worker = ScheduledPublicationWorker(
                runner.execute_once,
                poll_seconds=float(settings.publishing.worker_poll_seconds),
            )
            self._started = True
        except asyncio.CancelledError:
            await self.shutdown()
            raise
        except Exception as error:
            await self.shutdown()
            raise ScheduleWorkerStartupError(cause=error) from error

    async def wait(self) -> None:
        """Run the bounded-poll worker until cancellation."""
        if not self._started or self._worker is None:
            raise ScheduleWorkerStartupError
        await self._worker.run()

    async def shutdown(self) -> None:
        """Close each owned resource in reverse order exactly once."""
        if self._closed:
            return
        self._closed = True
        if self._session is not None:
            await self._session.close()
            self._session = None
        await self._foundation.shutdown()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_schedule_worker_application(*, sink: EventSink) -> ScheduleWorkerApplication:
    """Build one inert concrete operational scheduling lifecycle."""
    return ScheduleWorkerApplication(create_foundation_application(sink=sink))


async def run_schedule_worker_application(
    application: ScheduleWorkerApplication,
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
) -> FoundationExitCode:
    """Run until cancellation or safe startup failure, then always clean up."""
    try:
        await application.start(configuration_path, environ=environ)
        await application.wait()
    except asyncio.CancelledError:
        await application.shutdown()
        raise
    except ScheduleWorkerStartupError as error:
        await application.shutdown()
        if isinstance(error.__cause__, FoundationStartupError):
            return error.__cause__.exit_code
        return FoundationExitCode.INFRASTRUCTURE_ERROR
    await application.shutdown()
    return FoundationExitCode.SUCCESS


__all__ = (
    "ScheduleWorkerApplication",
    "ScheduleWorkerStartupError",
    "create_schedule_worker_application",
    "run_schedule_worker_application",
)
