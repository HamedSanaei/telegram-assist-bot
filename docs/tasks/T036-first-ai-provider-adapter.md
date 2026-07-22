# T036 — Adapter اولین Provider منتخب

## وضعیت

`Completed`

## هدف

پیاده‌سازی یک Adapter تک-Attempt برای نخستین Provider/Model واقعی که قبلاً به‌طور صریح انتخاب شده است، با Timeout، Auth امن، نگاشت request و طبقه‌بندی response/error؛ بدون Retry، Fallback یا Normalization نهایی.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `11.2 تنظیمات هر Provider`.
- `docs/REQUIREMENTS.md`، بخش‌های `11.3 روند فراخوانی پایپ‌لاین` تا `11.5 اعتبارسنجی پاسخ` فقط در حد مرز یک Attempt و تحویل پاسخ خام.
- `docs/REQUIREMENTS.md`، بخش `14 امنیت`.
- `docs/ARCHITECTURE.md`، بخش `6` (`AIProvider`)، بخش `12`، بخش `13`، بخش `14` و بخش `15`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام `4`.

## وابستگی‌ها

- `T005` و `T034` باید کامل شده باشند.

### پیش‌نیاز تصمیم و Blocker

تا زمانی که نام Provider و Model واقعی، API/version رسمی، Base URL، روش Auth و Secret reference، Taskهای پشتیبانی‌شده، Quota/limits، Streaming policy، timeout و نمونه request/response بدون Secret تصویب و در `docs/DECISIONS.md` ثبت نشده‌اند، این Task **Blocked برای پیاده‌سازی** است. نام‌های `Provider A` یا URLهای example در نیازمندی انتخاب واقعی محسوب نمی‌شوند.

## دامنه کار

- افزودن Config typed فقط برای Provider/Model منتخب و اعتبارسنجی capabilityهای مصوب.
- پیاده‌سازی Port `AIProvider` T034 برای دقیقاً یک HTTP/SDK Attempt.
- نگاشت request داخلی به protocol رسمی و response به raw envelope استاندارد T034.
- Timeout صریح، محدودیت اندازه پاسخ و cancellation/resource cleanup.
- طبقه‌بندی transport، HTTP، auth، quota/rate-limit، unavailable-model و malformed-envelope؛ بدون Retry.
- استفاده از Fake server و Fixture sanitized برای Contract test.

## خارج از دامنه

- Provider یا Model دوم (`T037`).
- Parse/repair/schema normalization (`T038`).
- Retry، Routing و Fallback (`T039`) یا Circuit/Rate reservation (`T040`).
- تماس زنده در Suite پیش‌فرض و هر API Key واقعی در مخزن.

## فایل‌ها و ماژول‌های مورد انتظار

- یک ماژول نام‌گذاری‌شده با نام واقعی Provider مصوب زیر `src/telegram_assist_bot/infrastructure/ai/`؛ نام فایل پیش از Decision تعیین نمی‌شود.
- `src/telegram_assist_bot/infrastructure/ai/http_client.py` فقط اگر abstraction موجود T005 کافی نیست.
- توسعه مدل Config در `src/telegram_assist_bot/infrastructure/config/`.
- `tests/unit/infrastructure/ai/test_first_provider_mapping.py`
- `tests/integration/ai/test_first_provider_adapter.py`
- Fixtureهای sanitized زیر `tests/fixtures/ai/first_provider/`؛ پس از Decision می‌توان نام Provider واقعی را نیز در metadata Fixture ثبت کرد.

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** Base URL/Model/capability و Secret env reference باید Validate شوند؛ Secret مقداردهی‌شده هرگز در example Config نباشد.
- **Migration:** Migration دیتابیس `N/A` است؛ تغییر Config Schema باید backward-compatible یا versioned و مستند باشد.
- **Compatibility:** API version رسمی pin و تغییر response فقط در Adapter محصور شود؛ نوع SDK وارد Application نشود.
- **Concurrency:** HTTP client باید lifecycle صریح و برای concurrency مصوب امن باشد؛ limit سراسری در T040 است.
- **Security:** TLS verification فعال، redirect/host غیرمصوب و SSRF رد، Authorization header و raw response حساس redacted شوند.

## معیارهای پذیرش عینی

1. Decision واقعی Provider/Model پیش از هر کد Adapter ثبت شده است.
2. request هر Task پشتیبانی‌شده دقیقاً مطابق contract رسمی Map می‌شود.
3. پاسخ موفق فقط به raw envelope تحویل می‌شود و به‌عنوان نتیجه معتبر AI برچسب نمی‌خورد.
4. timeout/errorهای مصوب به category داخلی و metadata غیرحساس Map می‌شوند.
5. Secret در Source، Fixture، Exception یا Log دیده نمی‌شود.
6. Adapter خودش Retry/Fallback/Repair انجام نمی‌دهد.

## تست‌های واحد الزامی

- mapping request، header غیرحساس، model و task capability.
- mapping response metadata و همه error categoryهای مصوب.
- رد Config ناقص، model/task پشتیبانی‌نشده و Base URL نامعتبر.
- Redaction API key و Authorization header.

## تست‌های یکپارچه‌سازی الزامی

- Fake HTTP server برای success، timeout، 4xx auth، 429، 5xx، body بیش‌ازحد و JSON/envelope خراب.
- Contract fixture sanitized بر اساس مستند رسمی Provider منتخب.
- Live smoke test فقط opt-in و با Secret خارج از Git؛ شرط Done یا Suite پیش‌فرض نیست.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/infrastructure/ai/test_first_provider_mapping.py
uv run pytest tests/integration/ai/test_first_provider_adapter.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت Provider/Model/API/Auth/capability/limit مصوب در `docs/DECISIONS.md` بدون Secret.
- افزودن Adapter و Config واقعی به `docs/CODE_MAP.md` و همگام‌سازی `docs/ARCHITECTURE.md`.
- به‌روزرسانی example Config فقط با Secret reference بی‌خطر.
- به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که Blocker با Decision واقعی رفع، Adapter یک-Attempt با Fake server/Contract fixture پاس، Secret redaction و Timeout اثبات، همه Quality Gateها موفق و هیچ Provider/Fallback فرضی افزوده نشده باشد.
