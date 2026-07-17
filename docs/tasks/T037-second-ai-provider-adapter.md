# T037 — Adapter Provider دوم و Model جایگزین

## وضعیت

`Completed`

## هدف

افزودن Adapter تک-Attempt برای Provider واقعی دوم و پشتیبانی قراردادی از Modelهای جایگزین مصوب، بدون اجرای ترتیب Fallback یا انتخاب خودکار Model/Provider.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `11.1 ترتیب اولویت ارائه‌دهندگان` در حد قابلیت پیکربندی descriptorها.
- `docs/REQUIREMENTS.md`، بخش `11.2 تنظیمات هر Provider`.
- `docs/REQUIREMENTS.md`، بخش `11.10 Fallback میان مدل‌های یک Provider` در حد قرارداد model-specific؛ orchestration در T039 است.
- `docs/ARCHITECTURE.md`، بخش `6` (`AIProvider`)، بخش‌های `12` تا `15`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام `4`.

## وابستگی‌ها

- `T036` باید کامل شده باشد.

### پیش‌نیاز تصمیم و Blocker

Provider دوم و دست‌کم یک Model جایگزین واقعی باید با API/version، Auth، Task capability، Quota، timeout و Fixture رسمی تصویب و در `docs/DECISIONS.md` ثبت شوند. همچنین باید روشن شود هر Model متعلق به کدام Provider و چه Taskهایی است. تا آن زمان پیاده‌سازی **Blocked** است و نام/مدل رایگان نباید حدس زده شود.

## دامنه کار

- توسعه descriptor عمومی برای چند Model مصوب بدون تغییر Port Application.
- پیاده‌سازی Adapter یک-Attempt Provider دوم مطابق همان raw envelope T034.
- اثبات اینکه Adapter Provider دارای Model parameter فقط Modelهای allowlisted و پشتیبان Task را می‌پذیرد.
- نگاشت protocol/errorهای اختصاصی Provider دوم در Infrastructure.
- Fixture/Contract test جدا برای Provider دوم و Modelهای جایگزین مصوب.
- حفظ جدایی client/DTO/Config دو Provider.

## خارج از دامنه

- مرتب‌سازی یا فراخوانی خودکار Provider/Model بعدی (`T039`).
- Retry، Rate limit reservation و Circuit (`T039`/`T040`).
- Normalization/Repair (`T038`).
- Provider سوم یا قابلیت اعلام‌نشده و live call پیش‌فرض.

## فایل‌ها و ماژول‌های مورد انتظار

- یک ماژول نام‌گذاری‌شده با نام واقعی Provider دوم مصوب زیر `src/telegram_assist_bot/infrastructure/ai/`؛ نام فایل پیش از Decision تعیین نمی‌شود.
- توسعه Config descriptorهای `src/telegram_assist_bot/infrastructure/config/`.
- بهبود مشترک `src/telegram_assist_bot/infrastructure/ai/` فقط اگر بدون coupling دو Provider باشد.
- `tests/unit/infrastructure/ai/test_second_provider_mapping.py`
- `tests/unit/infrastructure/ai/test_model_capabilities.py`
- `tests/integration/ai/test_second_provider_adapter.py`
- Fixtureهای sanitized دو Provider/Model.

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** Provider×Model باید ID پایدار، priority descriptor، capability و Secret reference مستقل داشته باشد؛ duplicate ID Fail-fast شود.
- **Migration:** Migration DB `N/A` است؛ Config قبلی تک‌مدلی باید سازگار خوانده یا migration مستند داشته باشد.
- **Compatibility:** abstraction مشترک نباید lowest-common-denominator ناسازگار بسازد؛ تفاوت protocol در Adapter بماند.
- **Concurrency:** client هر Provider lifecycle مستقل دارد؛ state سلامت مشترک/Rate limit در T040 و این Task stateless است.
- **Security:** Secretها و host allowlist مستقل، TLS فعال و raw payload/error redacted باشند.

## معیارهای پذیرش عینی

1. Provider/Modelهای واقعی و رسمی پیش از پیاده‌سازی ثبت شده‌اند.
2. Provider دوم همان Port و raw envelope T034 را بدون leakage نوع اختصاصی پیاده می‌کند.
3. Model نامعتبر یا ناسازگار با Task پیش از تماس خارجی رد می‌شود.
4. هر Provider request/error mapping و Fixture مستقل دارد.
5. Config چندمدلی قابل Validate است، اما هیچ Fallback خودکار رخ نمی‌دهد.
6. هیچ Secret یا Provider فرضی در مخزن نیست.

## تست‌های واحد الزامی

- mapping request/response/error Provider دوم.
- allowlist و capability دو یا چند Model مصوب.
- رد duplicate ID، Task ناسازگار، Secret reference مفقود و host نامعتبر.
- عدم import نوع Provider اول در Adapter دوم و برعکس.

## تست‌های یکپارچه‌سازی الزامی

- Fake server Provider دوم برای success/timeout/auth/429/5xx/malformed envelope.
- Contract fixtures مصوب برای هر Model جایگزین بدون تماس زنده.
- آزمون هم‌زیستی دو Adapter در Composition Root بدون Routing خودکار.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/infrastructure/ai/test_second_provider_mapping.py tests/unit/infrastructure/ai/test_model_capabilities.py
uv run pytest tests/integration/ai/test_second_provider_adapter.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت Provider دوم و Modelهای مصوب در `docs/DECISIONS.md` بدون Secret.
- افزودن Adapter/Config/Fixtureها به `docs/CODE_MAP.md` و همگام‌سازی معماری چندمدلی.
- به‌روزرسانی example Config، `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## نتایج راستی‌آزمایی جاری

- آزمون‌های متمرکز Provider دوم: `41 passed`.
- suite کامل non-live با MongoDB محلی: `1059 passed`، بدون failure یا skip.
- `mypy src tests scripts`: موفق.
- `ruff format --check src tests scripts`: موفق.
- `scripts/check_text_integrity.py --changed`: موفق.
- `git diff --check`: موفق.
- `ruff check src tests scripts`: موفق پس از اصلاح import-only دو فایل مجاور T036، بدون تغییر رفتار.
- `ruff format --check src tests scripts`: موفق؛ `259 files already formatted`.
- suite کامل non-live نهایی با MongoDB محلی: `1059 passed`.
- baseline تأییدشدهٔ پیاده‌سازی پیش از اصلاح import-only: commit فعلی `736da7c`.
- Adapterهای AI همچنان از Runtime، Worker، CLI و جریان‌های Telegram جدا هستند.

## تعریف Done

Task زمانی Done است که Decision واقعی رفع Blocker، Adapter دوم و Model descriptorها با Fake server/Contract fixture پاس، Quality Gateها موفق، Secretها امن و هیچ orchestration یا Provider اختراعی وارد Scope نشده باشد.
