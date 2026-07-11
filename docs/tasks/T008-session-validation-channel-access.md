# T008 — اعتبار Session، Premium و دسترسی کانال

## وضعیت

Planned

## هدف

اعتبارسنجی غیرتعاملی Session موجود، Premium بودن حساب و resolve/permission کانال‌های پیکربندی‌شده پیش از شروع Workerها، بدون دریافت History یا انتشار محتوا.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.1 ورود به حساب تلگرام`، اعتبار Session و حساب Premium.
- `docs/REQUIREMENTS.md`، بخش `5.2 کانال‌های مبدا`، کانال‌های فعال و مقصدهای مجاز.
- `docs/REQUIREMENTS.md`، بخش `13. مدیریت خطا و Retry`، Flood Wait/permission/network.
- `docs/ARCHITECTURE.md`، بخش `7. مسئولیت Telegram User API`، validation و resolve کانال.
- `docs/ARCHITECTURE.md`، بخش `13. Configuration و Secret`.
- `docs/DECISIONS.md`، `ADR-004`.

## وابستگی‌ها

- T007 — ورود و ذخیره Session؛ باید Completed باشد.

## محدوده

- Use Case غیرتعاملی `ValidateTelegramSession` برای authorized بودن Session و Premium بودن حساب.
- resolve همهٔ Sourceهای فعال و Destinationهای referenced به شناسهٔ عددی پایدار.
- بررسی حداقل دسترسی خواندن Source و توانایی لازم حساب برای انتشار در Destination، بدون ارسال پیام آزمایشی.
- تولید گزارش typed از کانال‌های معتبر و خطاهای تجمیع‌شده با مسیر Configuration.
- تفکیک permanent permission/not-found/invalid-session از timeout/network/flood-wait.
- Fail-fast پیش از Worker startup اگر Session، Premium یا کانال ضروری نامعتبر باشد.
- cache فقط در طول همان Startup؛ نتیجه پس از Restart دوباره اعتبارسنجی شود.

## خارج از محدوده

- login تعاملی یا repair Session؛ T007.
- Crawl، Listener، دانلود Media یا publication واقعی.
- Bot API و مجوز مدیران.
- پیوستن خودکار به کانال، درخواست دسترسی یا تغییر permission.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/validate_telegram_session.py`
- توسعهٔ محدود `src/telegram_assist_bot/application/ports/telegram_source_gateway.py`
- `src/telegram_assist_bot/infrastructure/telegram/user/channel_access_adapter.py`
- DTO/error mapping مربوط.
- `tests/unit/application/test_validate_telegram_session.py`
- `tests/unit/infrastructure/telegram/user/test_channel_access_adapter.py`
- `tests/contract/telegram/test_channel_access_contract.py`

## نکات پیاده‌سازی

- شناسه/Username پیکربندی‌شده را با نتیجهٔ resolve مقایسه و mismatch را خطای Configuration بدانید؛ Username mutable است، numeric ID identity است.
- permission مقصد از metadata SDK map شود و از ارسال آزمایشی جلوگیری شود.
- **ریسک Configuration:** source غیرفعال resolve نشود مگر مقصد دیگری به آن ارجاع دهد؛ referenceهای نامعتبر مسیر دقیق داشته باشند.
- **ریسک Migration:** ذخیرهٔ canonical numeric ID در Config در آینده نیازمند فرآیند صریح است؛ این Task فایل local را خودکار rewrite نمی‌کند.
- **ریسک Compatibility:** fieldهای permission SDK به DTO پایدار map و با fixture نسخه‌شده تست شوند.
- **ریسک Concurrency:** validation Startup یک‌بار و پیش از Workerها انجام شود؛ cache سراسری stale ساخته نشود.
- **ریسک Security:** phone/self details و فهرست خصوصی dialogها Log نشوند؛ فقط شناسهٔ کانال ضروری و error category ثبت شود.

## معیارهای پذیرش عینی

1. Session معتبر، authorized و Premium همراه همهٔ کانال‌های قابل‌دسترسی گزارش موفق می‌دهد.
2. Session invalid، حساب non-Premium و permission ناکافی سه failure مجزا و قابل‌فهم‌اند.
3. همهٔ کانال‌های نامعتبر در یک گزارش با مسیر Configuration دیده می‌شوند.
4. خطای موقت شبکه به permanent access failure تبدیل نمی‌شود.
5. هیچ History یا پیام publish/send در validation فراخوانی نمی‌شود.
6. Startup پیش از Worker در validation failure متوقف و Logها redacted می‌شوند.

## Unit Testهای الزامی

- authorized/Premium happy path و هر failure مستقل.
- resolve با Username و numeric ID، mismatch و source غیرفعال.
- permission source/destination و تجمیع چند خطا.
- طبقه‌بندی timeout، FloodWait و permission.
- اثبات عدم فراخوانی history/send و redaction اطلاعات حساب.

## Integration Testهای الزامی

- Contract test با fixtureهای SDK برای self info، entity resolve و permission mapping.
- Startup آزمایشی با fake Telegram gateway برای اثبات validation پیش از Worker.

تست زنده Telegram اختیاری و خارج از suite پیش‌فرض است؛ fixture/contract test بدون Secret الزامی است.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_validate_telegram_session.py tests/unit/infrastructure/telegram/user/test_channel_access_adapter.py
uv run pytest tests/contract/telegram/test_channel_access_contract.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

بازبینی fixtureهای Telegram برای نبود شناسه/credential واقعی و `git diff --check` الزامی است.

## به‌روزرسانی‌های مستندات

- ثبت Status/verification و به‌روزرسانی T008 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن Use Case، mapping و validation startup به `docs/CODE_MAP.md` و `docs/ARCHITECTURE.md`.
- ثبت تغییر پایدار در سیاست Premium/permission در `docs/DECISIONS.md` در صورت نیاز.
- به‌روزرسانی راهنمای Configuration کانال‌ها بدون دادهٔ واقعی.

## تعریف انجام‌شدن

- همهٔ حالت‌های اعتبار/دسترسی با test deterministic پاس شده‌اند.
- هیچ live send/history یا Secret وارد suite/Git نشده است.
- Quality Gateها و بررسی UTF-8 پاس شده‌اند.
- Scope فقط validation است و Task بعدی می‌تواند روی گزارش canonical کانال تکیه کند.
