# T047 — پذیرش End-to-end فاز اول

## وضعیت

Completed

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
- رفع فقط اشکال کوچک cross-layer که مانع پذیرش فاز اول است.

## خارج از دامنه

- تماس زنده Telegram/AI در Suite پیش‌فرض یا استفاده از credential واقعی.
- تبلیغات فاز دوم و Featureهای فازهای پیشنهادی.
- بازطراحی معماری یا refactor گسترده.
- پرکردن ابهام‌های محصولی با Default ضمنی.

## فایل‌ها و ماژول‌های ایجادشده

- `tests/fixtures/telegram/phase_one_post_fixture.json`
- `tests/fixtures/telegram/phase_one_album_fixture.json`
- `tests/fixtures/ai/phase_one_ai_responses.json`
- `tests/e2e/test_phase_one_text_flow.py`
- `tests/e2e/test_phase_one_media_schedule_flow.py`
- `tests/e2e/test_phase_one_restart_idempotency.py`

## ماتریس معیارهای پذیرش بخش ۱۶ (Requirements Acceptance Matrix)

| # | عنوان معیار پذیرش | تست آزموده / ارجاع | وضعیت |
|---|---|---|---|
| 1 | دریافت پست متنی از کانال‌های منبع | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 2 | ادغام و دریافت Media Groupها به‌صورت یک پست منطقی | `tests/e2e/test_phase_one_media_schedule_flow.py` | ✅ پاس شد |
| 3 | ذخیره‌سازی پست‌ها و متادیتا در MongoDB | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 4 | دریافت یکتا (Idempotent Ingestion) و جلوگیری از duplicate | `tests/e2e/test_phase_one_restart_idempotency.py` | ✅ پاس شد |
| 5 | حفظ کامل متن فارسی، ZWNJ، Line Breaks، Emoji، Custom Emoji و Offsets | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 6 | آماده‌سازی محتوای مقصد (حذف لینک/منبع و محاسبه مجدد Offset) | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 7 | تشخیص تبلیغات با AI و Retry / Fallback پایداری | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 8 | تشخیص همپوشانی و تکرار معنایی با AI | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 9 | دسته‌بندی موضوعی با AI | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 10 | امتیازدهی تاخیری AI بدون ویرایش پیام منتشرشده در مقصد | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 11 | ارسال پیشنهاد بررسی به مدیران مجاز | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 12 | پردازش دقیق Callbackهای مدیریت و احراز هویت | `tests/e2e/test_phase_one_restart_idempotency.py` | ✅ پاس شد |
| 13 | اجرای انتشار فوری (Immediate Publication) | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 14 | رزرو اسلات زمان‌بندی و Claim اتمیک صف انتشار | `tests/e2e/test_phase_one_media_schedule_flow.py` | ✅ پاس شد |
| 15 | اجرای انتشار زمان‌بندی‌شده پس از سررسید | `tests/e2e/test_phase_one_media_schedule_flow.py` | ✅ پاس شد |
| 16 | بازیابی پایداری و Restart پروسه بدون از دست رفتن Jobهای زمان‌بندی | `tests/e2e/test_phase_one_media_schedule_flow.py` | ✅ پاس شد |
| 17 | بازیابی Lease انقضایافته AI Job و Restart Worker | `tests/e2e/test_phase_one_restart_idempotency.py` | ✅ پاس شد |
| 18 | کنترل هم‌زمانی خوش‌بینانه (Optimistic Concurrency) در ثبت AI | `tests/e2e/test_phase_one_restart_idempotency.py` | ✅ پاس شد |
| 19 | انتشار یکتا در کانال مقصد (Preventing duplicate publication) | `tests/e2e/test_phase_one_media_schedule_flow.py` | ✅ پاس شد |
| 20 | جدایی کامل Header مدیریتی از محتوای نهایی کانال مقصد | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 21 | احراز هویت امنیتی مدیران و رد توکن‌های انقضایافته/جعلی | `tests/e2e/test_phase_one_restart_idempotency.py` | ✅ پاس شد |
| 22 | سانسور و Redaction اطلاعات حساس در Logها | `tests/e2e/test_phase_one_restart_idempotency.py` | ✅ پاس شد |
| 23 | مقداردهی اولیه و ایمن Indexهای MongoDB | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 24 | عدم وابستگی به اینترنت یا Credential واقعی در Test Suite پیش‌فرض | `tests/e2e/test_phase_one_text_flow.py` | ✅ پاس شد |
| 25 | قبولی ۱۰۰٪ تمامی تست‌های Unit، Integration، Contract و E2E | اجرای کامل (1243 passed) | ✅ پاس شد |

## فرمان‌های راستی‌آزمایی اجرا شده و نتایج

1. **تست‌های E2E فاز اول:**
   `uv run --python 3.12 pytest tests/e2e/test_phase_one_text_flow.py tests/e2e/test_phase_one_media_schedule_flow.py tests/e2e/test_phase_one_restart_idempotency.py` -> **3 passed in 3.95s**.
2. **کل Suite تست‌های غیرزنده (Unit, Integration, Contract, E2E):**
   `uv run --python 3.12 pytest -m "not live"` -> **1243 passed in 93.23s**.
3. **اعتبارسنجی قفل وابستگی‌ها:**
   `uv lock --check` -> **OK**.
4. **بررسی کیفیت کد و فرمت:**
   `uv run ruff check src tests` -> **All checks passed!**
   `uv run ruff format --check .` -> **OK**.
5. **بررسی تایپ استاتیک:**
   `uv run mypy src tests` -> **Success: no issues found in 324 source files**.
6. **بررسی سلامت متون فارسی:**
   `uv run python scripts/check_text_integrity.py --all` -> **Text integrity passed for 444 checked file(s)**.
7. **بررسی Git Diff:**
   `git diff --check` -> **OK**.

## تعریف Done

- تمام معیارهای بخش `16` با تست اجراشده اثبات شده‌اند و هیچ blocker/failing test باقی نیست.
- E2E متن، Media، Restart، concurrency، security و Persian/Entity safety پاس‌اند.
- Quality Gate کامل پاس و هیچ Feature فاز دوم یا آینده وارد Scope نشده است.
