# T031 — Worker پایدار، Lease و بازیابی

## وضعیت

`Completed`

## هدف

اجرای Jobهای زمان‌بندی‌شده Due از MongoDB با Claim اتمیک، Lease منقضی‌شونده، انتشار idempotent و بازیابی پس از Restart، بدون منطق لغو/Recompaction.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `5.18 انتشار زمان‌بندی‌شده`، به‌ویژه Persistence، Restart و جلوگیری از اجرای دوباره.
- `docs/REQUIREMENTS.md`، بخش `13 مدیریت خطا و Retry`.
- `docs/ARCHITECTURE.md`، بخش `5` (`RunDuePublication`)، بخش `6` (`ScheduleRepository` و `TelegramPublisherGateway`)، بخش‌های `7`، `9`، `11` و `14`.
- `docs/ARCHITECTURE.md`، بخش `15`، سناریوهای Restart/Concurrency.

## وابستگی‌ها

- `T008`، `T029` و `T030` باید کامل شده باشند.

## دامنه کار

- Claim اتمیک قدیمی‌ترین Job Due واجد شرایط با owner و lease expiry.
- اجرای یک iteration/loop قابل توقف Worker از طریق `RunDuePublication` و Publication idempotent T029.
- heartbeat/تمدید Lease فقط در صورت نیاز و با ownership check.
- تکمیل موفق، WaitingForRetry محدود، شکست دائمی و lease-lost به‌صورت صریح.
- بازیابی Jobهای Pending/Retry و Lease منقضی پس از Restart.
- Shutdown کنترل‌شده بدون علامت‌گذاری موفقیت جعلی.

## خارج از دامنه

- Cancellation و Recompaction (`T032`).
- محاسبه Slot جدید (`T030`) یا تغییر interval صف موجود.
- Dashboard، distributed broker یا چند نوع Job عمومی.
- Retry بی‌نهایت و live Telegram test پیش‌فرض.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/scheduling/run_due_publication.py`
- توسعه `src/telegram_assist_bot/application/ports/schedule_repository.py`
- توسعه `src/telegram_assist_bot/infrastructure/mongodb/schedule_repository.py`
- `src/telegram_assist_bot/workers/scheduled_publication_worker.py`
- Wiring/entry point Worker در Composition Root.
- `tests/unit/application/scheduling/test_run_due_publication.py`
- `tests/integration/scheduling/test_schedule_worker_restart.py`
- `tests/integration/scheduling/test_schedule_worker_concurrency.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** poll interval، batch/one-at-a-time، lease و shutdown timeout باید مثبت و bounded باشند؛ lease از حداکثر زمان Attempt منطقی کوتاه‌تر نباشد.
- **Migration:** Index claim روی status/due_at/next_attempt_at/lease باید online و idempotent ساخته شود.
- **Compatibility:** statusها و owner/version Persistence قرارداد پایدارند؛ Worker قدیم/جدید در rollout نباید یک Job را دو بار claim کنند.
- **Concurrency:** claim، heartbeat و complete همگی ownership/version شرطی دارند؛ lock محلی کافی نیست.
- **Security:** Worker Secret/Session را Log نمی‌کند و فقط Destination مجاز موجود در Job معتبر را اجرا می‌کند.

## معیارهای پذیرش عینی

1. فقط Jobهای Due و واجد شرایط Claim می‌شوند.
2. چند Worker یک Job را هم‌زمان اجرا نمی‌کنند.
3. توقف Worker پس از Claim و انقضای Lease، Job را برای Worker دیگر قابل بازیابی می‌کند.
4. موفقیت Publication idempotent دقیقاً یک‌بار به Completed Map می‌شود.
5. خطاهای موقت bounded Retry و خطاهای دائمی وضعیت نهایی روشن می‌گیرند.
6. Restart به حافظه Process برای بازسازی صف وابسته نیست.

## تست‌های واحد الزامی

- انتخاب نتیجه برای success/transient/permanent/lease-lost.
- محاسبه next attempt و توقف در max attempts.
- shutdown/cancellation داخلی Worker بدون ثبت موفقیت.
- عدم اجرای Job not-due یا cancelled/terminal.

## تست‌های یکپارچه‌سازی الزامی

- Crash شبیه‌سازی‌شده پس از Claim، انقضای Lease و بازیابی توسط Worker دوم.
- دو Worker هم‌زمان و شمارش یک تماس Publisher برای یک Job.
- Restart با MongoDB موجود و تکمیل Pending/WaitingForRetry.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/scheduling/test_run_due_publication.py
uv run pytest tests/integration/scheduling/test_schedule_worker_restart.py tests/integration/scheduling/test_schedule_worker_concurrency.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

## نتیجه راستی‌آزمایی

- `12` تست واحد Worker/use case و `3` تست مستقیم lease/concurrency موفق، `0` skip.
- فرمان عملیاتی: `uv run python -m telegram_assist_bot schedule-worker --config config/configuration.local.json`.
- crash، replacement worker، shutdown و یک Publisher call مؤثر تأیید شدند.

- ثبت lifecycle Worker، claim query، lease و shutdown در `docs/ARCHITECTURE.md`.
- افزودن Worker/entry point و Repository methods به `docs/CODE_MAP.md`.
- مستندسازی کلیدهای Worker در نمونه Config و به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و همین فایل.

## تعریف Done

Task زمانی Done است که چند Worker و Crash/Restart با MongoDB واقعی آزمایشی رفتار یک‌Claimی و بازیابی را اثبات، Retry bounded و ownership checks اجرا، Quality Gateها موفق و Cancellation خارج از Scope باشد.
