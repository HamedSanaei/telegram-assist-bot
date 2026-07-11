# T020 — Bot API و Authorization مدیر

## وضعیت

`Planned`

## هدف

ایجاد مرز حداقلی Telegram Bot API و Use Case احراز مجوز مدیر، به‌گونه‌ای که هر Command یا Callback پیش از ورود به منطق کاربردی بر اساس شناسه عددی، فعال‌بودن، نقش و مجوز مقصد بررسی شود؛ بدون ساخت پیام تأیید، Keyboard یا تغییر وضعیت پست.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `3`، زیر‌بخش‌های `Application` و `Presentation`.
- `docs/REQUIREMENTS.md`، بخش `5.13 مدیران مجاز`.
- `docs/REQUIREMENTS.md`، بخش `14 امنیت`.
- `docs/ARCHITECTURE.md`، بخش‌های `3`، `4` (`Admin`)، `5` (`AuthorizeAdminAction`)، `6` (`AdminMessagingGateway`)، `8`، `13`، `14` و `15`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام‌های `1`، `6` و `13`.

## وابستگی‌ها

- `T019` باید کامل شده باشد.

### پیش‌نیاز تصمیم

پیش از پیاده‌سازی، انتخاب SDK و نسخه آن، ماتریس حداقلی Role/Permission، رفتار پاسخ به کاربر غیرمجاز و محدوده Chatهای مدیریتی باید تصویب و در `docs/DECISIONS.md` ثبت شود. این Task مجاز نیست Role، Command یا UX رد دسترسی را از خود اختراع کند.

## دامنه کار

- تعریف مدل مستقل `Admin`/Permission در Domain یا قرارداد موجود آن، بدون نوع‌های SDK.
- تعریف ورودی و نتیجه صریح برای `AuthorizeAdminAction` شامل Admin، Action، Post و Destination اختیاری.
- تعریف Port حداقلی Bot برای دریافت/پاسخ مدیریتی و Mapper تبدیل Updateهای SDK به DTO داخلی.
- ساخت Handlerهای نازک برای اعمال Authorization پیش از Dispatch؛ Handler نباید منطق کسب‌وکار داشته باشد.
- اعمال Timeout محدود و دسته‌بندی خطای Bot در Adapter.
- Redact کردن Token، Payload حساس و جزئیات Credential از Log و Exception.

## خارج از دامنه

- ایجاد یا اعتبارسنجی Callback Token (`T021`).
- ساخت و ارسال پیام تأیید (`T022`) یا Keyboard (`T023`).
- Toggle مقصد، همگام‌سازی مدیران، انتشار و زمان‌بندی.
- تعریف Commandهای گزارش، Reject یا Roleهای فراتر از تصمیم مصوب.
- تماس زنده با Bot تولیدی یا استفاده از Token واقعی.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/admin.py`
- `src/telegram_assist_bot/application/admin/authorize_admin_action.py`
- `src/telegram_assist_bot/application/ports/admin_messaging.py`
- `src/telegram_assist_bot/infrastructure/telegram/bot_client.py`
- `src/telegram_assist_bot/presentation/bot/handlers.py`
- `tests/unit/application/admin/test_authorize_admin_action.py`
- `tests/integration/telegram/test_bot_authorization_boundary.py`
- فایل Composition Root و Configuration فقط در حد Wiring قراردادهای موجود.

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** فقط Admin ID، active، role/permissions و Secret reference معتبر از `T002` خوانده شود؛ Token در Config commitشدنی قرار نگیرد.
- **Migration:** اگر مدل Admin از Config به MongoDB منتقل نمی‌شود، Migration داده‌ای لازم نیست؛ هر تغییر Schema تنظیمات باید سازگار و در نمونه Config مستند شود.
- **Compatibility:** نوع Update/Exception اختصاصی SDK نباید از Infrastructure خارج شود؛ تعویض SDK نباید Use Case را تغییر دهد.
- **Concurrency:** Authorization باید برای هر Update دوباره روی وضعیت جاری انجام شود و نتیجه قبلی Cache امنیتی تلقی نشود.
- **Security:** پیش‌فرض رد دسترسی باشد؛ شناسه ارسال‌شده داخل Payload جای شناسه Actor معتبر Bot API را نگیرد و Secretها Log نشوند.

## معیارهای پذیرش عینی

1. مدیر فعال با Permission لازم مجاز و مدیر ناشناخته، غیرفعال یا فاقد Permission رد می‌شود.
2. Permission مقصد نیز در Action مقصددار بررسی می‌شود.
3. هیچ Handler محافظت‌شده‌ای پیش از Authorization به Use Case بعدی Dispatch نمی‌کند.
4. DTOهای Application هیچ Import از SDK تلگرام ندارند.
5. خطای خارجی Timeout محدود دارد و پیام مدیر فاقد جزئیات حساس است.
6. تصمیم‌های پیش‌نیاز این Task پیش از کدنویسی ثبت شده‌اند.

## تست‌های واحد الزامی

- پذیرش مدیر فعال و مجاز.
- رد Admin ناشناخته، غیرفعال، Role نامعتبر و مقصد غیرمجاز.
- اطمینان از عدم Dispatch پس از رد دسترسی.
- آزمون Redaction داده حساس در خطاهای نگاشت‌شده.

## تست‌های یکپارچه‌سازی الزامی

- نگاشت Update ثبت‌شده و بدون Secret از Adapter به DTO و عبور آن از مرز Authorization با Transport جعلی.
- Timeout/خطای Bot جعلی و اثبات عدم نشت Token.
- تست زنده Bot API در Suite پیش‌فرض ممنوع است.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/admin/test_authorize_admin_action.py
uv run pytest tests/integration/telegram/test_bot_authorization_boundary.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت تصمیم SDK، Role/Permission و رفتار دسترسی در `docs/DECISIONS.md`.
- افزودن مرز Bot، Handler و Authorization به `docs/CODE_MAP.md`.
- همگام‌سازی `docs/ARCHITECTURE.md` فقط اگر تصمیم مصوب با طرح فعلی تفاوت دارد.
- به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و همین فایل با نتیجه واقعی اجرا.

## تعریف Done

Task زمانی Done است که تصمیم‌های پیش‌نیاز ثبت، Authorization و مرز Bot در محدوده فوق پیاده‌سازی، تست‌های واحد و یکپارچه‌سازی و همه Quality Gateها موفق، Secretها و متن UTF-8 بازبینی، و مستندات حافظه پروژه همگام شده باشند.
