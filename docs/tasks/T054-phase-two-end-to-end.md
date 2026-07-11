# T054 — پذیرش End-to-end فاز دوم

## وضعیت

`Planned`

## هدف

تثبیت end-to-end فاز دوم از Campaign Configured تا fetch/cache، Slot، Collision، انتشار یکتا و گزارش مدیران با MongoDB و Gatewayهای Fake، و اثبات همه معیارهای بخش ۱۷؛ بدون افزودن Feature جدید.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `17 معیارهای پذیرش فاز دوم`، بندهای `1` تا `12`.
- `docs/REQUIREMENTS.md`، بخش‌های `6.1` تا `6.5`.
- `docs/ARCHITECTURE.md`، بخش‌های `4` تا `11`، `14` و `15` در محدوده تبلیغات.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام‌های `10`، `11` و `12` که باید پیش‌تر حل شده باشند.

## وابستگی‌ها

- `T052` و `T053` باید کامل شده باشند.

## دامنه کار

- Harness کنترل‌شده Config → fetch URL/Album → cache → expand slots → resolve collision → publish → audit → report.
- آزمون چند time/day/destination و timezone با Fake Clock.
- آزمون Restart در مرحله Snapshot، Slot pending و Lease claimed و جلوگیری از duplicate.
- آزمون سیاست‌های مصوب Cache/Edit و Collision/min-gap.
- آزمون Retry bounded، failure audit و گزارش today/upcoming/recent failures برای Admin مجاز.
- رفع فقط Regressionهای اثبات‌شده T048 تا T053 با تست متمرکز.

## خارج از دامنه

- قابلیت تازه Campaign، UI وب، analytics یا فازهای پیشنهادی ۳ تا ۵.
- تماس زنده Telegram/Bot در Suite پیش‌فرض.
- تصمیم دوباره درباره Cache/Collision یا گسترش policy مصوب.
- Refactor گسترده خارج از اشکال‌های Milestone 6.

## فایل‌ها و ماژول‌های مورد انتظار

- `tests/integration/advertisements/test_phase_two_end_to_end.py`
- `tests/integration/advertisements/test_phase_two_restart.py`
- `tests/integration/advertisements/test_phase_two_concurrency.py`
- Fixtureهای بدون Secret زیر `tests/fixtures/advertisements/` و Fakeها زیر `tests/fakes/`.
- فقط فایل‌های T048 تا T053 که رفع Regression مستند لازم دارد.

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** Fixture باید همه policyها/timezone/limits را صریح تعیین و Config تولیدی/Secret واقعی را نخواند.
- **Migration:** مسیر پایگاه تمیز و upgrade Schema قبلی تبلیغات آزموده شود؛ این Task Schema Feature جدید نمی‌سازد.
- **Compatibility:** تست Contract روی Port/رفتار باشد نه جزئیات SDK/Query، و Snapshot/Slot نسخه قبلی را پوشش دهد.
- **Concurrency:** Fake Clock و barrier استفاده شود؛ sleep واقعی و نتیجه غیرقطعی ممنوع است.
- **Security:** SSRF/path traversal، Admin غیرمجاز، Secret redaction و نشت header مدیریتی در انتشار پوشش داده شوند.

## معیارهای پذیرش عینی

1. هر ۱۲ بند بخش `17` به یک سناریوی تست یا شاهد مستند قابل ردیابی Map شده است.
2. Post/Album/Entity/Premium Emoji از URL تا Publication سالم می‌ماند.
3. چندزمانه/چندمقصدی و Restart Slotها را حفظ و duplicate ایجاد نمی‌کند.
4. Cache/Edit و Collision دقیقاً طبق Decisionهای ثبت‌شده اجرا می‌شوند.
5. Retry bounded، Audit کامل و گزارش مجاز today/upcoming/errors اثبات می‌شوند.
6. هیچ Feature جدید خارج از T048 تا T053 وارد نشده است.

## تست‌های واحد الزامی

- `N/A` برای رفتار جدید: این Task Stabilization است و منطق تازه نمی‌سازد.
- هر باگ کشف‌شده باید Regression unit test در نزدیک‌ترین ماژول T048 تا T053 داشته باشد.

## تست‌های یکپارچه‌سازی الزامی

- `test_phase_two_end_to_end.py`: مسیر موفق متن و Album، چند زمان/روز/مقصد و گزارش.
- `test_phase_two_restart.py`: Restart در fetch/slot/claim و نتیجه یکتا.
- `test_phase_two_concurrency.py`: refresh/expand/resolve/publish هم‌زمان و outcomeهای معتبر.
- سناریوهای failure/redaction/unauthorized بدون تماس زنده.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/integration/advertisements/test_phase_two_end_to_end.py
uv run pytest tests/integration/advertisements/test_phase_two_restart.py tests/integration/advertisements/test_phase_two_concurrency.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت trace معیارهای بخش `17` و جریان واقعی فاز دوم در `docs/CODE_MAP.md`.
- اصلاح `docs/ARCHITECTURE.md` فقط برای اختلاف اثبات‌شده طرح و اجرا.
- Decision جدید فقط برای ابهام معماری حل‌نشده؛ Regression معمولی Decision نیست.
- به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که هر ۱۲ معیار فاز دوم با تست قطعی پاس، Restart/concurrency/security اثبات، Regressionهای محدود رفع، همه Quality Gateها موفق و هیچ Feature فاز بعدی افزوده نشده باشد.
