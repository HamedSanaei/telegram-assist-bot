# T005 — Logging، خطا و Retry foundation

## وضعیت

Completed

## هدف

ساخت پایهٔ مشترک و مستقل از Provider برای خطاهای دسته‌بندی‌شده، Logging ساختاریافته و Retry محدود، تا Adapterهای آینده رفتار قابل‌مشاهده، redacted و قابل‌آزمون داشته باشند.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `12. Logging و مانیتورینگ`.
- `docs/REQUIREMENTS.md`، بخش `13. مدیریت خطا و Retry`.
- `docs/REQUIREMENTS.md`، بخش `14. امنیت`، منع ثبت Secret و الزام Timeout.
- `docs/REQUIREMENTS.md`، بخش `15. تست‌ها`، مدیریت Timeout و Retry.
- `docs/ARCHITECTURE.md`، بخش `14. Logging، Retry، Idempotency و هم‌زمانی`، زیربخش‌های Logging و Retry.
- `docs/ARCHITECTURE.md`، بخش `15. راهبرد تست`.

## وابستگی‌ها

- T001 — Bootstrap پروژه و Quality Gateها؛ باید Completed باشد.
- T002 — Configuration و Secret Validation؛ باید Completed باشد.

## محدوده

- تعریف taxonomy خطاهای مشترک: validation/configuration، permanent، transient، timeout، rate-limit/flood-wait، permission، concurrency/conflict و already-completed.
- تعریف metadata غیرحساس خطا و نگاشت آن به `error_category` پایدار.
- پیکربندی Logging ساختاریافته با فیلدهای پایهٔ `timestamp`، `level`، `event_name` و `correlation_id` و context اختیاری شناسه‌های Post/Channel/Destination/Admin/Job.
- redaction مرکزی پیش از formatter برای کلیدها و valueهای حساس شناخته‌شده، شامل URI credential و Authorization header.
- تعریف `RetryPolicy` عمومی با حداکثر تلاش، exponential backoff، سقف delay و jitter تزریق‌پذیر/قطعی در تست.
- اجرای retry helper فقط برای operation بدون side effect یا operation صراحتاً idempotent؛ classification تصمیم retry را کنترل کند.
- تعریف قرارداد timeout به‌عنوان مقدار الزامی برای Adapterهای خارجی، بدون پیاده‌سازی تماس خارجی.
- تضمین اینکه cancellation فرآیند async بلعیده نمی‌شود.

## خارج از محدوده

