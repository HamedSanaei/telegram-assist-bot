# وضعیت فعلی

- **Current milestone:** Milestone 15 — Production Incident Hardening (Approval
  Outbox، Restart Contract و بازیابی Reboot).
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
- **پنجرهٔ Guard ترمیم:** هر pass فقط آماده‌های قدیمی‌تر از
  `now - approval_outbox_reconcile_guard_seconds` (پیش‌فرض `300`) را اسکن می‌کند، پس
  Marker تازه‌ای که نوشتن درون‌خطی‌اش شکست خورده همیشه جلوتر از watermark می‌ماند و
  در pass بعدی ترمیم می‌شود. هیچ تأییدیه‌ای گم نمی‌شود و اسکن همیشه bounded است.
- **بازیابی Reboot:** `deploy/systemd/telegram-assist-boot.service` (نوع `oneshot`)
  و `deploy/boot_recovery.sh` در لایهٔ Host هر Instance را با یک
  `docker compose up -d` ایدمپوتنت بالا می‌آورند؛ سیاست Compose بدون تغییر و بدون
  حلقهٔ بی‌پایان باقی می‌ماند.
- **Indexهای جدید:** `ix_approval_delivery_claim_v3`،
  `ix_approval_delivery_sync_v1` (partial) و `ix_content_preparation_readiness_v1`؛
  index قدیمی `ix_approval_delivery_claim_v2` در همان مسیر initialization حذف
  می‌شود.
- **Production verification (اندازه‌گیری‌شده):** دو نصب Production از همین Commit
  ساخته و Deploy شدند؛ `content_preparations` و `approval_deliveries` هر دو کامل و
  بدون حذف/بازنویسی داده باقی ماندند (`kingofilter` 32,325 و `mehrdadproxy`
  7,742 هویت). CPU در حالت idle از حدود ۴۰–۷۰٪ (approval-bot) و ۲۰–۸۸٪ (MongoDB)
  به ترتیب به ~۰٫۲–۰٫۹٪ و ~۱–۵٪ رسید؛ MongoDB نصب idle صفر insert/query/update در
  ۱۰ ثانیه نشان می‌دهد. Runtime نصب `mehrdadproxy` به‌خاطر `TelegramPremiumRequiredError`
  عمداً متوقف است: `exit=0`، `RestartCount=0` و یک event `startup_failed_permanently`
  با `failure_class=permanent` و `exit_code=4` (به‌جای ~۲۸٬۷۷۶ restart).
- **Next recommended action:** رفع Session نامعتبر نصب `mehrdadproxy` (Premium یا
  login دوباره) و سپس `docker compose up -d` همان Instance؛ پس از آن پایش دوره‌ای
  `created_count` مسیر Reconciliation و تعداد eventهای `startup_failed_permanently`.
- **Verification (this session):** Gate کیفیت GitHub Actions روی همین Commit
  (`uv lock --check`، Suite کامل با branch coverage روی MongoDB، `ruff`، `mypy`،
  `check_text_integrity.py --all`، `detect-secrets`، Build/Distribution و
  `Docker and installer acceptance`) سبز است. به‌صورت محلی، focused unit و integration تست‌های جدید،
  `ruff check`، `ruff format --check`، `mypy src tests scripts` و
  `check_text_integrity.py --all` و Suite کامل روی MongoDB آزمایشی loopback اجرا
  و موفق شدند؛ جزئیات عددی در فایل Task ثبت شده است.
