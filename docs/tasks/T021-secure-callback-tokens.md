# T021 — Callback امن و غیرقابل جعل

## وضعیت

`Completed`

## هدف

تعریف و پیاده‌سازی Callback Data کوتاه و قابل اعتبارسنجی که Actor، Action، Post و Destination را به یک رکورد server-side امن پیوند دهد و Callback جعلی، منقضی یا خارج از مجوز را پیش از اجرای عملیات رد کند.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش‌های `5.13 مدیران مجاز` و `5.14 دکمه‌های کانال‌های مقصد`.
- `docs/REQUIREMENTS.md`، بخش `14 امنیت`.
- `docs/ARCHITECTURE.md`، بخش `6` (`IdGenerator` و `AdminMessagingGateway`)، بخش `8`، بخش `9` (`callback_tokens`)، بخش‌های `14` و `15`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام‌های `2`، `6` و `13`.

## وابستگی‌ها

- `T004` و `T020` باید کامل شده باشند.

### پیش‌نیاز تصمیم

پیش از پیاده‌سازی باید طرح نهایی Callback (Token تصادفی opaque یا HMAC کوتاه)، TTL، امکان/عدم امکان استفاده مجدد، سیاست revoke، طول مجاز و رفتار Callback منقضی تصویب و در `docs/DECISIONS.md` ثبت شود. این Task نباید معنای دکمه «فوری» یا وجود مرحله Confirm را تعیین کند.

## دامنه کار

- تعریف DTO/Value Object مستقل برای Action مجاز و شناسه‌های Post/Destination/Actor.
- تعریف `CallbackTokenRepository` و Use Caseهای صدور و Resolve/Validate Token.
- ذخیره حداقل claims لازم در MongoDB و قرار دادن فقط شناسه کوتاه غیرقابل حدس در Callback Data.
- ساخت Unique Index و TTL Index لازم مطابق قرارداد Migration/Index پروژه.
- اعتبارسنجی Actor واقعی Update، فعال‌بودن Admin، Action مجاز، Post موجود، Destination مجاز و انقضا.
- نگاشت خطاهای قابل انتظار به پاسخ کوتاه و غیرحساس؛ بدون اجرای Toggle.

## خارج از دامنه

- ساخت Layout دکمه‌ها (`T023`) و تعیین UX دکمه فوری.
- تغییر حالت مقصد (`T024`) یا همگام‌سازی پیام‌ها (`T025`).
- انتشار، زمان‌بندی یا ذخیره Job آن‌ها.
- Session وب، JWT عمومی یا لینک قابل استفاده خارج از Bot.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/callbacks/models.py`
- `src/telegram_assist_bot/application/callbacks/issue_callback_token.py`
- `src/telegram_assist_bot/application/callbacks/resolve_callback_token.py`
- `src/telegram_assist_bot/application/ports/callback_tokens.py`
- `src/telegram_assist_bot/infrastructure/mongodb/callback_token_repository.py`
- ماژول Index/Migration ایجادشده در `T004`
- `tests/unit/application/callbacks/`
- `tests/integration/mongodb/test_callback_token_repository.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** TTL و سقف طول فقط در صورت الزام تصمیم مصوب Configurable شوند و بازه نامعتبر Fail-fast باشد.
- **Migration:** Index یکتا و TTL باید با Migration/Startup صریح ساخته و خطای ساخت Fail-fast شود؛ TTL حذف آنی را تضمین نمی‌کند، پس زمان در Application نیز بررسی شود.
- **Compatibility:** فرمت Callback پس از انتشار قرارداد بیرونی است؛ نسخه/Prefix لازم برای تغییر سازگار در نظر گرفته شود.
- **Concurrency:** Validate/consume یا reuse دقیقاً مطابق تصمیم و با عملیات اتمیک باشد؛ check-then-write غیراتمیک پذیرفته نیست.
- **Security:** Token باید از CSPRNG یا سازوکار مصوب تولید شود، Actor از Update معتبر گرفته شود، مقایسه امضا در صورت HMAC زمان‌ثابت باشد و Payload قابل حدس شامل مجوز تلقی نشود.

## معیارهای پذیرش عینی

1. Callback Data از محدودیت مصوب Bot API کوتاه‌تر است و داده حساس یا شناسه قابل سوءاستفاده ندارد.
2. Token معتبر فقط برای Actor/Action/Post/Destination ثبت‌شده Resolve می‌شود.
3. Token جعلی، تغییرکرده، منقضی، revoked یا متعلق به Admin دیگر رد می‌شود.
4. Post ناموجود، وضعیت نامعتبر یا Destination غیرمجاز پیش از Dispatch رد می‌شود.
5. Indexهای یکتا و TTL به‌صورت قابل تکرار ساخته می‌شوند.
6. هیچ تصمیم ضمنی درباره Toggle یا انتشار فوری وارد این Task نشده است.

## تست‌های واحد الزامی

- صدور Token با Generator قطعی Fake و Callback کوتاه.
- رد Token جعلی، منقضی، Actor اشتباه، Action ناشناخته و Destination غیرمجاز.
- آزمون سیاست reuse/revoke دقیقاً مطابق Decision ثبت‌شده.
- عدم نمایش Token کامل و claims حساس در Log/Exception.

## تست‌های یکپارچه‌سازی الزامی

- ساخت Indexهای MongoDB و صدور/Resolve واقعی Repository.
- رقابت هم‌زمان روی Token در صورت single-use بودن، یا حفظ رفتار مصوب در صورت reusable بودن.
- اثبات رد رکورد منقضی حتی پیش از اجرای دوره‌ای TTL Monitor.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/callbacks
uv run pytest tests/integration/mongodb/test_callback_token_repository.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت طرح، TTL، replay/revoke و نسخه Callback در `docs/DECISIONS.md`.
- ثبت Collection/Index و مسیر Callback در `docs/ARCHITECTURE.md` و `docs/CODE_MAP.md` در صورت تغییر نسبت به طرح.
- به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که تصمیم امنیتی ثبت شده، Callback امن با ذخیره‌سازی و Index واقعی پیاده‌سازی و تحت آزمون جعل/انقضا/هم‌زمانی قرار گرفته، تمام فرمان‌های راستی‌آزمایی موفق و هیچ Secret یا Callback واقعی در Fixture/Log وجود نداشته باشد.

## نتایج نهایی

- Callback security: `7 passed`، صفر skip؛ forgery، expiry، revoke، actor mismatch، revalidation و reuse پاس شدند.
- unique/TTL index و concurrency روی MongoDB واقعی پاس شدند؛ Full Suite `718 passed`.
