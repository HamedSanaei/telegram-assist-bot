# T001 — Bootstrap پروژه و Quality Gateها

- **Status:** Completed
- **Milestone:** Milestone 0 — پایه قابل اجرا

## Goal

ایجاد یک Package خالی اما قابل نصب برای Python، ساختار پوشه‌های معماری، ابزارهای توسعه و Quality Gateهای تکرارپذیر تا Taskهای بعدی روی یک پایهٔ واحد، UTF-8-safe و قابل آزمون ساخته شوند. این Task فقط Scaffold و Development Tooling است و هیچ رفتار محصولی یا اتصال خارجی ایجاد نمی‌کند.

## Requirement references

- `docs/REQUIREMENTS.md` بخش‌های `1` و `3`: Python، ماژولار بودن، تست‌پذیری و جداسازی لایه‌ها.
- `docs/REQUIREMENTS.md` بخش `15`: پایهٔ Unit Test و Integration Test.
- `docs/REQUIREMENTS.md` بخش‌های `12` تا `14`: فقط الزامات عمومی Logging/Retry/Security که بر ابزار، فایل‌های Ignore و جلوگیری از Secret اثر دارند؛ پیاده‌سازی Runtime آن‌ها خارج از این Task است.
- `docs/ARCHITECTURE.md` بخش‌های `3`، `15` و `16`: جهت وابستگی، راهبرد تست و ساختار Package اولیه.
- `docs/DECISIONS.md`: `ADR-001`، `ADR-003` و `ADR-008`.
- `AGENTS.md` بخش‌های `6` تا `8` و `15` تا `18`.

## Dependencies

- ندارد.
- وجود و سازگاری اسناد برنامه‌ریزی پیش‌نیاز شروع است، اما هیچ Task پیاده‌سازی دیگری لازم نیست.

## Scope

1. ایجاد `pyproject.toml` معتبر با metadata پروژه، Build Backend، نسخهٔ Python و تنظیمات مرکزی ابزارها.
2. ایجاد Lockfile قابل Commit و workflow واحد برای نصب وابستگی‌ها.
3. ایجاد ساختار `src` برای Package و لایه‌های معماری فقط با فایل‌های حداقلی importable و Docstring انگلیسی.
4. ایجاد ساختار تست‌های `unit`، `integration`، `contract`، `e2e` و `fixtures` و تنظیم Markerها.
5. افزودن Smoke Testهای Package، تست‌های سلامت UTF-8/Persian fixture و تست ابزار بررسی متن.
6. پیکربندی lint، format، static type checking، coverage و build.
7. افزودن workflow CI برای اجرای همان فرمان‌های محلی روی نسخه‌های پشتیبانی‌شده Python.
8. تکمیل `.gitignore` و `.editorconfig` برای جلوگیری از Commit شدن Environment، Secret، Session، Config محلی، Media، Log، Cache و Artifact ساخت.
9. افزودن راهنمای کوتاه توسعه و اسکریپت tooling برای بررسی UTF-8 و نشانه‌های خرابی متن در فایل‌های تغییرکرده.

## Explicit out-of-scope items

- Telegram authentication، ساخت یا استفاده از Session و انتخاب Telegram SDK.
- Telegram crawling، History، Listener یا تبدیل Update.
- MongoDB client، repository، collection، index، migration یا اتصال آزمایشی.
- AI provider، Prompt، Schema، Job، Retry/Fallback یا تماس HTTP.
- انتشار فوری یا زمان‌بندی‌شده و هر نوع Scheduler/Worker runtime.
- دانلود، نگهداری، تجمیع یا Cleanup مدیا.
- Telegram Bot API، مدیران، پیام تأیید، Keyboard یا Callback.
- Configuration business schema و Secret loading؛ این رفتار متعلق به `T002` است.
- Domain model، state transition یا Use Case؛ این رفتار از `T003` به بعد ساخته می‌شود.
- Logging runtime، retry policy و Composition Root اجرایی؛ این موارد در Taskهای بعدی هستند.
- انتخاب Provider، Telegram library، MongoDB driver یا هر dependency کاربردیِ آینده.

## Expected files and directories

مسیرهای دقیق می‌توانند فقط برای رفع محدودیت ابزار build اندکی تغییر کنند؛ هر تغییر باید در `docs/CODE_MAP.md` ثبت شود.

