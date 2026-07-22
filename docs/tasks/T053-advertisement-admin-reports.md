# T053 — گزارش امروز، آینده و خطاها

## وضعیت

`Completed`

## هدف

ارائه گزارش کوتاه و مجاز Bot API از اجرای تبلیغات امروز، Slotهای آینده و خطاهای اخیر بر پایه داده Audit پایدار، بدون تغییر Campaign، Slot یا Publication.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `6.5 گزارش اجرای تبلیغات`.
- `docs/REQUIREMENTS.md`، بخش‌های `5.13 مدیران مجاز` و `14 امنیت`.
- `docs/ARCHITECTURE.md`، بخش `5` (`ReportAdvertisementRuns`)، بخش `6` (`AdvertisementRepository` و `AdminMessagingGateway`)، بخش‌های `8`، `9`، `13` و `14`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام `13` باید در T020 حل شده باشد.

## وابستگی‌ها

- `T020` و `T051` باید کامل شده باشند.

### پیش‌نیاز تصمیم

Role/Permission گزارش باید از Decision T020 موجود باشد. پیش از پیاده‌سازی، command surface، تعریف «امروز/آینده/اخیر»، timezone نمایش، سقف آیتم و pagination/truncation باید تصویب و مستند شود؛ Task نباید Command یا بازه نامحدود اختراع کند.

## دامنه کار

- تعریف Query/DTOهای read-only برای today runs، upcoming slots و recent failures.
- خواندن داده Audit T051 با فیلتر Destination مجاز، بازه زمانی bounded و ترتیب قطعی.
- Render کوتاه فارسی/UTF-8 شامل scheduled/actual time، Destination، status، retry، message ID و execution delay/خطای redacted در صورت مربوط.
- Handler نازک Bot با Authorization T020 و Timeout محدود.
- رفتار empty result، truncation/pagination و خطای repository مطابق Decision.

## خارج از دامنه

- create/edit/enable Campaign، retry دستی، cancel یا resolve Collision.
- Dashboard وب، export فایل یا analytics فاز چهارم.
- نمایش stack trace، Secret یا raw Telegram/provider error.
- تماس Telegram User API یا تغییر داده کسب‌وکار.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/advertisements/report_advertisement_runs.py`
- توسعه queryهای read-only `src/telegram_assist_bot/application/ports/advertisement_repository.py`
- توسعه MongoDB adapter متناظر.
- `src/telegram_assist_bot/presentation/bot/advertisement_reports.py`
- Wiring command در Handler/Composition Root موجود.
- `tests/unit/application/advertisements/test_report_advertisement_runs.py`
- `tests/unit/presentation/bot/test_advertisement_report_renderer.py`
- `tests/integration/advertisements/test_advertisement_admin_reports.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** timezone، limits و permission باید typed/bounded؛ query بدون limit ممنوع باشد.
- **Migration:** Indexهای scheduled_at/status/destination برای query لازم‌اند؛ تغییر audit schema باید reader قدیمی را با default سازگار نگه دارد.
- **Compatibility:** متن Command/Callback قرارداد بیرونی است و تغییرش نیازمند alias/version؛ DTO به Bot SDK وابسته نشود.
- **Concurrency:** گزارش Snapshot read-only است و باید status جاری سازگار نمایش دهد؛ اجرای هم‌زمان Publication نباید خطا یا رکورد نیمه‌تفسیرشده بسازد.
- **Security:** Authorization و Destination permission server-side، خطا redacted و Admin ID/Token/Session در پیام یا Log افشا نشود.

## معیارهای پذیرش عینی

1. مدیر مجاز سه گزارش today/upcoming/recent failures را با timezone و limit مصوب دریافت می‌کند.
2. مدیر غیرمجاز یا فاقد Permission هیچ داده‌ای دریافت نمی‌کند.
3. گزارش فیلدهای لازم بخش `6.5` را دقیق و مرتب نمایش می‌دهد.
4. Destination غیرمجاز، Secret، stack trace و raw error در خروجی وجود ندارد.
5. empty/large result مطابق Decision و محدودیت Bot رفتار می‌کند.
6. اجرای گزارش هیچ سند Campaign/Slot/Publication را تغییر نمی‌دهد.

## تست‌های واحد الزامی

- query boundary امروز در timezone مصوب، upcoming و recent failures.
- Authorization/Destination filtering و limit/order.
- Renderer فارسی برای success/failure/pending، empty و truncated.
- Redaction و عدم side effect.

## تست‌های یکپارچه‌سازی الزامی

- MongoDB audit fixtures + Bot Gateway جعلی برای هر سه گزارش.
- هم‌زمانی یک Publication update با query و نمایش status معتبر.
- Admin غیرمجاز و عدم ارسال/نشت داده.
- تست زنده Bot API خارج از Suite پیش‌فرض است.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/advertisements/test_report_advertisement_runs.py tests/unit/presentation/bot/test_advertisement_report_renderer.py
uv run pytest tests/integration/advertisements/test_advertisement_admin_reports.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت Decision command/range/timezone/limit در `docs/DECISIONS.md` اگر معماری‌مهم است؛ جزئیات عادی در مستند Command کافی است.
- افزودن Query/Renderer/Handler و Indexها به `docs/CODE_MAP.md` و همگام‌سازی معماری.
- به‌روزرسانی راهنمای Command، `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که گزارش‌های bounded و read-only با Authorization/Redaction و Mongo+Bot fake اثبات، فارسی دستی بازبینی، همه Quality Gateها موفق و هیچ mutation/Feature analytics افزوده نشده باشد.

## نتیجهٔ راستی‌آزمایی نهایی

- تست‌های متمرکز و Regression مرتبط: ۹۰ آزمون موفق.
- مجموعهٔ کامل non-live: ۱۳۴۹ آزمون موفق.
- Lock، Ruff، Ruff format، MyPy، یکپارچگی متن changed/all و `git diff --check`: موفق.
- هر سه Command فقط در private chat مدیر فعال دارای `approval.view` داده ارسال می‌کنند؛ Queryها bounded و read-only هستند.
