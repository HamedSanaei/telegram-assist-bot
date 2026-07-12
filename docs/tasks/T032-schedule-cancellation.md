# T032 — لغو و سیاست Recompaction

## وضعیت

`Completed`

## هدف

لغو اتمیک Job زمان‌بندی‌شده قابل لغو و اجرای سیاست Configurable حفظ زمان‌های بعدی یا Recompaction صف، همراه با همگام‌سازی پیام مدیران.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `5.19 لغو زمان‌بندی`.
- `docs/ARCHITECTURE.md`، بخش `5` (`CancelScheduledPost`)، بخش `6` (`ScheduleRepository`)، بخش `11` و بخش `14` (`Concurrency`).
- `docs/ARCHITECTURE.md`، بخش `15`، تست‌های Restart/Concurrency.

## وابستگی‌ها

- `T025`، `T030` و `T031` باید کامل شده باشند.

### پیش‌نیاز تصمیم

پیش از پیاده‌سازی باید مقدار پیش‌فرض قطعی `preserve` یا `recompact`، محدوده Jobهای متاثر، رفتار Job در حال Claim و نحوه اطلاع مدیر از جابه‌جایی زمان‌ها تصویب و در `docs/DECISIONS.md` ثبت شود؛ عبارت پیشنهادی نیازمندی به‌تنهایی مجوز تصمیم ضمنی نیست.

## دامنه کار

- تعریف `CancelScheduledPost` با Actor، Post، Destination، Job/version و سیاست مصوب.
- لغو اتمیک فقط statusهای قابل لغو و جلوگیری از اجرای بعدی آن‌ها.
- در حالت preserve، عدم تغییر due_at سایر Jobها.
- در حالت recompact، محاسبه مجدد فقط Jobهای واجد شرایط بعدی همان Destination با فاصله Configured و version check.
- حل رقابت Cancel با Claim/Complete از طریق نتیجه Conflict/AlreadyCompleted صریح.
- Trigger همگام‌سازی Approval T025 پس از Commit موفق.

## خارج از دامنه

- لغو Publication موفق یا حذف پیام منتشرشده.
- تغییر سیاست صف Destinationهای دیگر.
- Feature drag/drop یا زمان‌بندی دستی دلخواه.
- بازطراحی Worker یا افزودن Scheduler جدید.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/scheduling/cancel_scheduled_post.py`
- توسعه `src/telegram_assist_bot/domain/scheduled_publication.py`
- توسعه `src/telegram_assist_bot/application/ports/schedule_repository.py`
- توسعه `src/telegram_assist_bot/infrastructure/mongodb/schedule_repository.py`
- توسعه Configuration و Approval wiring در حد سیاست مصوب.
- `tests/unit/application/scheduling/test_cancel_scheduled_post.py`
- `tests/integration/mongodb/test_schedule_cancellation.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** Enum سیاست و default باید Validate و در example Config ثبت شود؛ مقدار ناشناخته Fail-fast است.
- **Migration:** افزودن cancellation metadata/policy version سازگار باشد؛ update جمعی Recompaction باید rollback/atomicity مصوب داشته باشد.
- **Compatibility:** تغییر due_at باید audit شود و Workerهای rollout قدیمی آن را درست ببینند؛ status نام‌گذاری‌شده بی‌مهاجرت عوض نشود.
- **Concurrency:** Cancel در برابر claim/complete و Recompaction هم‌زمان per-destination باید با version/transaction یا primitive اتمیک امن شود.
- **Security:** Actor و Permission مقصد مجدداً بررسی و علت/خطا redacted ثبت شود؛ Admin نمی‌تواند Job مقصد غیرمجاز را لغو کند.

## معیارهای پذیرش عینی

1. Job Pending/Waiting مجاز اتمیک Cancel و دیگر توسط Worker Claim نمی‌شود.
2. Job Completed یا در وضعیت غیرقابل لغو نتیجه صریح و بدون تغییر می‌دهد.
3. preserve due_at سایر Jobها را تغییر نمی‌دهد.
4. recompact فقط صف بعدی همان Destination را با فاصله معتبر بازچینش می‌کند.
5. رقابت Cancel/Worker یک نتیجه نهایی سازگار و بدون انتشار پس از Cancel موفق دارد.
6. Approvalها پس از Commit State جدید همگام می‌شوند.

## تست‌های واحد الزامی

- Cancellation مجاز، AlreadyCancelled، AlreadyCompleted و Permission denied.
- سیاست preserve و محاسبه recompact با Fake Clock/interval.
- عدم تاثیر روی Destination دیگر و Jobهای terminal/claimed مطابق Decision.
- عدم Sync در صورت rollback/conflict.

## تست‌های یکپارچه‌سازی الزامی

- رقابت Cancel و Claim MongoDB با outcomeهای مجاز و قطعی.
- preserve و recompact روی چند Job و دو Destination.
- Restart پس از Cancel و اثبات عدم اجرای Job لغوشده.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/scheduling/test_cancel_scheduled_post.py
uv run pytest tests/integration/mongodb/test_schedule_cancellation.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

## نتیجه راستی‌آزمایی

- `9` تست واحد و `3` تست MongoDB مستقیم موفق، `0` skip.
- `preserve`، `recompact`، مقصد مستقل و race لغو/claim چندبار تأیید شدند.
- Sync فقط پس از commit موفق اجرا می‌شود.

- ثبت Decision default/scope Recompaction در `docs/DECISIONS.md`.
- ثبت الگوریتم، statusها و رقابت Cancel/Claim در `docs/ARCHITECTURE.md` و مسیرها در `docs/CODE_MAP.md`.
- به‌روزرسانی example Config، `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که Decision سیاست ثبت، Cancel و هر دو mode با تست رقابتی/Restart اثبات، Approval sync پس از Commit انجام، Quality Gateها موفق و لغو Publication موفق خارج از Scope باشد.
