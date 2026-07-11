# T029 — Idempotency و Retry انتشار

## وضعیت

`Planned`

## هدف

سخت‌کردن انتشار متن/Media در برابر درخواست تکراری، Worker هم‌زمان، Restart و خطا با Publication claim اتمیک، کلید یکتا و Retry محدود و failure-aware، بدون ساخت Scheduler.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `5.17 انتشار فوری`، به‌ویژه جلوگیری از انتشار تکراری و Retry محدود.
- `docs/REQUIREMENTS.md`، بخش `13 مدیریت خطا و Retry`.
- `docs/ARCHITECTURE.md`، بخش `4` (`Publication`)، بخش `6` (`PublicationRepository`)، بخش `9` و بخش `14` (`Retry`، `Idempotency` و `Concurrency`).
- `docs/ARCHITECTURE.md`، بخش `15`، سناریوهای Restart/Concurrency.

## وابستگی‌ها

- `T004`، `T005`، `T027` و `T028` باید کامل شده باشند.

## دامنه کار

- تعریف idempotency key پایدار برای `post + destination + action` و Unique Index متناظر.
- Claim اتمیک Publication با status/version/lease یا قرارداد معادل و بازیابی Claim منقضی.
- بازگرداندن نتیجه موفق قبلی برای درخواست تکراری بدون تماس دوم با Telegram.
- Retry محدود با backoff/jitter برای خطاهای قطعاً موقت و قابل Retry.
- عدم Retry کور برای نتیجه مبهم پس از ارسال؛ ثبت وضعیت `OutcomeUnknown`/بررسی دستی طبق مدل مصوب.
- ثبت attempt، error category، next attempt، published ID و correlation بدون Secret.

## خارج از دامنه

- محاسبه Slot، Schedule Worker یا Cancellation (`T030` تا `T032`).
- ویرایش/حذف پیام مقصد و reconciliation گسترده خارج از سیاست مصوب.
- Retry نامحدود یا اتکا به Process-local lock.
- تغییر محتوای Post یا Media.

## فایل‌ها و ماژول‌های مورد انتظار

- توسعه `src/telegram_assist_bot/domain/publication.py`
- `src/telegram_assist_bot/application/publication/idempotent_publish.py`
- توسعه `src/telegram_assist_bot/application/ports/publication_repository.py`
- توسعه `src/telegram_assist_bot/infrastructure/mongodb/publication_repository.py`
- توسعه Wiring worker/entry point فقط در حد اجرای Attempt فوری.
- `tests/unit/application/publication/test_publication_retry_policy.py`
- `tests/integration/mongodb/test_publication_idempotency.py`
- `tests/integration/publication/test_publication_retry.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** max attempts، timeout و backoff باید bounded و Validate شوند؛ zero/unbounded نامعتبر باشد.
- **Migration:** Unique Index روی داده موجود ممکن است به duplicate برخورد کند؛ preflight/report و migration سازگار لازم است و حذف خودکار داده ممنوع.
- **Compatibility:** idempotency key و statusهای ذخیره‌شده قرارداد پایدارند؛ تغییر آینده نیازمند version/migration است.
- **Concurrency:** claim/complete با شرط owner/version انجام شود؛ Lease منقضی موفقیت Terminal را باز نمی‌کند.
- **Security:** خطا و attempt نباید Session، مسیر خصوصی، Token یا payload حساس را ذخیره/Log کند.

## معیارهای پذیرش عینی

1. درخواست‌های ترتیبی و هم‌زمان با یک key حداکثر یک Publication claim فعال/موفق می‌سازند.
2. درخواست تکراری پس از موفقیت نتیجه قبلی را بدون تماس Telegram برمی‌گرداند.
3. فقط خطای موقت قطعی Retry و خطای دائمی Fail-fast می‌شود.
4. نتیجه مبهم پس از تماس خارجی خودکار دوباره ارسال نمی‌شود.
5. Lease منقضیِ Attempt ناموفق قابل بازیابی است، اما موفقیت Terminal نیست.
6. attemptها و خطاهای redacted قابل مشاهده و bounded هستند.

## تست‌های واحد الزامی

- ساخت key قطعی و تفکیک Destination/action.
- طبقه‌بندی transient/permanent/ambiguous و محاسبه backoff محدود.
- رفتار AlreadyPublished، lease lost و max-attempts.
- عدم Retry برای auth/permission و outcome مبهم.

## تست‌های یکپارچه‌سازی الزامی

- Unique Index و چند Claim هم‌زمان MongoDB با اثبات یک winner.
- Restart/lease expiry و ادامه Attempt مجاز.
- Publisher Fake با خطای قبل از ارسال، خطای دائمی و timeout مبهم پس از ارسال؛ شمارش دقیق تماس‌ها.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/publication/test_publication_retry_policy.py
uv run pytest tests/integration/mongodb/test_publication_idempotency.py tests/integration/publication/test_publication_retry.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- مستندسازی idempotency key، State machine، lease و outcome مبهم در `docs/ARCHITECTURE.md`.
- افزودن Repository/Use Case و Indexها به `docs/CODE_MAP.md`.
- ثبت Decision فقط اگر سیاست outcome مبهم تصمیم معماری جدید و مهم است.
- به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که claim و Unique Index در رقابت واقعی اثبات، Retry محدود و failure-aware اجرا، outcome مبهم بدون duplicate احتمالی مدیریت، همه Quality Gateها موفق و Scheduler خارج از Scope مانده باشد.
