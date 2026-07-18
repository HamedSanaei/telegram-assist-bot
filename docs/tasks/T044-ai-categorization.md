# T044 — دسته‌بندی AI با Fallback پایه

## وضعیت

Completed

## هدف

افزودن دسته‌بندی AI به‌عنوان یک راه قابل تنظیم برای تعیین Category پست و بازگشت صریح به دسته‌بندی پایه T018 هنگام شکست، بدون تغییر منابع دسته‌بندی دستی یا قواعد کلمه‌کلیدی.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.11` «دسته‌بندی پست».
- `docs/REQUIREMENTS.md`، بخش `11.12` «رفتار در صورت شکست تمام Providerها».
- `docs/REQUIREMENTS.md`، بخش `5.21` فقط مرحله تعیین دسته‌بندی.
- `docs/ARCHITECTURE.md`، بخش‌های `4`، `5` و `12`.

## وابستگی‌ها

- T018 — دسته‌بندی پایه و Override.
- T035 — صف AI پایدار، اولویت و Lease.
- T039 — Routing، Retry، Fallback و شکست نهایی.

## دامنه

- enqueue یکتای Task `categorization` طبق feature flag و ترتیب روش‌های مصوب.
- Handler/Application Use Case برای تبدیل نتیجه استاندارد AI به Category داخلی موجود.
- اعتبارسنجی Category فقط در برابر Taxonomy تنظیم‌شده و فعال.
- ثبت منبع تصمیم، confidence، provider/model و Prompt version.
- Fallback به دسته‌بندی پایه T018 در شکست همه Providerها یا نتیجه خارج از Taxonomy، طبق سیاست صریح.
- حفظ Override دستی مدیر به‌عنوان تصمیم با اولویت بالاتر.

## خارج از دامنه

- تعریف Taxonomy جدید یا تغییر UX Override دستی.
- آموزش مدل، Prompt engineering گسترده یا افزودن Provider.
- Multi-label classification، استخراج topic/keyword یا رتبه‌بندی.
- تغییر هدر پیام مدیر جز استفاده از جریان موجود دسته‌بندی.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/use_cases/categorize_with_ai.py`
- `src/telegram_assist_bot/application/ai/task_handlers/categorization.py`
- تغییر محدود در مدل Category/Post موجود زیر `src/telegram_assist_bot/domain/`
- تغییر محدود در repository mapper زیر `src/telegram_assist_bot/infrastructure/mongodb/`
- `tests/unit/application/test_categorize_with_ai.py`
- `tests/unit/domain/test_post_category_source.py`
- `tests/integration/workflows/test_ai_categorization.py`

## نکات پیاده‌سازی

- **Configuration:** feature flag، Taxonomy و failure/fallback policy باید Typed باشند؛ نام Category ناشناخته نباید خودکار به Taxonomy افزوده شود.
- **Migration:** اگر metadata منبع دسته‌بندی افزوده می‌شود، Migration سازگار با اسناد قدیمی و Default خواندن صریح لازم است.
- **Compatibility:** قرارداد دسته‌بندی پایه و Override T018 حفظ شود؛ نتیجه Provider-specific وارد Domain نشود.
- **Concurrency:** completion دیرهنگام AI نباید Override دستی جدیدتر یا Post terminal را بازنویسی کند؛ update با expected version/source priority انجام شود.
- **Security:** متن لازم پست بدون metadata حساس ارسال شود و reason/خطا Sanitized باشد.
- **Persian:** نام‌های فارسی Category باید UTF-8 و بدون normalization ضمنی مقایسه شوند؛ mapping alias فقط با Configuration صریح مجاز است.

## معیارهای پذیرش عینی

1. feature خاموش یا روش AI غیرفعال هیچ Job دسته‌بندی نمی‌سازد.
2. نتیجه معتبر فقط به Category فعال Taxonomy نگاشت می‌شود.
3. Category، منبع `ai` و Metadata مدل/Prompt در رکورد ذخیره می‌شوند.
4. شکست همه Providerها و Category نامعتبر طبق policy به دسته‌بندی پایه T018 Fallback می‌کنند و نتیجه AI جعلی ثبت نمی‌شود.
5. Override دستی موجود یا هم‌زمان همیشه نسبت به completion AI اولویت دارد.
6. completion/Retry تکراری idempotent است.
7. نتیجه دسته‌بندی در جریان موجود هدر قابل مصرف است، بدون افزودن UX تازه.

## Unit Testهای الزامی

- feature flag و ترتیب روش‌ها.
- Category معتبر، ناشناخته، غیرفعال و alias صریح.
- Fallback به default/keyword پایه در شکست.
- رقابت AI completion با Override دستی.
- idempotency و ثبت source metadata.
- Categoryهای فارسی و ZWNJ بدون Mojibake.

## Integration Testهای الزامی

- Workflow Post + AI Job + Provider Fake برای success/failure/invalid category.
- Persistence source metadata و خواندن سازگار سند قدیمی.
- completion هم‌زمان و حفظ Override دستی.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_categorize_with_ai.py tests/unit/domain/test_post_category_source.py
uv run pytest tests/integration/workflows/test_ai_categorization.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

Diff فارسی و Category fixtureها باید دستی بازبینی شوند.

## بروزرسانی مستندات الزامی

- بروزرسانی همین Task، `docs/ROADMAP.md`، `docs/STATUS.md` و `docs/CODE_MAP.md`.
- همگام‌سازی precedence دسته‌بندی در `docs/ARCHITECTURE.md` اگر نسبت به طرح فعلی جزئیات جدید مصوب شد.
- ثبت تغییر مهم Taxonomy/precedence فقط با تصمیم صریح در `docs/DECISIONS.md`.

## تعریف Done

- مسیر AI و Fallback پایه با تمام معیارها و تست‌ها اثبات شده‌اند.
- Override دستی، سازگاری داده و Persian/UTF-8 حفظ شده‌اند.
- Taxonomy یا Provider جدید اختراع نشده و Scope به تحلیل موضوع گسترش نیافته است.

## نتیجهٔ نهایی راستی‌آزمایی

- دسته‌بندی AI با `category_id`، prompt نسخهٔ `2.0.0` و schema نسخهٔ `2` پیاده‌سازی شد.
- ترتیب صریح روش‌ها، fallback پایه، alias دقیق و تقدم همیشگی override دستی با آزمون واحد و MongoDB اثبات شد.
- suite کامل non-live با Python 3.12 و MongoDB محلی پس از تکمیل مستندات اجرا شد.
- این قابلیت عمداً به Runtime، Worker، CLI یا listener تلگرام متصل نشده است.
