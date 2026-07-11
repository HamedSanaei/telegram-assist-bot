# T003 — مدل Domain و چرخهٔ عمر Post

## وضعیت

Completed

## هدف

تعریف مدل مستقل Post، هویت منبع، وضعیت‌ها و Transitionهای مجاز به‌صورت pure Domain تا Use Caseهای بعدی بدون وابستگی به Telegram SDK یا MongoDB بر یک قرارداد پایدار و قابل‌آزمون تکیه کنند.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `3. معماری پیشنهادی`، زیربخش‌های Domain و Application.
- `docs/REQUIREMENTS.md`، بخش `5.4 ذخیره اطلاعات پست`.
- `docs/REQUIREMENTS.md`، بخش `10. وضعیت‌های پیشنهادی پست`.
- `docs/REQUIREMENTS.md`، بخش `15. تست‌ها`، قواعد state transition و انقضا.
- `docs/ARCHITECTURE.md`، بخش `3. لایه‌ها و جهت وابستگی`.
- `docs/ARCHITECTURE.md`، بخش `4. مدل Domain`.
- `docs/ARCHITECTURE.md`، بخش `14. Logging، Retry، Idempotency و هم‌زمانی`، زیربخش Concurrency.
- `docs/DECISIONS.md`، `ADR-003`.

## وابستگی‌ها

- T001 — Bootstrap پروژه و Quality Gateها؛ باید Completed باشد.

## محدوده

- تعریف value object هویت پیام منبع بر پایهٔ `(source_channel_id, source_message_id)`.
- تعریف مدل Post با داده‌های لازم در این مرحله: شناسه داخلی، هویت/اطلاعات منبع، متن و Caption اصلی، Entityهای اصلی، زمان انتشار/دریافت/انقضا، وضعیت پردازش، نسخهٔ optimistic concurrency و تاریخچهٔ Transition.
- تعریف representation داخلی Entity مستقل از SDK با offset/length/type و metadata لازم برای Custom Emoji.
- تثبیت UTC-aware بودن زمان‌ها و محاسبهٔ `expires_at = received_at + 14 days`.
- تبدیل فهرست پیشنهادی بخش ۱۰ به مجموعهٔ حداقلی statusها و Transitionهای مجاز برای Milestone 0، با امکان افزودن statusهای مرحله‌های بعد بدون تغییر هویت Post.
- تعریف خطاهای Domain برای Transition نامعتبر، زمان naive، تغییر نسخهٔ اصلی محتوا و invariant شکسته.
- جدا نگه‌داشتن وضعیت کلی Post از وضعیت آیندهٔ هر مقصد؛ در این Task فقط قرارداد/جایگاه آن روشن می‌شود و workflow مقصد ساخته نمی‌شود.

## خارج از محدوده

