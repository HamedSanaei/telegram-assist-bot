# T011 — هم‌زمانی Crawl/Listener و Idempotency

## وضعیت

Planned

## هدف

سخت‌سازی مسیر ingest متنی در برابر overlap خزش و Listener، رویداد تکراری، retry و چند Worker، به‌طوری‌که فقط یک Post ساخته و فقط یک بار برای مرحلهٔ بعدی claim شود.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.3 جلوگیری از پردازش تکراری`.
- `docs/REQUIREMENTS.md`، بخش `5.21 Pipeline کامل فاز اول`، مرحلهٔ یکتایی و ادامه از مرحلهٔ مناسب.
- `docs/REQUIREMENTS.md`، بخش `13. مدیریت خطا و Retry`، side effect تکراری.
- `docs/ARCHITECTURE.md`، بخش `9. MongoDB و مدل ماندگاری`، Unique/atomic update.
- `docs/ARCHITECTURE.md`، بخش `14. Logging، Retry، Idempotency و هم‌زمانی`، ingest key و Concurrency.

## وابستگی‌ها

- T009 — خزش پیام‌های متنی امروز یک کانال؛ باید Completed باشد.
- T010 — Listener زنده پیام متنی؛ باید Completed باشد.

## محدوده

- یکسان‌سازی endpoint application برای ingest هر دو producer و حذف مسیرهای write موازی.
- تعریف نتیجهٔ اتمیک `Created`/`AlreadyExists`/`Conflict` و شناسهٔ canonical Post.
- افزودن claim/transition محدود از Stored به «آمادهٔ پردازش بعدی» با expected version؛ فقط winner حق enqueue/emit downstream marker دارد.
- تست concurrent insert از Crawl، Listener و چند worker/process شبیه‌سازی‌شده روی MongoDB واقعی.
- مدیریت DuplicateKey race به‌عنوان idempotent success و retry خطاهای نامرتبط بدون بلعیدن.
- correlation/logging یک رویداد canonical و شمارندهٔ duplicate، بدون ثبت payload.
- اصلاح کوچک T009/T010 فقط در حد استفاده از مسیر واحد.

## خارج از محدوده

- Message broker یا outbox عمومی؛ اگر atomic downstream نیازمند outbox شد باید Task شکسته/تصمیم ثبت شود، نه پیاده‌سازی گسترده.
- Media Group concurrency؛ T015.
- AI job، Approval، Publication یا Schedule idempotency.
- edit/delete پیام منبع و semantic duplicate.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/ingest_post_idempotently.py`
- تغییر محدود `src/telegram_assist_bot/application/ports/post_repository.py` و Mongo adapter زیر `src/telegram_assist_bot/infrastructure/persistence/mongodb/`.
- تغییر محدود T009/T010 برای endpoint مشترک.
- `tests/unit/application/test_ingest_post_idempotently.py`
- `tests/integration/test_concurrent_idempotent_ingestion.py`

## نکات پیاده‌سازی

- check-then-insert ممنوع؛ winner فقط از نتیجهٔ atomic DB مشخص شود.
- اگر marker مرحلهٔ بعد در همان document ذخیره می‌شود، update باید با version/status شرطی باشد؛ event حافظه‌ای منبع حقیقت نباشد.
- **ریسک Configuration:** تعداد coroutine/worker تست bounded باشد؛ تنظیم product تازه اضافه نشود.
- **ریسک Migration:** فیلد claim/processed marker و index جدید در صورت نیاز schema version و index setup صریح می‌خواهد.
- **ریسک Compatibility:** نتیجهٔ Repository T004 نباید بی‌دلیل شکسته شود؛ تغییر contract و callerها هم‌زمان انجام شود.
- **ریسک Concurrency:** تست باید barrier واقعی داشته باشد و یک winner را اثبات کند؛ lock process-local راه‌حل معتبر نیست.
- **ریسک Security:** duplicate logs فقط identity عددی/correlation را ثبت و متن را حذف کنند.

## معیارهای پذیرش عینی

1. N producer هم‌زمان برای یک identity دقیقاً یک document می‌سازند.
2. همهٔ callerها همان canonical Post ID را می‌گیرند.
3. فقط یک caller transition/claim مرحلهٔ بعد را موفق می‌شود.
4. retry پس از failure مبهم document دوم یا claim دوم نمی‌سازد.
5. پیام‌های متفاوت همان channel یا message ID به‌درستی مستقل‌اند.
6. T009 و T010 هیچ write path دیگری ندارند.
7. conflict و infrastructure failure از already-existing تفکیک می‌شوند.

## Unit Testهای الزامی

- قرارداد result و رفتار Created/AlreadyExists/Conflict.
- فقط Created/claim winner اجازهٔ downstream marker دارد.
- DuplicateKey هدف idempotent و خطای index دیگر propagate می‌شود.
- retry caller و correlation ثابت.

## Integration Testهای الزامی

- barrier هم‌زمان برای Crawl/Listener fake و چند repository instance روی MongoDB آزمایشی.
- assertion یک document، یک canonical ID و یک downstream claim.
- crash/retry پس از insert و پیش از claim، سپس recovery بدون duplicate.
- رقابت identityهای متفاوت برای اثبات نبود serialization سراسری.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_ingest_post_idempotently.py
uv run pytest tests/integration/test_concurrent_idempotent_ingestion.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

Test Integration نباید skip شود؛ MongoDB test-only، تکرار چندبارهٔ test رقابتی و `git diff --check` الزامی‌اند.

## به‌روزرسانی‌های مستندات

- ثبت Status/verification و به‌روزرسانی T011 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن مسیر ingest/claim به `docs/CODE_MAP.md`.
- همگام‌سازی قرارداد idempotency/concurrency و هر index در `docs/ARCHITECTURE.md`.
- ثبت outbox/claim decision در `docs/DECISIONS.md` فقط اگر تصمیم معماری مهم گرفته شد.

## تعریف انجام‌شدن

- race واقعی با یک insert و یک claim پاس شده و flaky نیست.
- همهٔ writerها مسیر مشترک دارند و lock محلی جای DB atomicity را نگرفته است.
- suite کامل، lint، format، mypy، UTF-8 و secret checks پاس شده‌اند.
- هیچ downstream feature پیاده نشده است.
