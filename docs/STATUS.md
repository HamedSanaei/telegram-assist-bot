# وضعیت فعلی

- **Current milestone:** Milestone 15 — Production Incident Hardening (Approval
  Outbox و Restart Contract).
- **Active task:** None.
- **Last completed task:** [T100 — Approval Delivery Outbox و قرارداد Restart
  محدود](tasks/T100-approval-outbox-and-bounded-restart-contract.md).
- **Known blockers:** None.
- **Failing tests:** None.
- **Branch:** `main`.
- **Last verified commit before this work:** `6c797c5` (`chore(release): prepare
  v1.1.4`) روی `main`.
- **Root causes رفع‌شده (Production evidence):**
  1. `MongoOperationalApprovalRepository.claim_ready()` در هر poll هر ۵ ثانیه کل
     `content_preparations` آماده را اسکن و برای هر رکورد `insert_one`/`update_one`
     تلاش می‌کرد؛ هزینهٔ idle poll متناسب با تاریخ بود، نه کار واقعی.
  2. خطای Startup دائمی `TelegramPremiumRequiredError` با `restart:
     unless-stopped` حدود ۲۸٬۷۷۶ restart در چهار روز ایجاد می‌کرد، چون Docker
     کلاس خطا را نمی‌شناخت.
  3. Healthcheck دیتابیس هر ۵ ثانیه یک Process کامل `mongosh` می‌ساخت.
- **اقدام‌های معماری:** هویت durable تحویل دقیقاً در مرز آماده‌شدن پایدار ساخته
  می‌شود (`ReadyApprovalOutbox.ensure_delivery`) و `claim_ready()` فقط یک
  `findAndModify` ایندکس‌شده روی `approval_deliveries` است؛ آماده‌های legacy با
  `ApprovalOutboxReconciliationLoop`، watermark پایدار در `approval_outbox_state` و
  batch محدود backfill می‌شوند. خطای Startup دائمی با exit code `4` و event
  `startup_failed_permanently` به توقف تمیز Container نگاشت می‌شود و سیاست
  `restart: on-failure:20` فقط خطاهای گذرا را با backoff محدود restart می‌کند.
- **Indexهای جدید:** `ix_approval_delivery_claim_v3`،
  `ix_approval_delivery_sync_v1` (partial) و `ix_content_preparation_readiness_v1`؛
  index قدیمی `ix_approval_delivery_claim_v2` در همان مسیر initialization حذف
  می‌شود.
- **Production state (reported):** هیچ داده‌ای در MongoDB Production حذف یا دستی
  بازنویسی نشده است؛ backfill خودکار است. دو نصب Production با CPU اشباع داشتند و
  پس از Deploy نسخهٔ جدید به restart سرویس‌ها (و در صورت Reboot، `tabctl start`)
  نیاز دارند.
- **Next recommended action:** Deploy نسخهٔ جدید روی دو نصب Production، سپس
  مقایسهٔ CPU/ترافیک MongoDB در حالت idle و بررسی نبود event
  `startup_failed_permanently` ادامه‌دار.
- **Verification (this session):** Gate کیفیت GitHub Actions روی همین Commit
  (`uv lock --check`، Suite کامل با branch coverage روی MongoDB، `ruff`، `mypy`،
  `check_text_integrity.py --all`، `detect-secrets`، Build/Distribution و
  `Docker and installer acceptance`) سبز است. به‌صورت محلی، focused unit و integration تست‌های جدید،
  `ruff check`، `ruff format --check`، `mypy src tests scripts` و
  `check_text_integrity.py --all` و Suite کامل روی MongoDB آزمایشی loopback اجرا
  و موفق شدند؛ جزئیات عددی در فایل Task ثبت شده است.