- MongoDB document mapping، Repository و Index.
- دانلود Media یا مدل کامل چرخهٔ Media.
- پاک‌سازی متن و rebasing Entity.
- Telegram authentication/crawling، AI، Approval، Callback، Publication و Scheduling.
- طراحی کامل `DestinationSelection`، `Publication` یا Jobهای پایدار.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/posts/models.py`
- `src/telegram_assist_bot/domain/posts/status.py`
- `src/telegram_assist_bot/domain/posts/entities.py`
- `src/telegram_assist_bot/domain/posts/errors.py`
- `src/telegram_assist_bot/domain/posts/__init__.py`
- `tests/unit/domain/posts/test_post_identity.py`
- `tests/unit/domain/posts/test_post_lifecycle.py`
- `tests/unit/domain/posts/test_post_expiration.py`
- `tests/unit/domain/posts/test_telegram_entity.py`
- اسناد پروژه طبق بخش «به‌روزرسانی‌های مستندات».

## نکات پیاده‌سازی

- Domain فقط از کتابخانهٔ استاندارد و ماژول‌های Domain import کند؛ مدل driver/SDK ممنوع است.
- متن، Caption و Entityهای اصلی پس از ساخت Post immutable باشند؛ متن مقصدی بعداً به‌صورت artifact مشتق می‌شود.
- Transition باید actor category، reason، timestamp و correlation ID اختیاری را ثبت کند، ولی اطلاعات حساس یا object خارجی نپذیرد.
- statusهای بخش ۱۰ «پیشنهادی» هستند؛ Task باید جدول Transitionهای مجاز را مستند کند و از یک Enum کلی برای نمایش وضعیت چند مقصد سوءاستفاده نکند.
- **ریسک Configuration:** ندارد؛ Domain نباید Configuration loader را import کند. مقدار retention به الزام ثابت ۱۴ روز محدود است تا Task آینده سیاست configurable را صریحاً اضافه کند.
- **ریسک Migration:** نام statusها و فیلدهای public روی persistence آینده اثر دارند؛ تغییر نام بعدی نیازمند migration خواهد بود، پس قرارداد نهایی این Task در `ARCHITECTURE` ثبت شود.
- **ریسک Compatibility:** offset واحد Entity باید به‌صورت صریح در مدل نام‌گذاری/مستند شود؛ تبدیل SDK در Adapterهای بعدی انجام می‌شود.
- **ریسک Concurrency:** `version` و expected version بخشی از قرارداد باشند؛ Atomic enforcement در T004 انجام می‌شود.
- **ریسک Security:** reason و metadata نباید اجازهٔ ذخیرهٔ Secret یا object دلخواه serializationناپذیر بدهند.

## معیارهای پذیرش عینی

1. دو Post با زوج source یکسان هویت idempotency یکسان و با هر جزء متفاوت هویت متفاوت دارند.
2. Post فقط با datetimeهای timezone-aware ساخته می‌شود و انقضا دقیقاً ۱۴ روز پس از `received_at` است.
3. متن/Caption/Entityهای اصلی پس از ساخت قابل تغییر نیستند.
4. همهٔ Transitionهای مجاز Milestone 0 موفق و Transition نامعتبر با Domain exception رد می‌شود.
5. هر Transition تاریخچهٔ کامل قبلی/جدید، زمان، actor و reason را ثبت می‌کند و version را دقیقاً یک واحد افزایش می‌دهد.
6. مدل Domain هیچ import از `infrastructure`، `presentation`، MongoDB یا Telegram SDK ندارد.
7. متن فارسی، نیم‌فاصله، Emoji و Custom Emoji metadata بدون تغییر round-trip می‌شوند.

## Unit Testهای الزامی

- equality/hash یا key هویت منبع و validation شناسه‌های نامعتبر.
- ساخت Post با زمان aware و رد زمان naive.
- محاسبهٔ انقضای ۱۴روزه در مرز ماه/سال و بدون وابستگی به ساعت سیستم.
- جدول همهٔ Transitionهای مجاز و نمونه‌های Transition ممنوع.
- افزایش version، ترتیب history و رد expected version نامعتبر در قرارداد Domain.
- immutability متن اصلی و Entityها.
- حفظ دقیق Persian/ZWNJ/Emoji و metadata مربوط به Custom Emoji.
- تست منع import لایه‌های بیرونی، در صورت وجود ابزار معماری T001.

## Integration Testهای الزامی

N/A. این Task pure Domain است و هیچ Adapter، دیتابیس، فایل‌سیستم یا سرویس خارجی ندارد؛ تمام معیارها با Unit Test قطعی اثبات می‌شوند.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/domain/posts
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

بازبینی جدول Transitionها، importها، `git diff --check` و نمایش دستی fixture فارسی الزامی است.

## به‌روزرسانی‌های مستندات

- ثبت Status و نتایج verification در همین فایل.
- به‌روزرسانی T003 در `docs/ROADMAP.md` و وضعیت جاری در `docs/STATUS.md` پس از تکمیل.
- افزودن مدل‌ها، invariantها و مسیرها به `docs/CODE_MAP.md`.
- جایگزینی فهرست پیشنهادی با مدل واقعاً پیاده‌شده و جدول Transition در `docs/ARCHITECTURE.md`.
- ثبت تصمیم مهم دربارهٔ status split یا واحد Entity در `docs/DECISIONS.md` در صورت نیاز.

## نتایج راستی‌آزمایی

- **Verified on:** 2026-07-11
- **Toolchain:** `uv 0.11.28`، CPython `3.12.13` و `3.13.14`.
- **Integration tests:** طبق Scope برابر N/A؛ این Task فقط Domain خالص و بدون
  I/O، Adapter، شبکه، فایل‌سیستم یا پایگاه‌داده است.
- **Remote CI:** اجرا نشد؛ آزمون‌ها، Ruff، format و mypy روی هر دو Minor
  پشتیبانی‌شده به‌صورت محلی موفق شدند.

| Command or check | Result |
|---|---|
| `uv run pytest tests/unit/domain/posts` | Pass روی Python 3.12 و 3.13؛ ۱۴۴ تست Domain |
| `uv run pytest` | Pass روی Python 3.12 و 3.13؛ ۳۴۵ تست کامل non-live و بدون skip |
| آزمون کامل با `--cov=telegram_assist_bot --cov-branch --cov-fail-under=90` | Pass روی هر دو Minor؛ پوشش کل ۹۵٫۲۱٪ و پوشش package پست ۱۰۰٪ statement/branch |
| `uv run ruff check .` | Pass روی هر دو Minor |
| `uv run ruff format --check .` | Pass روی هر دو Minor |
| `uv run mypy src tests` و Gate کامل `mypy src tests scripts` | Pass در حالت strict روی هر دو Minor |
| `uv run python scripts/check_text_integrity.py --changed` | Pass؛ تمام فایل‌های تغییرکرده و untracked غیرignored با UTF-8 سخت‌گیرانه بررسی شدند |
| `uv run python scripts/check_text_integrity.py --all` | Pass؛ کل متن Git-visible مخزن بررسی شد |
| `uv lock --check` و `uv sync --locked --group dev` | Pass؛ Lockfile بدون تغییر و ۳۰ Package قفل‌شده هماهنگ‌اند |
| `uv build --no-build-isolation` و `check_distribution.py dist` | Pass؛ Wheel/sdist و پنج ماژول جدید Post Domain تأیید شدند |
| Clean-wheel install/import | Pass روی Python 3.12؛ API عمومی `Post` و `TelegramEntity` از wheel تمیز import شد |
| `detect-secrets-hook --baseline .secrets.baseline` | Pass برای همهٔ فایل‌های tracked و untracked غیرignored |
| `git diff --check` و بازبینی دستی diff/متن فارسی | Pass؛ فارسی، نیم‌فاصله، خط‌شکست، Emoji و Custom Emoji metadata سالم ماندند |
| Secret/Session/generated-file review | Pass؛ Secret، Configuration محلی، Session، cache یا artifact تولیدی Git-visible افزوده نشد |

### بررسی معیارهای پذیرش

| # | Result | Evidence |
|---|---|---|
| ۱ | Pass | `SourceMessageIdentity` هر دو جزء را در equality/hash و `as_tuple` به‌کار می‌برد؛ تفاوت هر جزء تست شده است. |
| ۲ | Pass | زمان naive و conversion/overflow نامعتبر با Domain exception رد و زمان aware به UTC canonical می‌شود؛ انقضا دقیقاً ۱۴ روز است. |
| ۳ | Pass | `Post`، `OriginalPostContent` و `TelegramEntity` frozen هستند و sequenceها defensive copy می‌شوند. |
| ۴ | Pass | جدول immutable سه وضعیت، هر سه edge مجاز و همهٔ edgeهای ممنوع به‌صورت exhaustive تست شده‌اند. |
| ۵ | Pass | هر Transition actor/reason/time/correlation، وضعیت قبلی/جدید و history کامل را نگه می‌دارد و version را یک واحد افزایش می‌دهد. |
| ۶ | Pass | تست AST همهٔ فایل‌های Domain را فقط به stdlib/Domain محدود و import پویا یا SDK/لایهٔ بیرونی را رد می‌کند. |
| ۷ | Pass | fixtureهای inline برابری byte-for-byte فارسی، ZWNJ، line break، Emoji، مختصات UTF-16 و `custom_emoji_id` را اثبات می‌کنند. |

## تعریف انجام‌شدن

- مدل و invariantها مستقل از Infrastructure پیاده و مستند شده‌اند.
- تمام Unit Testها و Quality Gateها پاس شده‌اند.
- هیچ status مربوط به Feature بعدی به workflow اجرایی تبدیل نشده است.
- UTF-8 و محتوای فارسی/Emoji دستی و خودکار بررسی شده‌اند.
- مستندات با کد واقعی همگام و هیچ Test لازم skip نشده است.
