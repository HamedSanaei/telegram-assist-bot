# T100 — Approval Delivery Outbox و قرارداد Restart محدود

## وضعیت

Completed

## هدف

رفع دو نقص اثبات‌شدهٔ Production که یک VM با ۴ vCPU را به اشباع CPU و مصرف دائمی
شبکه رسانده بود:

1. **Restart storm:** یک خطای پیکربندی دائمی (`TelegramPremiumRequiredError`) با
   `restart: unless-stopped` حدود ۲۸٫۷۷۶ بار در چهار روز باعث restart بی‌پایان
   container می‌شد.
2. **CPU/DB amplification:** `MongoOperationalApprovalRepository.claim_ready()`
   در هر poll هر ۵ ثانیه تمام `content_preparations` آمادهٔ تاریخی را اسکن و
   برای هر کدام `insert_one`/`update_one` تلاش می‌کرد.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بندهای `5.12` تا `5.19`، `13`، `14` و `16`.

## وابستگی‌ها

- T061 (Runtime تأیید و انتشار عملیاتی).
- T066 (انصاف تحویل و پردازش پیوستهٔ صف).
- T079 (Cleanup پیام Approval و `approval_expired`).

هیچ Task ناتمامی باقی نمانده است.

## محدوده

- تفکیک صریح خطای Startup دائمی/غیرقابل‌retry از خطای گذرا و نگاشت آن به exit
  code پایدار جدید `4` همراه با یک event ساختاریافتهٔ CRITICAL.
- قرارداد container: entrypoint فقط برای خطای دائمی container را تمیز و بدون
  restart متوقف می‌کند و سیاست `on-failure:20` بقیهٔ خطاها را محدود و
  backoff-دار restart می‌کند.
- تبدیل polling تحویل approval به یک claim ارزان فقط روی `approval_deliveries`.
- ایجاد idempotent هویت outbox دقیقاً در مرز آماده‌شدن پایدار preparation
  (`PreparePostPipeline` بلافاصله پس از `mark_preparation_ready`).
- reconciliation محدود، watermark-محور و restart-safe برای آماده‌های legacy.
- indexهای لازم برای pending/retry/lease/sync و اسکن readiness، و حذف index
  قدیمی و بی‌استفادهٔ `ix_approval_delivery_claim_v2`.
- Healthcheck ارزان‌تر MongoDB و رفع reaping در container دیتابیس.
- تست‌های Regression و Performance شامل assert تعداد عملیات و query plan واقعی.
- به‌روزرسانی checkpoint `restore_runtime_check` در `scripts/v1_acceptance.sh`:
  fixture پذیرش Telegram Session واقعی ندارد، پس runtime آن لزوماً با خطای دائمی
  متوقف می‌شود. این checkpoint قبلاً فقط به‌دلیل crash-loop بی‌پایان
  `unless-stopped` سبز می‌شد؛ اکنون توقف تمیز با `exit code 0`، وجود event
  `startup_failed_permanently` و سقف `RestartCount` را تأیید می‌کند و هر
  بازگشت به restart بی‌پایان را fail می‌کند.

## خارج از محدوده

