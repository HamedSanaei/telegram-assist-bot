# T041 — Cache، Audit و آمار Providerهای AI

## وضعیت

Completed

## هدف

کاهش مصرف سهمیه با Cache نسخه‌دار و ایجاد Audit/Metrics امن و قابل پرس‌وجو برای هر Attempt AI، بدون تغییر ترتیب هوشمند Providerها.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `11.15` «Cache نتایج هوش مصنوعی».
- `docs/REQUIREMENTS.md`، بخش `11.16` «ثبت Prompt Version».
- `docs/REQUIREMENTS.md`، بخش `11.17` «لاگ و آمار Providerها».
- `docs/ARCHITECTURE.md`، بخش‌های `9`، `12`، `14` و `15`.

## وابستگی‌ها

- T004 — MongoDB و Persistence یکتای Post.
- T039 — Routing، Retry، Fallback و شکست نهایی.
- T040 — Rate Limit، Cooldown و Circuit Breaker.

## دامنه

- ساخت کلید Cache قطعی از Task type، hash ورودی Normalizeشده، Prompt version، Schema version و زبان.
- خواندن Cache معتبر پیش از Reservation/تماس خارجی و نوشتن نتیجه معتبر پس از موفقیت.
- TTL قابل تنظیم برای هر Task و invalidation طبیعی با تغییر نسخه‌ها.
- ثبت Audit هر Attempt/Fallback و Cache hit با داده Sanitized.
- به‌روزرسانی اتمیک شمارنده‌های تجمعی Provider/Model و زمان‌های آخرین موفقیت/خطا.
- اتصال محدود به Pipeline T039/T040 بدون تغییر قرارداد Featureها.

## خارج از دامنه

- تغییر خودکار اولویت Provider با Metrics.
- داشبورد، API گزارش یا Billing دقیق هزینه.
- ذخیره اجباری Prompt/Response خام؛ این رفتار فقط طبق Configuration مصوب مجاز است.
- تشخیص تبلیغ، Duplicate، دسته‌بندی و امتیازدهی.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/ai/cache_key.py`
- `src/telegram_assist_bot/application/ports/ai_cache_repository.py`
- `src/telegram_assist_bot/application/ports/ai_audit_repository.py`
- `src/telegram_assist_bot/infrastructure/mongodb/ai_cache_repository.py`
- `src/telegram_assist_bot/infrastructure/mongodb/ai_audit_repository.py`
- `src/telegram_assist_bot/infrastructure/mongodb/provider_metrics_repository.py`
- `tests/unit/application/ai/test_ai_cache_key.py`
- `tests/unit/application/ai/test_ai_audit_redaction.py`
- `tests/integration/mongodb/test_ai_cache_audit_metrics.py`

## نکات پیاده‌سازی

- **Configuration:** TTL و نگهداری raw data برای هر Task Typed باشد؛ raw prompt/response پیش‌فرض امن و مطابق تصمیم قبلی بماند.
- **Migration:** Collectionها، TTL/Unique indexها و Schema version باید Migration صریح و سازگار با داده قدیمی داشته باشند.
- **Compatibility:** Cache فقط `AIResult` استاندارد را برگرداند و cache hit از نظر مصرف‌کننده با نتیجه معتبر عادی سازگار باشد، با Metadata صریح `cache_hit`.
- **Concurrency:** insert/upsert Cache و increment Metrics اتمیک باشند؛ Race چند Worker نباید چند نتیجه متناقض یا شمارنده از‌دست‌رفته بسازد.
- **Security:** متن خام، Prompt، Header، API Key و PII در Audit/Log ممنوع است مگر ذخیره raw به‌طور صریح فعال و Sanitization/Retention آن مصوب شده باشد.
- Normalize ورودی نباید متن فارسی، ZWNJ یا Entityهای Telegram را بی‌قاعده تغییر دهد؛ از قرارداد Normalize مصوب استفاده شود.

## معیارهای پذیرش عینی

1. ورودی‌های همسان با Task/Prompt/Schema/language یکسان کلید پایدار یکسان می‌سازند.
2. تغییر هر جزء نسخه یا زبان Cache miss ایجاد می‌کند.
3. Cache hit معتبر بدون Reservation و تماس Provider نتیجه استاندارد برمی‌گرداند.
4. ورودی منقضی یا Result نامعتبر استفاده نمی‌شود.
5. Attemptها، Retry/Fallback، latency، error category، token count موجود و cache usage ثبت می‌شوند.
6. Metrics تجمعی در updateهای هم‌زمان شمارش را از دست نمی‌دهد.
7. Audit و Logها Secret یا متن خام ممنوع را افشا نمی‌کنند.
8. Cache failure به‌عنوان خرابی جانبی نتیجه AI معتبر را خراب نمی‌کند، اما خطا مشاهده‌پذیر ثبت می‌شود.

## Unit Testهای الزامی

- ثبات و جداسازی کلید Cache برای همه اجزای لازم.
- متن فارسی، ZWNJ و زبان در hash/key.
- Cache hit/miss/expiry و Metadata نتیجه.
- Redaction Audit و mapping دسته خطا.
- محاسبه شمارنده و میانگین/latency طبق قرارداد مصوب.

## Integration Testهای الزامی

- Unique/TTL indexهای Cache و expiry semantics در MongoDB.
- upsert هم‌زمان یک Cache key و increment هم‌زمان Metrics.
- Pipeline با Cache hit که هیچ Fake Provider call ثبت نمی‌کند.
- Audit شکست و موفقیت با بررسی عدم حضور Secret fixture.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/ai/test_ai_cache_key.py tests/unit/application/ai/test_ai_audit_redaction.py
uv run pytest tests/integration/mongodb/test_ai_cache_audit_metrics.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

Diff فارسی و نمونه Cache key/Audit به‌صورت دستی بازبینی شود.

## بروزرسانی مستندات الزامی

- بروزرسانی همین Task، `docs/ROADMAP.md`، `docs/STATUS.md` و `docs/CODE_MAP.md`.
- ثبت Collectionها، Indexها، Retention و جریان Cache/Audit واقعی در `docs/ARCHITECTURE.md`.
- اگر ذخیره raw data یا Retention به تصمیم مهم نیاز داشت، ثبت صریح در `docs/DECISIONS.md`.

## تعریف Done

- Cache نسخه‌دار، Audit امن و Metrics هم‌زمانی‌امن همه معیارها و تست‌ها را پاس کرده‌اند.
- Persian/UTF-8 و Secret redaction دستی و خودکار بررسی شده‌اند.
- Auto-routing، Dashboard و Featureهای AI بعدی وارد Scope نشده‌اند.

## نتیجهٔ راستی‌آزمایی

- آزمون‌های متمرکز T041 با MongoDB واقعی: `16 passed`.
- suite کامل non-live روی Python 3.12: `1127 passed`.
- `uv lock --check`، Ruff، format، mypy، هر دو بررسی text-integrity و
  `git diff --check` پاس شدند.
- Cache، Audit و Metrics فقط در pipeline ایزولهٔ AI ترکیب شده‌اند و به Runtime،
  Worker، CLI، Telegram، Approval، Publication یا Scheduling متصل نشده‌اند.