```text
.
├── .editorconfig
├── .gitignore
├── .github/
│   └── workflows/
│       └── quality.yml
├── README.md
├── pyproject.toml
├── uv.lock
├── scripts/
│   └── check_text_integrity.py
├── src/
│   └── telegram_assist_bot/
│       ├── __init__.py
│       ├── py.typed
│       ├── domain/__init__.py
│       ├── application/__init__.py
│       ├── infrastructure/__init__.py
│       ├── presentation/__init__.py
│       ├── workers/__init__.py
│       ├── shared/__init__.py
│       └── bootstrap/__init__.py
└── tests/
    ├── unit/
    │   ├── test_package_import.py
    │   └── test_text_integrity.py
    ├── integration/
    ├── contract/
    ├── e2e/
    └── fixtures/
        └── persian_utf8.json
```

فایل‌های نگهدارنده مانند `.gitkeep` فقط برای پوشه‌های عمداً خالی مجازند. هیچ Entry Point اجراییِ محصول در این Task ایجاد نمی‌شود.

## Python version policy

- Baseline رسمی پروژه `CPython 3.12` است.
- محدودهٔ metadata باید `>=3.12,<3.15` باشد؛ Python 3.12، 3.13 و 3.14 نسخه‌های پشتیبانی‌شده‌اند.
- CI باید حداقل روی آخرین Patch در شاخه‌های 3.12، 3.13 و 3.14 اجرا شود.
- نسخهٔ Patch قفل نمی‌شود، اما نسخهٔ Minor خارج از این بازه تا زمان عبور همهٔ Quality Gateها و ثبت تصمیم سازگاری پشتیبانی‌شده نیست.
- استفاده از قابلیت‌های مخصوص یک Minor بالاتر از 3.12 ممنوع است، مگر با Guard روشن و تست روی تمام نسخه‌های پشتیبانی‌شده.

> یادداشت نگه‌داری: اجرای اصلی T001 فقط روی Python 3.12 و 3.13 راستی‌آزمایی
> شده بود. پشتیبانی Python 3.14 بعداً با به‌روزرسانی metadata، CI، Lockfile و
> Quality Gateهای همان نسخه افزوده شد؛ سیاست فعلی در ADR-001 ثبت است.
- تغییر این سیاست نیازمند به‌روزرسانی `pyproject.toml`، CI، Lockfile، مستندات و در صورت اثر معماری یک ADR است.

## Dependency management approach

- `pyproject.toml` منبع حقیقت metadata و dependency declaration بر پایهٔ PEP 621 است.
- `uv` ابزار رسمی resolve، lock و اجرای فرمان‌ها است و `uv.lock` باید Commit شود.
- Build Backend، `hatchling` است؛ Package باید با layout نوع `src` به wheel و sdist ساخته شود.
- dependencyهای توسعه در گروه `dev` نگهداری می‌شوند و حداقل شامل `pytest`، `pytest-cov`، `ruff` و `mypy` هستند.
- T001 نباید runtime dependency برای Telegram، MongoDB، AI، HTTP، Scheduler یا Configuration اضافه کند.
- نسخه‌های resolveشده در `uv.lock` دقیق‌اند؛ تغییر declaration یا lockfile باید در یک Diff واحد و با `uv lock --check` راستی‌آزمایی شود.
- نصب رسمی توسعه با `uv sync --locked --group dev` انجام می‌شود؛ دستورهای جایگزین فقط می‌توانند در README مستند شوند و نباید Lockfile دوم بسازند.

## Project package structure

- نام distribution و import package باید به‌ترتیب `telegram-assist-bot` و `telegram_assist_bot` باشد.
- Packageها زیر `src/telegram_assist_bot/` قرار می‌گیرند.
- جهت وابستگی مورد انتظار `domain ← application ← adapters/composition` است.
- در T001 همهٔ زیرPackageها فقط Scaffold هستند؛ `domain` و `application` نباید import خارجی یا import از لایه‌های بیرونی داشته باشند.
- `shared` فقط برای primitiveهای واقعاً مشترک آینده رزرو می‌شود و نباید به محل تجمیع utilityهای نامرتبط تبدیل شود.
- `bootstrap` فقط محل Composition Root آینده است و در این Task Process یا service راه‌اندازی نمی‌کند.
- Package باید marker فایل `py.typed` داشته باشد.

## Testing foundation