- تغییر semantics رخداد/lease/retry تحویل approval.
- افزایش `approval_delivery_poll_seconds` به‌عنوان راه‌حل.
- هر گونه حذف یا بازنویسی دادهٔ تاریخی در MongoDB Production.
- اعمال CPU/مموری limit در Compose (فقط مستندسازی توصیهٔ محافظه‌کارانه).
- افزایش نسخهٔ Package/Image و Tag/Release.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/shared/errors.py`
- `src/telegram_assist_bot/bootstrap/runtime.py`
- `src/telegram_assist_bot/bootstrap/text_ingestion.py`
- `src/telegram_assist_bot/bootstrap/approval_bot.py`
- `src/telegram_assist_bot/container_entrypoint.py` (جدید)
- `src/telegram_assist_bot/application/operational_approval.py`
- `src/telegram_assist_bot/application/ports/operational_approval.py`
- `src/telegram_assist_bot/application/ports/media.py`
- `src/telegram_assist_bot/application/prepare_post_pipeline.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/operational_approval_repository.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/content_repository.py`
- `src/telegram_assist_bot/shared/config/models.py` و
  `config/configuration.example.json`
- `Dockerfile` و `compose.yaml`

## نکات پیاده‌سازی

- classification فقط بر پایهٔ category اعلام‌شدهٔ خود خطا انجام می‌شود؛ wrapperهای
  Bootstrap معتبر نیستند و خطای ناشناخته همیشه transient می‌ماند تا مسیر بازیابی
  زیرساختی از دست نرود.
- `ensure_delivery` تنها با `$setOnInsert` روی `_id` می‌نویسد و هیچ حالت، پیشرفت
  یا status موجود را بازنویسی نمی‌کند؛ فقط فیلدهای ترتیب غایب رکوردهای pending
  legacy اصلاح می‌شوند.
- `reconcile_missing_deliveries` با watermark پایدار در
  `approval_outbox_state` و کلید `(ready_at, _id)` پیش می‌رود؛ ابتدای اسکن با
  `INITIAL_RECONCILIATION_WATERMARK` مرز واقعی دارد تا اولین batch هم روی index
  اجرا شود.
- چرخهٔ reconciliation در `approval-bot` با batch محدود، مکث بین batchها و
  interval ۳۰۰ ثانیه اجرا می‌شود؛ pass بدون کار هیچ eventی تولید نمی‌کند.
- ایندکس claim با ترتیب `(status, claim_due_at, created_at, _id, lease_until,
  next_attempt_at)` هر سه شاخهٔ `$or` را bounded نگه می‌دارد و SORT مسدودکننده
  ندارد.

## معیارهای پذیرش عینی

- خطای `TelegramPremiumRequiredError` در Startup باعث exit code `4`، یک event
  CRITICAL و توقف تمیز container بدون restart بی‌پایان می‌شود.
- خطای گذرا همان رفتار restart محدود را حفظ می‌کند.
- `claim_ready()` هیچ‌گاه `content_preparations` را نمی‌خواند و در dataset با
  تاریخ بزرگ فقط یک `findAndModify` روی `approval_deliveries` اجرا می‌کند.
- آماده‌شدن تازه یک preparation دقیقاً یک هویت outbox idempotent می‌سازد.
- آماده‌های legacy با batch محدود و watermark به‌صورت افزاینده backfill
  می‌شوند و pass تکراری هیچ scan/نوشت اضافه‌ای ندارد.
- ترتیب claim بر پایهٔ `claim_due_at`/`created_at`/`_id` حفظ می‌شود.
- Healthcheck MongoDB در حالت پایدار هر ۳۰ ثانیه اجرا می‌شود و container
  دیتابیس proccessهای healthcheck را reap می‌کند.

## Unit Testهای الزامی

- `tests/unit/infrastructure/persistence/test_operational_approval_outbox.py`
- `tests/unit/application/test_approval_outbox_reconciliation.py`
- `tests/unit/bootstrap/test_startup_failure_contract.py`
- `tests/unit/deployment/test_container_entrypoint.py`
- به‌روزرسانی `tests/unit/deployment/test_compose_contract.py`،
  `tests/unit/test_text_ingestion_bootstrap.py` و
  `tests/unit/test_approval_bot_runtime_bootstrap.py`.

## Integration Testهای الزامی

- `tests/integration/mongodb/test_approval_outbox_reconciliation.py` شامل
  شمارش واقعی Commandها و `explain` روی MongoDB آزمایشی.
- به‌روزرسانی `tests/integration/mongodb/test_operational_approval_runtime.py`.

## فرمان‌های راستی‌آزمایی

```bash
uv run pytest tests/unit/infrastructure/persistence/test_operational_approval_outbox.py
uv run pytest tests/unit/application/test_approval_outbox_reconciliation.py
uv run pytest tests/unit/bootstrap/test_startup_failure_contract.py
uv run pytest tests/unit/deployment -q
uv run pytest tests/integration/mongodb -q
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --all
```

## به‌روزرسانی‌های مستندات

`docs/ARCHITECTURE.md`، `docs/CODE_MAP.md`، `docs/DECISIONS.md`،
`docs/STATUS.md`، `docs/ROADMAP.md`، `docs/OPERATIONS.md` و `README.md`.

## نتیجهٔ راستی‌آزمایی

- Unit متمرکز: `tests/unit/infrastructure/persistence/test_operational_approval_outbox.py`
  `8 passed`، `tests/unit/application/test_approval_outbox_reconciliation.py`
  `7 passed`، `tests/unit/bootstrap/test_startup_failure_contract.py` `13 passed`،
  `tests/unit/deployment/test_container_entrypoint.py` `10 passed` و
  `tests/unit/deployment/test_compose_contract.py` `7 passed`.
- Integration متمرکز: `tests/integration/mongodb/test_approval_outbox_reconciliation.py`
  `7 passed` و `tests/integration/mongodb/test_operational_approval_runtime.py` روی
  MongoDB آزمایشی loopback موفق.
- Suite کامل (اعتبارسنجی نهایی یک‌بار): `2030 passed` با branch coverage `90.24%`
  (آستانه ۹۰) روی `TEST_MONGODB_URI` loopback و بدون test skip.
- `ruff check .`، `ruff format --check .`، `mypy src tests scripts`،
  `check_text_integrity.py --all` (۵۶۳ فایل)، `uv lock --check`،
  `detect-secrets-hook`، `uv build --no-build-isolation`،
  `check_distribution.py dist` و `git diff --check` موفق.
- `docker compose config` با Docker `29.7.2` / Compose `v5.3.1` بدون خطا resolve
  شد و `restart: on-failure:20`، `interval: 30s`، `start_interval: 2s` و
  `init: true` را تأیید کرد.
- علت شکست اولیهٔ Gate پذیرش به‌صورت محلی بازتولید شد: بدون Session معتبر،
  `runtime` عمداً `startup_failed_permanently` با `exit code 4` تولید می‌کند و
  entrypoint آن را به توقف تمیز نگاشت می‌کند؛ پس Container واقعاً متوقف می‌ماند و
  چک قدیمی «runtime باید در حال اجرا باشد» فقط با crash-loop سبز می‌شد.
  Backtest `bash -n scripts/v1_acceptance.sh` و
  `tests/unit/deployment -q` (`82 passed`) موفق است.
- هیچ دادهٔ Production حذف یا بازنویسی نشده و هیچ Tag/Release ساخته نشده است.

## تکمیل — پنجرهٔ Guard، بازیابی Reboot و Audit حلقه‌ها

### پنجرهٔ Guard ترمیم (کد جدید این مرحله)

حذف اسکن تاریخی از `claim_ready()` یک شکاف دوام ایجاد می‌کرد: اگر نوشتن درون‌خطی
هویت تحویل (`ReadyApprovalOutbox.ensure_delivery`) بلافاصله پس از آماده‌شدن شکست
می‌خورد، دیگر هیچ اسکن دوره‌ای آن را ترمیم نمی‌کرد و watermark جلوتر از آن رکورد
می‌ماند. اکنون `reconcile_missing_deliveries` فقط آماده‌های قدیمی‌تر از
`now - approval_outbox_reconcile_guard_seconds` را اسکن می‌کند:

- watermark هرگز داخل پنجرهٔ اخیر جلو نمی‌رود، پس Marker تازه جلوتر از آن می‌ماند و
  در یکی از passهای بعدی ساخته می‌شود.
- اگر guard بزرگ‌تر شود و watermark داخل/جلوتر از پنجرهٔ جدید بیفتد، همان pass
  یک‌بار watermark را به مرز پنجره برمی‌گرداند؛ بنابراین پنجرهٔ جدید یک‌بار (و
  bounded) بازبینی می‌شود و هیچ‌گاه بی‌نهایت اسکن نمی‌شود.
- passهایی که فقط پنجره را re-verify می‌کنند و هویتی نمی‌سازند هیچ eventی ثبت
  نمی‌کنند، پس لاگ در حالت پایدار ساکت می‌ماند.

### Audit حلقه‌های Polling (کل Repository)

هر حلقهٔ `while True`/`asyncio.sleep` بررسی شد و کار هر poll در حالت بی‌کار
طبقه‌بندی شد: `ApprovalDeliveryLoop` یک `findAndModify` روی `approval_deliveries`؛
approval sync یک claim partial روی `sync_required`؛ AI worker یک claim ایندکس‌شده روی
`ai_jobs`؛ scheduled publication یک claim ایندکس‌شده روی scheduleها؛ album finalizer
یک claim روی `ix_media_group_finalization_v1`؛ approval cleanup batchهای محدود؛
live listener رویدادمحور و بدون کار DB. هیچ حلقهٔ دیگری الگوی اسکن/نوشتن تاریخی
ندارد؛ جدول کامل در `docs/ARCHITECTURE.md` ثبت شده است.

### بازیابی Reboot (لایهٔ Host)

`restart: on-failure:20` عمداً در Restart خودکار Docker Daemon شرکت نمی‌کند، پس
Recovery در لایهٔ Host اضافه شد: `deploy/systemd/telegram-assist-boot.service`
(نوع `oneshot`) و `deploy/boot_recovery.sh` که برای هر Instance دارای `compose.yaml`
یک `docker compose up -d` ایدمپوتنت می‌زند؛ بدون `--force-recreate`، بدون حذف
Volume و بدون تغییر سیاست Restart. روی سرور Production فعال و یک‌بار اجرا شد
(`Result=success`، `ExecMainStatus=0`، «2 instance(s) started, 0 failed») و
Containerهای در حال اجرا دست‌نخورده ماندند.

### راستی‌آزمایی Production (پس از Deploy)

| متریک | قبل | بعد |
|---|---:|---:|
| Host load average (۱ دقیقه) | ~۸ | ۳٫۰۶ |
| approval-bot CPU | ۴۰–۷۰٪ | ۰٫۲۲–۰٫۹۴٪ |
| MongoDB CPU | ۲۰–۸۸٪ | ۰٫۸۳–۴٫۸٪ |
| MongoDB ops نصب idle (۱۰s) | هزاران insert/update | `insert=0 query=0 update=0` |
| MongoDB connections | ثبت نشده | ۱۲–۱۹ |
| Runtime `RestartCount` | ~۲۸٬۷۷۶ در چهار روز | ۰ (همهٔ Containerها) |

- تصویر Deployشده `revision=e4abbd0509a774082d0a72ddc645dc52cad96a1a` است و
  Containerهای `mehrdadproxy` یک event `startup_failed_permanently` با
  `failure_class=permanent`، `error_category=authorization`،
  `failure_type=TelegramPremiumRequiredError`، `exit_code=4` و سطح `CRITICAL` ثبت
  کردند؛ سپس تمیز با `exit=0` و بدون restart متوقف ماندند.
- Backfill بدون حذف داده کامل شد: `content_preparations` و `approval_deliveries`
  هر دو ۳۲٬۳۲۵ (kingofilter) و ۷٬۷۴۲ (mehrdadproxy) و سند watermark در
  `approval_outbox_state` پیشرفته است.

## تعریف انجام‌شدن

مسیر polling تحویل approval دیگر هیچ کاری به اندازهٔ تاریخ `content_preparations`
انجام نمی‌دهد، خطای دائمی Startup دیگر restart storm نمی‌سازد، semantics
دوام/retry/lease حفظ شده و همهٔ Gateهای قابل‌اجرا موفق‌اند.
