# T019 — Stabilization آماده‌سازی محتوا

## وضعیت

Planned

## هدف

تثبیت مسیر کنترل‌شدهٔ Post از دریافت ذخیره‌شده تا Media آماده، Album تجمیع‌شده، duplicate دقیق، دسته‌بندی پایه و محتوای مقصدی آمادهٔ مرحلهٔ Approval، بدون پیاده‌سازی AI یا تعامل مدیران.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش‌های `5.4` تا `5.11`.
- `docs/REQUIREMENTS.md`، بخش `5.21 Pipeline کامل فاز اول`، فقط مراحل پیاده‌شده تا آماده‌سازی محتوا.
- `docs/REQUIREMENTS.md`، بخش `15. تست‌ها`، Media Group، retention، duplicate exact، pruning و Entity.
- `docs/ARCHITECTURE.md`، بخش `5. Use Caseهای Application`، مسیر دریافت و پردازش.
- `docs/ARCHITECTURE.md`، بخش `10. ذخیره Media`.
- `docs/ARCHITECTURE.md`، بخش `15. راهبرد تست`، Integration/Restart/Concurrency/Security.
- `docs/ARCHITECTURE.md`، بخش `17. ابهام‌های باز`، بندهای ۸ تا ۱۰.

## وابستگی‌ها

- T014 — انقضا و Cleanup فایل‌های Media؛ باید Completed باشد.
- T015 — تجمیع Album/Media Group؛ باید Completed باشد.
- T016 — Normalize و Duplicate دقیق؛ باید Completed باشد.
- T017 — پاک‌سازی مقصدی و بازسازی Entity؛ باید Completed باشد.
- T018 — دسته‌بندی پایه و Override؛ باید Completed باشد.

## محدوده

- orchestration مرحله‌ایِ رفتارهای موجود T013–T018 با status/expected version و resume از آخرین مرحلهٔ موفق.
- سناریوی text-only، single Media و Album تا artifact آمادهٔ مقصد، با MongoDB و filesystem آزمایشی و Telegram gateway fake.
- تثبیت restart/crash میان download، finalize group، duplicate check، categorization و content preparation.
- اثبات cleanup retention مستقل و عدم حذف فایل referenced.
- تثبیت failure isolation و retry فقط در مرحلهٔ شکست‌خورده، بدون اجرای دوبارهٔ مراحل کامل‌شده.
- تعریف وضعیت صریح برای مراحل AI که هنوز پیاده نشده‌اند: با Feature Flag خاموش `NotRequested/Disabled` ثبت شود؛ روشن‌کردن Advertisement AI یا semantic duplicate پیش از T042/T043 باید در Startup خطای Configuration روشن بدهد، نه skip خاموش یا نتیجهٔ جعلی.
- رفع فقط defectهای میان‌لایه‌ای Taskهای وابسته و افزودن regression test.

## خارج از محدوده

