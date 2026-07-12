# T028 — انتشار Media/Album و Premium Emoji

## وضعیت

`Completed`

## هدف

گسترش مسیر انتشار فوری T027 برای انواع Media پشتیبانی‌شده و Album مرتب، با Caption/Entity و Custom Emoji صحیح از Telegram User API.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش‌های `5.5 مدیریت مدیا`، `5.6 مدیریت آلبوم‌ها`، `5.7 حفظ Premium Emoji` و `5.17 انتشار فوری`.
- `docs/ARCHITECTURE.md`، بخش‌های `4` (`Media` و `Publication`)، `6` (`TelegramPublisherGateway` و `MediaStorage`)، `7`، `10` و `14`.

## وابستگی‌ها

- `T015` و `T027` باید کامل شده باشند.

## دامنه کار

- توسعه Publisher request برای عکس، ویدیو، فایل، صوت، Voice، Animation، Sticker و Video Note مطابق قابلیت SDK مصوب.
- خواندن امن Media آماده از `MediaStorage` و اعتبارسنجی وضعیت/انقضا پیش از ارسال.
- انتشار Media Group به‌عنوان یک Album واحد با ترتیب ذخیره‌شده.
- قرار دادن Caption و Entityها در عضو/محل درست مطابق قرارداد Telegram.
- ثبت Message ID یا IDهای نتیجه Album در Publication پایه.
- Cleanup resource/stream در موفقیت و خطا با Timeout محدود.

## خارج از دامنه

- دانلود، Hash یا Cleanup فایل که در T013/T014 انجام شده است.
- تجمیع Album که در T015 انجام شده است.
- Retry و Idempotency رقابتی (`T029`).
- Scheduler و تبدیل مجدد متن/Entity.

## فایل‌ها و ماژول‌های مورد انتظار

- توسعه `src/telegram_assist_bot/application/publication/` برای requestهای Media.
- توسعه `src/telegram_assist_bot/application/ports/telegram_publisher.py`
- توسعه `src/telegram_assist_bot/infrastructure/telegram/user_publisher.py`
- Mapperهای Media SDK در `src/telegram_assist_bot/infrastructure/telegram/mappers.py`
- `tests/unit/application/publication/test_publish_media_immediately.py`
- `tests/integration/telegram/test_media_album_publication_adapter.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** محدودیت حجم/نوع و Timeout فقط از Config/SDK مصوب مصرف شود؛ Task سقف Production جدید اختراع نمی‌کند.
- **Migration:** شکل ذخیره IDهای چندپیامی Publication باید با سند تک‌پیامی T027 سازگار و قابل Upgrade باشد.
- **Compatibility:** تفاوت قابلیت Caption/Entity میان انواع Media در Adapter محصور و نسخه SDK pinشده رعایت شود.
- **Concurrency:** محدودیت idempotency T027 تا T029 باقی است؛ Stream مشترک میان Attemptها بدون reset امن استفاده نشود.
- **Security:** مسیر Media از storage key معتبر resolve شود، Path traversal رد و Session/مسیر خصوصی Log نشود.

## معیارهای پذیرش عینی

1. هر نوع Media الزامی با Mapper و Fixture نماینده پوشش دارد.
2. اعضای Album یک‌جا و دقیقاً با ترتیب Domain ارسال می‌شوند.
3. Caption، Entity و Custom Emoji بدون خرابی Offset منتقل می‌شوند.
4. Media گمشده، منقضی یا خارج از Storage پیش از ارسال با خطای صریح رد می‌شود.
5. نتیجه همه Message IDهای لازم را ثبت و resourceها را آزاد می‌کند.
6. Bot API برای انتشار مقصد استفاده نمی‌شود.

## تست‌های واحد الزامی

- ساخت request هر Media type و Album مرتب.
- جای‌گذاری Caption/Entity و حفظ فارسی/Custom Emoji.
- رد Media missing/expired/not-ready و Album خالی/نامرتب.
- آزادسازی stream پس از موفقیت و exception.

## تست‌های یکپارچه‌سازی الزامی

- Adapter با Fixtureهای sanitized SDK برای Media تک و Album چندعضوی.
- اتصال MediaStorage آزمایشی به Publisher و آزمون ترتیب/Message IDها.
- تست زنده Telegram N/A در Suite پیش‌فرض است؛ نیازمند Sandbox و Config opt-in جداگانه است.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/publication/test_publish_media_immediately.py
uv run pytest tests/integration/telegram/test_media_album_publication_adapter.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

## نتیجه راستی‌آزمایی

- `11` تست واحد Media/Album و `2` تست Adapter موفق، `0` skip.
- مسیر خصوصی، ترتیب Album، Caption فارسی و همه Message IDها تأیید شدند.
- full suite نهایی `806 passed` با Coverage برابر `90.12%` است.

- افزودن mapper/typeهای واقعی Media و جریان Album به `docs/CODE_MAP.md`.
- همگام‌سازی `docs/ARCHITECTURE.md` با قرارداد Publisher و شکل نتیجه چندپیامی.
- مستندسازی هر تغییر Schema سازگار و به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و همین فایل.

## تعریف Done

Task زمانی Done است که Media و Album با ترتیب، Caption و Entity صحیح از User API Adapter عبور کنند، Path/resource safety و Fixtureهای همه نوع پوشش داده شوند، Quality Gateها موفق و Retry/idempotency خارج از Scope باقی بماند.
