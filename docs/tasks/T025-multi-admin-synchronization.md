# T025 — همگام‌سازی پیام تمام مدیران

## وضعیت

`Completed`

## هدف

همگام‌سازی best-effort هدر و Keyboard همه `ApprovalReference`های یک Post پس از تغییر معتبر وضعیت، با خواندن آخرین State از MongoDB و ثبت شکست مستقل هر پیام برای Retry.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `5.16 هماهنگی میان مدیران`.
- `docs/REQUIREMENTS.md`، بخش `5.15 رفتار Toggle دکمه‌ها` فقط برای وضعیت نمایشی.
- `docs/ARCHITECTURE.md`، بخش `4` (`ApprovalReference` و `DestinationSelection`)، بخش `5` (`SynchronizeApprovalMessages`)، بخش `6` (`ApprovalRepository` و `AdminMessagingGateway`)، بخش‌های `8`، `9` و `14` (`Concurrency`).
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام `6`.

## وابستگی‌ها

- `T022` و `T024` باید کامل شده باشند.
- Decision توپولوژی Approval در `T022` باید ثبت شده باشد؛ در غیر این صورت Task Blocked است.

## دامنه کار

- تعریف `SynchronizeApprovalMessages` برای خواندن State/version جاری Post و همه Referenceهای فعال.
- Render دوباره هدر و Keyboard از State جاری، بدون حذف دکمه‌های معتبر.
- ویرایش fan-out به‌صورت best-effort؛ شکست یک Reference مانع ویرایش بقیه نباشد.
- ثبت per-reference وضعیت sync، نسخه آخر، attempt، next retry و خطای redacted.
- فراهم‌کردن عملیات محدود برای Claim/Retry رکوردهای ناموفق بدون ساخت Scheduler عمومی.
- جلوگیری از بازنویسی State جدیدتر توسط Sync قدیمی با version/expected rendered version.

## خارج از دامنه

- تغییر State مقصد یا حل Conflict Toggle که در `T024` انجام می‌شود.
- انتشار، Schedule، Cancel یا ویرایش پیام منتشرشده در مقصد.
- افزودن Audience جدید خارج از Decision مصوب.
- Retry نامحدود یا Worker زمان‌بندی عمومی.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/approvals/synchronize_approval_messages.py`
- توسعه `src/telegram_assist_bot/application/ports/approval_repository.py`
- توسعه `src/telegram_assist_bot/infrastructure/mongodb/approval_repository.py`
- توسعه Renderer/Keyboard در `src/telegram_assist_bot/presentation/bot/`
- `tests/unit/application/approvals/test_synchronize_approval_messages.py`
- `tests/integration/approvals/test_multi_admin_synchronization.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** سیاست Retry باید محدود و از Foundation موجود خوانده شود؛ Audience فقط از Decision/Config معتبر می‌آید.
- **Migration:** فیلدهای sync version، attempt، next retry و last error باید با Referenceهای قدیمی سازگار و Index claim قابل تکرار باشد.
- **Compatibility:** خطای «message not modified» و پیام حذف‌شده باید در Adapter به نتیجه داخلی پایدار Map شوند، نه Exception SDK در Application.
- **Concurrency:** هر Edit از Snapshot نسخه‌دار ساخته شود؛ Sync قدیمی نباید UI جدیدتر را overwrite کند و Claim retry باید اتمیک باشد.
- **Security:** فقط Referenceهای متعلق به Audience مجاز و Post درست ویرایش شوند؛ متن خطای Bot و Token در Mongo/Log ذخیره نشود.

## معیارهای پذیرش عینی

1. پس از یک Transition، همه Referenceهای فعال با آخرین State قابل مشاهده ویرایش می‌شوند.
2. شکست یک پیام، موفقیت سایر پیام‌ها را rollback یا متوقف نمی‌کند.
3. شکست با retry metadata مستقل ثبت و اجرای مجدد محدود امکان‌پذیر است.
4. Sync قدیمی نمی‌تواند نمایش version جدیدتر را بازنویسی کند.
5. Keyboard معتبر باقی می‌ماند و فقط مطابق State جاری Render می‌شود.
6. State کسب‌وکار در این Use Case تغییر نمی‌کند.

## تست‌های واحد الزامی

- fan-out موفق برای چند Reference و Render از State جاری.
- ترکیب موفقیت/شکست و ادامه پردازش پس از خطای یک Gateway.
- نگاشت message-not-modified، deleted و خطای موقت.
- جلوگیری از overwrite نسخه جدید و محاسبه Retry محدود.

## تست‌های یکپارچه‌سازی الزامی

- MongoDB و Bot Gateway جعلی با چند Reference، یک شکست و ثبت retry metadata.
- دو Sync خارج از ترتیب و اثبات باقی‌ماندن جدیدترین version.
- Claim هم‌زمان Retry و اثبات پردازش یک‌باره هر رکورد در هر Lease.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/approvals/test_synchronize_approval_messages.py
uv run pytest tests/integration/approvals/test_multi_admin_synchronization.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- افزودن جریان fan-out/retry و Stateهای Sync به `docs/ARCHITECTURE.md` و `docs/CODE_MAP.md`.
- ثبت Decision جدید فقط در صورت تغییر مهم سیاست هم‌گام‌سازی مصوب.
- مستندسازی Index/Migration و به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و همین فایل.

## تعریف Done

Task زمانی Done است که همگام‌سازی version-aware و best-effort با Retry پایدار پیاده‌سازی، شکست جزئی و ترتیب معکوس با تست یکپارچه اثبات، همه Quality Gateها موفق و Decision توپولوژی رعایت شده باشد.

## نتایج نهایی

- Sync/fan-out/retry: `4 passed`، صفر skip؛ failure مستقل، stale protection، deleted inactive و claim تک‌برنده پاس شدند.
- Attempt سقف ۳ و فقط category امن persisted می‌شود؛ Full Suite `718 passed`.
