# T060 — Runtime media ingestion and content-preparation wiring

## وضعیت

Completed

## هدف

اتصال اجزای تکمیل‌شدهٔ T013 تا T019 به runtime واقعی دریافت Telegram، به‌طوری‌که پیام متنی، Media تکی و Album از مسیر مشترک Crawl/Listener به Post، فایل خصوصی، metadata پایدار و `content_preparations` آماده برسند.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش‌های `5.2` تا `5.11` برای دریافت، Media، Album، duplicate، متن مقصد و دسته‌بندی.
- `docs/REQUIREMENTS.md`، بخش‌های `13`، `14` و `16` برای امنیت، Logging و خطاپذیری.
- `docs/ARCHITECTURE.md`، بخش‌های Telegram User API، Media storage، content preparation، Composition Root و lifecycle.
- ADR-015 و ADR-016 در `docs/DECISIONS.md`.

## وابستگی‌ها

- T012، T013، T014، T015، T016، T017، T018 و T019؛ همگی Completed هستند.

## محدوده

- یک مسیر Application مشترک برای Post، دانلود Media، Album و content preparation.
- استفاده از همان Telethon client/session برای validation، Crawl، Listener و streaming.
- سیم‌کشی repository/indexهای موجود Milestone 2 در Composition Root دریافت.
- فرمان عمومی `ingest` و alias سازگار `ingest-text`.
- فرمان one-shot امن `media-cleanup`.
- backpressure محدود، Logging ساختاریافتهٔ بدون payload و recovery idempotent.
- تست‌های Unit، filesystem، MongoDB و E2E غیرزنده.

## خارج از محدوده

- AI provider، advertisement/semantic duplicate/AI categorization و delayed scoring.
- Approval Bot، publication و schedule-worker redesign.
- تغییر الگوریتم‌های تکمیل‌شدهٔ Milestone 2، VPN و refactor نامرتبط.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/runtime_ingestion.py`
- `src/telegram_assist_bot/bootstrap/text_ingestion.py`
- `src/telegram_assist_bot/bootstrap/media_cleanup.py`
- `src/telegram_assist_bot/bootstrap/cli.py`
- Adapterهای محدود Telegram media و MongoDB content preparation.
- تست‌های Unit/Integration/E2E مرتبط با runtime.
- مستندات وضعیت، نقشهٔ کد، معماری و راه‌اندازی.

## نکات پیاده‌سازی

- Application و Domain از Telethon/PyMongo/filesystem مستقل می‌مانند.
- `source_channel_id + source_message_id` هویت Post و قرارداد موجود `MediaIdentity` هویت Media است.
- دانلود با timeout/size/retry موجود، فایل `.partial` و move اتمیک انجام می‌شود.
- نتیجهٔ مراحل پایدار دوباره مصرف می‌شود و فایل سالم دوباره دانلود نمی‌شود.
- Album فقط پس از policy محدود quiet/max-wait آماده می‌شود و ترتیب اعضا deterministic است.
- پردازش مستقیم و ترتیبی Crawl/Listener backpressure طبیعی و محدود ایجاد می‌کند؛ task نامحدود ساخته نمی‌شود.
- قابلیت‌های AI پیاده‌نشده در حالت فعال fail-fast می‌مانند.

## معیارهای پذیرش عینی

1. پیام Media از History و Live با همان session به فایل خصوصی، `media_items` و preparation آماده می‌رسد.
2. پیام text-only بدون Media مصنوعی آماده می‌شود.
3. overlap و restart رکورد، فایل، عضو Album یا preparation تکراری نمی‌سازند.
4. Album خارج‌ترتیب یک aggregate مرتب می‌سازد و پیش از تکمیل policy آماده نمی‌شود.
5. cancellation/failure فایل partial، readiness کاذب، task، session lock یا client باز باقی نمی‌گذارد.
6. `ingest` و `ingest-text` یک runtime کامل را اجرا می‌کنند و `media-cleanup` cleanup محدود one-shot است.
7. متن فارسی، ZWNJ، خطوط، Emoji و Entityهای UTF-16 بدون تغییر باقی می‌مانند.
8. Log شامل payload، opaque reference، مسیر مطلق خصوصی یا secret نیست.

## Unit Testهای الزامی

- mapping متن و Mediaهای Photo/Video/Document و unsupported بدون SDK leak.
- حفظ caption/entity و رفتار idempotent/retry/cancellation/reuse دانلود.
- آماده‌سازی text-only و Media، hashها، Album مرتب و readiness محدود.
- رویدادهای امن، aliasهای CLI و cleanup one-shot.

## Integration Testهای الزامی

- persistence/index/idempotency برای `media_items`، `media_groups` و `content_preparations` با MongoDB محلی تست.
- filesystem موقت برای partial/atomic/size/traversal/symlink/reuse/cleanup.
- E2E fake Telegram برای History Photo، Live Video، text-only، Album، overlap، restart و shutdown.
- هیچ تماس زندهٔ Telegram یا credential واقعی مجاز نیست.

## فرمان‌های راستی‌آزمایی

```powershell
uv run --python 3.12 pytest tests/unit -k "media or preparation or ingestion or telegram"
uv run --python 3.12 pytest tests/contract/telegram
$env:TEST_MONGODB_URI = "mongodb://127.0.0.1:27017/?directConnection=true"
uv run --python 3.12 pytest tests/integration tests/e2e -k "media or content_preparation or ingestion"
uv run --python 3.12 ruff check src tests
uv run --python 3.12 ruff format --check src tests
uv run --python 3.12 mypy src tests scripts
uv run python scripts/check_text_integrity.py --changed
uv run python scripts/check_text_integrity.py --all
git diff --check
uv run --python 3.12 pytest -m "not live" --basetemp <unique-path>
```

## به‌روزرسانی مستندات

- `docs/ROADMAP.md`، `docs/STATUS.md`، `docs/CODE_MAP.md` و بخش‌های واقعی runtime در `docs/ARCHITECTURE.md`.
- README برای `ingest`، alias `ingest-text` و `media-cleanup`.
- `docs/DECISIONS.md` فقط اگر تصمیم معماری پایدار تازه‌ای لازم شود.

## نتایج نهایی راستی‌آزمایی

- Unit متمرکز رسمی: `177 passed`، `0 skipped` و `601 deselected`.
- Contract تلگرام: `3 passed` و `0 skipped`.
- Integration/E2E متمرکز با MongoDB محلی و filesystem موقت: `14 passed`، `0 skipped` و `44 deselected`؛ دو سناریوی اختصاصی runtime نیز مستقل `2 passed` شدند.
- suite کامل non-live: `839 passed`، `0 skipped` و Branch Coverage برابر `90.00%`.
- `uv lock --check`، Ruff، format، mypy، text-integrity برای changed/all، detect-secrets، build، distribution، import و `git diff --check` موفق شدند.
- فرمان operational کامل `ingest` است؛ `ingest-text` alias سازگار همان runtime و `media-cleanup` cleanup یک‌مرحله‌ای محدود است.
- تست زندهٔ Telegram اجرا نشد و برای Done الزامی نیست؛ تأیید دستی با credential/session واقعی بر عهدهٔ اپراتور است.

## تعریف انجام‌شدن

- همهٔ معیارها و تست‌های الزامی بدون skip پاس شده‌اند.
- suite کامل non-live، Ruff، format، mypy، UTF-8، secret و diff checks موفق‌اند.
- هیچ session، Media، secret، config محلی یا artifact تولیدی track نشده است.
- T060 Completed شده و T034 دوباره تنها Task فعال است.
