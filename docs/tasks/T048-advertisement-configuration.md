# T048 — مدل و Validation تنظیم تبلیغات

## وضعیت

Planned

## هدف

تعریف مدل Typed و اعتبارسنجی Fail-fast برای Campaignهای تبلیغاتی Config-driven فاز دوم، بدون دریافت پست، ساخت Slot یا انتشار تبلیغ.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `6` «فاز دوم: مدیریت پست‌های تبلیغاتی زمان‌بندی‌شده».
- `docs/REQUIREMENTS.md`، بخش `6.2` «زمان‌بندی چندگانه» فقط فیلدهای Configuration زمان/روز/منطقه زمانی.
- `docs/REQUIREMENTS.md`، بخش `4` «مدیریت تنظیمات» فقط تنظیمات پست‌های تبلیغاتی.
- `docs/ARCHITECTURE.md`، بخش‌های `4`، `13` و `16` و بخش `17` بندهای `11` و `12`.

## وابستگی‌ها

- T002 — Configuration و Secret Validation.
- T047 — پذیرش End-to-end فاز اول.

## دامنه

- مدل Typed برای Campaign: شناسه/نام، enabled، source URL، source channel، destinationها، weekdays، چند time، date range، timezone، publication mode، priority، minimum gap، error policy و max retries.
- Validation تجمعی با مسیر دقیق فیلد برای URL تلگرام، زمان‌ها، ZoneInfo، بازه تاریخ، مقادیر مثبت و ارجاع مقصدهای موجود.
- جلوگیری از شناسه Campaign تکراری و Slot time تکراری در یک Campaign.
- افزودن نمونه امن و غیرحساس به `config/configuration.example.json`.
- mapper Configuration به مدل Application-owned بدون ایجاد Mongo Document یا Job.
- تثبیت رفتار fieldهای اختیاری فقط مطابق تصمیم‌های صریح موجود.

## خارج از دامنه

- Resolve یا دریافت URL تبلیغ (T049).
- Cache محتوا و سیاست refresh پس از edit منبع (T049).
- محاسبه/ذخیره Slot و Worker (T050).
- انتشار، Retry/Audit اجرا و گزارش مدیر (T051–T053).
- تعیین سیاست Collision تبلیغ با صف عادی (T052)؛ هیچ Default بی‌صدایی در این Task مجاز نیست.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/advertisements/campaign.py`
- `src/telegram_assist_bot/application/config/advertisements.py`
- تغییر محدود در loader/validator زیر `src/telegram_assist_bot/infrastructure/config/`
- `config/configuration.example.json`
- `tests/unit/config/test_advertisement_configuration.py`
- `tests/integration/config/test_advertisement_configuration_loading.py`

## نکات پیاده‌سازی

- **Configuration:** Default سیاست Collision و Cache refresh نامشخص است؛ فیلدهای مربوط یا باید required باشند یا پیاده‌سازی تا تصمیم مستند متوقف شود. مقدار دلخواه انتخاب نشود.
- **Migration:** این Task داده MongoDB نمی‌نویسد و Migration دیتابیس ندارد. تغییر Schema فایل config باید backward compatibility و پیام migration/config error روشن داشته باشد.
- **Compatibility:** Configuration قدیمی بدون بخش advertisements باید طبق feature semantics مصوب همچنان معتبر باشد؛ کلیدهای موجود rename نشوند.
- **Concurrency:** N/A برای state توزیع‌شده؛ مدل باید Immutable/read-only پس از startup باشد تا Workerها snapshot متناقض نگیرند. Dynamic reload خارج از Scope است.
- **Security:** example فقط URL عمومی خیالی و Secret placeholder داشته باشد؛ token/API key/session path خصوصی ممنوع است. Error نباید مقدار حساس کامل را echo کند.
- **زمان:** weekday/time و date با ZoneInfo معتبر شوند؛ تبدیل به UTC و DST slot behavior در T050 است.

## معیارهای پذیرش عینی

1. یک Campaign کامل معتبر به مدل Typed و Immutable تبدیل می‌شود.
2. همه فیلدهای الزامی بخش `6` پوشش و خطاهای چندگانه یک‌جا با path دقیق گزارش می‌شوند.
3. URL غیرتلگرامی/نامعتبر، timezone نامعتبر، روز/زمان نامعتبر، date range وارونه، destination ناشناخته و retry منفی رد می‌شوند.
4. شناسه Campaign و timeهای تکراری با خطای روشن رد می‌شوند.
5. بخش absent/disabled مطابق رفتار سازگار مصوب هیچ Job یا اتصال خارجی ایجاد نمی‌کند.
6. example config معتبر، UTF-8، بدون Secret و با `ensure_ascii=False` در هر مسیر تولید JSON است.
7. هیچ policy مبهم Collision یا Cache update بی‌صدا Default نمی‌شود.
8. هیچ Mongo collection، Job، fetch یا publication در این Task ساخته نمی‌شود.

## Unit Testهای الزامی

- parse Campaign معتبر با چند روز/time/destination.
- هر خطای validation ذکرشده و گزارش تجمعی pathها.
- duplicate campaign/time و مرز date/retry/gap.
- absent/disabled configuration و backward compatibility.
- JSON نمونه با متن فارسی، UTF-8 و عدم حضور Secret واقعی.

## Integration Testهای الزامی

- بارگذاری `config/configuration.example.json` از loader واقعی و تبدیل به مدل Typed.
- fail-fast startup config پیش از هر external connection با config نامعتبر.
- بررسی resolution ارجاع destinationها در configuration کامل آزمایشی.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/config/test_advertisement_configuration.py
uv run pytest tests/integration/config/test_advertisement_configuration_loading.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

فایل JSON نمونه و Diff فارسی باید دستی بازبینی و نبود credential تأیید شود.

## بروزرسانی مستندات الزامی

- بروزرسانی همین Task، `docs/ROADMAP.md`، `docs/STATUS.md` و `docs/CODE_MAP.md`.
- ثبت مدل config واقعی و restart-only behavior در `docs/ARCHITECTURE.md`.
- اگر policyهای مبهم Cache/Collision تعیین شدند، تصمیم باید صریحاً در `docs/DECISIONS.md` و Requirement مرتبط ثبت شود؛ T048 نباید آن‌ها را ضمنی تعیین کند.
- بروزرسانی راهنمای Configuration/اجرای پروژه در صورت وجود.

## تعریف Done

- مدل و Validation همه معیارها و تست‌ها را پاس کرده و example config معتبر و امن است.
- هیچ Feature دریافت/زمان‌بندی/انتشار تبلیغ ساخته نشده است.
- ابهام‌های Collision/Cache بی‌صدا حل نشده‌اند.
- تمام Quality Gateها، بررسی UTF-8/Persian و بازبینی Secret پاس شده‌اند.
