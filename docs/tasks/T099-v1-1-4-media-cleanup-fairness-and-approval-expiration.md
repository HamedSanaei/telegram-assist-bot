# T099 — v1.1.4 Media Cleanup Fairness and Approval-Expiration Patch

## وضعیت

Completed

## هدف

رفع دو نقص اثبات‌شدهٔ Production در چرخهٔ عمر پاک‌سازی Media خصوصی و آماده‌سازی
patch release نسخهٔ `1.1.4`:

1. گرسنگی صف Candidate (head-of-line starvation) که باعث می‌شد Mediaهای
   unreferenced اما هرگز بررسی‌نشده تا مدت نامحدود پاک نشوند.
2. زنده نگه‌داشتن Media توسط Approval تمام‌شده‌ای که چرخهٔ عمر تأیید آن صریحاً
   منقضی شده بود، فقط به‌دلیل باقی‌ماندن Post در پنجرهٔ مستقل ۱۴روزه.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بندهای `5.4`، `5.5`، `5.12` تا `5.19`، `13` و `16`.

## وابستگی‌ها

- T014 (Media Retention و Cleanup).
- T078 (Retention مستقل Media).
- T079 (Cleanup پیام Approval منقضی و ثبت `approval_expired`).

هیچ Task ناتمامی باقی نمانده است.

## محدوده

- مرتب‌سازی صف Candidate پاک‌سازی بر پایهٔ دو کلاس صریح: هرگز-بررسی‌نشده
  (`cleanup_next_check_at` غایب) به‌صورت قطعی پیش از retryهای deferشده.
- افزودن index نسخه‌دار و idempotent `ix_media_cleanup_fairness_v4`.
- اعمال `approval_expired != True` روی بررسی مرجع Approval تمام‌شده.
- حفظ کامل سایر مرجع‌های محافظ Media و سیاست shared-path.
- افزودن متریک افزودنی `reference_deferred` به نتیجهٔ batch و لاگ ساختاریافته.
- تست Regression برای هر دو نقص، شامل اجرای واقعی Candidate ordering روی
  MongoDB، چند Cycle محدود Worker و گذر از فاصلهٔ defer.
- همگام‌سازی سطوح نسخهٔ Package، Image، Installer، Manager، Distribution و
  مستندات با `1.1.4`.

## خارج از محدوده

- تغییر Retention چهارده‌روزهٔ Post یا نشانه‌گذاری زودهنگام Post به‌عنوان
  `Expired`.
- حذف یا سست‌کردن هر مرجع محافظ دیگر (Publication، Schedule، Native Schedule،
  Album/preparation، Advertisement، Media تازه، Approval غیرterminal).