- Retry/Fallback ویژهٔ AI، Circuit Breaker و Rate-limit reservation.
- Telegram FloodWait adapter، HTTP client یا MongoDB retry اجرایی.
- DLQ/Job persistence.
- Backend مانیتورینگ خارجی، dashboard، alerting یا distributed tracing کامل.
- منطق idempotency اختصاصی Publication/Ingest.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/shared/errors.py`
- `src/telegram_assist_bot/shared/observability/logging.py`
- `src/telegram_assist_bot/shared/observability/context.py`
- `src/telegram_assist_bot/shared/retry/policy.py`
- `src/telegram_assist_bot/shared/retry/executor.py`
- `tests/unit/shared/observability/test_structured_logging.py`
- `tests/unit/shared/observability/test_redaction.py`
- `tests/unit/shared/retry/test_policy.py`
- `tests/unit/shared/retry/test_executor.py`
- اسناد پروژه طبق بخش «به‌روزرسانی‌های مستندات».

## نکات پیاده‌سازی

- Log schema باید application-owned باشد؛ object/exception SDK مستقیماً serialize نشود.
- redaction بر اساس key به‌تنهایی کافی نیست؛ URI و متن header دارای credential نیز پوشش داده شوند، بدون log کردن payload خام برای تشخیص.
- jitter و sleep پشت dependency قابل‌تزریق باشند تا Testها sleep واقعی نداشته باشند.
- retry attempt باید شامل شماره تلاش و delay برنامه‌ریزی‌شده باشد، ولی exception message حساس sanitize شود.
- **ریسک Configuration:** مقادیر level/format/max attempts/delay از T002 validate شوند؛ مقدار منفی یا retry بی‌نهایت ممنوع است.
- **ریسک Migration:** نام event و error category قرارداد observability است؛ جدول اولیه مستند شود و rename بی‌سروصدا انجام نشود.
- **ریسک Compatibility:** helper باید sync/async را مخلوط نکند؛ API اصلی async باشد و cancellation semantics نسخه Python T001 را رعایت کند.
- **ریسک Concurrency:** correlation/context باید با `contextvars` یا روش task-local باشد تا بین coroutineها نشت نکند.
- **ریسک Security:** Secret sentinel در message، nested mapping، exception و URI باید حذف شود؛ متن کامل Telegram به‌صورت پیش‌فرض log نشود.

## معیارهای پذیرش عینی

1. هر Log معتبر JSON/ساختار معادل با فیلدهای پایه و context همان task تولید می‌کند.
2. context دو coroutine هم‌زمان با هم مخلوط نمی‌شود.
3. هیچ Secret sentinel در خروجی حالت‌های key/value/URI/exception باقی نمی‌ماند.
4. transient/timeout طبق policy retry می‌شوند و permanent/configuration/permission بدون retry خاتمه می‌یابند.
5. تعداد تلاش و delay هرگز از سقف Configuration عبور نمی‌کند و backoff با Test قطعی اثبات می‌شود.
6. cancellation فوراً propagate می‌شود.
7. هر retry یک event ساختاریافتهٔ redacted با attempt ثبت می‌کند.
8. Foundation هیچ Provider یا SDK خارجی را import نمی‌کند.

## Unit Testهای الزامی

- schema و level/event/context یک Log موفق و خطادار.
- isolation context در coroutineهای هم‌زمان.
- redaction Secret در nested dict، URL، header، exception و رشتهٔ فارسی پیرامون آن.
- classification همهٔ دسته‌های خطا.
- backoff، cap، jitter deterministic، max attempts و zero retry.
- عدم retry خطای permanent و propagation cancellation.
- ثبت attempt/final failure بدون نشت exception حساس.

## Integration Testهای الزامی

N/A. در این Task هیچ Adapter یا سیستم خارجی پیاده نمی‌شود؛ logger و retry executor با fake clock/sleeper و capture خروجی به‌صورت قطعی پوشش داده می‌شوند. اتصال واقعی هر Adapter در Task خودش Integration Test خواهد داشت.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/shared/observability tests/unit/shared/retry
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

بازبینی دستی یک Log نمونهٔ خطا، `git diff --check` و جست‌وجوی Secret sentinel الزامی است.

## به‌روزرسانی‌های مستندات

- ثبت Status و خروجی verification در همین فایل.
- به‌روزرسانی T005 در `docs/ROADMAP.md` و `docs/STATUS.md` پس از تکمیل.
- افزودن taxonomy، logger و retry modules به `docs/CODE_MAP.md`.
- همگام‌سازی log schema و retry semantics با `docs/ARCHITECTURE.md`.
- ثبت انتخاب logging library یا قرارداد event taxonomy در `docs/DECISIONS.md` فقط اگر تصمیم پایدار و غیرروتین است.

## نتایج راستی‌آزمایی ثبت‌شده

- `uv run pytest tests/unit/shared/observability tests/unit/shared/retry`: موفق؛ `83 passed` و هیچ sleep/network واقعی اجرا نشد.
- `uv run pytest`: موفق؛ `534 passed` شامل MongoDB آزمایشی و بدون skip.
- اجرای CI-style با Branch Coverage: موفق؛ `534 passed` و پوشش `93.56%`.
- `uv run ruff check .` و `uv run ruff format --check .`: موفق؛ `61` فایل formatted.
- `uv run mypy src tests scripts`: موفق؛ `61` فایل بدون خطا.
- `uv run python scripts/check_text_integrity.py --changed`: موفق؛ `24` فایل و حالت `--all` نیز `139` فایل را تأیید کرد.
- `uv lock --check`، Build wheel/sdist، `scripts/check_distribution.py` و smoke import رسمی Wheel: موفق.
- Secret detection مطابق CI روی trackedها و scan تکمیلی فایل‌های جدید: موفق؛ baseline تغییر نکرد.
- Log نمونه JSON با stream صریح UTF-8 بازبینی شد؛ متن فارسی/Emoji سالم و Cookie، Authorization و محتوای Post با marker ثابت پوشیده بودند.
- جست‌وجوی Mojibake، فایل generated/Session tracked و `git diff --check`: موفق؛ موردی یافت نشد.

## تعریف انجام‌شدن

- معیارهای پذیرش با Unit Test قطعی پاس شده‌اند.
- Quality Gateهای T001 پاس شده و هیچ sleep/network واقعی در Testها وجود ندارد.
- خروجی Logging در تمام fixtureهای حساس redacted است.
- UTF-8 و متن فارسی بررسی شده و Mojibake وجود ندارد.
- مستندات واقعی‌اند و Task وارد retry اختصاصی هیچ Provider نشده است.
