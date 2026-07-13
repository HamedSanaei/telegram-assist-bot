# T063 — Operational runtime startup ordering

## Status

Completed

## Goal

شروع heartbeat، publication worker و live ingestion پیش از history crawl و جداسازی crawl اولیه به‌صورت background و non-critical، بدون تغییر مالک واحد Telethon یا اجرای jobهای واقعی در تست.

## Requirement references

- `docs/REQUIREMENTS.md`: بخش‌های `5.2`، `5.17`، `5.18`، `13`، `14` و `16`.
- T009–T012، T029–T033، T060–T062.

## Dependencies

- T009–T012، T029–T033 و T060–T062: Completed.

## Scope

- heartbeat اولیهٔ فوری و publication polling با latency حداکثر دو ثانیه در runtime یکپارچه.
- شروع live listener و workerهای حیاتی پیش از crawl.
- crawl اولیهٔ supervised background با retry امن و بدون توقف publication/heartbeat.
- lifecycle eventهای امن و readiness واقعی.
- propagation شکست heartbeat/publication/live و drain کامل taskها در shutdown.
- تست‌های unit و MongoDB برای ترتیب، latency، lease و restart.

## Out of scope

- تغییر مدل publication، الگوریتم schedule یا cancellation.
- Redis یا wake-up بیرونی بین Processها.
- اجرای job موجود، Telegram زنده، تغییر config محلی، AI یا refactor نامرتبط.

## Expected files and modules

- `src/telegram_assist_bot/bootstrap/text_ingestion.py`
- `src/telegram_assist_bot/workers/scheduled_publication_worker.py` در صورت نیاز.
- تست‌های runtime bootstrap، ingestion و MongoDB.
- README و اسناد project memory مرتبط.

## Implementation notes

- MongoDB تنها منبع durable صف می‌ماند.
- approval-bot و runtime دو Process مستقل می‌مانند؛ polling کوتاه bounded راه بیدارسازی است.
- history failure فقط safe category/type ثبت می‌کند و payload یا exception message لاگ نمی‌شود.
- shutdown ابتدا تمام taskها را cancel/gather و سپس Telethon و MongoDB را می‌بندد.

## Acceptance criteria

1. اولین heartbeat پیش از شروع crawl نوشته می‌شود.
2. publication worker و live listener هنگام crawl مسدود فعال‌اند.
3. `operational_runtime_ready` پس از readiness heartbeat/publication و پیش از crawl صادر می‌شود.
4. job فوری سالم حداکثر در دو ثانیه poll می‌شود و job future زود claim نمی‌شود.
5. شکست crawl retry می‌شود و runtime حیاتی را متوقف نمی‌کند.
6. شکست heartbeat، publication یا live listener exit زیرساختی غیرصفر می‌سازد.
7. Ctrl+C تمام taskها را پیش از بستن gateway/foundation drain می‌کند.
8. یک gateway/client/session تنها مالک User API باقی می‌ماند.
9. lease/idempotency موجود پس از restart مانع publication تکراری است.

## Unit tests

- crawl مسدود در برابر heartbeat، publication و live start.
- ترتیب lifecycle eventها و readiness.
- crawl failure isolation/retry.
- critical failure propagation و shutdown drain.
- polling interval bounded و مالک واحد gateway.

## Integration tests

- claim job فوری پس از startup، latency bounded، عدم claim future و uniqueness پس از restart با MongoDB محلی.

## Verification commands

```powershell
$env:TEST_MONGODB_URI='mongodb://127.0.0.1:27017/?directConnection=true'
uv run --python 3.12 pytest <focused tests>
uv run --python 3.12 ruff check .
uv run --python 3.12 ruff format --check .
uv run --python 3.12 mypy src tests scripts
uv lock --check
git diff --check
uv run --python 3.12 pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90 --basetemp <unique>
```

## Documentation updates

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/CODE_MAP.md`
- `docs/ROADMAP.md`
- `docs/STATUS.md`
- همین Task.

## Definition of done

- همهٔ acceptance criteria و verificationها پاس شده‌اند.
- suite کامل non-live صفر failed/error/mandatory skip و branch coverage حداقل ۹۰٪ دارد.
- هیچ job واقعی، Telegram live، config محلی یا عملیات commit/push لمس نشده است.
- T063 Completed و T034 دوباره تنها Active است.

## Verification results

- focused runtime/history/publication/MongoDB: `32 passed`.
- تمام unit tests: `816 passed`.
- suite کامل non-live با Python 3.12 و MongoDB محلی: `884 passed`، `0 skipped`، exit code `0` و branch coverage برابر `90.0819%`.
- `ruff check .`، `ruff format --check .`، `mypy src tests scripts`، `uv lock --check` و `git diff --check`: Passed.
- UTF-8/Persian/mojibake و secret scan برای فایل‌های تغییرکرده: Passed.
- هیچ Telegram live call، job موجود، config محلی، commit یا push استفاده نشد.
- اسکن `--all` متن همچنان به‌علت artifactهای pytest tracked از commit پیشین `5475bec` خارج از T063 قابل اجرا نیست.