- تشخیص تبلیغ AI بخش `5.8`؛ T042.
- duplicate معنایی بخش `5.9`؛ T043.
- categorization AI؛ T044.
- ارسال پیام Approval، Bot API، Callback، publication و scheduling.
- ادعای تکمیل همهٔ `5.4–5.11` در حالت Feature Flagهای AI روشن.
- Feature جدید، refactor گسترده یا API زنده Telegram.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/prepare_post_pipeline.py` یا orchestrator هم‌ارز کوچک.
- `src/telegram_assist_bot/workers/content_preparation.py`
- تغییر محدود status/wiring/taskهای T013–T018 فقط برای defect اثبات‌شده.
- `tests/integration/test_content_preparation_pipeline.py`
- `tests/e2e/test_content_preparation_restart.py`
- fixtureهای فارسی/Emoji/Album و fake Telegram بدون دادهٔ واقعی.

## نکات پیاده‌سازی

- Pipeline state در MongoDB منبع حقیقت است؛ زنجیرهٔ in-memory پس از Restart کافی نیست.
- هر مرحله input/output/status مشخص و idempotency guard داشته باشد؛ اجرای مجدد فقط نتیجهٔ موجود را مصرف کند.
- conflict باید state تازه را reload و تصمیم بگیرد، نه blind retry.
- **ریسک Configuration:** فعال‌سازی Flag AI پیاده‌نشده Fail-fast است؛ destination/category/media config باید قبلاً validate شود.
- **ریسک Migration:** status/artifact تازه فقط با schema version/index صریح؛ Stabilization migration گسترده نمی‌سازد.
- **ریسک Compatibility:** DTO/Entity/Media fixtureهای انتخاب SDK حفظ و تغییر قراردادهای T013–T018 مستند شوند.
- **ریسک Concurrency:** دو worker برای یک Post/Album با claim/version کار کنند و artifact مقصدی دوم نسازند.
- **ریسک Security:** Media path confinement، payload-free logs، fixture مصنوعی و عدم exposure فایل حفظ شود.

## معیارهای پذیرش عینی

1. Post متنی از Stored تا content artifact مقصدی با duplicate exact و baseline category پیش می‌رود.
2. single Media و Album فقط پس از آماده‌شدن همهٔ اجزای لازم به مرحلهٔ بعد می‌روند.
3. restart در هر مرز مرحله فقط کار ناقص را ادامه و مرحلهٔ کامل را دوباره side-effect نمی‌دهد.
4. دو worker هم‌زمان یک artifact/readiness canonical می‌سازند.
5. exact duplicate طبق policy متوقف/علامت‌گذاری می‌شود و semantic result جعل نمی‌شود.
6. Feature Flag AI خاموش state صریح و Flag روشن خطای Startup قابل‌فهم تا Task مربوط می‌دهد.
7. cleanup فایل non-expired/referenced را حذف نمی‌کند.
8. Persian/ZWNJ/Custom Emoji و Entityها در original و خروجی مقصد درست‌اند.
9. هیچ Approval message یا publication رخ نمی‌دهد.

## Unit Testهای الزامی

- Feature جدیدی انتظار نمی‌رود؛ هر defect fix باید regression Unit Test در ماژول مالک داشته باشد.
- state-machine/orchestrator: skip مرحلهٔ Completed، توقف در failure، resume و conflict reload.
- validation مربوط به Flagهای AI پیاده‌نشده.

## Integration Testهای الزامی

- text-only pipeline با MongoDB و destination artifacts چند مقصد.
- single Media و Album out-of-order/replayed با filesystem موقت.
- exact duplicate داخل ۱۴ روز و non-duplicate.
- crash/restart در هر مرز مهم و assertion عدم side effect تکراری.
- دو worker هم‌زمان و یک readiness/artifact.
- cleanup race و preservation فایل referenced.
- Flagهای AI خاموش/روشن بدون فراخوانی Provider.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/integration/test_content_preparation_pipeline.py tests/e2e/test_content_preparation_restart.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

Integration/E2E و MongoDB نباید skip شوند؛ suite دو بار برای flakiness، بازبینی دستی Persian/Entity diff، فهرست فایل‌ها و `git diff --check` الزامی است.

## به‌روزرسانی‌های مستندات

- ثبت Status، regression fixها و verification واقعی در همین فایل.
- به‌روزرسانی T019/Milestone 2 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- به‌روزرسانی pipeline/worker/artifact flow در `docs/CODE_MAP.md`.
- همگام‌سازی status/resume/AI-disabled boundaries در `docs/ARCHITECTURE.md`.
- ثبت تصمیم مهم orchestration یا conflict recovery در `docs/DECISIONS.md`.
- اصلاح Requirement فقط با تأیید محصول؛ این Task تناقض ترتیب AI را بی‌صدا بازنویسی نمی‌کند.

## تعریف انجام‌شدن

- سناریوهای text/Media/Album/duplicate/restart/concurrency بدون skip پاس شده‌اند.
- مراحل AI جعل یا زودهنگام پیاده نشده و رفتار Flag روشن/خاموش صریح است.
- Quality Gate، UTF-8، path/Secret safety و Persian/Entity review پاس شده‌اند.
- هیچ blocker/failing test شناخته‌شده باقی نمانده و مستندات با pipeline واقعی همگام است.
