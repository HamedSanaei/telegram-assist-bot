# T016 — Normalize و تشخیص Duplicate دقیق

## وضعیت

Planned

## هدف

تشخیص deterministic محتوای دقیقاً تکراری در پنجرهٔ ۱۴روزه با normalization محدود و نسخه‌شده، hash متن/Media و ثبت نتیجه، بدون Embedding یا داوری معنایی.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.9 بررسی محتوای تکراری`، زیربخش `تکرار دقیق`.
- `docs/REQUIREMENTS.md`، بخش `5.4 ذخیره اطلاعات پست`، نتیجه duplicate و retention.
- `docs/REQUIREMENTS.md`، بخش `15. تست‌ها`، Normalize و تشخیص پیام تکراری.
- `docs/ARCHITECTURE.md`، بخش `5. Use Caseهای Application`، `DetectExactDuplicate`.
- `docs/ARCHITECTURE.md`، بخش `15. راهبرد تست`، Normalize/Hash.
- `docs/ARCHITECTURE.md`، بخش `17. ابهام‌های باز`، بند ۸.

## وابستگی‌ها

- T004 — MongoDB و Persistence یکتای Post؛ باید Completed باشد.
- T012 — تست Restart و Stabilization دریافت؛ باید Completed باشد.

## محدوده

- تعریف normalization policy حداقلی، صریح و version‌شده برای duplicate exact؛ هر تبدیل Unicode/Persian باید موردبه‌مورد مستند شود.
- محاسبهٔ hash پایدار شامل متن/Caption normalized و hashهای Media مرتب‌شده در صورت وجود.
- استفادهٔ اختیاری از forward origin فقط به‌عنوان signal دقیق، اگر DTO موجود آن را قابل‌اعتماد فراهم کند.
- query Postهای غیرمنقضی ۱۴ روز گذشته با hash و حذف self.
- تولید/ثبت `DuplicateCheckResult` شامل is_duplicate، matched post، method، normalization version و timestamp.
- Transition اتمیک نتیجه و جلوگیری از اجرای هم‌زمان دوبارهٔ همان check.
- رفتار pipeline در exact duplicate فقط طبق policy موجود/تصمیم فعال Task؛ بدون اختراع threshold «بسیار نزدیک».

## خارج از محدوده

- fuzzy/near-text threshold، AI، Embedding و duplicate معنایی؛ T043.
- پاک‌سازی Username/link مقصدی؛ T017.
- hash algorithm برای dedup فایل storage در سطح زیرساخت به‌جز مصرف hash T013.
- مقایسهٔ بیش از ۱۴ روز یا cross-language similarity.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/duplicates/models.py`
- `src/telegram_assist_bot/application/text_normalization.py`
- `src/telegram_assist_bot/application/detect_exact_duplicate.py`
- توسعهٔ محدود `PostRepository`/Mongo index برای hash/window query.
- `tests/unit/application/test_text_normalization.py`
- `tests/unit/application/test_detect_exact_duplicate.py`
- `tests/integration/test_exact_duplicate_detection.py`

## نکات پیاده‌سازی

- متن اصلی هرگز overwrite نشود؛ normalized text/hash فیلد مشتق‌شده است.
- normalization نباید بی‌صدا `ی/ي`، `ک/ك`، ZWNJ، diacritic، case یا punctuation را تغییر دهد مگر تصمیم و test صریح.
- hash algorithm/version در داده ثبت شود تا migration ممکن باشد.
- **ریسک Configuration:** تصمیم reject/manual و هر گزینهٔ normalization فقط با schema T002؛ threshold معنایی ممنوع.
- **ریسک Migration:** تغییر normalization/hash version نیازمند coexistence/recompute برنامه‌ریزی‌شده است؛ این Task migration همهٔ داده‌های قدیمی ندارد.
- **ریسک Compatibility:** ترتیب Media hash canonical و serialization hash مستقل از Python runtime باشد.
- **ریسک Concurrency:** دو checker با conditional status/version فقط یک نتیجه canonical ثبت کنند.
- **ریسک Security:** متن normalized/raw در Log نیاید؛ فقط hash کوتاه/شناسه‌ها در context.

## معیارهای پذیرش عینی

1. محتوای برابر تحت policy دقیق hash یکسان و محتوای خارج policy hash متفاوت می‌گیرد.
2. Persian letters، ZWNJ و Emoji طبق جدول صریح بدون تغییر ناخواسته‌اند.
3. duplicate در بازهٔ ۱۴روزه matched و خارج بازه نادیده گرفته می‌شود.
4. self-match رخ نمی‌دهد و matched ID/method/version ثبت می‌شود.
5. ترتیب Media canonical است و تغییر Media نتیجه را تغییر می‌دهد.
6. اجرای هم‌زمان یک check یک نتیجهٔ پایدار می‌سازد.
7. متن اصلی دست‌نخورده می‌ماند و هیچ similarity/AI اجرا نمی‌شود.

## Unit Testهای الزامی

- جدول normalization whitespace/newline و همهٔ تبدیل‌های مجاز/غیرمجاز.
- Persian `ی/ي`، `ک/ك`، ZWNJ، combining mark، Emoji و URL نمونه.
- hash deterministic متن/Caption/Media و version.
- window boundary، self exclusion و result model.
- conflict concurrent و policy ادامه/reject فقط در حد تصمیم Task.

## Integration Testهای الزامی

- MongoDB آزمایشی با duplicate داخل/خارج ۱۴ روز و index/query واقعی.
- دو checker هم‌زمان برای یک Post.
- round-trip hash/result فارسی و Restart بدون اجرای نتیجهٔ دوم.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_text_normalization.py tests/unit/application/test_detect_exact_duplicate.py
uv run pytest tests/integration/test_exact_duplicate_detection.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

بازبینی دستی diff/fixture فارسی و policy normalization، اجرای `git diff --check` و عدم skip MongoDB الزامی است.

## به‌روزرسانی‌های مستندات

- ثبت Status/verification و به‌روزرسانی T016 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن normalization/hash/query به `docs/CODE_MAP.md`.
- ثبت policy/version/window و رفتار duplicate در `docs/ARCHITECTURE.md`.
- ثبت تصمیم normalization/threshold exact در `docs/DECISIONS.md`؛ ابهام «بسیار نزدیک» برای T043/Requirement باز بماند.

## تعریف انجام‌شدن

- policy دقیق، version‌شده و با Persian tests پاس شده است.
- query/window/concurrency واقعی روی MongoDB پاس شده‌اند.
- Quality Gate و UTF-8 پاس شده و متن اصلی تغییر نکرده است.
- هیچ semantic/fuzzy/AI behavior اضافه نشده است.
