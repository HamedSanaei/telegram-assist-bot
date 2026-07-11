# T006 — Startup و Stabilization پایه

## وضعیت

Planned

## هدف

اتصال حداقلی Configuration، Logging و MongoDB Post persistence در یک Composition Root قابل‌اجرا و تثبیت مسیر Startup/Shutdown، بدون آغاز Telegram، Workerهای محصول یا Featureهای بعدی.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `4. مدیریت تنظیمات`، اعتبارسنجی در Startup.
- `docs/REQUIREMENTS.md`، بخش `12. Logging و مانیتورینگ`، شروع/توقف و اتصال MongoDB.
- `docs/REQUIREMENTS.md`، بخش `13. مدیریت خطا و Retry`، تفکیک خطا و timeout.
- `docs/REQUIREMENTS.md`، بخش `14. امنیت`، توقف امن و عدم نشت Secret.
- `docs/ARCHITECTURE.md`، بخش `3. لایه‌ها و جهت وابستگی`، Composition Root.
- `docs/ARCHITECTURE.md`، بخش `9. MongoDB و مدل ماندگاری`، index setup صریح.
- `docs/ARCHITECTURE.md`، بخش `15. راهبرد تست`، Stabilization پایه.

## وابستگی‌ها

- T002 — Configuration و Secret Validation؛ باید Completed باشد.
- T004 — MongoDB و Persistence یکتای Post؛ باید Completed باشد.
- T005 — Logging، خطا و Retry foundation؛ باید Completed باشد.

## محدوده

- ایجاد Composition Root واحد که مسیر Configuration را از argument/environment غیرحساس می‌گیرد، Configuration را load/validate می‌کند، Logging را می‌سازد و سپس MongoDB را متصل می‌کند.
- تضمین ترتیب Fail-fast: Config نامعتبر پیش از هر اتصال خارجی، سپس initialization Logging، سپس Mongo health/index setup.
- تعریف lifecycle async روشن برای start، readiness حداقلی و shutdown idempotent منابع ایجادشده.
- افزودن CLI/entry point پایه برای اجرای Startup و خروج کنترل‌شده؛ هیچ Worker محصولی شروع نشود.
- ثبت eventهای ساختاریافتهٔ start، config validation result، Mongo connected/indexes ready، shutdown و failure با redaction.
- تست recovery از failure میانهٔ Startup و بسته‌شدن فقط resourceهای ساخته‌شده.
- اجرای سناریوی smoke روی MongoDB آزمایشی و تثبیت Quality Gateهای Milestone 0.

## خارج از محدوده