- Runner رسمی `pytest` است و discovery، Markerها و coverage در `pyproject.toml` پیکربندی می‌شوند.
- اجرای پیش‌فرض نباید شبکه، MongoDB، Telegram یا credential واقعی بخواهد.
- Markerهای `integration`، `contract`، `e2e` و `live` باید ثبت شوند؛ `live` در اجرای پیش‌فرض کنار گذاشته می‌شود.
- Coverage باید branch coverage را فعال کند و حداقل اولیهٔ کل Package برابر 90 درصد باشد؛ کاهش threshold برای عبور مصنوعی مجاز نیست.
- Fixture فارسی باید شامل حروف فارسی، نیم‌فاصله، خط جدید، نشانه‌گذاری و Emoji باشد و بدون normalization خوانده شود.
- هیچ تستی نباید به timezone سیستم، شبکه، ترتیب فایل‌سیستم یا Secret محلی وابسته باشد.

## Linting

- `ruff check .` linter رسمی است.
- Rule set باید خطاهای Python، import، naming/docstringهای public و خطاهای رایج async/security را در حد سازگار با Scaffold پوشش دهد.
- Ignoreها باید محدود، دارای دلیل و ترجیحاً در نزدیک‌ترین scope باشند؛ Ignore سراسری برای خاموش‌کردن خطاهای واقعی پذیرفته نیست.
- فایل تولیدشده فقط در صورت اجبار ابزار و با exclusion صریح از lint خارج می‌شود.

## Formatting

- `ruff format` formatter رسمی Python است.
- CI فقط `ruff format --check .` اجرا می‌کند و فایل‌ها را تغییر نمی‌دهد.
- Line ending فایل‌های متنی `LF` و encoding آن‌ها UTF-8 است؛ `.editorconfig` این قواعد را اعلام می‌کند.
- Formatter دیگری که با Ruff خروجی متعارض بسازد افزوده نمی‌شود.

## Static type checking

- `mypy` type checker رسمی است و باید روی `src`، `tests` و `scripts` اجرا شود.
- تنظیمات اولیه باید strict باشد؛ هر استثنا باید کوچک، توضیح‌دار و module-specific باشد.
- `disallow_untyped_defs` و بررسی return/type narrowing نباید برای عبور موقت خاموش شوند.
- Third-party stub یا ignore_missing_imports فقط هنگام افزودن dependency مربوط و با دلیل قابل ردیابی مجاز است.

## UTF-8 and Persian-content requirements

- همهٔ فایل‌های متنی جدید UTF-8 و فاقد BOM ناخواسته باشند.
- هر File I/O پایتون در tooling باید `encoding="utf-8"` را صریحاً مشخص کند.
- JSON فارسی باید هنگام serialization از `ensure_ascii=False` استفاده کند.
- تست representative باید برابری دقیق متن فارسی، نیم‌فاصله، line break و Emoji را پس از read/JSON round-trip ثابت کند.
- هیچ normalization ضمنی مجاز نیست.
- `scripts/check_text_integrity.py` باید فایل‌های متنی تغییرکرده را با decode سخت‌گیرانه بررسی کند و در برابر replacement character، الگوهای رایج mojibake و رشتهٔ غیرمنتظرهٔ question mark شکست بخورد؛ نمونه‌های عمدی اسناد فقط با allowlist کوچک و مستند پذیرفته می‌شوند.
- Diff انسانی فایل‌های فارسی بخشی از Definition of Done است.

## Implementation notes

- این Task باید یک Commit عمودی و کوچک باقی بماند؛ خالی بودن Packageها عمدی است.
- CI باید دقیقاً از `uv.lock` استفاده کند و resolve تازهٔ وابستگی‌ها را پنهان نکند.
- Artifactهای `.venv/`، `.pytest_cache/`، `.mypy_cache/`، `.ruff_cache/`، `.coverage*`، `htmlcov/`، `dist/` و `build/` باید Ignore شوند.
- `.env*` به‌جز template صریح، `configuration.json` محلی، فایل‌های `*.session*`، `var/`، Media و Log باید Ignore شوند.
- Ruleهای Ignore نباید `config/configuration.example.json` یا fixtureهای مصنوعی را مخفی کنند.
- workflow CI نباید Secret یا live service استفاده کند و permission آن باید حداقلی و read-only باشد.
- Scaffold نباید دربارهٔ SDKهای آینده تصمیم ضمنی بگیرد.

## Objective acceptance criteria

