"""Real MongoDB and fake Bot integration tests for T053 reports."""

from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.advertisements.report_advertisement_runs import (
    RenderAdvertisementReport,
    ReportAdvertisementRuns,
)
from telegram_assist_bot.application.approvals import AuthorizeAdminAction
from telegram_assist_bot.application.ports import AdvertisementReportKind, BotUpdate
from telegram_assist_bot.domain import Administrator, AdminPermission
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoAdvertisementSlotRepository,
)
from telegram_assist_bot.presentation.bot.advertisement_reports import (
    AdvertisementReportHandlers,
)

NOW = datetime(2026, 7, 22, 8, tzinfo=UTC)
ALLOWED = -1001
DENIED = -1002


class FakeBotGateway:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_header(
        self, chat_id: int, text: str, _keyboard: object = None, **_kwargs: object
    ) -> int:
        self.sent.append((chat_id, text))
        return len(self.sent)


def slot_doc(
    record_id: str,
    *,
    destination_id: int = ALLOWED,
    destination_name: str = "کانال الف",
    campaign_id: str | None = None,
    effective_due_at: datetime = NOW,
    status: str = "scheduled",
    updated_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "_id": record_id,
        "slot_id": record_id,
        "campaign_id": campaign_id or record_id,
        "destination_name": destination_name,
        "destination_id": destination_id,
        "status": status,
        "due_at": effective_due_at,
        "effective_due_at": effective_due_at,
        "updated_at": updated_at or NOW,
        "publication_attempt_count": 2 if status != "scheduled" else 0,
        "claim_count": 2 if status != "scheduled" else 0,
        "published_at": NOW if status == "completed" else None,
        "message_ids": [801] if status == "completed" else [],
        "execution_delay_seconds": 9.0 if status == "completed" else None,
        "last_error_category": (
            "telegram_transient" if status != "scheduled" else None
        ),
        "last_failure_reason_code": "SAFE_CODE",
        "document_type": "advertisement_slot",
        "sentinel": "must-remain-unchanged",
    }


def administrator(
    *, active: bool = True, permissions: frozenset[AdminPermission] | None = None
) -> Administrator:
    return Administrator(
        77,
        active,
        "admin",
        permissions if permissions is not None else frozenset({AdminPermission.VIEW}),
        frozenset({ALLOWED}),
    )


async def _reports_cover_authorization_ranges_order_and_read_only() -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        os.environ["TEST_MONGODB_URI"], tz_aware=True
    )
    database_name = f"tab_t053_{uuid4().hex}"
    collection = client[database_name]["advertisement_slots"]
    repository = MongoAdvertisementSlotRepository(collection)
    try:
        await repository.initialize_indexes()
        docs = [
            slot_doc("today-b", destination_name="کانال ب", campaign_id="campaign-b"),
            slot_doc("today-a", destination_name="کانال الف", campaign_id="campaign-a"),
            slot_doc("denied", destination_id=DENIED),
            slot_doc("upcoming-lower", effective_due_at=NOW),
            slot_doc("upcoming-upper", effective_due_at=NOW + timedelta(days=7)),
            slot_doc(
                "collision-adjusted",
                effective_due_at=NOW + timedelta(hours=1),
                campaign_id="campaign-adjusted",
            ),
            slot_doc(
                "failure-lower",
                status="waiting_for_retry",
                updated_at=NOW - timedelta(days=7),
            ),
            slot_doc(
                "failure-upper",
                status="permanent_failed",
                updated_at=NOW,
                campaign_id="campaign-failed",
            ),
            slot_doc(
                "successful-old-failure",
                status="completed",
                updated_at=NOW,
            ),
        ]
        await collection.insert_many(docs)
        before = deepcopy(await collection.find({}).sort("_id").to_list())
        gateway = FakeBotGateway()
        service = ReportAdvertisementRuns(
            repository,
            timezone="Asia/Tehran",
            upcoming_horizon_days=7,
            failure_horizon_days=7,
            max_items=20,
            clock=lambda: NOW,
        )
        handlers = AdvertisementReportHandlers(
            AuthorizeAdminAction((administrator(),)),
            service,
            RenderAdvertisementReport(),
            gateway,  # type: ignore[arg-type]
        )
        update = BotUpdate(77, 77, "private")
        for kind in AdvertisementReportKind:
            assert await handlers.handle(update, kind) is True

        assert len(gateway.sent) == 3
        all_text = "\n".join(text for _, text in gateway.sent)
        assert "denied" not in all_text
        assert "campaign-adjusted" in gateway.sent[1][1]
        assert "upcoming-upper" not in all_text
        assert "telegram_transient" in gateway.sent[2][1]
        assert "successful-old-failure" not in gateway.sent[2][1]
        assert "failure-lower" in gateway.sent[2][1]
        assert "campaign-failed" in gateway.sent[2][1]
        assert gateway.sent[0][1].index("campaign-a") < gateway.sent[0][1].index(
            "campaign-b"
        )
        after = await collection.find({}).sort("_id").to_list()
        assert after == before
    finally:
        await client.drop_database(database_name)
        await client.close()


