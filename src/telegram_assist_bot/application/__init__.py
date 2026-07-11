"""Application use cases and external-system ports."""

from telegram_assist_bot.application.authenticate_telegram_session import (
    AuthenticateTelegramSession,
    AuthenticationOutcome,
    AuthenticationResult,
    TelegramLoginInput,
)
from telegram_assist_bot.application.crawl_today_text_posts import (
    CrawlTodayResult,
    CrawlTodayTextPosts,
    HistoryPaginationLimitError,
    current_local_day_interval,
)
from telegram_assist_bot.application.handle_live_message import (
    HandleLiveMessage,
    LiveMessageOutcome,
)
from telegram_assist_bot.application.ingest_post_idempotently import (
    IngestionOutcome,
    IngestionResult,
    IngestPostIdempotently,
    TextMessageIngestor,
)
from telegram_assist_bot.application.text_ingestion import (
    PostIdFactory,
    build_stored_post,
)
from telegram_assist_bot.application.validate_telegram_session import (
    TelegramChannelValidationError,
    TelegramChannelValidationIssue,
    TelegramPremiumRequiredError,
    TelegramValidationReport,
    ValidatedTelegramChannel,
    ValidateTelegramSession,
)

__all__ = (
    "AuthenticateTelegramSession",
    "AuthenticationOutcome",
    "AuthenticationResult",
    "CrawlTodayResult",
    "CrawlTodayTextPosts",
    "HandleLiveMessage",
    "HistoryPaginationLimitError",
    "IngestPostIdempotently",
    "IngestionOutcome",
    "IngestionResult",
    "LiveMessageOutcome",
    "PostIdFactory",
    "TelegramChannelValidationError",
    "TelegramChannelValidationIssue",
    "TelegramLoginInput",
    "TelegramPremiumRequiredError",
    "TelegramValidationReport",
    "TextMessageIngestor",
    "ValidateTelegramSession",
    "ValidatedTelegramChannel",
    "build_stored_post",
    "current_local_day_interval",
)
