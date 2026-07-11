# T022 — هدر و محتوای پیام تأیید

## وضعیت

`Planned`

## هدف

ساخت و تحویل پیام پیشنهادی مدیران شامل هدر متادیتا و محتوای اصلی به‌صورت جدا از payload قابل انتشار، و ثبت Reference هر پیام تحویل‌شده برای همگام‌سازی آینده.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `5.12 ارسال پست برای مدیران`، زیر‌بخش‌های `هدر متادیتا` و `محتوای پست`.
- `docs/REQUIREMENTS.md`، بخش‌های `5.5 مدیریت مدیا`، `5.6 مدیریت آلبوم‌ها` و `5.7 حفظ Premium Emoji` فقط در حد نمایش محتوای آماده‌شده.
- `docs/ARCHITECTURE.md`، بخش `4` (`ApprovalReference`)، بخش `5` (`SendPostForApproval`)، بخش `6` (`ApprovalRepository` و `AdminMessagingGateway`)، بخش‌های `8`، `9`، `14` و `15`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام `6`.

## وابستگی‌ها

- `T013`، `T019` و `T020` باید کامل شده باشند.

### پیش‌نیاز تصمیم

پیش از پیاده‌سازی باید مقصد و توپولوژی پیام تأیید (گروه/کانال مشترک، گفت‌وگوی جداگانه مدیران یا مجموعه دقیق حالت‌های پشتیبانی‌شده)، روش تفکیک هدر از متن/Caption و Reference مرجع ویرایش تصویب و در `docs/DECISIONS.md` ثبت شود. این Task نباید یکی از این UXها را بی‌صدا انتخاب کند.

## دامنه کار

- تعریف مدل View مستقل برای هدر، محتوای Post و Reference پیام مدیریتی.
- ساخت Renderer قطعی هدر با همه فیلدهای الزامی و نمایش صریح مقدارهای pending/unavailable.
- حفظ متن، Caption، Entity و ترتیب Media آماده‌شده از Taskهای قبلی در مسیر مدیریتی.
- ارسال از `AdminMessagingGateway` با Timeout محدود و ثبت `ApprovalReference` فقط پس از موفقیت قابل تشخیص.
- ذخیره ارتباط Post، محل Chat، Message ID، Admin/Audience و نسخه نمایشی لازم برای Sync.
- تضمین جدایی metadata مدیریتی از محتوای مقصد.

## خارج از دامنه

- Keyboard و Callback (`T023` و `T021`).
- Toggle، ویرایش هم‌زمان تمام پیام‌ها یا Retry fan-out (`T024` و `T025`).
- تصمیم انتشار، User API و Scheduler.
- بازپردازش Media، پاک‌سازی متن یا دسته‌بندی که در Taskهای قبلی انجام شده‌اند.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/approvals/models.py`
- `src/telegram_assist_bot/application/approvals/send_post_for_approval.py`
- `src/telegram_assist_bot/application/ports/approval_repository.py`
- `src/telegram_assist_bot/presentation/bot/approval_renderer.py`
- پیاده‌سازی ارسال در `src/telegram_assist_bot/infrastructure/telegram/bot_client.py`
- `src/telegram_assist_bot/infrastructure/mongodb/approval_repository.py`
- `tests/unit/presentation/bot/test_approval_renderer.py`
- `tests/unit/application/approvals/test_send_post_for_approval.py`
- `tests/integration/approvals/test_approval_delivery.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** مقصدهای تأیید باید از Config معتبر `T002` خوانده شوند؛ حالت پشتیبانی‌نشده در Startup رد شود.
- **Migration:** Collection/Index مربوط به Approval Reference باید سازگار با اسناد موجود و ایجادشونده به‌صورت idempotent باشد.
- **Compatibility:** محدودیت متن/Caption و تفاوت نوع پیام Bot در DTO مرزی مدیریت شود؛ مدل Application به SDK وابسته نشود.
- **Concurrency:** ارسال تکراری یا Retry نباید Referenceهای مبهم بسازد؛ کلید تحویل پایدار و وضعیت هر Attempt ثبت شود، اما Sync چندمدیره در `T025` است.
- **Security:** پیام فقط به Audience مصوب ارسال شود، metadata خصوصی وارد محتوای مقصد نشود و خطا/Log شامل Bot Token یا متن خام حساس نباشد.

## معیارهای پذیرش عینی

1. هدر همه فیلدهای اجباری بخش `5.12` را با ترتیب قطعی نمایش می‌دهد.
2. متن/Caption/Media پس از هدر و بدون تغییر ناخواسته فارسی، Emoji و Entity تحویل می‌شوند.
3. metadata مدیران در مدل محتوای قابل انتشار ذخیره یا concatenate نمی‌شود.
4. برای هر تحویل موفق یک `ApprovalReference` معتبر ثبت و برای شکست، موفقیت جعلی ثبت نمی‌شود.
5. Destination/Audience تأیید دقیقاً مطابق Decision و Config است.
6. هیچ Keyboard یا رفتار Callback در این Task پیاده‌سازی نشده است.

## تست‌های واحد الزامی

- Renderer برای Post متنی، Captionدار، Media و مقدارهای pending.
- حفظ متن فارسی، نیم‌فاصله، Emoji و خط‌شکنی نماینده.
- عدم حضور header مدیریتی در payload انتشار.
- ثبت Reference فقط پس از نتیجه موفق Gateway و نگاشت خطای امن.

## تست‌های یکپارچه‌سازی الزامی

- تحویل با Bot Gateway جعلی/Contract fixture و ذخیره Reference در MongoDB آزمایشی.
- شکست ارسال و اثبات عدم ثبت Reference موفق.
- تست زنده Bot API جزو Suite پیش‌فرض نیست.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/presentation/bot/test_approval_renderer.py tests/unit/application/approvals/test_send_post_for_approval.py
uv run pytest tests/integration/approvals/test_approval_delivery.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت Decision توپولوژی پیام و روش تفکیک هدر/محتوا در `docs/DECISIONS.md`.
- افزودن Renderer، Use Case، Repository و جریان Reference به `docs/CODE_MAP.md`.
- همگام‌سازی بخش‌های مرتبط `docs/ARCHITECTURE.md` در صورت تفاوت تصمیم نهایی.
- به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و نتیجه همین فایل.

## تعریف Done

Task زمانی Done است که Decision UX ثبت، پیام تأیید مطابق نیازمندی تحویل و Reference آن پایدار ذخیره شود، جدایی metadata با تست اثبات گردد، آزمون‌های فارسی/Entity و تمام Quality Gateها موفق باشند و هیچ رفتار Keyboard/انتشار وارد Scope نشده باشد.
