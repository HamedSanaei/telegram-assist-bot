# T059 — کانال مبدا فقط با Username

- **Status:** Completed
- **Goal:** حذف الزام شناسه عددی از پیکربندی کانال‌های مبدا.
- **Requirement references:** `5.1`، `5.2`، `14`.
- **Dependencies:** T008، T012.
- **Scope:** `source_channels.username` اجباری است؛ `telegram_channel_id` اختیاری و فقط برای سازگاری Config قدیمی است. شناسه canonical در startup از Telegram resolve می‌شود.
- **Out of scope:** تغییر قرارداد destination channel یا schema پایگاه‌داده.
- **Expected files:** مدل Config، پورت اعتبارسنجی Telegram، adapter، نمونه Config و تست‌ها.
- **Implementation notes:** فقط در صورت وجود شناسه پیکربندی‌شده، canonical-id mismatch بررسی می‌شود.
- **Acceptance criteria:** Config مبدا بدون شناسه عددی load و startup validation شود؛ Config قدیمی همچنان کار کند؛ شناسه resolved برای ingest استفاده شود.
- **Required unit tests:** Config، validation و bootstrap ingestion.
- **Required integration tests:** ندارد؛ adapter شبکه واقعی در suite پیش‌فرض اجرا نمی‌شود.
- **Verification commands:** `uv run pytest tests/unit/shared/config/test_validation.py tests/unit/application/test_validate_telegram_session.py tests/unit/test_text_ingestion_bootstrap.py tests/unit/infrastructure/telegram/user/test_text_ingestion_gateway.py`، `uv run ruff check .`، `uv run mypy src tests`.
- **Required documentation updates:** Roadmap، Status، نمونه Config و Architecture.
- **Definition of done:** معیارهای بالا و Quality Gateهای مرتبط اجرا شده‌اند.

## Verification results

- 2026-07-12: 111 تست مرتبط موفق شد.
