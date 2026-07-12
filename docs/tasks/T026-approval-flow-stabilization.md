# T026 — Stabilization جریان تأیید

## وضعیت

`Completed`

## هدف

تثبیت سناریوهای بین‌لایه‌ای T020 تا T025 از دریافت Update مدیریتی تا Toggle اتمیک و همگام‌سازی همه پیام‌ها، با تست امنیت، رقابت و Restart؛ بدون افزودن قابلیت جدید.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش‌های `5.12` تا `5.16`.
- `docs/REQUIREMENTS.md`، بخش‌های `14 امنیت` و `15 تست‌ها`.
- `docs/ARCHITECTURE.md`، بخش‌های `3` تا `6`، `8`، `9`، `14` و `15`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام‌های `1`، `2`، `6` و `13`.

## وابستگی‌ها

- `T021` تا `T025` باید کامل شده باشند.
- Decisionهای SDK/Role، Callback lifecycle، معنای فوری، overflow و توپولوژی Approval باید ثبت شده باشند؛ نبود هرکدام Blocker است.

## دامنه کار

- ساخت Harness یکپارچه با MongoDB آزمایشی و Gatewayهای Fake برای Bot.
- آزمون جریان: Admin مجاز → Callback معتبر → Toggle اتمیک → Render/Sync همه Referenceها.
- آزمون Callback جعلی/منقضی، Admin غیرمجاز و Destination غیرمجاز بدون side effect.
- آزمون دو مدیر هم‌زمان، Conflict و نمایش State نهایی واحد.
- آزمون شکست جزئی fan-out، Restart و بازیابی retry ثبت‌شده.
- رفع فقط اشکال‌های اثبات‌شده در T020 تا T025 و افزودن Regression test متمرکز.

## خارج از دامنه

- انتشار فوری یا زمان‌بندی‌شده و هر تماس Telegram User API.
- Feature یا UX جدید، Command گزارش، Reject یا Role جدید.
- تماس زنده Bot API در Suite پیش‌فرض.
- Refactor گسترده خارج از اشکال‌های Milestone 3.

## فایل‌ها و ماژول‌های مورد انتظار

- `tests/integration/approvals/test_approval_flow.py`
- `tests/integration/approvals/test_approval_flow_concurrency.py`
- `tests/integration/approvals/test_approval_sync_restart.py`
- Fixtureهای بدون Secret زیر `tests/fixtures/telegram/bot/`
- فقط فایل‌های T020 تا T025 که برای رفع Regression مستند لازم‌اند.

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** Harness باید Config حداقلی معتبر و Secret جعلی تزریق کند و Config تولیدی را نخواند.
- **Migration:** تست از پایگاه تمیز و Schema قابل Upgrade اجرا شود؛ این Task Migration Feature جدید نمی‌سازد.
- **Compatibility:** Fixtureهای Adapter قرارداد SDK مصوب را تثبیت کنند، اما به جزئیات داخلی Handler قفل نشوند.
- **Concurrency:** تست‌ها barrier/event قطعی داشته باشند و به sleep یا ترتیب تصادفی وابسته نباشند.
- **Security:** هیچ Token/API credential واقعی در Fixture/Log نباشد و همه مسیرهای رد دسترسی عدم side effect را اثبات کنند.

## معیارهای پذیرش عینی

1. جریان مجاز کامل، State واحد را ذخیره و همه Referenceها را همگام می‌کند.
2. Callback جعلی/منقضی و Admin/Destination غیرمجاز هیچ تغییر DB یا ارسال بعدی ایجاد نمی‌کند.
3. دو Callback هم‌زمان State نامعتبر یا انتخاب دوگانه نمی‌سازند.
4. شکست یک Edit ثبت می‌شود و پس از Restart قابل retry است.
5. metadata هدر وارد payload محتوای قابل انتشار نمی‌شود.
6. هیچ قابلیت خارج از T020 تا T025 افزوده نشده است.

## تست‌های واحد الزامی

- `N/A` برای رفتار جدید: این Task Stabilization است و منطق جدید نمی‌سازد.
- برای هر باگ کشف‌شده، Regression unit test در نزدیک‌ترین ماژول T020 تا T025 الزامی است.

## تست‌های یکپارچه‌سازی الزامی

- `test_approval_flow.py`: مسیر موفق و همه مسیرهای Authorization/Callback ردشده.
- `test_approval_flow_concurrency.py`: رقابت دو مدیر و State/UI نهایی.
- `test_approval_sync_restart.py`: شکست fan-out، Restart و retry.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/integration/approvals/test_approval_flow.py
uv run pytest tests/integration/approvals/test_approval_flow_concurrency.py tests/integration/approvals/test_approval_sync_restart.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت سناریوهای تثبیت‌شده و مسیر واقعی Approval در `docs/CODE_MAP.md`.
- اصلاح `docs/ARCHITECTURE.md` فقط اگر تست، اختلاف طرح و پیاده‌سازی را آشکار کند.
- ثبت Decision جدید فقط برای ابهام مهم؛ باگ معمولی Decision نیست.
- به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که همه Decisionهای Milestone ثبت، سناریوهای موفق/امنیتی/رقابتی/Restart با تست قطعی پاس، Regressionهای کشف‌شده در همان Scope رفع، همه Quality Gateها موفق و هیچ Feature انتشار وارد نشده باشد.

## نتایج نهایی

- Harness MongoDB/Bot مصنوعی دو بار، هر بار `3 passed` و صفر skip؛ concurrency و restart/retry پایدار ماند.
- Full coverage: `718 passed`، صفر skip، `90.20%`؛ Full repeat نیز `718 passed` بود.
- Ruff، format، mypy، UTF-8، secrets، build و distribution موفق‌اند.
