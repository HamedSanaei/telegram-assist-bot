# T055 — تعریف محصولی تحلیل و اولویت‌بندی هوشمند

## وضعیت

`Planned`

## هدف

تبدیل فهرست پیشنهادی فاز سوم به نیازمندی محصولی تصویب‌شده، قابل آزمون و اولویت‌بندی‌شده، سپس شکستن Scope مصوب به Taskهای کوچک؛ بدون ایجاد Feature code، Config اجرایی یا تست کاربردی.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `7 فاز سوم پیشنهادی: تحلیل و اولویت‌بندی هوشمند محتوا`.
- `docs/ARCHITECTURE.md`، بخش `12` برای مرز Pipeline AI موجود و بخش `16` برای مرز اولیه/آینده.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام‌های `5` و `14`.

## وابستگی‌ها

- `T054` باید کامل شده باشد.

### پیش‌نیاز تصمیم

مالک محصول باید قابلیت‌های داخل/خارج نسخه بعد، اولویت و outcome مورد انتظار را تصویب کند. اگر انتخاب محصولی یا داده لازم موجود نیست، Task باید Blocker را مستند کند و نباید Requirement یا Provider/Model را حدس بزند.

## دامنه کار

- تعریف Actorها، مسئله، outcome و workflow هر قابلیت مصوب از رتبه‌بندی تا خلاصه/بازنویسی/ترجمه/انتخاب مقصد.
- تعیین input/output schema، زبان، latency، human review/override، failure policy، کیفیت قابل اندازه‌گیری و acceptance criteria.
- تعیین وابستگی به Pipeline AI موجود، داده/Prompt/version، quota/cost/privacy و observability در سطح Requirement.
- حذف یا صریحاً Deferred کردن قابلیت‌های تأییدنشده؛ جلوگیری از تکرار Featureهای AI فاز اول.
- به‌روزرسانی Requirements/Architecture/Roadmap و ساخت task specهای یک‌جلسه‌ای با ID یکتا، dependency، scope، tests و verification.
- ثبت تصمیم‌های معماری مهم، نه جزئیات پیاده‌سازی فرضی.

## خارج از دامنه

- هر تغییر در `src/`، `tests/`، `config/`، dependencyها یا deployment.
- انتخاب Provider/Model/SDK بدون داده و Decision مصوب.
- ساخت Prompt، Schema Python، Job، UI یا Migration.
- علامت‌زدن taskهای جدید به‌عنوان Active/Completed در این Gate.

## فایل‌ها و ماژول‌های مورد انتظار

- `docs/REQUIREMENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/DECISIONS.md` فقط برای تصمیم‌های مهم مصوب.
- `docs/STATUS.md`
- task specهای جدید با IDهای بعدی آزاد زیر `docs/tasks/`؛ هیچ فایل اجرایی انتظار نمی‌رود.

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** فقط requirement کلیدهای احتمالی و validation آن‌ها تعریف شود؛ Config runtime در این Gate تغییر نمی‌کند.
- **Migration:** داده/Prompt/result migration لازم برای آینده باید به task مستقل تبدیل شود؛ هیچ Migration اجرا نمی‌شود.
- **Compatibility:** اثر روی AIResult، Post lifecycle، Callback و API موجود به‌صراحت در Requirements/Taskها قید شود.
- **Concurrency:** هر workflow async/job/override باید ownership، idempotency و race acceptance criterion داشته باشد؛ پیاده‌سازی نمی‌شود.
- **Security:** privacy متن، raw response، ترجمه/بازنویسی، quota و prompt injection باید requirement و تست آینده داشته باشند؛ Secret واقعی ثبت نشود.

## معیارهای پذیرش عینی

1. قابلیت‌های مصوب/Deferred و ترتیب تحویل با تأیید محصولی صریح‌اند.
2. هر قابلیت مصوب Actor، input/output، failure/override و معیار پذیرش قابل اندازه‌گیری دارد.
3. Requirements، Architecture و Roadmap بدون تناقض و بدون معماری خیالی همگام‌اند.
4. هر Task جدید در یک Session قابل اجرا، dependency آن کامل/مرتب و task file آن دارای همه بخش‌های AGENTS است.
5. هیچ Provider، مدل، UX یا threshold بدون Decision اختراع نشده است.
6. هیچ فایل کد، تست کاربردی، Config یا Migration تغییر نکرده است.

## تست‌های واحد الزامی

- `N/A`: خروجی این Gate صرفاً مستندات محصول و task specification است و رفتار اجرایی نمی‌سازد.

## تست‌های یکپارچه‌سازی الزامی

- `N/A`: هیچ Adapter، Persistence یا جریان اجرایی ساخته نمی‌شود؛ taskهای آینده باید تست‌های لازم خود را مشخص کنند.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff فارسی و همه معیارهای پذیرش/ارجاع‌های متقابل باید به‌صورت انسانی بازبینی شوند.

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- همین Task ذاتاً مستندسازی است: `docs/REQUIREMENTS.md`، `docs/ARCHITECTURE.md`، `docs/ROADMAP.md`، `docs/STATUS.md` و task fileهای جدید باید یکپارچه به‌روزرسانی شوند.
- `docs/DECISIONS.md` فقط برای تصمیم مصوب و با پیامدهای آن تغییر کند.
- `docs/CODE_MAP.md` نباید با ماژول‌های خیالی تغییر کند؛ فقط در صورت نیاز پیوند به Gate مستنداتی افزوده شود.

## تعریف Done

Task زمانی Done است که Scope فاز سوم با تأیید محصولی، معیارهای آزمون‌پذیر و taskهای کوچک مستند شود، cross-reference و Quality Gate متن پاس، هیچ Feature code/test/config ساخته نشود و هیچ ابهام بی‌صدا حل نشده باشد.