- Telegram User/Bot API، authentication، crawl یا listener.
- HTTP server، health endpoint شبکه‌ای یا orchestration deployment.
- Media storage، AI، scheduling، publication و callback.
- daemonization، multi-process supervisor یا Docker production image مگر T001 آن را صریحاً پایه گذاشته باشد.
- retry نامحدود اتصال Startup؛ خطای configuration/index باید Fail-fast بماند.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/bootstrap.py`
- `src/telegram_assist_bot/__main__.py`
- `src/telegram_assist_bot/shared/lifecycle.py` در صورت نیاز.
- `tests/unit/test_bootstrap.py`
- `tests/integration/test_foundation_startup.py`
- script/fixture تست MongoDB مطابق قرارداد T004، بدون افزودن مسیر موازی.
- اسناد پروژه طبق بخش «به‌روزرسانی‌های مستندات».

## نکات پیاده‌سازی

- Composition Root تنها محل import هم‌زمان Configuration، Infrastructure و concrete adapters است.
- import ماژول نباید اتصال یا side effect بسازد؛ همه‌چیز در تابع صریح `main`/factory انجام شود.
- shutdown چندباره و cancellation حین Startup باید منابع بازشده را دقیقاً یک بار ببندد.
- readiness فقط پس از Config معتبر، ping موفق و Index setup موفق اعلام شود.
- **ریسک Configuration:** precedence مسیر Config و Environment باید مستند و deterministic باشد؛ startup با Config ناشناخته ادامه ندهد.
- **ریسک Migration:** Index/schema ناسازگار readiness را رد کند؛ این Task migration destructive انجام نمی‌دهد.
- **ریسک Compatibility:** exit codeهای خطای config و infrastructure قرارداد CLI هستند و باید تست/مستند شوند.
- **ریسک Concurrency:** اجرای هم‌زمان initializer باید به index idempotent T004 تکیه کند؛ global mutable singleton ساخته نشود.
- **ریسک Security:** command line نباید Secret بپذیرد؛ URI و exception driver پیش از Log sanitize شوند.

## معیارهای پذیرش عینی

1. entry point با Config معتبر و MongoDB آزمایشی Startup کامل و Shutdown تمیز دارد.
2. Config نامعتبر هیچ تلاش MongoDB ایجاد نمی‌کند و exit code غیرصفر مشخص می‌دهد.
3. Mongo unavailable یا Index ناسازگار readiness را رد، خطای redacted ثبت و resourceهای باز را می‌بندد.
4. ترتیب eventهای Startup و Shutdown قابل‌assert و دارای correlation ID است.
5. فراخوانی shutdown دوباره بی‌اثر و بدون exception است.
6. هیچ Telegram/AI/Media/Scheduler worker در graph ساخته نمی‌شود.
7. اجرای دو initializer مستقل index اضافی/ناسازگار نمی‌سازد.
8. Quality Gateهای کل Milestone 0 پاس می‌شوند.

## Unit Testهای الزامی

- ترتیب wiring با fake loader/logger/Mongo resource.
- عدم ساخت resource پس از validation failure.
- cleanup معکوس resourceها هنگام failure میانهٔ Startup.
- shutdown idempotent و propagation cancellation.
- exit code و پیام امن برای failureهای config و infrastructure.
- منع side effect هنگام import entry point.

## Integration Testهای الزامی

- Startup/Shutdown واقعی با فایل Config موقت، Environment Secret مصنوعی و MongoDB آزمایشی.
- اجرای مجدد Startup برای اثبات idempotency index setup.
- سناریوی URI نامعتبر/instance unavailable با timeout کوتاه و عدم نشت credential.
- Config نامعتبر با spy یا endpoint غیرقابل‌دسترس برای اثبات اینکه اتصال اصلاً تلاش نشده است.

MongoDB آزمایشی الزامی است؛ skip شدن Integration Testها برای اعلام completion قابل قبول نیست.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/test_bootstrap.py
uv run pytest tests/integration/test_foundation_startup.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

اجرای smoke entry point با Configuration آزمایشی، `git diff --check` و بازبینی Log redacted نیز الزامی است؛ فرمان دقیق smoke باید پس از تثبیت CLI در همین فایل ثبت شود.

## به‌روزرسانی‌های مستندات

- ثبت Status و همهٔ فرمان‌ها/نتایج واقعی در همین فایل.
- به‌روزرسانی T006 در `docs/ROADMAP.md` و انتقال Active task در `docs/STATUS.md` پس از تکمیل.
- افزودن entry point، Composition Root و lifecycle به `docs/CODE_MAP.md`.
- اصلاح بخش‌های Startup/Composition Root در `docs/ARCHITECTURE.md` مطابق wiring واقعی.
- ثبت قرارداد CLI/lifecycle در `docs/DECISIONS.md` فقط در صورت تصمیم معماری پایدار.

## تعریف انجام‌شدن

- همهٔ Unit/Integration Testهای Task و suite کامل پاس شده‌اند و MongoDB testها skip نیستند.
- Startup تنها foundation را wire می‌کند و به‌طور امن Fail-fast/Shutdown می‌شود.
- lint، format، mypy و بررسی UTF-8/Secret پاس شده‌اند.
- هیچ application feature خارج از Milestone 0 ساخته نشده است.
- مستندات با entry point و رفتار واقعی همگام‌اند.
