# T002 — Configuration و اعتبارسنجی Secretها

## وضعیت

Completed

## هدف

ایجاد یک سامانهٔ متمرکز، type-safe و قابل‌آزمون برای خواندن Configuration، resolve کردن Secretها و اعتبارسنجی کامل تنظیمات پیش از هر اتصال خارجی، بدون پیاده‌سازی هیچ Adapter عملیاتی Telegram، MongoDB یا AI.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `4. مدیریت تنظیمات`.
- `docs/REQUIREMENTS.md`، بخش `14. امنیت`، فقط قواعد نگهداری Secret و توقف امن Startup.
- `docs/REQUIREMENTS.md`، بخش `15. تست‌ها`، مورد اعتبارسنجی تنظیمات.
- `docs/ARCHITECTURE.md`، بخش `13. Configuration و Secret`.
- `docs/ARCHITECTURE.md`، بخش `16. مرز اولیه و توسعه آینده`، ردیف Config.
- `docs/DECISIONS.md`، `ADR-008`.

## وابستگی‌ها

- T001 — Bootstrap پروژه و Quality Gateها؛ باید Completed باشد.

## محدوده

- تعریف مدل‌های typed برای تنظیمات پایهٔ برنامه: MongoDB، مسیر Session، Bot، Adminها، Source/Destinationها، Feature Flagها، Timezone، Logging و اسکلت routing مربوط به AI و Advertisement.
- تعریف یک قرارداد صریح برای ارجاع Secret از Environment Variable؛ فایل نمونه فقط نام متغیر را نگه می‌دارد، نه مقدار Secret را.
- خواندن JSON با `encoding="utf-8"` و گزارش خطاهای parse/encoding با Exception کاربردی و بدون افشای محتوا یا Secret.
- تجمیع همهٔ خطاهای اعتبارسنجی و گزارش آن‌ها با مسیر دقیق فیلد.
- اعتبارسنجی فیلدهای مشترک: فیلدهای اجباری، Enumها، بازه‌های عددی، `ZoneInfo`، یکتایی شناسه‌ها/نام‌ها، مقصدهای مجاز و ارجاع Secret موجود.
- افزودن `config/configuration.example.json` امن، دارای متن فارسی سالم در دادهٔ نمونه و فاقد Credential واقعی.
- افزودن الگوهای لازم به `.gitignore` برای فایل Configuration محلی، Session و مسیرهای Runtime حاوی Secret.
- فراهم کردن API واحد برای Composition Root؛ هیچ ماژول کسب‌وکار نباید JSON یا Environment را مستقیم بخواند.

## خارج از محدوده

