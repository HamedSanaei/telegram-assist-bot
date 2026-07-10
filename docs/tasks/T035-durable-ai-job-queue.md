# T035 — صف AI پایدار، اولویت و Lease

## وضعیت

`Planned`

## هدف

ایجاد صف MongoDB ماندگار و یکتای AI Job با اولویت، Claim اتمیک و Lease منقضی‌شونده، بدون فراخوانی Provider یا پیاده‌سازی Routing/Fallback.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `11.13 صف پردازش هوش مصنوعی`.
- `docs/REQUIREMENTS.md`، بخش `11.14 اولویت‌بندی Jobهای هوش مصنوعی`.
- `docs/REQUIREMENTS.md`، بخش `11.18 عدم پردازش هم‌زمان تکراری`.
- `docs/ARCHITECTURE.md`، بخش `4` (`AIJob`)، بخش `5` (`EnqueueAIJob` و `ClaimAIJob`)، بخش `6` (`AIJobRepository`)، بخش‌های `9`، `11`، `12`، `14` و `15`.

## وابستگی‌ها

- `T004` و `T034` باید کامل شده باشند.

## دامنه کار

- تعریف lifecycle و مدل AI Job با statusهای لازم Requirement و version صریح.
- ساخت idempotency key از Post، Task، Prompt version و Schema version.
- `EnqueueAIJob` idempotent با priority و زمان اجرای بعدی.
- `ClaimAIJob` اتمیک بر اساس priority، due time و oldest-created با owner/lease.
- complete/fail/release/lease-expiry operations با ownership/version check.
- بازیابی Job پس از Restart و جلوگیری از Claim هم‌زمان تکراری.

## خارج از دامنه

- تماس AI Provider، HTTP، Validation پاسخ یا نتیجه AI.
- Retry/Fallback orchestration (`T039`) و Rate limit/Circuit (`T040`).
- Cache/Metrics (`T041`) و Featureهای T042 به بعد.
- Broker یا Queue درون‌حافظه‌ای به‌عنوان منبع حقیقت.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/ai_job.py`
- `src/telegram_assist_bot/application/ai/enqueue_ai_job.py`
- `src/telegram_assist_bot/application/ai/claim_ai_job.py`
- `src/telegram_assist_bot/application/ports/ai_job_repository.py`
- `src/telegram_assist_bot/infrastructure/mongodb/ai_job_repository.py`
- Index/Migrationهای MongoDB مربوط.
- `tests/unit/application/ai/test_ai_job_queue.py`
- `tests/integration/mongodb/test_ai_job_queue.py`
- `tests/integration/mongodb/test_ai_job_lease.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** priority mapping، lease و max attempts باید typed، bounded و Fail-fast باشند؛ Provider list در این Task مصرف نمی‌شود.
- **Migration:** Unique Index idempotency و Index claim روی status/priority/next_run_at/lease لازم است؛ duplicate موجود باید preflight گزارش شود نه حذف خودکار.
- **Compatibility:** status، key و version Schema پایدارند؛ افزودن status آینده با reader سازگار انجام شود.
- **Concurrency:** enqueue و claim با عملیات MongoDB اتمیک باشند؛ owner/version برای complete الزامی و Process lock ناکافی است.
- **Security:** متن کامل Post یا Secret در Log/index key قرار نگیرد؛ Hash پایدار و شناسه‌های غیرحساس استفاده شود.

## معیارهای پذیرش عینی

1. enqueue تکراری برای همان key یک Job دوم نمی‌سازد.
2. Jobهای Due ابتدا بر اساس priority و سپس ترتیب قطعی Claim می‌شوند.
3. دو Worker نمی‌توانند هم‌زمان مالک یک Job باشند.
4. Lease منقضی پس از Crash Job را قابل بازیابی می‌کند.
5. Worker بدون ownership/version نمی‌تواند Job را complete کند.
6. Restart هیچ Job پایدار را از بین نمی‌برد و هیچ Provider فراخوانی نمی‌شود.

## تست‌های واحد الزامی

- key generation و تفاوت prompt/schema/task.
- ترتیب priority/due/created و Transitionهای lifecycle.
- رد complete با owner/version اشتباه و behavior lease expiry.
- Validation تنظیمات priority/lease.

## تست‌های یکپارچه‌سازی الزامی

- Unique enqueue و Indexها روی MongoDB واقعی آزمایشی.
- Claim هم‌زمان چند Worker و اثبات یک owner.
- Crash/lease expiry/Restart و reclaim.
- ترتیب Claim چند priority با Clock قطعی.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/ai/test_ai_job_queue.py
uv run pytest tests/integration/mongodb/test_ai_job_queue.py tests/integration/mongodb/test_ai_job_lease.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت lifecycle، key، priority، claim و lease در `docs/ARCHITECTURE.md`.
- افزودن مدل/Use Case/Repository/Index به `docs/CODE_MAP.md`.
- مستندسازی کلیدهای Queue در example Config و به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و همین فایل.

## تعریف Done

Task زمانی Done است که enqueue/claim/lease با تست رقابتی و Restart MongoDB اثبات، Config و Migration امن، همه Quality Gateها موفق و هیچ کد Provider/Routing وارد Scope نشده باشد.
