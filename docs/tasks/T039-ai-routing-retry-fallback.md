# T039 — Routing، Retry، Fallback و شکست نهایی AI

## وضعیت

Completed

## هدف

پیاده‌سازی Orchestrator لایه Application برای انتخاب Config-driven ترکیب‌های `Provider × Model`، تفکیک Retry داخلی از Fallback و ثبت شکست نهایی بدون تولید نتیجه جعلی.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `11.3` «روند فراخوانی پایپ‌لاین».
- `docs/REQUIREMENTS.md`، بخش `11.6` «Retry داخلی و Fallback».
- `docs/REQUIREMENTS.md`، بخش `11.9` «تنظیم Provider بر اساس نوع عملیات».
- `docs/REQUIREMENTS.md`، بخش `11.10` «Fallback میان مدل‌های یک Provider».
- `docs/REQUIREMENTS.md`، بخش `11.12` «رفتار در صورت شکست تمام Providerها».
- `docs/ARCHITECTURE.md`، بخش‌های `5`، `6`، `12` و `14`.

## وابستگی‌ها

- T035 — صف AI پایدار، اولویت و Lease.
- T038 — Validation، Repair و Normalization پاسخ AI.

هر دو وابستگی باید `Completed` باشند. Providerها، Modelها، روش Auth، Quota و Schema واقعی هنوز ابهام محصول/فنی‌اند؛ این Task حق انتخاب بی‌صدای آن‌ها را ندارد و فقط قراردادها و Configuration مصوب Taskهای پیشین را مصرف می‌کند.

## دامنه

- ساخت Use Case یا Service محدود `ExecuteAIWithFallback` در Application.
- فیلتر Provider/Modelهای فعال و پشتیبان Task و مرتب‌سازی قطعی طبق Configuration.
- اجرای Retry محدود فقط برای خطاهای موقت، با Backoff/Jitter تزریق‌پذیر و قابل تست.
- حرکت به Model بعدی همان Provider و سپس Provider بعدی طبق ترتیب Config.
- توقف در اولین نتیجه معتبر استانداردشده.
- ذخیره Attemptها، شمار Retry/Fallback و نتیجه نهایی در قراردادهای صف/نتیجه موجود.
- اعمال سیاست شکست نهاییِ از قبل تعریف‌شده برای هر نوع Task، بدون جعل `AIResult` موفق.

## خارج از دامنه