- اتصال واقعی به MongoDB، Telegram یا Providerهای AI.
- ورود Telegram، ساخت Session یا بررسی Premium.
- Dynamic reload، پنل Configuration یا Secret Manager اختصاصی Cloud.
- انتخاب Provider/Model واقعی AI یا اعتبارسنجی دسترسی شبکه‌ای آن‌ها.
- Migration داده یا ایجاد Index دیتابیس.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/shared/config/models.py`
- `src/telegram_assist_bot/shared/config/loader.py`
- `src/telegram_assist_bot/shared/config/errors.py`
- `src/telegram_assist_bot/shared/config/__init__.py`
- `config/configuration.example.json`
- `.gitignore`
- `tests/unit/shared/config/test_loader.py`
- `tests/unit/shared/config/test_validation.py`
- `tests/unit/shared/config/test_secret_resolution.py`
- اسناد پروژه طبق بخش «به‌روزرسانی‌های مستندات».

نام دقیق ماژول‌ها می‌تواند با ساختار تثبیت‌شده در T001 سازگار شود، اما مسئولیت‌ها و مرز لایه‌ای نباید تغییر کنند.

## نکات پیاده‌سازی

- مدل Configuration باید immutable یا عملاً read-only باشد و فقط در Composition Root ساخته شود.
- JSON دارای متن فارسی باید با `ensure_ascii=False` در fixtureها یا ابزار تولید نوشته شود؛ فایل نمونه باید UTF-8 باشد.
- مقدار Secret در `repr`، Exception یا Log ظاهر نشود. تست redaction باید مقدار sentinel را در کل پیام خطا جست‌وجو کند.
- مسیر فایل محلی از ورودی Configuration نباید به‌عنوان URL عمومی عرضه شود؛ canonicalization و محدودیت مسیر Runtime در Task مرتبط انجام می‌شود.
- **ریسک Configuration:** نام کلیدها قرارداد عمومی‌اند؛ تغییر بعدی نیازمند migration و سازگاری است. Schema ابتدایی و نسخهٔ آن باید صریح باشد.
- **ریسک Migration:** این Task فقط `configuration_schema_version` را تعریف می‌کند؛ migration خودکار Configuration خارج از محدوده است و نسخهٔ ناشناخته باید Fail-fast شود.
- **ریسک Compatibility:** مقدارهای پیش‌فرض فقط برای گزینه‌های واقعاً اختیاری مجازند؛ Secret یا شناسهٔ حیاتی نباید default جعلی بگیرد.
- **ریسک Concurrency:** Configuration پس از Startup تغییر نمی‌کند؛ cache سراسری mutable یا reload هم‌زمان ساخته نشود.
- **ریسک Security:** fixtureها مصنوعی باشند، فایل محلی و Session در Git ignore شوند و خطاها فقط نام Environment Variable گمشده را بگویند.

## معیارهای پذیرش عینی

1. یک فایل نمونهٔ معتبر بدون Secret واقعی وجود دارد و به مدل typed تبدیل می‌شود.
2. نبودن فایل، JSON نامعتبر، UTF-8 نامعتبر و Schema version پشتیبانی‌نشده با خطای مشخص و Fail-fast پایان می‌یابد.
3. همهٔ خطاهای اعتبارسنجی مستقل در یک بار اجرا با مسیر فیلد گزارش می‌شوند.
4. `Asia/Tehran` و ZoneInfoهای معتبر پذیرفته و مقدار نامعتبر رد می‌شود.
5. Secretهای لازم فقط از Environment resolve می‌شوند و هیچ مقدار Secret در خروجی خطا/`repr` دیده نمی‌شود.
6. شناسهٔ تکراری Admin/Channel، مقصد ناشناخته و بازهٔ عددی نامعتبر رد می‌شود.
7. فایل نمونه، فایل‌های Python و fixtureهای جدید UTF-8 و فاقد Mojibake هستند.
8. هیچ اتصال خارجی هنگام load/validation انجام نمی‌شود.

## Unit Testهای الزامی

- load موفق فایل نمونه و تبدیل نوع همهٔ بخش‌ها.
- خطاهای فایل مفقود، JSON خراب، encoding خراب و Schema version ناشناخته.
- تجمیع چند خطا و درج مسیر دقیق هر فیلد.
- validation مربوط به Timezone، Enum، بازه، یکتایی و reference مقصد.
- resolve موفق/ناموفق Environment Secret و عدم نشت sentinel در پیام/`repr`.
- حفظ متن فارسی، نیم‌فاصله و Emoji در Configuration و fixture.

## Integration Testهای الزامی

N/A. این Task عمداً فقط parsing، validation و Secret resolution بدون I/O شبکه‌ای یا دیتابیس را می‌سازد؛ رفتار آن با Unit Test و filesystem موقت کاملاً پوشش‌پذیر است.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/shared/config
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

بازبینی دستی `config/configuration.example.json`، diff فارسی و `git diff --check` نیز الزامی است. هر مورد عمدیِ خراب‌متن در fixture باید به‌صورت صریح allowlist شده باشد.

## به‌روزرسانی‌های مستندات

- ثبت نتیجه و فرمان‌های اجراشده در همین فایل و تغییر Status فقط پس از قبولی همهٔ معیارها.
- علامت‌گذاری T002 در `docs/ROADMAP.md` پس از تکمیل.
- به‌روزرسانی `docs/STATUS.md` با آخرین Task کامل و Task بعدی.
- افزودن مسیر و جریان Configuration به `docs/CODE_MAP.md`.
- همگام‌سازی `docs/ARCHITECTURE.md` اگر نام/مرز مدل‌ها با طرح فعلی تفاوت پیدا کرد.
- ثبت تصمیم Schema/version یا کتابخانهٔ مدل‌سازی در `docs/DECISIONS.md` فقط اگر تصمیم معماری پایدار است.

## نتایج راستی‌آزمایی

- **Verified on:** 2026-07-11
- **Toolchain:** `uv 0.11.28`، CPython `3.12.13` و `3.13.14`.
- **Integration tests:** طبق Scope برابر N/A؛ همهٔ I/O فایل با `tmp_path` در Unit
  Test و بدون شبکه/دیتابیس پوشش داده شد.
- **Remote CI:** اجرا نشد؛ Matrix معادل CI روی هر دو Minor به‌صورت محلی موفق شد.

| Command or check | Result |
|---|---|
| `uv run pytest tests/unit/shared/config` | Pass؛ ۱۳۹ تست Configuration |
| `uv run pytest` | Pass؛ ۲۰۱ تست کل مخزن |
| آزمون کامل با `--cov=telegram_assist_bot --cov-branch --cov-fail-under=90` | Pass روی Python 3.12 و 3.13؛ branch coverage کل ۹۲٫۷۳٪ |
| `uv run ruff check .` | Pass روی هر دو Minor |
| `uv run ruff format --check .` | Pass روی هر دو Minor |
| `uv run mypy src tests` و Gate کامل `mypy src tests scripts` | Pass در حالت strict روی هر دو Minor |
| `uv run python scripts/check_text_integrity.py --changed` | Pass؛ همهٔ فایل‌های changed/untracked غیرignored UTF-8 و فاقد marker خراب بودند |
| `uv run python scripts/check_text_integrity.py --all` | Pass؛ کل متن مخزن بررسی شد |
| `uv lock --check` و `uv sync --locked --group dev` | Pass؛ Pydantic، tzdata و dependencyهای توسعه در ۳۰ Package قفل شدند |
| `uv build --no-build-isolation` و `check_distribution.py dist` | Pass؛ Config sample در sdist و ماژول‌های Config در Wheel تأیید شدند |
| Clean-wheel install/import | Pass روی Python 3.12 و 3.13 همراه runtime dependencyها و `Asia/Tehran` |
| `detect-secrets-hook --baseline .secrets.baseline` | Pass برای همهٔ فایل‌های Git-visible شامل فایل‌های جدید |
| `git diff --check` و بازبینی دستی diff/نمونهٔ فارسی | Pass؛ Persian، ZWNJ، LF و Emoji سالم ماندند |
| Secret/Session/generated-file review | Pass؛ Credential واقعی، Config محلی، Session یا فایل Runtime افزوده نشد |

### بررسی معیارهای پذیرش

| # | Result | Evidence |
|---|---|---|
| ۱ | Pass | `configuration.example.json` با Environment referenceهای مصنوعی به مدل کامل typed تبدیل شد. |
| ۲ | Pass | خطاهای مستقل file-not-found، read، UTF-8، JSON/root و Schema ناشناخته تست شدند. |
| ۳ | Pass | خطاهای structural، semantic و Secret حتی در یک section نامعتبر، یکجا و با path دقیق گزارش شدند. |
| ۴ | Pass | `Asia/Tehran` و `UTC` پذیرفته و ZoneInfo نامعتبر رد شد؛ `tzdata` قفل است. |
| ۵ | Pass | resolve فقط از Mapping محیطی انجام و sentinel از `str`، `repr`، cause/context و snapshot حذف شد. |
| ۶ | Pass | Admin/Channel/Provider/Route تکراری، مقصد/Provider ناشناخته و boundaryهای عددی رد شدند. |
| ۷ | Pass | نمونه، Source و fixtureها UTF-8 هستند و فارسی، نیم‌فاصله، line break و Emoji round-trip دقیق دارند. |
| ۸ | Pass | تست no-I/O اثبات کرد Loader هیچ Socket، Session path، MongoDB، Telegram یا AI را لمس نمی‌کند. |

## تعریف انجام‌شدن

- همهٔ معیارهای پذیرش و Testهای الزامی پاس شده‌اند.
- Quality Gateهای T001 پاس شده‌اند و هیچ Test لازم skip نشده است.
- Config نمونه امن، UTF-8 و قابل load است و Secret scanner/بازبینی diff هیچ Credential پیدا نمی‌کند.
- مرز متمرکز Configuration رعایت شده و هیچ اتصال خارجی اضافه نشده است.
- مستندات لازم به‌روز شده و Taskهای بعدی می‌توانند فقط به مدل typed وابسته شوند.