1. `uv sync --locked --group dev` روی Python پشتیبانی‌شده بدون dependency کاربردی اجرا می‌شود.
2. `python -c "import telegram_assist_bot"` در محیط ساخته‌شده موفق است.
3. wheel و sdist قابل ساخت‌اند و wheel فقط Package مورد انتظار را دارد.
4. همهٔ Packageهای معماری فهرست‌شده importable و فاقد رفتار محصولی‌اند.
5. Unit suite بدون network، database، Telegram و credential عبور می‌کند.
6. pytest Markerها ثبت شده‌اند و Marker ناشناخته خطا محسوب می‌شود.
7. Ruff lint و format check بدون خطا عبور می‌کنند.
8. mypy روی `src`، `tests` و `scripts` بدون خطا عبور می‌کند.
9. coverage با branch coverage حداقل 90 درصد است.
10. تست UTF-8 برابری دقیق نمونهٔ فارسی، نیم‌فاصله و Emoji و serialization خوانای JSON را ثابت می‌کند.
11. اسکریپت سلامت متن، UTF-8 نامعتبر و نمونهٔ mojibake مصنوعی را رد می‌کند و فایل سالم فارسی را می‌پذیرد.
12. `.gitignore` از Secret/Session/Config محلی/Media/Log/Artifact جلوگیری می‌کند و template/fixture مجاز را Ignore نمی‌کند.
13. CI همان Quality Gateهای محلی را روی Python 3.12، 3.13 و 3.14 اجرا می‌کند و به Secret نیاز ندارد.
14. `git diff --check` عبور می‌کند و هیچ فایل کاربردی، generated session یا credential افزوده نشده است.

## Required unit tests

- Import موفق `telegram_assist_bot` و تطبیق metadata/version با قرارداد Package.
- خواندن fixture فارسی با `encoding="utf-8"` و تطبیق byte-for-byte/character-for-character متن، نیم‌فاصله، line break و Emoji.
- JSON round-trip با `ensure_ascii=False` و اثبات باقی‌ماندن متن فارسی خوانا.
- پذیرش فایل UTF-8 سالم توسط ابزار سلامت متن.
- رد byte sequence نامعتبر UTF-8، replacement character، marker خرابی مصنوعی و question-mark run غیرمنتظره توسط ابزار.
- کنترل اینکه allowlist ابزار فقط استثنای صریح و محدود را می‌پذیرد.

## Required integration tests

- اتصال خارجی در T001 وجود ندارد؛ بنابراین Integration Test مربوط به MongoDB، Telegram، HTTP یا filesystem adapter عمداً **N/A** است.
- به‌جای جعل Integration Test، یک packaging smoke check الزامی است: ساخت wheel/sdist و import Package از Artifact در یک محیط تمیز یا روش معادل مورد پشتیبانی `uv`.
- پوشه و Marker `integration` باید ایجاد و ثبت شوند تا `T004` و Taskهای Adapter بتوانند بدون تغییر convention تست اضافه کنند.

## Verification commands

تمام فرمان‌ها باید از ریشهٔ مخزن و روی یکی از نسخه‌های پشتیبانی‌شده Python اجرا شوند:

```powershell
uv sync --locked --group dev
uv lock --check
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-report=term-missing --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --changed
uv build
uv run python -c "import telegram_assist_bot"
git diff --check
git status --short
```

علاوه بر خروجی فرمان‌ها، Diff فایل‌های فارسی باید دستی بررسی شود و فهرست فایل‌های جدید با قواعد Secret/Session تطبیق داده شود. اجرای واقعی روی هر دو Minor در CI الزامی است؛ اگر CI در دسترس نیست، Task کامل اعلام نمی‌شود مگر هر دو نسخه محلی راستی‌آزمایی شده باشند.

## Required documentation updates

- علامت‌گذاری `T001` به‌عنوان `Completed` در `docs/ROADMAP.md` فقط پس از عبور همهٔ معیارها.
- تغییر `docs/STATUS.md`: ثبت آخرین Task تکمیل‌شده، نتیجهٔ Quality Gateها و فعال‌کردن فقط `T002`.
- به‌روزرسانی `docs/CODE_MAP.md` از ساختار پیشنهادی به مسیرهای واقعی ایجادشده.
- به‌روزرسانی `docs/ARCHITECTURE.md` فقط اگر layout یا policy نهایی با طرح فعلی تفاوت معنادار دارد.
- ثبت تصمیم Python/toolchain در `docs/DECISIONS.md` با تکمیل پیامدهای `ADR-001`؛ تصمیم روزمرهٔ ابزار نباید ADR جداگانهٔ غیرضروری بسازد.
- افزودن فرمان‌های نصب و Quality Gate به `README.md`.
- Requirement محصول در `docs/REQUIREMENTS.md` نباید در این Task تغییر کند.

