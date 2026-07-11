# T047 — پذیرش End-to-end فاز اول

## وضعیت

Planned

## هدف

اثبات جریان کنترل‌شده فاز اول از دریافت تا تأیید و انتشار/زمان‌بندی و AI با MongoDB آزمایشی و Gatewayهای Fake، و ثبت پوشش معیارهای پذیرش بدون افزودن قابلیت محصولی تازه.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.21` «Pipeline کامل فاز اول».
- `docs/REQUIREMENTS.md`، بخش `16` «معیارهای پذیرش فاز اول».
- `docs/REQUIREMENTS.md`، بخش‌های `12` تا `15` برای logging، error، security و testing.
- `docs/ARCHITECTURE.md`، بخش‌های `2`، `15` و `16`.

## وابستگی‌ها

- T012 — Stabilization دریافت.
- T019 — Stabilization آماده‌سازی محتوا.
- T026 — Stabilization جریان تأیید.
- T033 — Stabilization زمان‌بندی/Restart.
- T046 — Stabilization Pipeline AI.

## دامنه

- تهیه Matrix بندهای بخش `16` و ارجاع هر بند به تست مالک قبلی یا سناریوی E2E این Task.
- یک جریان E2E متنی representative: ingest یکتا، پردازش، Approval، Toggle، انتشار فوری و نتیجه AI.
- یک جریان representative Media Group با Entity/Custom Emoji fixtureشده و زمان‌بندی پایدار.
- سناریوی Restart در میانه Job زمان‌بندی/AI و بازیابی Lease.
- سناریوی duplicate event، callback غیرمجاز و publication تکراری.
- بررسی جدایی header مدیر از محتوای مقصد و preservation متن/Entity فارسی.
- رفع فقط اشکال کوچک cross-layer که مانع پذیرش فاز اول است؛ ایراد بزرگ به Task کوچک جدید و Blocker تبدیل شود.

## خارج از دامنه

- تماس زنده Telegram/AI در Suite پیش‌فرض یا استفاده از credential واقعی.
- تبلیغات فاز دوم و Featureهای فازهای پیشنهادی.
- بازطراحی معماری یا refactor گسترده.
- پرکردن ابهام‌های محصولی با Default ضمنی.

## فایل‌ها و ماژول‌های مورد انتظار

- `tests/e2e/test_phase_one_text_flow.py`
- `tests/e2e/test_phase_one_media_schedule_flow.py`
- `tests/e2e/test_phase_one_restart_idempotency.py`
- fixtureهای Sanitized زیر `tests/fixtures/telegram/` و `tests/fixtures/ai/`
- اصلاح‌های محدود و ضروری زیر `src/telegram_assist_bot/`

## نکات پیاده‌سازی

- **Configuration:** یک config آزمایشی کامل با channel/admin/provider خیالی و Secret reference غیرواقعی استفاده شود؛ Config production تغییر نکند.
- **Migration:** E2E باید Startup/Migration واقعی و Indexهای لازم را اجرا کند؛ Schema جدید فقط برای رفع blocker کوچک و با تست migration مجاز است.
- **Compatibility:** Fixtureهای DTO/Entity و callback contract نسخه موجود را تثبیت کنند؛ تغییر contract نیازمند migration/decision جداست.
- **Concurrency:** duplicate ingest، callback هم‌زمان، claim پس از Restart و publication idempotency با MongoDB واقعی آزمایشی سنجیده شوند.
- **Security:** Admin غیرمجاز، callback جعلی، Secret redaction و media path safety حداقل با شواهد Taskهای مالک به Matrix متصل شوند.
- **Persian:** fixture نماینده باید متن فارسی، ZWNJ، line break، Emoji و Custom Emoji entity داشته باشد و diff خروجی دستی بررسی شود.

## معیارهای پذیرش عینی

1. همه ۲۵ بند بخش `16` به تست پاس‌شده یا محدودیت/Blocker دقیق و صادقانه نگاشت شده‌اند.
2. جریان متن از ingest تا انتشار، بدون duplicate و بدون header مدیریتی در مقصد کامل می‌شود.
3. جریان Media Group ترتیب، Caption و Entityهای fixture را حفظ و پس از Restart زمان‌بندی را بازیابی می‌کند.
4. callback غیرمجاز یا تکراری اثر جانبی انتشار ایجاد نمی‌کند.
5. publication/AI Job تکراری در چند Worker یک نتیجه منطقی دارد.
6. شکست خارجی به وضعیت قابل بازیابی/بررسی منتقل و structured log Sanitized تولید می‌کند.
7. Suite پیش‌فرض هیچ اتصال زنده Telegram/Provider یا Secret لازم ندارد.
8. Task فقط وقتی Completed است که هیچ معیار لازم fail یا unverified نباشد.

## Unit Testهای الزامی

- Unit Test جدید فقط برای regression هر Bug کوچک رفع‌شده لازم است.
- اگر هیچ pure-logic bug اصلاح نشود، Unit Test جدید `N/A` است؛ دلیل: این Task Gate پذیرش E2E رفتارهای قبلاً Unit-tested است. Unit Suite کامل باید پاس شود.

## Integration Testهای الزامی

- سه سناریوی E2E ذکرشده با MongoDB واقعی آزمایشی و Gateway Fake.
- Startup/Migration/Index و Restart worker.
- سناریوهای concurrency/idempotency/security representative.
- Sandbox زنده Telegram فقط به‌صورت opt-in خارج از Suite پیش‌فرض و برای Done الزامی نیست مگر Task در زمان اجرا صریحاً آن را مقرر کند.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/e2e
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

خروجی نماینده فارسی/Entity و final diff باید دستی بازبینی شوند.

## بروزرسانی مستندات الزامی

- ثبت Matrix، نتایج و محدودیت‌های واقعی در همین Task.
- بروزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و `docs/CODE_MAP.md`.
- همگام‌سازی `docs/ARCHITECTURE.md` با جریان اثبات‌شده.
- ابهام یا نقص بزرگ به Task کوچک جدید در `docs/tasks/` و Roadmap تبدیل شود؛ معیار تأییدنشده پنهان نشود.

## تعریف Done

- تمام معیارهای بخش `16` با تست اجراشده اثبات شده‌اند و هیچ blocker/failing test باقی نیست.
- E2E متن، Media، Restart، concurrency، security و Persian/Entity safety پاس‌اند.
- Quality Gate کامل پاس و هیچ Feature فاز دوم یا آینده وارد Scope نشده است.
