# T042 — تشخیص تبلیغ و سیاست شکست

## وضعیت

Planned

## هدف

افزودن یک برش Application برای تشخیص تبلیغاتی بودن پست از طریق AI Job، ذخیره نتیجه استاندارد و اجرای سیاست صریح شکست/رد در Pipeline محتوا.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.8` «بررسی تبلیغاتی بودن پست».
- `docs/REQUIREMENTS.md`، بخش `5.21` «Pipeline کامل فاز اول» فقط مرحله تشخیص تبلیغ.
- `docs/REQUIREMENTS.md`، بخش `11.12` «رفتار در صورت شکست تمام Providerها».
- `docs/ARCHITECTURE.md`، بخش‌های `4`، `5`، `12` و `17` بند `8`.

## وابستگی‌ها

- T019 — Stabilization آماده‌سازی محتوا.
- T035 — صف AI پایدار، اولویت و Lease.
- T039 — Routing، Retry، Fallback و شکست نهایی.
- T041 — Cache، Audit و آمار Provider.

پیش از پیاده‌سازی باید سیاست شکست نهایی تشخیص تبلیغ و مسیر «بررسی دستی» از Configuration/تصمیم مصوب قابل استخراج باشد؛ گزینه پیش‌فرض نباید بی‌صدا انتخاب شود.

## دامنه

- Command/Use Case برای enqueue یکتای `advertisement_detection`.
- mapping نتیجه AI استاندارد به `AdvertisementCheckResult` شامل نتیجه، confidence، reason کوتاه، provider/model، زمان و Prompt version.
- Transition اتمیک پست برای رد تبلیغاتی و جلوگیری از ادامه Pipeline عادی.
- اعمال یکی از سیاست‌های از قبل مصوب: ادامه، توقف، retry آینده یا بررسی دستی در شکست همه Providerها.
- رعایت feature flagهای سراسری و per-source موجود.

## خارج از دامنه

- طراحی Provider/Prompt/Schema جدید؛ قراردادهای T034–T041 مصرف می‌شوند.
- انتشار تبلیغات زمان‌بندی‌شده Milestone 6.
- ارسال UX خلاصه رد به مدیر، مگر Port/رفتار آن قبلاً مصوب و صرفاً invocation موجود باشد.
- تکرار معنایی، دسته‌بندی و scoring.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/use_cases/detect_advertisement.py`
- `src/telegram_assist_bot/application/ai/task_handlers/advertisement_detection.py`
- تغییر محدود در مدل/Transition موجود زیر `src/telegram_assist_bot/domain/`
- تغییر محدود در repository mapping موجود زیر `src/telegram_assist_bot/infrastructure/mongodb/`
- `tests/unit/application/test_detect_advertisement.py`
- `tests/unit/domain/test_advertisement_transition.py`
- `tests/integration/workflows/test_advertisement_detection.py`

## نکات پیاده‌سازی

- **Configuration:** feature flag و failure policy باید Typed و صریح باشند؛ نبود policy معتبر Fail-fast است و Default جدید اختراع نمی‌شود.
- **Migration:** اگر فیلد نتیجه/وضعیت در Schema موجود ناقص است، Migration سازگار با اسناد قدیمی و تست mapping لازم است.
- **Compatibility:** متن/Caption/Entity اصلی Immutable می‌مانند؛ نتیجه Provider-specific وارد Domain نمی‌شود.
- **Concurrency:** enqueue و Transition رد/ادامه باید با idempotency key و expected version انجام شوند؛ completion دیرهنگام نباید Post منقضی/منتشرشده را به عقب برگرداند.
- **Security:** reason و خطا برای مدیر Sanitized و محدود باشد؛ Prompt/response خام و Secret افشا نشود.
- متن فارسی برای AI باید بدون normalization خارج از قرارداد ارسال/Hash شود.

## معیارهای پذیرش عینی

1. وقتی feature خاموش است هیچ AI Job ساخته نمی‌شود و Pipeline طبق قرارداد موجود ادامه می‌یابد.
2. وقتی فعال است برای هر Post/Prompt/Schema فقط یک Job منطقی ساخته می‌شود.
3. نتیجه معتبر با همه Metadata الزامی ذخیره می‌شود.
4. نتیجه تبلیغاتی Transition اتمیک به `RejectedAsAdvertisement` می‌دهد و مراحل عادی بعدی اجرا نمی‌شوند.
5. نتیجه غیرتبلیغاتی فقط مرحله درست Pipeline را جلو می‌برد.
6. شکست همه Providerها دقیقاً سیاست Config مصوب را اجرا و آن را audit می‌کند؛ هیچ نتیجه ساختگی ندارد.
7. Retry/completion تکراری اثر جانبی یا Transition دوم ایجاد نمی‌کند.

## Unit Testهای الزامی

- feature flag سراسری و per-source.
- mapping نتیجه معتبر و محدوده confidence.
- Transition تبلیغ/غیرتبلیغ و state conflict.
- هر failure policy مصوب، از جمله retry/manual/continue/stop.
- idempotent completion و Post منقضی/terminal.
- حفظ متن فارسی و Prompt version.

## Integration Testهای الزامی

- Post repository + AI job/result با MongoDB و Gateway Fake برای نتیجه تبلیغاتی/غیرتبلیغاتی.
- شکست همه Providerها و اجرای policy بدون Job تکراری.
- رقابت دو completion و تضمین یک Transition معتبر.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_detect_advertisement.py tests/unit/domain/test_advertisement_transition.py
uv run pytest tests/integration/workflows/test_advertisement_detection.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

Diff فارسی و fixtureهای متن/Reason دستی بازبینی شوند.

## بروزرسانی مستندات الزامی

- بروزرسانی همین Task، `docs/ROADMAP.md`، `docs/STATUS.md` و `docs/CODE_MAP.md`.
- همگام‌سازی جریان و Transition واقعی در `docs/ARCHITECTURE.md`.
- ثبت سیاست failure/manual review در `docs/DECISIONS.md` یا اصلاح Requirement پیش از ادعای تکمیل.

## تعریف Done

- سیاست شکست صریح و مستند است و همه مسیرهای flag/result/failure با تست اثبات شده‌اند.
- تشخیص تبلیغ idempotent، هم‌زمانی‌امن و بدون نشت Provider/Secret است.
- تبلیغات زمان‌بندی‌شده یا سایر Featureهای AI وارد Scope نشده‌اند.
- همه Quality Gateها و بررسی Persian/UTF-8 پاس شده‌اند.