async def _unauthorized_variants_receive_no_report_data() -> None:
    for admin, update in (
        (administrator(), BotUpdate(999, 999, "private")),
        (administrator(active=False), BotUpdate(77, 77, "private")),
        (
            administrator(permissions=frozenset()),
            BotUpdate(77, 77, "private"),
        ),
        (administrator(), BotUpdate(77, 77, "group")),
    ):
        gateway = FakeBotGateway()
        repository = _FailIfQueriedRepository()
        handlers = AdvertisementReportHandlers(
            AuthorizeAdminAction((admin,)),
            ReportAdvertisementRuns(
                repository,  # type: ignore[arg-type]
                timezone="Asia/Tehran",
                upcoming_horizon_days=7,
                failure_horizon_days=7,
                max_items=20,
                clock=lambda: NOW,
            ),
            RenderAdvertisementReport(),
            gateway,  # type: ignore[arg-type]
        )
        assert await handlers.handle(update, AdvertisementReportKind.TODAY) is False
        assert gateway.sent == []


class _FailIfQueriedRepository:
    async def list_report_records(self, _query: object) -> tuple[object, ...]:
        raise AssertionError("unauthorized report reached persistence")


async def _concurrent_publication_update_has_one_valid_snapshot() -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        os.environ["TEST_MONGODB_URI"], tz_aware=True
    )
    database_name = f"tab_t053_race_{uuid4().hex}"
    collection = client[database_name]["advertisement_slots"]
    repository = MongoAdvertisementSlotRepository(collection)
    try:
        await repository.initialize_indexes()
        await collection.insert_one(slot_doc("race-slot"))
        service = ReportAdvertisementRuns(
            repository,
            timezone="Asia/Tehran",
            upcoming_horizon_days=7,
            failure_horizon_days=7,
            max_items=20,
            clock=lambda: NOW,
        )

        async def update() -> None:
            await collection.update_one(
                {"_id": "race-slot"},
                {
                    "$set": {
                        "status": "completed",
                        "published_at": NOW,
                        "message_ids": [999],
                        "execution_delay_seconds": 0.0,
                    }
                },
            )

        report, _ = await asyncio.gather(
            service.execute(
                AdvertisementReportKind.TODAY,
                allowed_destination_ids=frozenset({ALLOWED}),
            ),
            update(),
        )
        item = next(value for value in report.records if value.record_id == "race-slot")
        assert (item.status, item.message_ids) in {
            ("scheduled", ()),
            ("completed", (999,)),
        }
    finally:
        await client.drop_database(database_name)
        await client.close()


def test_all_commands_ranges_order_filtering_and_zero_mutation() -> None:
    asyncio.run(_reports_cover_authorization_ranges_order_and_read_only())


def test_unknown_inactive_missing_permission_and_non_private_are_silent() -> None:
    asyncio.run(_unauthorized_variants_receive_no_report_data())


def test_simultaneous_publication_update_returns_a_valid_snapshot() -> None:
    asyncio.run(_concurrent_publication_update_has_one_valid_snapshot())
