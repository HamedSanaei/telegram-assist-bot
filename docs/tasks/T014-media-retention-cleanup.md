# T014 — انقضا و Cleanup فایل‌های Media

## وضعیت

Planned

## هدف

همگام‌کردن retention چهارده‌روزهٔ Post با حذف idempotent فایل‌های Media منقضی و Orphan از storage خصوصی، با worker قابل Restart و بدون اتکا به زمان نامعین TTL monitor.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.4 ذخیره اطلاعات پست`، retention و TTL.
- `docs/REQUIREMENTS.md`، بخش `5.5 مدیریت مدیا`، حذف فایل وابسته پس از انقضا.
- `docs/REQUIREMENTS.md`، بخش `15. تست‌ها`، تشخیص انقضای ۱۴روزه.
- `docs/ARCHITECTURE.md`، بخش `9. MongoDB و مدل ماندگاری`، محدودیت TTL.
- `docs/ARCHITECTURE.md`، بخش `10. ذخیره Media`، Cleanup Worker.
- `docs/ARCHITECTURE.md`، بخش `14. Logging، Retry، Idempotency و هم‌زمانی`.

## وابستگی‌ها

- T004 — MongoDB و Persistence یکتای Post؛ باید Completed باشد.
- T013 — دانلود و ذخیره انواع Media؛ باید Completed باشد.

## محدوده

- تعریف query/Port محدود برای یافتن Media منقضی و Orphan candidate با batch/continuation bounded.
- Use Case حذف یک candidate به‌صورت idempotent و Worker اجرای دوره‌ای/یک‌باره.
- حذف منطقی بر پایهٔ `expires_at <= now` حتی اگر Post هنوز توسط TTL monitor حذف نشده باشد.
- شناسایی Orphan فقط با grace period تنظیم‌شده تا race فایل تازه commitشده حذف نشود.
- ثبت وضعیت/نتیجهٔ cleanup و retry محدود خطای filesystem موقت؛ file-not-found موفق idempotent است.
- جلوگیری از حذف فایل referenced توسط Post غیرمنقضی یا hash مشترک مورد استفاده.
- cleanup فایل‌های temp stale متعلق به storage تحت root مجاز.

## خارج از محدوده

- تغییر retention از ۱۴ روز، archive یا backup.
- حذف فوری document MongoDB به جای TTL.
- Object Storage lifecycle policy.
- دانلود، Album، publication یا cleanup داده‌های AI/Schedule.
- scan کل filesystem خارج از Media root.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/cleanup_expired_media.py`
- توسعهٔ محدود Portهای Post/MediaStorage.
- `src/telegram_assist_bot/workers/media_cleanup.py`
- mapper/index لازم برای cleanup candidate در Infrastructure.
- `tests/unit/application/test_cleanup_expired_media.py`
- `tests/unit/workers/test_media_cleanup.py`
- `tests/integration/test_media_retention_cleanup.py`

## نکات پیاده‌سازی

- TTL حذف فایل را trigger نمی‌کند؛ query باید از metadata Media یا collection مرجع مستقل استفاده کند و race حذف document را تحمل کند.
- Clock تزریق و batch limit bounded باشد.
- **ریسک Configuration:** interval، batch size و orphan grace مثبت/محدود؛ retention اصلی با الزام ۱۴ روز سازگار بماند.
- **ریسک Migration:** index query cleanup در صورت نیاز صریح و idempotent ایجاد شود؛ drop خودکار ممنوع.
- **ریسک Compatibility:** file-not-found و permission error به نتیجه‌های پایدار map شوند.
- **ریسک Concurrency:** دو cleanup worker ممکن است یک candidate را ببینند؛ conditional claim یا delete idempotent و reference recheck لازم است.
- **ریسک Security:** فقط path canonical زیر Media root حذف شود؛ symlink و path ذخیره‌شدهٔ دستکاری‌شده رد و Log شود.

## معیارهای پذیرش عینی

1. فایل Media منقضی حذف و metadata cleanup به‌درستی ثبت می‌شود.
2. فایل Post غیرمنقضی و فایل hash مشترکِ referenced حذف نمی‌شود.
3. نبود فایل موفق idempotent است؛ permission/transient failure ثبت و محدود retry می‌شود.
4. دو Worker هم‌زمان حذف مخرب یا خطای دائمی کاذب نمی‌سازند.
5. orphan جوان‌تر از grace حفظ و orphan قدیمی حذف می‌شود.
6. هیچ path خارج Media root حتی با symlink/traversal حذف نمی‌شود.
7. Restart batch را از candidateهای باقی‌مانده ادامه می‌دهد.

## Unit Testهای الزامی

- boundary دقیق انقضا و Clock ثابت.
- referenced/shared file، orphan grace و temp stale.
- file-not-found، permission و transient retry.
- batch/continuation و cancellation.
- path containment و concurrent idempotent result.

## Integration Testهای الزامی

- MongoDB آزمایشی + filesystem موقت با Post منقضی، تازه، shared و orphan.
- race دو worker و assertion حذف فقط candidate مجاز.
- document حذف‌شده توسط TTL شبیه‌سازی‌شده ولی metadata/file باقی‌مانده.
- restart میان batchها و ادامهٔ cleanup.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_cleanup_expired_media.py tests/unit/workers/test_media_cleanup.py
uv run pytest tests/integration/test_media_retention_cleanup.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

مسیر storage و DB باید آزمایشی باشند؛ بازبینی فهرست فایل قبل/بعد، Persian diff و `git diff --check` الزامی است.

## به‌روزرسانی‌های مستندات

- ثبت Status/verification و به‌روزرسانی T014 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن Worker/query/data flow به `docs/CODE_MAP.md`.
- همگام‌سازی semantics TTL/orphan/cleanup در `docs/ARCHITECTURE.md`.
- به‌روزرسانی Config نمونه برای interval/batch/grace غیرحساس.
- ADR فقط در صورت تصمیم پایدار دربارهٔ shared storage reference.

## تعریف انجام‌شدن

- retention، orphan، race و path-security tests بدون skip پاس شده‌اند.
- Worker bounded، idempotent و restart-safe است.
- Quality Gate و UTF-8 پاس شده‌اند و فایل خارج root حذف نمی‌شود.
- Scope فقط cleanup Media است.

