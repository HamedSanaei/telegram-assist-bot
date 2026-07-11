# T012 — تست Restart و Stabilization دریافت

## وضعیت

Completed

## هدف

تثبیت vertical slice دریافت متن از Startup تا MongoDB در سناریوهای crawl، live event، disconnect و Restart، بدون افزودن رفتار محصولی جدید یا Media.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش‌های `5.1` تا `5.4`.
- `docs/REQUIREMENTS.md`، بخش `5.21 Pipeline کامل فاز اول`، مراحل دریافت تا ذخیرهٔ نسخهٔ اولیه.
- `docs/REQUIREMENTS.md`، بخش `16. معیارهای پذیرش فاز اول`، بندهای ۱ تا ۵.
- `docs/ARCHITECTURE.md`، بخش `2. اهداف معماری`، Restart و idempotency.
- `docs/ARCHITECTURE.md`، بخش `7. مسئولیت Telegram User API`.
- `docs/ARCHITECTURE.md`، بخش `15. راهبرد تست`، End-to-end کنترل‌شده و Restart/Concurrency.

## وابستگی‌ها

- T011 — هم‌زمانی Crawl/Listener و Idempotency؛ باید Completed باشد.

## محدوده

- wiring کامل foundation + validation session/channel + crawl امروز + سپس listener، با gateway fake و MongoDB واقعی آزمایشی.
- تثبیت ترتیب startup برای کمینه‌کردن gap: subscription/crawl strategy باید صریح و با idempotency نتیجهٔ یکسان بدهد.
- تست Restart با همان Session fixture و database: عدم login مجدد، crawl مجدد امن و ادامهٔ live ingest.
- تست crash/disconnect در نقاط کنترل‌شده و recovery بدون duplicate/corruption.
- بررسی فیلدهای پایهٔ Post، Entity و expiration در سند ذخیره‌شده.
- رفع فقط defectهای T007–T011 که سناریوهای فوق آشکار می‌کنند.
- ثبت eventهای lifecycle و counts ساختاریافته/redacted.

## خارج از محدوده

- Media، Album، cleanup، duplicate محتوایی، پاک‌سازی یا categorization.
- Telegram live sandbox به‌عنوان شرط suite.
- edit/delete پیام منبع، چند حساب و چند process production.
- Bot API، AI، Approval، Publication و Scheduling.
- refactor گسترده یا Feature جدید به نام Stabilization.

## فایل‌ها و ماژول‌های مورد انتظار

- تغییر محدود Composition Root و worker orchestration موجود.
- `tests/e2e/test_text_ingestion_restart.py`
- `tests/integration/test_ingestion_recovery.py`
- fixtureهای fake Telegram/session و MongoDB آزمایشی.
- اصلاح‌های کوچک در فایل‌های T007–T011 فقط در صورت failure اثبات‌شده.

## نکات پیاده‌سازی

- تست e2e کنترل‌شده باید Clock و event stream قابل‌کنترل داشته باشد و هیچ API زنده‌ای صدا نزند.
- crash را با قطع lifecycle در مرزهای مشخص شبیه‌سازی کنید، نه kill nondeterministic.
- **ریسک Configuration:** Config تست شامل Source فعال، Timezone و Secret مصنوعی؛ هیچ Config production استفاده نشود.
- **ریسک Migration:** schema/index تازه در Stabilization فقط با توجیه defect و update صریح مجاز است.
- **ریسک Compatibility:** fixtureهای Session/SDK نسخه‌شده باشند؛ SDK upgrade خارج از Task است.
- **ریسک Concurrency:** overlap crawl/listener و lease/claim T011 با barrier قطعی تست شود.
- **ریسک Security:** Session fixture مصنوعی و cleanup شده؛ logها فاقد payload و credential.

## معیارهای پذیرش عینی

1. Startup با Session موجود هیچ prompt ورود ایجاد نمی‌کند.
2. پیام‌های امروز و eventهای زنده در یک run ذخیره می‌شوند.
3. event حاضر در هر دو History و Listener فقط یک document و یک claim می‌سازد.
4. Restart با همان DB/Session هیچ duplicate نمی‌سازد و event جدید را می‌پذیرد.
5. disconnect/crash کنترل‌شده پس از Restart recover می‌شود و Session خراب نمی‌شود.
6. indexها، expiration، متن فارسی و Entity سند نهایی درست‌اند.
7. shutdown همهٔ task/clientها را می‌بندد و logها redacted هستند.

## Unit Testهای الزامی

- Feature جدیدی در این Stabilization پیش‌بینی نشده است؛ هر defect fix باید regression Unit Test متمرکز در ماژول مالک خود داشته باشد.
- assertion helperهای e2e برای نبود duplicate و resource leak، در صورت داشتن منطق، Unit Test شوند.

## Integration Testهای الزامی

- crawl + overlap live + MongoDB واقعی.
- دو lifecycle متوالی با Session fixture/database مشترک.
- disconnect و restart در مرز قبل/بعد insert/claim.
- invalid session startup و transient network بدون حذف Session.
- shutdown/restart و نبود task معلق.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/integration/test_ingestion_recovery.py tests/e2e/test_text_ingestion_restart.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

Integration/E2E کنترل‌شده و MongoDB نباید skip شوند؛ اجرای suite دوبار برای کشف flakiness، `git diff --check` و بازبینی Log الزامی است.

## نتایج نهایی راستی‌آزمایی

- Integration/E2E متمرکز T012: هر بار `3 passed` و `0 skipped` در دو اجرای ترتیبی با MongoDB واقعی آزمایشی.
- هر دو اجرا با `uv run pytest tests/integration/test_ingestion_recovery.py tests/e2e/test_text_ingestion_restart.py --basetemp <unique>` انجام شدند.
- restart با Session/database مشترک، overlap listener-before-crawl، disconnect، failure پیش/پس از insert/claim، shutdown بدون task معلق و عدم prompt مجدد اثبات شد.
- Suite کامل non-live: `669 passed` و `0 skipped` در دو اجرای ترتیبی؛ Branch Coverage برابر `90.02%`.
- `uv lock --check`، `uv sync --locked --group dev`، Ruff، format، mypy، هر دو text-integrity check، secret detection، build، distribution، import، `git diff --check` و artifact audit موفق‌اند.
- دو فرمان suite نهایی به‌ترتیب `uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-report=term-missing --cov-fail-under=90 --basetemp <unique>` و `uv run pytest -m "not live" --basetemp <unique>` بودند.
- تست زندهٔ Telegram اجرا نشده و طبق Task اختیاری است؛ هیچ credential یا channel واقعی در suite وجود ندارد.

## به‌روزرسانی‌های مستندات

- ثبت Status، defect fixها و نتایج واقعی در همین فایل.
- به‌روزرسانی T012/Milestone 1 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- به‌روزرسانی data flow و entry pointها در `docs/CODE_MAP.md`.
- اصلاح `docs/ARCHITECTURE.md` فقط برای wiring/recovery واقعاً پیاده‌شده.
- ADR فقط برای تصمیم معماری تازه و مهم، نه جزئیات تست.

## تعریف انجام‌شدن

- سناریوهای Restart/overlap/recovery deterministic و بدون skip پاس شده‌اند.
- هیچ failure شناخته‌شده یا task/resource leak باقی نمانده است.
- suite کامل و Quality Gate/UTF-8/Secret checks پاس شده‌اند.
- Stabilization به محدودهٔ دریافت متن محدود و مستندات با رفتار واقعی همگام است.