## Verification results

- **Verified on:** 2026-07-11
- **Toolchain:** `uv 0.11.28`، CPython `3.12.13` و `3.13.14`.
- **Remote CI:** اجرا نشد؛ معادل کامل Matrix روی هر دو Minor به‌صورت محلی اجرا و موفق شد.
- **Integration tests:** برای T001 طبق Scope برابر N/A؛ Packaging smoke جایگزین الزامی اجرا شد.

| Command or check | Result |
|---|---|
| `uv sync --locked --group dev` | Pass روی Python 3.12 و 3.13؛ ۲۵ Package قفل‌شده و بدون runtime dependency |
| `uv sync --locked --group dev --offline` | Pass؛ editable build از `hatchling==1.31.0` و `editables==0.5` قفل‌شده و بدون شبکه |
| `uv lock --check` | Pass؛ Lockfile با metadata و dependency declaration هماهنگ است |
| `uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-report=term-missing --cov-fail-under=90` | Pass روی هر دو Minor؛ ۶۱ تست و branch coverage برابر ۱۰۰٪ |
| `uv run ruff check .` | Pass روی هر دو Minor |
| `uv run ruff format --check .` | Pass روی هر دو Minor |
| `uv run mypy src tests scripts` | Pass در حالت strict روی هر دو Minor |
| `uv run python scripts/check_text_integrity.py --changed` | Pass؛ فایل‌های staged، unstaged و untracked غیرignored بررسی شدند |
| `uv run python scripts/check_text_integrity.py --all` | Pass؛ ۹۱ فایل متنی با UTF-8 سخت‌گیرانه بررسی شدند |
| `uv run detect-secrets-hook --no-verify --baseline .secrets.baseline <Git-visible files>` | Pass؛ baseline بدون finding و بدون دسترسی شبکه باقی ماند |
| `uv build` | Pass؛ wheel و sdist با build dependencyهای pin‌شده ساخته شدند |
| `uv build --no-build-isolation --offline` | Pass؛ Build رسمی locked و بدون Resolve پنهان اجرا شد |
| `uv run python scripts/check_distribution.py dist` | Pass؛ membership دقیق Wheel، metadata، Python range و sdist تأیید شد |
| Clean-wheel install/import | Pass روی Python 3.12 و 3.13 با `--no-deps` و isolated import |
| `uv run python -c "import telegram_assist_bot"` | Pass روی هر دو Minor |
| Ignore policy tests | Pass؛ Secret، private key، Session، Config محلی، Media، Log و Artifact پوشش داده شدند و template/fixture/Lockfile قابل Track ماندند |
| UTF-8/Persian manual review | Pass؛ متن فارسی، نیم‌فاصله، line break و Emoji سالم‌اند و Mojibake جدیدی دیده نشد |
| `git diff --check` و Secret/Session file review | Pass؛ فایل کاربردی، credential یا generated session افزوده نشده است |

هر ۱۴ معیار پذیرش به‌صورت جداگانه بازبینی و تأیید شد. دو بازبینی مستقل نیز پس از اصلاح baseline، build isolation، Wheel membership و قواعد private-key هیچ blocker باقی‌مانده‌ای گزارش نکردند.

## Definition of done

- تمام Scope و ۱۴ معیار پذیرش بالا برآورده شده‌اند.
- هیچ مورد Out-of-scope پیاده‌سازی نشده است.
- تمام Unit Testها، packaging check، lint، format، type check، text-integrity check و build واقعاً اجرا و موفق شده‌اند.
- نتیجهٔ CI برای Python 3.12، 3.13 و 3.14 موفق است یا اجرای معادل هر سه نسخه مستند شده است.
- Diff نهایی فقط Bootstrap/Tooling و مستندات الزامی آن را شامل می‌شود.
- هیچ Secret، Session، Config محلی، Media خصوصی یا Artifact تولیدشده Commit نشده است.
- UTF-8 و متن نمایندهٔ فارسی دستی و خودکار بررسی شده و normalization یا mojibake رخ نداده است.
- `docs/ROADMAP.md`، `docs/STATUS.md`، فایل این Task و `docs/CODE_MAP.md` با واقعیت پیاده‌سازی به‌روزرسانی شده‌اند.
- فایل Task نتیجهٔ دقیق همهٔ فرمان‌های verification را ثبت می‌کند و محدودیت اجراشده‌نشده‌ای پنهان نمی‌ماند.
- یک Git commit message کوتاه و انگلیسی پیشنهاد شده است.
