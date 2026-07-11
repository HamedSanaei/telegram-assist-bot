# T040 — Rate Limit، Cooldown و Circuit Breaker AI

## وضعیت

Planned

## هدف

افزودن کنترل پایدار و هم‌زمانی‌امن ظرفیت، Cooldown و Circuit Breaker مستقل برای هر ترکیب `Provider × Model` تا Router پیش از تماس خارجی گزینه ناسالم یا فاقد سهمیه را رد کند.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `11.7` «مدیریت Rate Limit».
- `docs/REQUIREMENTS.md`، بخش `11.8` «Circuit Breaker».
- `docs/REQUIREMENTS.md`، بخش `11.18` «عدم پردازش هم‌زمان تکراری».
- `docs/ARCHITECTURE.md`، بخش‌های `9`، `12`، `14` و `15`.

## وابستگی‌ها

- T035 — صف AI پایدار، اولویت و Lease.
- T039 — Routing، Retry، Fallback و شکست نهایی.

## دامنه

- مدل Application-owned برای وضعیت ظرفیت و Circuit هر `Provider × Model`.
- Port اختصاصی Reservation/Release و ثبت Success/Failure.
- Adapter MongoDB با Reservation اتمیک برای محدودیت هم‌زمانی و پنجره‌های مصوب.
- State machineهای `Closed`، `Open` و `HalfOpen` با Clock تزریق‌پذیر.
- پردازش 429 و Metadata استاندارد Rate Limit برای تعیین Cooldown/زمان آزادشدن.
- اتصال Router T039 به eligibility check و reservation پیش از ارسال درخواست.

## خارج از دامنه

- انتخاب Quota واقعی Providerها یا تفسیر Header اختصاصی جدید در Adapterها بدون Specification مصوب.
- Auto-tuning اولویت Providerها، داشبورد Metrics و Cache (T041).
- Queue/Lease عمومی AI که در T035 تکمیل شده است.
- اجرای Featureهای AI T042–T045.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/ai/provider_health.py`
- `src/telegram_assist_bot/application/ports/provider_state_repository.py`
- `src/telegram_assist_bot/application/ai/provider_guard.py`
- `src/telegram_assist_bot/infrastructure/mongodb/provider_state_repository.py`
- تغییر محدود در Router T039 زیر `src/telegram_assist_bot/application/ai/`
- `tests/unit/domain/ai/test_provider_health.py`
- `tests/unit/application/ai/test_provider_guard.py`
- `tests/integration/mongodb/test_provider_state_repository.py`

## نکات پیاده‌سازی

- **Configuration:** حدود دقیقه/ساعت/روز، concurrency، آستانه Circuit و Cooldown باید Typed و اعتبارسنجی‌شده باشند؛ مقدار واقعی نامشخص Provider نباید حدس زده شود.
- **Migration:** Collection/Indexهای `provider_state` باید Migration صریح، کلید یکتای Provider/Model و مسیر سازگار برای رکوردهای بدون State داشته باشند.
- **Compatibility:** Router فقط Port و تصمیم eligibility استاندارد را ببیند؛ Headerهای اختصاصی در Adapter Provider باقی بمانند.
- **Concurrency:** check-then-increment ممنوع است؛ Reservation و HalfOpen probe باید با update اتمیک و نسخه/Lease انجام شوند و Release دوباره idempotent باشد.
- **Security:** State و Logها فقط نام منطقی Provider/Model را نگه دارند؛ API Key، Authorization header و URL حساس ممنوع است.
- رفتار پنجره Token/Request فقط در حد داده قابل اتکای Configuration/Adapter اجرا شود؛ تخمین غیرمستند Quota مجاز نیست.

## معیارهای پذیرش عینی

1. State هر `Provider × Model` مستقل و با کلید یکتا ذخیره می‌شود.
2. چند Worker نمی‌توانند بیش از concurrency مصوب Reservation موفق بگیرند.
3. گزینه دارای سهمیه تمام‌شده یا Circuit باز پیش از تماس خارجی skip می‌شود.
4. 429 زمان Cooldown استاندارد و نزدیک‌ترین زمان eligibility را ثبت می‌کند.
5. عبور از آستانه خطا Circuit را باز و پس از انقضا دقیقاً یک probe اتمیک HalfOpen مجاز می‌کند.
6. موفقیت probe Circuit را می‌بندد و شکست آن دوباره Circuit را باز می‌کند.
7. Reservation منقضی/رهاشده ظرفیت را بدون شمارش منفی بازیابی می‌کند.
8. Router T039 گزینه Skipشده را Attempt خارجی محسوب نمی‌کند و به گزینه بعدی می‌رود.

## Unit Testهای الزامی

- Transitionهای Closed/Open/HalfOpen و مرز زمانی دقیق.
- آستانه خطا، Success reset و HalfOpen probe.
- تصمیم eligibility برای quota/concurrency/cooldown.
- 429 با و بدون زمان reset استاندارد.
- Release idempotent و جلوگیری از شمارنده منفی.

## Integration Testهای الزامی

- رقابت چند claimant روی MongoDB و عدم عبور از concurrency limit.
- Unique index برای `provider + model` و update اتمیک State.
- بازیابی Reservation منقضی و HalfOpen probe واحد میان چند Worker.
- تست Router با State repository واقعی و Gateway Fake؛ بدون Provider زنده.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/domain/ai/test_provider_health.py tests/unit/application/ai/test_provider_guard.py
uv run pytest tests/integration/mongodb/test_provider_state_repository.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

Diff فارسی و خروجی‌های State/خطا باید دستی بازبینی شوند.

## بروزرسانی مستندات الزامی

- بروزرسانی همین Task، `docs/ROADMAP.md`، `docs/STATUS.md` و `docs/CODE_MAP.md`.
- ثبت Index/State flow واقعی در `docs/ARCHITECTURE.md`.
- ثبت تصمیم فقط اگر معنا یا پنجره Quota/Circuit نسبت به Requirement نیازمند انتخاب مهم باشد.

## تعریف Done

- معیارها و تست‌های Unit/Integration، lint، format، type check و text integrity پاس شده‌اند.
- Reservation و Circuit در چند Worker اتمیک‌اند و Timeout/Clock قطعی دارند.
- هیچ Quota یا Provider واقعی بدون تصمیم مستند فرض نشده و Secret در State/Log نیست.
- Scope به Cache/Metrics یا Featureهای AI گسترش نیافته است.