- حذف دستی داده، `deleteMany`، `updateMany` یا تغییر دستی `cleaned_at`.
- Backfill یا بازنویسی رکوردهای تاریخی Approval.
- بزرگ‌کردن بی‌مرز Batch یا حذف سقف Cycle Worker.
- Refactor گستردهٔ Observability فراتر از یک فیلد افزودنی.
- ساخت Tag، GitHub Release یا انتشار GHCR.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/infrastructure/persistence/mongodb/content_repository.py`
- `src/telegram_assist_bot/application/cleanup_expired_media.py`
- `src/telegram_assist_bot/bootstrap/media_cleanup.py`
- `tests/integration/test_media_retention_cleanup.py`
- `tests/unit/application/test_cleanup_expired_media.py`
- `tests/unit/application/m2_fakes.py`
- `tests/unit/test_media_cleanup_bootstrap.py`
- `tests/unit/deployment/test_release_contract.py` و
  `tests/unit/deployment/test_tabctl.py`
- Surfaceهای نسخه و Release packaging
- `docs/ARCHITECTURE.md`، `docs/CODE_MAP.md`، `docs/DECISIONS.md`،
  `docs/RELEASE_NOTES.md`، `docs/OPERATIONS.md`، `docs/ROADMAP.md`،
  `docs/STATUS.md`

## نکات پیاده‌سازی

- انتخاب Candidate به دو کلاس صریح و دو Query تقسیم می‌شود: کلاس
  هرگز-بررسی‌نشده (`cleanup_next_check_at: None` که هم `null` صریح و هم فیلد
  غایب را می‌گیرد) با ترتیب `_id`، سپس کلاس retry موعد-رسیده
  (`cleanup_next_check_at: {$lte: now}`) با ترتیب `cleanup_next_check_at`/`_id`.
  مقایسهٔ `$lte` با datetime در MongoDB type-bracketed است و هرگز `null` یا
  فیلد غایب را انتخاب نمی‌کند؛ بنابراین دو کلاس disjoint باقی می‌مانند.
- یک sort تک‌پرسشی با index
  `(cleaned_at, cleanup_next_check_at, _id)` عمداً انتخاب نشد: چون
  `media_expires_at` در آن index نیست، پلن انتخابی به مدل هزینهٔ MongoDB وابسته
  می‌شود و در Collection واقعی با جمعیت بزرگ Media تازه می‌تواند همان جمعیت را
  اسکن کند. اندازه‌گیری روی Dataset Production-shaped (۱۰۶٫۲۰۰ سند) نشان داد
  پلن تک‌پرسشی ۶٫۲۰۲ کلید و ۱۲٫۴۰۰ سند را با یک SORT می‌خواند (۶۷۵ms)، در حالی
  که تقسیم دو‌کلاسی روی همان داده کلاس retry را بدون SORT و با ۱۰۰ کلید می‌خواند.
- فیلتر کلاس هرگز-بررسی‌نشده مرزهای انقضا را از indexهای موجود `v2` و `v3`
  می‌گیرد، پس اسکن آن به جمعیت منقضی محدود می‌ماند و جمعیت تازه (که در
  Production بی‌مرز رشد می‌کند) پیموده نمی‌شود. کلاس هرگز-بررسی‌نشده یک top-k
  sort محدود روی همین مجموعه دارد؛ این هزینه عمدی و قابل اندازه‌گیری است و
  فقط برای حذف sort پرداخت نمی‌شود.
- index جدید `ix_media_cleanup_fairness_v4` برابر
  `(cleaned_at, cleanup_next_check_at, _id)` است و مستقیماً Query و ترتیب کلاس
  retry را سرو می‌کند. indexهای `v1` تا `v3` دست‌نخورده باقی می‌مانند و ایجاد
  index جدید اضافی و بدون migration مخرب است.
- رکوردهای legacy فاقد `cleanup_next_check_at` همان کلاس «هرگز بررسی‌نشده» و
  بنابراین پیش‌تاز باقی می‌مانند؛ هیچ backfill لازم نیست.
- در بررسی Approval، شرط `"approval_expired": {"$ne": True}` رفتار
  backward-compatible دارد: مقدار `False` و فیلد غایب محافظت را حفظ می‌کنند و
  فقط `True` که توسط `expire_ui` در چرخهٔ عمر Approval ثبت می‌شود محافظت را
  آزاد می‌کند.
- بررسی مرجع Publication، Schedule و Native Schedule همچنان با `post_id` (نه
  فقط Post فعال) انجام می‌شود؛ پس اگر انتشار یا زمان‌بندی فعالی همان Media را
  لازم داشته باشد، آزادسازی مرجع Approval فایل را حذف نمی‌کند.
- Worker همچنان bounded و shutdown-safe است؛ انصاف در صف Candidate پیاده شده و
  سقف `media.cleanup_max_batches_per_cycle` تغییری نکرده است.

## معیارهای پذیرش عینی

1. یک گروه bounded از Candidateهای همیشه deferشده نمی‌تواند به‌صورت دائمی
   مانع دریافت تلاش پاک‌سازی توسط Candidateهای منقضی‌شدهٔ هرگز-بررسی‌نشده شود.
2. ترتیب Candidateها قطعی است: ابتدا فیلد غایب/`null` بر اساس `_id` صعودی، سپس
   رکوردهای due بر اساس `cleanup_next_check_at` و سپس `_id` صعودی؛ دو کلاس
   disjoint‌اند و retry با `cleanup_next_check_at` آینده هرگز برنمی‌گردد.
3. با کد قبلی (`sort("_id")`)، تست Regression گرسنگی شکست می‌خورد و با کد
   اصلاح‌شده موفق می‌شود.
4. Approval تمام‌شده با `approval_expired = True` و Post فعال، Media را محافظت
   نمی‌کند؛ با `approval_expired = False` یا فیلد غایب محافظت ادامه دارد.
5. Approval غیرterminal (`pending`، `claimed`، `retry`) همچنان محافظت می‌کند.
6. Publication یا Schedule فعال پس از انقضای Approval همچنان Media را محافظت
   می‌کند و Media تازه با همان `storage_path` نیز محافظت را حفظ می‌کند.
7. بررسی Post فعال برای Approval تمام‌شده حذف نشده است.
8. نسخهٔ Package، Image، Installer، Manager، Distribution و مستندات `1.1.4` است
   و هیچ Tag یا Release ساخته نشده است.
9. روی Dataset Production-shaped، هیچ‌کدام از دو Query انتخاب Candidate
   COLLSCAN ندارد و کلیدهای بررسی‌شده به جمعیت منقضی محدود می‌مانند
   (کلاس هرگز-بررسی‌نشده ۵٫۲۰۲ کلید از ۵٫۲۰۰ رکورد منقضی و کلاس retry ۱۰۰ کلید
   بدون SORT).
10. پس از `approval_expired = True`، مسیر باقی‌ماندهٔ حذف پیام Approval هیچ
    نیاز دیگری به فایل محلی Media ندارد (فقط `chat_id`/`message_id`).
11. تفاضل ۳٫۳۰۳ رکورد `expired_uncleaned` و ۳٫۲۵۴ رکورد blocked در Production
    در مجموع ۴۹ رکورد است، نه ۴۹ رکورد برای هر مسیر.

## Unit Testهای الزامی

- تفکیک `reference_deferred` از defer ناشی از Retry/شکست.
- عدم تغییر رفتار دفاعی سایر مسیرهای Cleanup.
- Contract فیلدهای متریک batch در Composition Root.

## Integration Testهای الزامی

- Contract واقعی ترتیب `missing`/`null`/`date` روی MongoDB.
- Regression گرسنگی با ۴۰۰ رکورد blocked، ۵۰ رکورد پاک‌شدنی و چند Cycle محدود
  Worker با گذر از فاصلهٔ defer.
- شش حالت مرجع Approval (تمام‌شدهٔ منقضی، تمام‌شدهٔ زنده، تمام‌شدهٔ legacy،
  `pending`، `claimed`، `retry`).
- محافظت Publication، Schedule، Media تازهٔ همان مسیر و حفظ بررسی Post فعال.
- اجرای واقعی `CleanupExpiredMedia` روی MongoDB: حذف فقط Media با Approval
  منقضی و defer شدن Media با Approval زنده.
- وجود index جدید در `media_items`.
- Dataset Production-shaped با ۱۰۰٫۰۰۰ Media تازهٔ غیرمنقضی، ۵٫۰۰۰ منقضی
  هرگز-بررسی‌نشده، ۱٫۰۰۰ retry موعد-رسیده و ۲۰۰ رکورد legacy؛ اثبات محدود بودن
  `totalKeysExamined`/`totalDocsExamined`، نبود COLLSCAN و نبود SORT در کلاس
  retry.
- اثبات عبور از کلاس هرگز-بررسی‌نشده به retry موعد-رسیده پس از drain شدن و رد
  شدن retry آینده.

## فرمان‌های راستی‌آزمایی

```powershell
uv lock --check
uv run pytest tests/integration/test_media_retention_cleanup.py tests/unit/application/test_cleanup_expired_media.py tests/unit/workers/test_media_cleanup.py tests/unit/test_media_cleanup_bootstrap.py -q
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --all
uv build --no-build-isolation
uv run python scripts/check_distribution.py dist
git diff --check
```

## به‌روزرسانی‌های مستندات

- ROADMAP، STATUS، ARCHITECTURE، CODE_MAP، DECISIONS، RELEASE_NOTES،
  OPERATIONS و این Task.

## نتیجهٔ راستی‌آزمایی

- تست‌های متمرکز Cleanup (Integration + Unit) برابر `53 passed` هستند.
- کل Suite غیرزنده با MongoDB loopback برابر `1977 passed` با branch coverage
  `90.15%` (آستانه ۹۰) است.
- `uv lock --check`، `ruff check .`، `ruff format --check .`،
  `mypy src tests scripts` و تست‌های Release contract/version و package import
  موفق‌اند.
- راستی‌آزمایی `explain("executionStats")` روی Dataset Production-shaped با
  ۱۰۶٫۲۰۰ سند و همان indexهای Production (`v1` تا `v4`):
  - کلاس هرگز-بررسی‌نشده: `SORT -> OR -> FETCH -> IXSCAN(ix_media_cleanup_deferral_v3)`،
    `nReturned = 100`, `totalKeysExamined = 5202`, `totalDocsExamined = 5200`،
    بدون COLLSCAN.
  - کلاس retry موعد-رسیده:
    `LIMIT -> FETCH -> IXSCAN(ix_media_cleanup_fairness_v4)`،
    `nReturned = 100`, `totalKeysExamined = 100`, `totalDocsExamined = 100`،
    بدون SORT.
  - پلن تک‌پرسشی نسخهٔ قبل روی همان داده: SORT + دو FETCH، ۶٫۲۰۲ کلید و
    ۱۲٫۴۰۰ سند برای ۱۰۰ نتیجه؛ بنابراین تقسیم دو‌کلاسی هم سریع‌تر و هم
    قابل‌پیش‌بینی‌تر است.
- **Race audit حذف Approval:** مسیر باقی‌ماندهٔ حذف پیام پس از
  `approval_expired = True` فقط شناسه دارد: `CleanupExpiredApprovals._process_claim`
  ابتدا `expire_ui` را commit می‌کند و سپس فقط
  `ApprovalMessageDeleteGateway.delete_approval_message(chat_id, message_id)` را
  صدا می‌زند؛ پیاده‌سازی در `infrastructure/telegram/bot/adapter.py` آن را به
  `Bot.delete_message(chat_id, message_id)` نگاشت می‌کند. هیچ Storage، مسیر
  Media یا upload محلی در این مسیر نیست، پس پاک‌شدن هم‌زمان فایل محلی امن است.
  تحویل `completed` هم دوباره claim نمی‌شود، چون `claim_ready` فقط `pending`،
  `retry` موعد-رسیده و `claimed` با lease منقضی را می‌گیرد.
- **تصحیح فرمان Deployment:** `tabctl --instance X media cleanup` در همین
  Repository وجود دارد (به `media-cleanup` در Container route می‌شود و
  `tests/unit/deployment/test_tabctl.py::test_media_usage_dispatch_and_cleanup_routing`
  آن را پوشش می‌دهد)؛ اگر `tabctl` نصب‌شده روی Host قدیمی باشد و `media` را رد
  کند، فرمان معادل مستقیم در Container مستند شد.
- **تصحیح عددی:** تفاضل ۳٫۳۰۳ رکورد `expired_uncleaned` و ۳٫۲۵۴ رکورد blocked،
  در مجموع ۴۹ رکورد است و به‌صورت «۴۹ رکورد برای هر مسیر» بیان نمی‌شود.
- اثبات Regression: با بازگردانی موقت دو اصلاح،
  `test_cleanup_candidate_ordering_is_missing_first_then_due`،
  `test_bounded_worker_cycles_cannot_starve_never_attempted_candidates`
  (با `assert 0 == 50` یعنی هیچ رکورد پاک‌شدنی‌ای در چند Cycle محدود بررسی
  نشد)، `test_approval_reference_respects_explicit_approval_expiration
  [completed-approval-expired]` و
  `test_cleanup_releases_media_only_after_its_approval_expired` شکست خوردند و
  پس از بازگرداندن اصلاح دوباره موفق شدند.
- هیچ Tag، GitHub Release یا انتشار GHCR ساخته نشده و هیچ دادهٔ Production
  دستی حذف یا دست‌کاری نشده است.

## تعریف انجام‌شدن

هر دو نقص با Test معنادار روی MongoDB آزمایشی اثبات و رفع شده، سیاست انصاف صف
Candidates بدون تغییر سقف‌های Worker اعمال شده، هیچ مرجع محافظ دیگری تضعیف
نشده، نسخه و مستندات همگام است و تمام Gateهای قابل‌اجرا موفق‌اند.
