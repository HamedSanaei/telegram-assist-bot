# T007 — ورود و ذخیرهٔ Session تلگرام

## وضعیت

Active

## هدف

پیاده‌سازی مرز `TelegramSourceGateway` و Adapter حداقلی Telegram User API برای ورود تعاملی نخست، ذخیرهٔ امن Session و استفادهٔ مجدد غیرتعاملی از آن، بدون خزش، انتشار یا بررسی دسترسی کانال.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.1 ورود به حساب تلگرام`.
- `docs/REQUIREMENTS.md`، بخش `14. امنیت`، منع Commit فایل Session.
- `docs/REQUIREMENTS.md`، بخش `16. معیارهای پذیرش فاز اول`، بندهای ۱ و ۲.
- `docs/ARCHITECTURE.md`، بخش `6. Portها و Interfaceها`، `TelegramSourceGateway`.
- `docs/ARCHITECTURE.md`، بخش `7. مسئولیت Telegram User API`، فقط authentication/session.
- `docs/ARCHITECTURE.md`، بخش `17. ابهام‌های باز`، بند ۱ دربارهٔ SDK.
- `docs/DECISIONS.md`، `ADR-004` و `ADR-008`.

## وابستگی‌ها

- T006 — Startup و Stabilization پایه؛ باید Completed باشد.

## محدوده

- انتخاب یک SDK async سازگار با نسخهٔ Python تثبیت‌شده در T001، ثبت دلیل و pin کردن dependency.
- تعریف قرارداد application-owned برای وضعیت Session و challengeهای ورود، بدون عبور objectهای SDK.
- ورود مرحله‌ای با phone، code و در صورت نیاز 2FA password از ورودی تعاملی تزریق‌پذیر؛ credential در Log ذخیره نشود.
- ایجاد و نگهداری Session در مسیر Runtime خارج از Git با permission مناسب سیستم‌عامل تا حد قابل‌پشتیبانی.
- استفادهٔ مجدد از Session معتبر در اجرای بعد بدون درخواست code.
- تفکیک Session نامعتبر/منقضی از خطای موقت شبکه؛ خطای شبکه Session موجود را حذف یا بازنویسی نکند.
- افزودن wiring opt-in به Startup؛ ورود تعاملی فقط با command/حالت صریح انجام شود و startup عادی روی prompt پنهان block نشود.

## خارج از محدوده

- بررسی Premium، resolve کانال و مجوز انتشار؛ T008.
- History، Listener، Media، Bot API و انتشار.
- پشتیبانی چند حساب یا migration بین SDKها.
- ذخیرهٔ Session در Git، MongoDB یا Configuration نمونه.
- دورزدن 2FA، CAPTCHA یا سیاست‌های Telegram.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/ports/telegram_source_gateway.py`
- `src/telegram_assist_bot/application/authenticate_telegram_session.py`
- `src/telegram_assist_bot/infrastructure/telegram/user/session_adapter.py`
- `src/telegram_assist_bot/infrastructure/telegram/user/dto.py`
- تغییر محدود Composition Root/CLI برای command ورود.
- `tests/unit/application/test_authenticate_telegram_session.py`
- `tests/unit/infrastructure/telegram/user/test_session_adapter.py`
- `tests/contract/telegram/test_session_contract.py`
- اسناد پروژه طبق بخش «به‌روزرسانی‌های مستندات».

## نکات پیاده‌سازی

- پیش از implementation، SDK و نسخهٔ آن باید بر اساس Python T001 و نیاز Custom Emoji/Album ارزیابی و تصمیم در `docs/DECISIONS.md` ثبت شود.
- prompt/reader یک Port کوچک باشد تا Unit Test تعاملی واقعی نخواهد؛ code/password هرگز در exception ذخیره نشود.
- تمام تماس‌های SDK timeout محدود داشته و FloodWait فقط طبق زمان اعلام‌شده و سقف سیاست مدیریت شود.
- write Session باید در اختیار SDK و atomic تا حد ممکن باشد؛ failure نباید Session سالم قبلی را truncate کند.
- **ریسک Configuration:** API ID/hash، phone reference و Session path از T002؛ مقدار مفقود Fail-fast و redacted است.
- **ریسک Migration:** فرمت Session وابسته به SDK است؛ تعویض SDK خارج از Task و نیازمند migration/reauthentication مستند است.
- **ریسک Compatibility:** نسخهٔ SDK/Python باید pin و contract DTO مستقل باشد.
- **ریسک Concurrency:** دو فرآیند نباید هم‌زمان Session واحد را mutate کنند؛ lock فایل/process یا رد روشن login concurrent لازم است.
- **ریسک Security:** Session و 2FA Secret محسوب می‌شوند؛ permission، `.gitignore`، عدم Log و cleanup fixture الزامی است.

## معیارهای پذیرش عینی

1. بدون Session، Use Case مراحل لازم ورود را از طریق Port طی و Session را در مسیر مجاز ایجاد می‌کند.
2. با Session معتبر، اجرای بعد هیچ code/password درخواست نمی‌کند.
3. خطای شبکه Session سالم را حذف، truncate یا نامعتبر نمی‌کند.
4. Session revoked/expired با error category مشخص و راهنمای re-authentication گزارش می‌شود.
5. login هم‌زمان روی یک مسیر یا serialize می‌شود یا conflict روشن می‌دهد.
6. Log/exception/test artifact فاقد phone کامل، code، password، API hash و Session content است.
7. objectهای SDK از Infrastructure خارج نمی‌شوند.

## Unit Testهای الزامی

- flow بدون 2FA و flow دارای 2FA با fake gateway/input.
- reuse Session و اثبات عدم فراخوانی prompt.
- تفکیک invalid session، invalid code، timeout و transient network.
- عدم حذف Session در network failure و عدم overwrite در failure میانه.
- redaction تمام Secret sentinelها و conflict ورود هم‌زمان.

## Integration Testهای الزامی

- Contract test Adapter با fake/recorded SDK boundary و filesystem موقت برای create/reuse Session، بدون Credential واقعی.
- اجرای دو lifecycle متوالی و اثبات reuse فایل Session مصنوعی.

تست زنده Telegram بخشی از suite پیش‌فرض نیست؛ فقط به‌صورت opt-in در sandbox مورد تأیید قابل اجراست و نبود آن مانع Task نیست، زیرا contract test deterministic الزامی است.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_authenticate_telegram_session.py tests/unit/infrastructure/telegram/user/test_session_adapter.py
uv run pytest tests/contract/telegram/test_session_contract.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

بازبینی `.gitignore`، permission فایل آزمایشی، `git status --ignored` و `git diff --check` الزامی است.

## به‌روزرسانی‌های مستندات

- ثبت Status و نتایج واقعی در همین فایل؛ به‌روزرسانی T007 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن Use Case، Port، Adapter، command و testها به `docs/CODE_MAP.md`.
- همگام‌سازی مسئولیت Session در `docs/ARCHITECTURE.md` و ثبت انتخاب SDK در `docs/DECISIONS.md`.
- به‌روزرسانی راهنمای اجرای login و Configuration نمونه بدون افزودن Secret.

## تعریف انجام‌شدن

- flow create/reuse/failure با Testهای deterministic پاس شده و تست زنده شرط نشده است.
- Quality Gateهای T001، UTF-8 و Secret safety پاس شده‌اند.
- Session واقعی/fixture حساس در Git نیست و خطای شبکه آن را خراب نمی‌کند.
- Scope به authentication محدود مانده و Premium/channel/crawl پیاده نشده است.
