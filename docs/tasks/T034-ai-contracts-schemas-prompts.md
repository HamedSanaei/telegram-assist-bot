# T034 — قرارداد AI، Schema و Prompt version

## وضعیت

`Planned`

## هدف

تعریف قراردادهای مستقل از Provider برای Taskهای AI فاز اول، Schemaهای ورودی/خروجی و Promptهای نسخه‌دار، بدون انتخاب یا فراخوانی Provider واقعی.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش‌های `11.1` تا `11.5` در حد قرارداد و قابلیت Config-driven.
- `docs/REQUIREMENTS.md`، بخش `11.11 یکسان‌سازی خروجی Providerها`.
- `docs/REQUIREMENTS.md`، بخش `11.16 ثبت Prompt Version`.
- `docs/ARCHITECTURE.md`، بخش `4` (`AIJob` و `AIAnalysis`)، بخش `5` (Use Caseهای AI)، بخش `6` (`AIProvider`)، بخش `12`، بخش `13` و بخش `15`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام‌های `4` و `5`؛ این Task Provider-agnostic می‌ماند.

## وابستگی‌ها

- `T002` و `T003` باید کامل شده باشند.

## دامنه کار

- تعریف `AITaskType` فقط برای تشخیص تبلیغ، تکرار معنایی، دسته‌بندی و امتیازدهی فاز اول.
- تعریف request context و Schema خروجی Application-owned برای هر Task با type/range/enum صریح.
- تعریف قرارداد `AIProvider` برای یک Attempt روی یک Provider/Model و Raw response envelope بدون نوع SDK.
- تعریف `AIResult` shell/metadata استاندارد موردنیاز Normalization آتی، بدون ادعای valid بودن پاسخ خام.
- نگهداری Prompt templateهای UTF-8 با `prompt_version`، `schema_version` و Hash قطعی.
- Validation registry برای سازگاری Task، Prompt و Schema پیش از enqueue.

## خارج از دامنه

- انتخاب نام Provider، Model، Base URL، Auth یا Quota واقعی.
- HTTP Adapter (`T036` و `T037`)، صف (`T035`) و Routing/Retry/Fallback (`T039`).
- Repair/Normalization پاسخ (`T038`).
- ساخت Featureهای AI سطح Post مانند تبلیغ/duplicate/categorization/scoring.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/ai/contracts.py`
- `src/telegram_assist_bot/application/ai/schemas.py`
- `src/telegram_assist_bot/application/ai/prompt_registry.py`
- `src/telegram_assist_bot/application/ports/ai_provider.py`
- Promptها زیر `src/telegram_assist_bot/application/ai/prompts/`
- `tests/unit/application/ai/test_ai_contracts.py`
- `tests/unit/application/ai/test_prompt_registry.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** فقط descriptor عمومی Provider/Model از T002 مصرف شود؛ placeholder مثال نباید Provider قابل اجرا تلقی شود.
- **Migration:** Migration DB در این Task N/A است؛ versionهای Prompt/Schema از ابتدا پایدار باشند تا T035/T041 آن‌ها را ذخیره کنند.
- **Compatibility:** افزودن فیلد optional سازگار است؛ حذف/تغییر معنا نیازمند schema version جدید است.
- **Concurrency:** Registry پس از startup immutable و بدون state سراسری mutable باشد؛ Hash در همه Processها قطعی باشد.
- **Security:** Prompt/Fixture نباید Secret داشته باشد؛ سیاست ذخیره raw response هنوز اجرا نمی‌شود و متن حساس Log نمی‌شود.

## معیارهای پذیرش عینی

1. هر چهار Task فاز اول request/output Schema مستقل و version صریح دارد.
2. Application contract هیچ نام/SDK/response model مربوط به Provider واقعی ندارد.
3. Registry ناسازگاری Task/Prompt/Schema و version تکراری را Fail-fast می‌کند.
4. Hash Prompt برای محتوای UTF-8 یکسان قطعی و تغییر محتوا موجب تغییر Hash است.
5. فایل‌خوانی Prompt صراحتاً `encoding="utf-8"` دارد و متن فارسی سالم می‌ماند.
6. هیچ تماس شبکه یا Provider ساختگی در این Task وجود ندارد.

## تست‌های واحد الزامی

- ساخت قرارداد هر Task و رد type/range/enum نامعتبر در Schema definition.
- version/Hash قطعی Prompt و تغییر Hash پس از تغییر محتوا.
- رد duplicate version و mismatch Task/Schema.
- حفظ فارسی، نیم‌فاصله و Emoji هنگام load Prompt.
- آزمون منع import Infrastructure/SDK در قرارداد Application.

## تست‌های یکپارچه‌سازی الزامی

- `N/A`: این Task فقط قرارداد و Registry خالص Application می‌سازد و هیچ Adapter/Persistence ندارد؛ Contract integration Provider در T036/T037 و Queue integration در T035 انجام می‌شود.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/ai/test_ai_contracts.py tests/unit/application/ai/test_prompt_registry.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت Task typeها، Schema/version و قرارداد `AIProvider` در `docs/ARCHITECTURE.md` و مسیرها در `docs/CODE_MAP.md`.
- ثبت Decision فقط برای انتخاب مهم نسخه/Schema، نه Provider واقعی.
- مستندسازی Promptها و به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که قراردادها و Promptهای versioned برای چهار Task با تست UTF-8/Hash/compatibility پاس، همه Quality Gateها موفق، هیچ Provider واقعی یا تماس شبکه اختراع نشده و مرزهای Taskهای بعدی حفظ شده باشند.