- انتخاب یا ساخت Provider/Model جدید و تغییر Adapterهای T036/T037.
- Rate Limit، Cooldown و Circuit Breaker (T040).
- Cache، Audit تجمیعی و Metrics (T041).
- رفتار کسب‌وکاری تشخیص تبلیغ، Duplicate، دسته‌بندی یا امتیازدهی (T042–T045).
- تغییر Schema پاسخ یا Promptهای T034/T038.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/ai/routing.py`
- `src/telegram_assist_bot/application/ai/retry.py`
- `src/telegram_assist_bot/application/ai/use_cases/execute_ai_with_fallback.py`
- قراردادهای مرتبط موجود زیر `src/telegram_assist_bot/application/ports/`
- `tests/unit/application/ai/test_ai_routing.py`
- `tests/unit/application/ai/test_ai_retry_fallback.py`
- `tests/integration/ai/test_ai_fallback_pipeline.py`

نام دقیق فایل‌ها باید با ساختار تثبیت‌شده T001/T034 سازگار شود؛ جابه‌جایی گسترده ماژول‌ها مجاز نیست.

## نکات پیاده‌سازی

- **Configuration:** ترتیب و سیاست شکست فقط از مدل Typed موجود خوانده شود؛ نبود route یا سیاست معتبر باید خطای Configuration روشن باشد. انتخاب Provider واقعی در این Task ممنوع است.
- **Migration:** Schema جدید MongoDB پیش‌بینی نمی‌شود؛ اگر ثبت Attempt فیلد جدید لازم داشت، Migration سازگار با عقب و تست خواندن رکورد قدیمی الزامی است.
- **Compatibility:** قرارداد `AIProvider` و `AIResult` نباید Provider-specific شود؛ خطاهای Adapter به Taxonomy مالک Application نگاشت شوند.
- **Concurrency:** وضعیت Job فقط با Lease/نسخه مورد انتظار به‌روزرسانی شود؛ Orchestrator نباید Claim موازی جدید بسازد یا Retry را با Job-level retry مخلوط کند.
- **Security:** Prompt، پاسخ خام و Exception نباید API Key، Header یا URL حساس را در Log/Result افشا کنند.
- Clock، Sleeper و Random/Jitter باید تزریق‌پذیر باشند تا تست‌ها بدون انتظار واقعی قطعی بمانند.

## معیارهای پذیرش عینی

1. Route فقط Provider/Modelهای فعال و پشتیبان Task را با ترتیب Config انتخاب می‌کند.
2. خطای موقت تا سقف مصوب همان Model Retry و سپس Fallback می‌شود.
3. خطای دائمی، Auth، Model-not-found یا unsupported بدون Retry داخلی به گزینه بعدی می‌رود.
4. مدل جایگزین همان Provider پیش از Provider بعدی اجرا می‌شود، مگر Config ترتیب دیگری را صریحاً تعریف کرده باشد.
5. پاسخ نامعتبر پس از مسیر Repair موجود باعث Fallback می‌شود.
6. اولین نتیجه معتبر Pipeline را متوقف می‌کند و Attempt/Fallback count درست ثبت می‌شود.
7. شکست همه گزینه‌ها وضعیت و خطاهای Sanitized را ثبت می‌کند و هیچ نتیجه AI جعلی نمی‌سازد.
8. سیاست شکست نهایی برای Task از Configuration خوانده و فقط به خروجی Application-owned تبدیل می‌شود.

## Unit Testهای الزامی

- مرتب‌سازی، فیلتر disabled/unsupported و ترتیب Modelها.
- طبقه‌بندی خطای Retryable و Non-retryable.
- سقف Retry، Backoff/Jitter تزریق‌شده و توقف پس از موفقیت.
- Fallback روی Timeout، پاسخ نامعتبر و خطای دائمی.
- شکست همه Providerها و نبود نتیجه جعلی.
- نبود route/policy و Configuration error قابل‌فهم.

## Integration Testهای الزامی

- اجرای Job Claimشده با دو Adapter Fake/HTTP آزمایشی: شکست گزینه اول و موفقیت دوم، همراه با ثبت Attemptها.
- سناریوی شکست همه گزینه‌ها و ماندگاری وضعیت نهایی/Retry آینده در MongoDB آزمایشی.
- هیچ درخواست زنده Provider در Suite پیش‌فرض مجاز نیست.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/ai/test_ai_routing.py tests/unit/application/ai/test_ai_retry_fallback.py
uv run pytest tests/integration/ai/test_ai_fallback_pipeline.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

Diff فارسی، نام Taskها و پیام‌های خطا نیز باید دستی بازبینی شوند.

## بروزرسانی مستندات الزامی

- تکمیل وضعیت و نتایج راستی‌آزمایی همین فایل.
- بروزرسانی `docs/ROADMAP.md` و `docs/STATUS.md` پس از تکمیل.
- بروزرسانی `docs/CODE_MAP.md` برای Orchestrator و جریان Retry/Fallback.
- بروزرسانی `docs/ARCHITECTURE.md` فقط اگر قرارداد اجرا با طرح فعلی تفاوت واقعی دارد.
- ثبت تصمیم Provider/Model یا سیاست شکست در `docs/DECISIONS.md` فقط پس از تصمیم صریح، نه با فرض ضمنی.

## تعریف Done

- تمام معیارهای پذیرش و تست‌های الزامی پاس شده‌اند.
- مرز Application/Infrastructure حفظ و Retry از Fallback تفکیک شده است.
- شکست نهایی بدون جعل نتیجه و بدون افشای Secret ثبت می‌شود.
- هیچ قابلیت T040 یا Feature AI بعدی وارد Scope نشده است.
- مستندات پروژه با کد و نتایج واقعی همگام و بررسی UTF-8/Persian کامل شده است.

## نتایج راستی‌آزمایی

تست‌های مربوطه و تمام تست‌های مجموعه با موفقیت اجرا شدند:
- اجرای ۱۹ تست اختصاصی برای AI Routing, Retry, Fallback (واحد و یکپارچه‌ساز) با موفقیت کامل پاس شدند.
- کل مجموعه تست‌های پروژه شامل ۱۰۹۱ تست با موفقیت ۱۰۰٪ پاس شدند.
- تمام لک‌های Ruff لایه AI برطرف شدند و کل فایل‌های تغییریافته بدون خطا و قالب‌بندی شده‌اند.
- کدهای ایجاد شده از اصول Clean Architecture به خوبی تبعیت می‌کنند و تفکیک لایه‌ای کاملاً رعایت شده است.
