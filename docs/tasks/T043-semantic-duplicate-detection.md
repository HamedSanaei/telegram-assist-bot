# T043 — تشخیص Duplicate معنایی ۱۴روزه

## وضعیت

Completed

## هدف

تشخیص شباهت معنایی پست جدید در پنجره ۱۴روزه با AI Pipeline، ذخیره نتیجه قابل ردیابی و اعمال سیاست صریح رد یا بررسی دستی، بدون تغییر تشخیص Duplicate دقیق.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.9` «بررسی محتوای تکراری»، زیربخش «تکرار معنایی».
- `docs/REQUIREMENTS.md`، بخش `5.21` فقط مرحله Duplicate معنایی.
- `docs/ARCHITECTURE.md`، بخش‌های `4`، `5`، `9`، `12` و `17` بند `9`.

## وابستگی‌ها

- T016 — Normalize و Duplicate دقیق.
- T035 — صف AI پایدار، اولویت و Lease.
- T039 — Routing، Retry، Fallback و شکست نهایی.
- T041 — Cache، Audit و آمار Provider.

آستانهٔ صریح `0.88` با مرز `similarity >= threshold` و policyهای
`reject`، `manual_review` و `continue_processing` در ADR-031 تصویب شده‌اند؛
هیچ‌کدام default پنهان Runtime نیستند.

## دامنه

- Query Port برای نامزدهای غیرمنقضی ۱۴ روز گذشته با projection محدود و ترتیب قطعی.
- enqueue یکتای Task معنایی پس از عبور از Duplicate دقیق.
- Use Case `DetectSemanticDuplicate` و mapping نتیجه شامل duplicate، similarity، matched post، reason، method و model.
- اعتبارسنجی آستانه Config و تصمیم اتمیک مطابق policy مصوب (رد/بررسی دستی/ادامه).
- جلوگیری از self-match و نامزد خارج از پنجره/منقضی.

## خارج از دامنه

- الگوریتم/Embedding store یا Vector DB جدید بدون تصمیم معماری.
- تغییر Normalize/Hash و Duplicate دقیق T016.
- تعیین خودکار آستانه یا policy محصول.
- Ranking عمومی، clustering یا تحلیل عملکرد.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/use_cases/detect_semantic_duplicate.py`
- `src/telegram_assist_bot/application/ports/semantic_duplicate_candidates.py`
- `src/telegram_assist_bot/application/ai/task_handlers/semantic_duplicate.py`
- تغییر محدود در Post repository زیر `src/telegram_assist_bot/infrastructure/mongodb/`
- `tests/unit/application/test_detect_semantic_duplicate.py`
- `tests/unit/domain/test_duplicate_check_result.py`
- `tests/integration/workflows/test_semantic_duplicate_detection.py`

## نکات پیاده‌سازی

- **Configuration:** threshold، feature flag و duplicate policy Typed و دارای بازه معتبر باشند؛ نبود تصمیم معتبر خطای Configuration است.
- **Migration:** فیلدهای result/matched id و Index لازم برای query پنجره زمانی باید Migration سازگار و explain/query test داشته باشند؛ Vector storage خارج از Scope است.
- **Compatibility:** `DuplicateCheckResult` روش exact و semantic را متمایز کند و نتیجه T016 بازنویسی نشود.
- **Concurrency:** completion AI با expected version اعمال شود؛ دو Worker یا نتیجه دیرهنگام نباید Post terminal را تغییر دهند.
- **Security:** فقط داده لازم نامزدها به Provider ارسال شود؛ شناسه داخلی/متادیتای حساس و Secret در Prompt/Log قرار نگیرد.
- **Persian:** ZWNJ، حروف فارسی/عربی و Emoji فقط طبق normalization مصوب T016 پردازش شوند؛ normalization تازه ممنوع است.

## معیارهای پذیرش عینی

1. فقط پست‌های معتبر ۱۴ روز گذشته و غیرخودی به‌عنوان نامزد انتخاب می‌شوند.
2. feature خاموش هیچ Job معنایی ایجاد نمی‌کند.
3. Job منطقی برای Post/Task/Prompt/Schema یکتا است.
4. نتیجه معتبر همه فیلدهای لازم و matched post موجود را ذخیره می‌کند.
5. similarity برابر مرز threshold رفتاری قطعی و تست‌شده طبق تصمیم مصوب دارد.
6. policy رد/دستی/ادامه فقط از Config اجرا و audit می‌شود.
7. شکست AI طبق policy T039 ثبت می‌شود و Duplicate جعلی تولید نمی‌کند.
8. Duplicate دقیق T016 همچنان short-circuit می‌کند و Semantic اجرا نمی‌شود.

## Unit Testهای الزامی

- فیلتر پنجره ۱۴روزه، self-match و expiry با Clock ثابت.
- مرز threshold و policyهای مصوب.
- mapping/validation نتیجه، matched id نامعتبر و نتیجه غیرتکراری.
- short-circuit Duplicate دقیق و feature flag.
- idempotency و conflict completion.
- نمونه‌های فارسی، ZWNJ و متن متفاوت با معنای fixtureشده؛ بدون تماس زنده AI.

## Integration Testهای الزامی

- Query MongoDB برای پنجره ۱۴روزه و projection/index مورد نیاز.
- Workflow با Provider Fake برای match/non-match و ثبت نتیجه/Transition.
- اجرای هم‌زمان completion و جلوگیری از Transition تکراری.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_detect_semantic_duplicate.py tests/unit/domain/test_duplicate_check_result.py
uv run pytest tests/integration/workflows/test_semantic_duplicate_detection.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

Diff و fixtureهای فارسی/مرز زمانی دستی بازبینی شوند.

## بروزرسانی مستندات الزامی

- بروزرسانی همین Task، `docs/ROADMAP.md`، `docs/STATUS.md` و `docs/CODE_MAP.md`.
- ثبت query/data flow واقعی در `docs/ARCHITECTURE.md`.
- ثبت آستانه و policy نهایی در `docs/DECISIONS.md` یا Requirement اصلاح‌شده؛ بدون آن Task کامل نیست.

## تعریف Done

- پنجره ۱۴روزه، threshold و policy مستند و با تست‌های مرزی اثبات شده‌اند.
- نتیجه semantic با exact سازگار، idempotent و concurrency-safe است.
- هیچ Vector DB/Provider/Default جدید بی‌تصمیم افزوده نشده است.
- تمام فرمان‌های راستی‌آزمایی و کنترل UTF-8/Persian پاس شده‌اند.

## نتیجهٔ راستی‌آزمایی

- آزمون‌های واحد متمرکز: `15 passed`.
- آزمون MongoDB متمرکز: `1 passed`.
- suite کامل non-live روی Python 3.12 و MongoDB محلی: `1189 passed`، بدون شکست یا skip.
- Ruff، format، mypy، lock، text-integrity changed/all و `git diff --check` در پایان Task اجرا شدند.
