# T052 — سیاست تداخل تبلیغ و صف عادی

## وضعیت

`Planned`

## هدف

اعمال اتمیک سیاست Configured برای تداخل Advertisement Slot با صف Publication عادی و رعایت حداقل فاصله هر Destination، بدون تغییر محتوای پست یا گزارش مدیریتی.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `6.4 تداخل با صف محتوای عادی`.
- `docs/ARCHITECTURE.md`، بخش `5` (`ResolvePublicationCollision`)، بخش `6` (`ScheduleRepository` و `AdvertisementRepository`)، بخش `11` و بخش `14` (`Idempotency` و `Concurrency`).
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام `11`.

## وابستگی‌ها

- `T033` و `T051` باید کامل شده باشند.

### پیش‌نیاز تصمیم

پیش از پیاده‌سازی باید default قطعی Collision، معنای دقیق هر mode پشتیبانی‌شده، حداقل فاصله، اولویت چند تبلیغ هم‌زمان، سقف defer، رفتار پس از missed window و اینکه کدام Jobهای عادی قابل جابه‌جایی‌اند تصویب و در `docs/DECISIONS.md` ثبت شود. گزینه‌های پیشنهادی Requirement خودبه‌خود policy اجرایی نیستند.

## دامنه کار

- تعریف مدل/نتیجه `ResolvePublicationCollision` مستقل از MongoDB query و Telegram.
- تشخیص conflict پیرامون due_at برای یک Destination بر اساس min-gap Configured.
- اجرای فقط policyهای مصوب از میان exact advertisement، shift normal، defer advertisement و next-free/min-gap.
- update اتمیک Advertisement Slot و/یا Scheduled Publicationهای مجاز با version/audit.
- جلوگیری از starvation/loop بر اساس سقف مصوب و نتیجه manual/final صریح.
- تحویل فقط Slot resolved/eligible به Worker T051؛ بدون انتشار مستقیم در Resolver.

## خارج از دامنه

- اختراع default یا semantics حل‌نشده.
- تغییر Content، Refresh Cache، تولید Slot یا گزارش مدیران.
- جابه‌جایی Publication موفق/درحال‌ارسال برخلاف Decision.
- Optimization عمومی Calendar یا priority queue تازه.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/publication_collision.py`
- `src/telegram_assist_bot/application/advertisements/resolve_publication_collision.py`
- توسعه Portهای `schedule_repository.py` و `advertisement_repository.py`
- توسعه Adapterهای MongoDB متناظر.
- `tests/unit/application/advertisements/test_resolve_publication_collision.py`
- `tests/integration/advertisements/test_advertisement_queue_collision.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** policy، min gap، max defer و priority باید per-destination typed/bounded و Fail-fast باشند؛ default فقط از Decision می‌آید.
- **Migration:** collision audit/version و Indexهای بازه زمانی باید سازگار باشند؛ reschedule جمعی نیازمند transaction/atomic protocol مستند است.
- **Compatibility:** due_at گذشته/آینده و statusهای T031/T051 بدون rename شکسته مصرف شوند؛ rollout Worker قدیمی/جدید بررسی شود.
- **Concurrency:** resolverهای هم‌زمان، Worker claim و Cancel باید با version/transaction نتیجه سازگار بدهند؛ lock process-local کافی نیست.
- **Security:** فقط Campaign/Destination مجاز تغییر کند، Actor/service در audit ثبت و payload/Secret در خطا Log نشود.

## معیارهای پذیرش عینی

1. conflict و no-conflict برای یک Destination با Clock قطعی درست تشخیص داده می‌شود.
2. هر policy مصوب دقیقاً updateهای تعریف‌شده و فقط روی Jobهای مجاز همان Destination دارد.
3. min-gap پس از Resolve نقض نمی‌شود مگر outcome صریح manual/final طبق Decision.
4. رقابت Resolver/Worker/Cancel وضعیت گمشده یا Publication duplicate نمی‌سازد.
5. max defer/starvation guard bounded و قابل مشاهده است.
6. Resolver هیچ تماس Telegram یا تغییر محتوا انجام نمی‌دهد.

## تست‌های واحد الزامی

- no-conflict و هر policy مصوب با مرز دقیق min-gap.
- دو تبلیغ هم‌زمان، چند پست عادی، سقف defer و missed window.
- Job terminal/claimed/cancelled و Destination مستقل.
- خروجی audit و Conflict version.

## تست‌های یکپارچه‌سازی الزامی

- MongoDB با صف عادی+تبلیغ و update اتمیک هر policy.
- اجرای هم‌زمان دو Resolver و رقابت با Worker/Cancel.
- Restart پس از Resolve و حفظ due_at/status بدون resolution دوباره ناسازگار.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/advertisements/test_resolve_publication_collision.py
uv run pytest tests/integration/advertisements/test_advertisement_queue_collision.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت Decision کامل Collision/default/min-gap در `docs/DECISIONS.md`.
- ثبت الگوریتم، update boundary و رقابت‌ها در `docs/ARCHITECTURE.md` و `docs/CODE_MAP.md`.
- به‌روزرسانی example Config policyها و سپس `docs/ROADMAP.md`، `docs/STATUS.md` و همین فایل.

## تعریف Done

Task زمانی Done است که Decision Collision ثبت، policyهای مصوب با تست اتمیک/رقابتی و Restart اثبات، min-gap و bounded defer رعایت، همه Quality Gateها موفق و هیچ policy ضمنی یا تماس Telegram افزوده نشده باشد.
