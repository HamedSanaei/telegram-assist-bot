# T027 — انتشار فوری متن با User API

## وضعیت

`Planned`

## هدف

پیاده‌سازی مسیر یک‌بارۀ انتشار فوری یک Post متنی برای یک Destination با Telegram User API، متن مقصدی و Entityهای آماده‌شده، و ثبت نتیجه؛ بدون Media و بدون طراحی کامل Retry/Idempotency توزیع‌شده.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `5.17 انتشار فوری`.
- `docs/REQUIREMENTS.md`، بخش‌های `5.7 حفظ Premium Emoji` و `5.10 پاک‌سازی و بازنویسی متن` در محدوده Post متنی.
- `docs/ARCHITECTURE.md`، بخش `5` (`PublishPostImmediately`)، بخش `6` (`TelegramPublisherGateway` و `PublicationRepository`)، بخش `7` و بخش `14`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام `2` که باید پیش‌تر حل شده باشد.

## وابستگی‌ها

- `T008`، `T017` و `T026` باید کامل شده باشند.
- قرارداد مصوب دکمه فوری/Confirm از Milestone 3 باید رعایت شود.

## دامنه کار

- تعریف Command/Result انتشار فوری متن برای یک `Post × Destination`.
- بررسی Authorization، State قابل انتشار، انتخاب فوری و دسترسی حساب Premium به Destination.
- تولید/دریافت نسخه متن مقصدی از T017 و ارسال متن با Entityهای صحیح از User API.
- ثبت Attempt، Message ID مقصد، published_at و نتیجه موفق/ناموفق در مدل Publication پایه.
- نگاشت Timeout و خطاهای User API به خطاهای داخلی و پیام مدیریتی کوتاه.
- Guard ترتیبی روی Publication موفق موجود؛ hardening رقابتی و Retry در `T029`.

## خارج از دامنه

- Media/Album (`T028`).
- Unique idempotency و Retry/Flood-wait کامل در شرایط رقابتی (`T029`).
- Queue/Scheduler و انتشار چند مقصد در یک Transaction.
- ویرایش محتوای منتشرشده یا استفاده از Bot API برای مقصد.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/publication.py`
- `src/telegram_assist_bot/application/publication/publish_text_immediately.py`
- `src/telegram_assist_bot/application/ports/telegram_publisher.py`
- `src/telegram_assist_bot/application/ports/publication_repository.py`
- `src/telegram_assist_bot/infrastructure/telegram/user_publisher.py`
- MongoDB adapter پایه Publication.
- `tests/unit/application/publication/test_publish_text_immediately.py`
- `tests/integration/telegram/test_text_publication_adapter.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** Destination access و Timeout از Config معتبر خوانده شوند؛ مقدار hardcoded و Credential در کد ممنوع است.
- **Migration:** Collection/fields پایه Publication باید forward-compatible با unique key و Retry fields آتی T029 باشد.
- **Compatibility:** Entity/offset DTO داخلی در Adapter به نوع SDK Map شود؛ نوع SDK وارد Domain/Application نشود.
- **Concurrency:** این Task فقط guard پایه دارد؛ محدودیت تضمین هم‌زمانی صریح مستند و در T029 رفع شود.
- **Security:** فقط User API حساب مصوب منتشر کند؛ هدر مدیران، Session، Token و خطای حساس هرگز به مقصد/Log نروند.

## معیارهای پذیرش عینی

1. Post متنی مجاز با نسخه مخصوص Destination از User API منتشر می‌شود.
2. Entityها، Custom Emoji و متن فارسی بدون تغییر ناخواسته به Adapter می‌رسند.
3. هدر و metadata مدیران در پیام مقصد وجود ندارد.
4. Message ID و زمان موفقیت یا خطای دسته‌بندی‌شده ثبت می‌شود.
5. State/Permission/دسترسی نامعتبر پیش از تماس خارجی رد می‌شود.
6. Bot API، Media و Scheduler در این Task استفاده نمی‌شوند.

## تست‌های واحد الزامی

- مسیر موفق با Publisher Fake و ثبت نتیجه.
- رد State، Permission، Destination access و Publication موفق قبلی.
- عدم وجود metadata هدر در request و حفظ فارسی/Entity.
- نگاشت خطای موقت، دائمی و Timeout بدون ادعای موفقیت.

## تست‌های یکپارچه‌سازی الزامی

- نگاشت request/response با Adapter SDK و Fixture ثبت‌شده بدون Session واقعی.
- تست متن فارسی، نیم‌فاصله و Custom Emoji Entity در مرز Adapter.
- تست زنده Telegram فقط opt-in و خارج از Suite پیش‌فرض است.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/publication/test_publish_text_immediately.py
uv run pytest tests/integration/telegram/test_text_publication_adapter.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- افزودن Use Case، Port، Adapter و مدل Publication به `docs/CODE_MAP.md`.
- همگام‌سازی بخش User API/Publication در `docs/ARCHITECTURE.md` با پیاده‌سازی واقعی.
- مستندسازی محدودیت موقت concurrency تا T029 در `docs/STATUS.md` بدون ادعای تکمیل آن.
- به‌روزرسانی `docs/ROADMAP.md` و نتیجه همین فایل.

## تعریف Done

Task زمانی Done است که انتشار فوری متن در محدوده تعریف‌شده با Fake/Contract test اثبات، metadata مدیران حذف، Entity و UTF-8 بازبینی، همه Quality Gateها موفق و محدودیت idempotency آتی صریح باقی مانده باشد.
