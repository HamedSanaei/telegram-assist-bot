# T038 — Validation، Repair و Normalization

## وضعیت

`Planned`

## هدف

تبدیل پاسخ خام Adapterهای واقعی مصوب به نتیجه Application-owned پس از Parse، اعتبارسنجی Schema و حداکثر یک Repair محدود و قطعی؛ و برگرداندن شکست صریح برای پاسخ نامعتبر، بدون Routing/Fallback.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `11.4 شرایط شکست Provider`.
- `docs/REQUIREMENTS.md`، بخش `11.5 اعتبارسنجی پاسخ`.
- `docs/REQUIREMENTS.md`، بخش `11.11 یکسان‌سازی خروجی Providerها`.
- `docs/ARCHITECTURE.md`، بخش `6` (`AIProvider`)، بخش `12`، بخش `14` و بخش `15`.

## وابستگی‌ها

- `T034`، `T036` و `T037` باید کامل شده باشند.
- فقط Fixtureها و Providerهای واقعی ثبت‌شده در Decisionهای T036/T037 مبنا هستند؛ Provider تازه در این Task تعریف نمی‌شود.

## دامنه کار

- Parse امن raw envelope و استخراج payload مطابق Adapter مصوب.
- Validation بر اساس Task/Schema/version T034: required fields، type، enum، range و نبود متن اضافه غیرمجاز.
- حداکثر یک Repair deterministic و allowlisted، بدون `eval` و بدون درخواست AI دوم.
- تبدیل پاسخ معتبر به `AIResult` استاندارد با provider/model/task/prompt version/latency/token metadata موجود.
- نتیجه شکست typed برای empty/invalid JSON/schema/range/unrelated/oversized پاسخ تا T039 بتواند تصمیم Fallback بگیرد.
- حفظ متن فارسی/Unicode و عدم جعل field یا confidence مفقود.

## خارج از دامنه

- Retry، انتخاب Model/Provider و Fallback (`T039`).
- Rate limit، Circuit، Cache، Audit persistence یا Metrics.
- Featureهای مصرف‌کننده نتیجه AI (`T042` تا `T045`).
- Repair با مدل AI، حدس مقدار مفقود یا Provider جدید.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/ai/response_parser.py`
- `src/telegram_assist_bot/application/ai/response_validator.py`
- `src/telegram_assist_bot/application/ai/response_normalizer.py`
- توسعه Schema/Result در `src/telegram_assist_bot/application/ai/` فقط در محدوده قرارداد T034.
- `tests/unit/application/ai/test_ai_response_validation.py`
- `tests/unit/application/ai/test_ai_response_normalization.py`
- `tests/integration/ai/test_provider_response_normalization.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** سقف اندازه پاسخ و فعال‌بودن Repair در صورت Configurable بودن باید bounded و Fail-fast باشد؛ ruleها کدنویسی‌شده و versioned باشند.
- **Migration:** Persistence در این Task `N/A` است؛ تغییر شکل `AIResult` نیازمند schema version سازگار برای T041 است.
- **Compatibility:** تفاوت envelope Provider در Adapter باز شود؛ Normalizer فقط قرارداد مشترک و schema version مشخص را ببیند.
- **Concurrency:** Parser/validator stateless و thread-safe باشد و state سراسری mutable نداشته باشد.
- **Security:** پاسخ untrusted است؛ اندازه/عمق محدود، `eval`/dynamic import ممنوع و raw body به‌طور پیش‌فرض Log نشود.

## معیارهای پذیرش عینی

1. پاسخ معتبر هر Task به `AIResult` استاندارد با metadata واقعی تبدیل می‌شود.
2. JSON خالی/خراب، field مفقود، type/range/enum غلط و متن اضافه نامجاز شکست typed می‌دهند.
3. فقط یک Repair allowlisted انجام و پس از شکست دوم نتیجه نامعتبر بازگردانده می‌شود.
4. هیچ مقدار، confidence یا نتیجه AI برای پرکردن خلأ ساخته نمی‌شود.
5. Fixtureهای هر دو Provider واقعی به قرارداد یکسان Normalize می‌شوند.
6. هیچ Retry/Fallback یا تماس شبکه در Normalizer رخ نمی‌دهد.

## تست‌های واحد الزامی

- پاسخ معتبر چهار Task و metadataهای optional/required.
- empty، invalid JSON، code fence/text اضافه، missing field، wrong type، enum و range.
- Repair موفق یک‌باره و شکست پس از یک Attempt.
- پاسخ oversized/deep و رشته malicious بدون اجرای کد.
- حفظ فارسی، نیم‌فاصله، Emoji و نبود fabricated field.

## تست‌های یکپارچه‌سازی الزامی

- عبور Fixture sanitized خروجی T036 و T037 از Adapter raw envelope تا Normalizer.
- مقایسه نتیجه استاندارد یک Task یکسان از دو Provider واقعی.
- تماس زنده Provider N/A است؛ Fixture contract برای این مرز کافی و deterministic است.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/ai/test_ai_response_validation.py tests/unit/application/ai/test_ai_response_normalization.py
uv run pytest tests/integration/ai/test_provider_response_normalization.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت جریان raw → parse → repair → validate → normalize و failure taxonomy در `docs/ARCHITECTURE.md`.
- افزودن Parser/Validator/Normalizer و Fixture contractها به `docs/CODE_MAP.md`.
- ثبت Decision فقط اگر Repair policy یک انتخاب معماری مهم باشد.
- به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که پاسخ هر دو Adapter واقعی با Fixture به `AIResult` یکسان Normalize، همه حالت‌های نامعتبر و Repair محدود تست، Unicode سالم، هیچ نتیجه جعلی تولید نشود، همه Quality Gateها موفق و Routing/Fallback خارج از Scope بماند.
