# T056 — تعریف محصولی تحلیل عملکرد

## وضعیت

`Planned`

## هدف

تبدیل پیشنهادهای تحلیل عملکرد فاز چهارم به قرارداد محصولی و داده‌ای قابل آزمون و Taskهای کوچک، بدون جمع‌آوری Metric، ساخت گزارش اجرایی یا تغییر کد.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `8 فاز چهارم پیشنهادی: تحلیل عملکرد کانال‌ها`.
- `docs/ARCHITECTURE.md`، بخش `16` برای مرز قابلیت‌های آینده و بخش `17`، ابهام `14`.
- `docs/ARCHITECTURE.md`، بخش‌های `7` و `14` فقط برای بررسی محدودیت User API، Retry و داده حساس آینده.

## وابستگی‌ها

- `T054` باید کامل شده باشد.

### پیش‌نیاز تصمیم

مالک محصول باید Metricها، منبع رسمی داده، Destinationهای هدف، cadence، retention، timezone، مخاطب گزارش و outcome تصمیم‌گیری را تصویب کند. نبود API/مجوز قابل اتکا یا تعریف Metric باید به‌عنوان Blocker ثبت شود، نه اینکه با داده فرضی پر شود.

## دامنه کار

- تعریف دقیق view/reaction/forward/growth و نسبت آن‌ها به Publication/Destination/زمان Snapshot.
- تعیین منبع داده، قابلیت/محدودیت Telegram API، cadence و backfill/retry در سطح نیازمندی.
- تعریف retention، aggregation windows، timezone، missing/late data و اصلاح Metric.
- تعریف گزارش روزانه/هفتگی، مقایسه category/source/time و هم‌بستگی AI با outcome بدون ادعای علیت.
- تعیین privacy، rate limit، observability، volume و acceptance criteria قابل اندازه‌گیری.
- همگام‌سازی اسناد و ساخت task specهای کوچک برای contract، collector اثبات‌شده، persistence، aggregation، report و stabilization فقط در صورت تصویب.

## خارج از دامنه

- هر فایل `src/`، `tests/`، `config/`، dashboard یا database migration.
- scrape غیرمصوب، داده ساختگی، انتخاب SDK یا endpoint بدون اثبات رسمی.
- ساخت query/report/collection Worker یا schema اجرایی.
- ادغام با فاز پنجم یا فعال‌کردن Task جدید.

## فایل‌ها و ماژول‌های مورد انتظار

- `docs/REQUIREMENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/DECISIONS.md` فقط برای تصمیم‌های مصوب داده/Metric.
- `docs/STATUS.md`
- task specهای جدید با ID یکتا زیر `docs/tasks/`؛ هیچ فایل اجرایی انتظار نمی‌رود.

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** cadence/retention/timezone/limits فقط به‌صورت requirement تعریف شود؛ Config runtime تغییر نکند.
- **Migration:** حجم، index، retention و backfill آینده باید در task مستقل با rollback/compatibility باشد؛ Migration اجرا نشود.
- **Compatibility:** اتصال Metric به Publication ID و تغییر تعریف Metric باید versioned و قابل بازتفسیر مستند شود.
- **Concurrency:** late/duplicate snapshots، collectorهای هم‌زمان و aggregation replay باید acceptance idempotency داشته باشند.
- **Security:** مجوز دسترسی کانال، privacy، حداقل‌سازی داده، redaction و rate-limit باید criteria آینده داشته باشند؛ credential ثبت نشود.

## معیارهای پذیرش عینی

1. هر Metric مصوب تعریف، واحد، منبع، timestamp و رفتار missing/correction روشن دارد.
2. امکان فنی/مجوز API با منبع معتبر ثبت یا Blocker صریح شده است.
3. retention/cadence/aggregation/report audience و معیارهای موفقیت آزمون‌پذیرند.
4. Requirements/Architecture/Roadmap و taskهای کوچک جدید سازگار و بدون Feature فرضی‌اند.
5. taskهای جدید ریسک volume، idempotency، rate limit، privacy و migration را پوشش می‌دهند.
6. هیچ code/test/config/schema اجرایی ساخته نشده است.

## تست‌های واحد الزامی

- `N/A`: Gate فقط تعریف محصول/داده و برنامه‌ریزی مستنداتی انجام می‌دهد.

## تست‌های یکپارچه‌سازی الزامی

- `N/A`: هیچ Collector یا Persistence ساخته نمی‌شود؛ feasibility evidence و تست‌های آینده در taskهای حاصل تعریف می‌شوند.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff فارسی، تعریف Metricها و cross-reference اسناد باید به‌صورت انسانی بازبینی شوند.

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- `docs/REQUIREMENTS.md` با قرارداد Metric/Report مصوب و out-of-scope صریح تکمیل شود.
- `docs/ARCHITECTURE.md` فقط معماری برنامه‌ریزی‌شده milestone فعال و constraints داده را نشان دهد.
- `docs/ROADMAP.md`، `docs/STATUS.md` و task specهای جدید همگام شوند؛ Decisionهای مهم در `docs/DECISIONS.md`.
- `docs/CODE_MAP.md` با ماژول خیالی به‌روزرسانی نشود.

## تعریف Done

Task زمانی Done است که فاز چهارم از پیشنهاد به قرارداد محصولی/داده‌ای آزمون‌پذیر و taskهای کوچک تبدیل، feasibility/Blockerها صریح، Quality Gate متن پاس و هیچ Feature code/test/config ساخته نشده باشد.
