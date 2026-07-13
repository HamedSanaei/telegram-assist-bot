# T064 — Operational runtime lifetime supervision

## Status

Completed

## Goal

جلوگیری از shutdown زودرس runtime پس از readiness با جداسازی initialization و
کارهای non-critical از taskهای واقعاً حیاتی، نگه‌داشتن همان Telethon client تا
disconnect یا درخواست توقف، و ثبت علت امن shutdown.

## Requirement references

- `docs/REQUIREMENTS.md`: بخش‌های `5.2`، `5.17`، `5.18`، `13`، `14` و `16`.
- T009–T012، T029–T033 و T060–T063.

## Dependencies

- T009–T012، T029–T033 و T060–T063: Completed.

## Scope

- supervisor صریح برای heartbeat/publication و disconnect همان Telethon client.
- stop event صریح و علت‌های امن shutdown.
- جداسازی listener registration، crawl و passهای initialization از critical lifetime.
- تشخیص بازگشت عادی، cancellation یا failure غیرمنتظرهٔ task حیاتی.
- drain کامل taskها پیش از بستن Telethon و MongoDB.
- تست‌های regression برای lifetime، readiness، publication polling و shutdown.

## Out of scope

- تغییر الگوریتم ingestion، publication، schedule، approval یا cancellation.
- client/session دوم، Redis، Telegram زنده یا اجرای jobهای موجود.
- تغییر `configuration.local.json`، AI یا refactor نامرتبط.

## Expected files and modules

- `src/telegram_assist_bot/bootstrap/text_ingestion.py`
- `src/telegram_assist_bot/bootstrap/runtime.py`
- `src/telegram_assist_bot/infrastructure/telegram/user/session_adapter.py`
- `src/telegram_assist_bot/infrastructure/telegram/user/text_ingestion_gateway.py`
- تست‌های unit مربوط به runtime، gateway و session.
- اسناد project memory مرتبط.

## Implementation notes

- MongoDB منبع durable صف باقی می‌ماند.
- gateway موجود یک disconnect awaitable روی همان client مالک ارائه می‌کند.
- taskهای non-critical با بازگشت عادی runtime را متوقف نمی‌کنند.
- log تشخیصی فقط نام task، نوع completion و نام type خطا را نگه می‌دارد.

## Acceptance criteria

1. runtime پس از `operational_runtime_ready` بدون درخواست یا failure زنده می‌ماند.
2. پایان listener registration یا history crawl shutdown ایجاد نمی‌کند.
3. heartbeat و publication polling چند چرخه ادامه می‌یابند.
4. بازگشت عادی یا failure task حیاتی exit زیرساختی غیرصفر می‌سازد.
5. disconnect غیرمنتظرهٔ Telethon علت مستقل و امن دارد.
6. Ctrl+C یا stop صریح shutdown تمیز با علت `requested` می‌سازد.
7. همهٔ taskها پیش از close همان Telethon client و MongoDB gather می‌شوند.
8. هیچ exception مشاهده‌نشده یا field رزروشده در log مسیر runtime باقی نمی‌ماند.

## Unit tests

- listener registration و history completion غیرحیاتی.
- ادامهٔ heartbeat/publication پس از readiness و crawl.
- failure heartbeat/publication و بازگشت عادی task حیاتی.
- Telethon disconnect و stop صریح.
- ترتیب drain و close و نبود warning task مشاهده‌نشده.

## Integration tests

- استفاده از تست‌های MongoDB موجود برای claim/lease، polling و idempotency runtime؛
  این task مدل persistence جدیدی اضافه نمی‌کند.

## Verification commands

```powershell
$env:TEST_MONGODB_URI='mongodb://127.0.0.1:27017/?directConnection=true'
uv run --python 3.12 pytest <focused runtime tests>
uv run --python 3.12 ruff check .
uv run --python 3.12 ruff format --check .
uv run --python 3.12 mypy src tests scripts
uv lock --check
git diff --check
uv run --python 3.12 pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90 --basetemp <unique>
```

## Documentation updates

- `docs/ARCHITECTURE.md`
- `docs/CODE_MAP.md`
- `docs/ROADMAP.md`
- `docs/STATUS.md`
- همین Task.

## Definition of done

- همهٔ acceptance criteria و verificationها پاس شده‌اند.
- suite کامل non-live صفر failed/error/mandatory skip و branch coverage حداقل ۹۰٪ دارد.
- هیچ Telegram live call، job موجود، config محلی، commit یا push انجام نشده است.
- T064 Completed و T034 دوباره تنها Active است.

## Verification results

- focused runtime/session/supervision: `34 passed`.
- تمام unit tests: `821 passed`.
- suite کامل non-live با Python 3.12 و MongoDB محلی: `890 passed`، `0 skipped`،
  exit code `0` و Branch Coverage برابر `90.23173300809403%`.
- `ruff check .`، `ruff format --check .`، `mypy src tests scripts`،
  `uv lock --check` و `git diff --check`: Passed.
- build، distribution validation، import smoke و secret scan تمام `1054` فایل
  tracked: Passed.
- UTF-8/Persian/mojibake برای ۱۵ فایل تغییرکرده: Passed.
- اسکن `--all` همچنان فقط به‌علت ۵۲ fixture خراب عمدی داخل artifactهای pytest
  از قبل tracked شکست می‌خورد؛ این فایل‌ها خارج از T064 و بدون تغییرند.
- هیچ Telegram live call، job موجود، config محلی، commit یا push استفاده نشد.
