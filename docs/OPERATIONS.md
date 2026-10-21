# Operations — Advanced CLI Reference and Command Map

این سند برای توسعه‌دهندگان و اتوماسیون است. کاربر عادی Production باید از منوی
تعاملی استفاده کند:

```bash
tabctl
```

`tabctl` بدون آرگومان منوی Bash را باز می‌کند؛ با آرگومان، دقیقاً همان فرمان
های غیرتعاملی قبلی را اجرا می‌کند (Backward compatible).

## نقشهٔ فرمان‌ها — قدیم → منو

| عملیات | فرمان قدیمی | مکان در منو |
|---|---|---|
| شروع/توقف/restart همه | `manage.sh start/stop/restart` یا `tabctl --instance X start` | ۱. Service Management |
| شروع/توقف/restart یک سرویس | `docker compose ... stop runtime` | ۱ → گزینه‌های ۶–۸ |
| Recreate | `docker compose up -d --force-recreate` | ۱ → ۵ و ۱۳ → ۴ |
| وضعیت | `tabctl --instance X status` | ۱ → ۴/۱۰ و ۸ |
| Validation Config | `tabctl --instance X config check` | ۱ → ۱۱ و ۷ → ۹ |
| Telegram login | `manage.sh login` | ۲. Telegram Session |
| وضعیت Session | (جدید) `tabctl --instance X session status` | ۲ → ۲ |
| Reset Session | (جدید، مخرب) `tabctl session reset --yes` | ۲ → ۳ |
| Bot Token | ویرایش دستی `.env` | ۳ → ۱ (hidden input) |
| Approval Chat | ویرایش دستی Config | ۳ → ۳ و ۷ → ۷ |
| مدیران | `tabctl admin add/remove/enable/disable` | ۳ → ۴ و ۶ |
| کانال‌های منبع | `tabctl source add/remove/enable/disable` | ۴ |
| مقصدها | `tabctl destination add/remove/enable/disable` | ۵ |
| Timezone/Preview/Cleanup interval | ویرایش دستی Config | ۷ → ۲/۴/۵ |
| Retention | `tabctl retention set N` | ۷ → ۳ و ۱۱ → ۳ |
| Logging level | (جدید) `tabctl config set logging LEVEL` | ۷ → ۶ |
| Logs | `tabctl logs --service X --tail N` | ۹ |
| Diagnostics export | `tabctl diagnostics export` | ۹ → ۸ و ۱۶ → ۲ |
| Publication/Approval queue | `publication-queue` / `approval-queue` | ۱۰ |
| Recoveryها | `publication-recover-*` / `approval-retry` و غیره | ۱۰ → ۷–۹ (dry-run اول) |
| Media usage/cleanup | (جدید) `tabctl media usage/cleanup` | ۱۱ → ۱/۲ |
| Media reset (مخرب) | — | ۱۱ → ۸ |
| Backup | `tabctl backup create/list/verify/restore` | ۱۲ |
| Export/Import archive | (جدید) `tabctl backup export/import` | ۱۲ → ۹/۱۰ |
| Docker images/pull/prune ایمن | `docker ...` دستی | ۱۳ |
| Update/Rollback | `tabctl update --version X / --rollback` | ۱۴ |
| Instanceها | `tabctl instance list/import` | ۱۵ |
| Repair/Doctor | `tabctl repair --dry-run/--apply` | ۱۶ |
| Uninstall/Purge | `tabctl uninstall/purge --yes` | ۱۷ |

هر توانایی قدیمی که در منو نیست، همچنان به‌صورت CLI در دسترس است؛ هیچ قابلیتی
عمداً حذف نشده است.

## Backup و Restore

### حالت‌ها

- `core`: `configuration.json`، `instance.json` (metadata بدون Secret) و
  `mongodb.archive.gz` (dump سازگار mongodump).
- `full` (پیش‌فرض): core + `.env` + `compose.yaml` + `session.tar.gz` +
  `media.tar.gz`. با `--exclude-media` می‌توان Media را حذف کرد.
- `--encrypt`: هر فایل مؤلفه با `openssl enc -aes-256-cbc -pbkdf2 -iter 200000`
  رمز می‌شود؛ passphrase از `TAB_BACKUP_PASSPHRASE` یا prompt مخفی خوانده
  می‌شود و هرگز ذخیره/چاپ نمی‌شود. Manifest readable شامل
  `encrypted: true`، الگوریتم و checksumهای stored و plaintext است.

### Restore

- پیش از restore: verify (schema + checksum + رمزگشایی در صورت نیاز).
- عدم تطابق نام instance بدون `--to-instance` رد می‌شود.
- `--to-instance NAME`: فقط Config، Session، Media و MongoDB بازمی‌گردد؛
  `.env`، `compose.yaml` و هویت مقصد حفظ می‌شوند (`env_skipped` چاپ می‌شود).
- پیش از restore، MongoDB بالا می‌آید و یک پیش‌backup core گرفته می‌شود؛ در
  شکست، فایل‌ها به بایت اول rollback و backup پیشین معرفی می‌شود.
- بعد از restore: `up -d` و `runtime check` برای health اجرا می‌شود.

### جابجایی سرور

```bash
tabctl --instance X backup create            # full migration backup
tabctl --instance X backup export BACKUP_ID  # یک archive .tar.gz
# در سرور جدید:
bash <(curl -fsSL https://raw.githubusercontent.com/HamedSanaei/telegram-assist-bot/main/install.sh)
tabctl --instance default backup import --file backup-....tar.gz
tabctl --instance default backup restore BACKUP_ID --yes
```

Archiveها و passphraseها حساس‌اند: انتقال امن و حذف پس از restore.

## CLI پیشرفته (اتوماسیون)

```bash
tabctl                                            # منوی تعاملی
tabctl status --json                              # وضعیت ساختاریافته (بدون Secret)
tabctl --instance X session status                # state=present|absent|unavailable
tabctl --instance X service restart runtime       # start|stop|restart|recreate
tabctl --instance X queue inspect --kind approval --status retry
tabctl --instance X queue cancel --job-id ID
tabctl --instance X queue recover immediate --approval-post-id ID --dry-run
tabctl --instance X media usage
tabctl --instance X media cleanup                 # یک batch پاک‌سازی امن مرجع‌آگاه
printf '%s\n' "$TOKEN" | tabctl --instance X env set TAB_TELEGRAM_BOT_TOKEN
tabctl --instance X config set timezone Asia/Tehran
tabctl --instance X config set preview true
tabctl --instance X config set cleanup-interval 1800
tabctl --instance X backup create --mode core --encrypt
TAB_BACKUP_PASSPHRASE=... tabctl --instance X backup verify ID
```

خروجی‌های `status --json`، `backup verify` و `diagnostics` JSON هستند؛
`session status` و `media usage` خطوط `key=value` چاپ می‌کنند.

## پاک‌سازی Media و صف منصفانه

Cleanup فقط Media منقضی و بی‌مرجع را حذف می‌کند. انتخاب Candidate دو کلاس صریح
دارد: ابتدا Mediaهای منقضی که هیچ تلاش پاک‌سازی نداشته‌اند و سپس retryهای
موعد-رسیده. بنابراین Mediaهای blocked نمی‌توانند Mediaهای منقضی بعدی را برای
همیشه از صف خارج کنند. هر retry پس از `media.cleanup_defer_seconds` دوباره
واجدشرایط می‌شود و لاگ هر batch فیلد `reference_deferred` را جدا از `deferred`
گزارش می‌کند تا قفل‌شدن مرجع از پیشرفت عادی قابل تشخیص باشد. هر `media cleanup`
یک batch محدود پردازش می‌کند؛ برای تخلیهٔ صف بزرگ چند اجرا یا انتظار چند Cycle
Worker لازم است و هیچ حذف دستی لازم نیست.

`tabctl --instance X media cleanup` فقط در نسخه‌های این Repository که subcommand
`media` را دارند موجود است. اگر `tabctl` نصب‌شده روی Host قدیمی‌تر باشد و
`media` را رد کند، همان یک batch را مستقیماً در Container در حال اجرا اجرا
کنید:

```bash
RUNTIME="$(docker ps \
  --filter label=com.docker.compose.service=runtime \
  --filter status=running --format '{{.Names}}' | head -n 1)"
docker exec "$RUNTIME" /app/.venv/bin/python -m telegram_assist_bot \
  media-cleanup --config /app/config/configuration.json
```

همین فرمان با `media-cleanup-worker` نسخهٔ دوره‌ای را اجرا می‌کند؛ Service
`media-cleanup-worker` نیز در Compose همین فرمان را اجرا می‌کند.

## قرارداد Restart و خطای Startup دائمی

سه Service برنامه (`runtime`، `approval-bot`، `media-cleanup-worker`) با سیاست
محدود `restart: on-failure:20` اجرا می‌شوند و Image نیز از
`telegram_assist_bot.container_entrypoint` بالا می‌آید. رفتار قابل انتظار:

| نوع خطا | exit code برنامه | نتیجه در Container |
|---|---|---|
| موفقیت | `0` | توقف عادی |
| Config نامعتبر | `2` | restart محدود (خطای قابل اصلاح) |
| زیرساخت/شبکه گذرا | `3` | restart محدود با backoff |
| خطای دائمی و غیرقابل‌retry | `4` | توقف تمیز و بدون restart |

خطای دائمی (برای نمونه اکانت Telegram بدون Premium، Authorization نامعتبر یا
خطای اعلام‌شدهٔ `permanent`/`validation`/`configuration`/`authorization`)
دقیقاً یک event ساختاریافتهٔ `startup_failed_permanently` در سطح `CRITICAL` با
`failure_class`، `failure_category`، `failure_type` و `exit_code` ثبت می‌کند و
Container بدون تکرار بی‌پایان متوقف می‌ماند. خطاهای گذرا با
`startup_failed_transient` در سطح `ERROR` ثبت می‌شوند و مسیر restart و بازیابی
را حفظ می‌کنند. هیچ‌یک از این eventها مقدار Secret ندارد.

خطاهای ناشناخته همیشه transient در نظر گرفته می‌شوند تا بازیابی زیرساختی از دست
نرود؛ فقط categoryی که خود خطا اعلام می‌کند مبنای تصمیم است. بنابراین یک
`TelegramPremiumRequiredError` هرگز restart storm نمی‌سازد.

نکتهٔ عملیاتی: سیاست `on-failure` در Restart خودکار Docker Daemon شرکت نمی‌کند.
پس از Reboot سرور، Serviceها را با `tabctl --instance X start` یا
`docker compose up -d` بالا بیاورید. وضعیت یک Container متوقف‌شدهٔ عمدی با
`tabctl --instance X status` و event `container_terminal_startup_failure`
قابل تشخیص است؛ پس از رفع علت (مثلاً Premium یا Config) یک start دستی کافی است.

### بازیابی خودکار پس از Reboot (سطح Host)

برای اینکه Reboot سرور به Restart دستی نیاز نداشته باشد و در عین حال قرارداد
محدود `on-failure:20` ضعیف نشود، بازیابی در لایهٔ Host و خارج از Compose انجام
می‌شود. Unit آماده در `deploy/systemd/telegram-assist-boot.service` و اسکریپت
`deploy/boot_recovery.sh` قرار دارند و فقط برای هر Instance یک
`docker compose up -d` ایدمپوتنت اجرا می‌کنند (بدون `--force-recreate`، بدون
حذف Volume و بدون تغییر سیاست Restart):

```bash
sudo install -m 0755 deploy/boot_recovery.sh /usr/local/bin/telegram-assist-boot-recovery
sudo install -m 0644 deploy/systemd/telegram-assist-boot.service \
  /etc/systemd/system/telegram-assist-boot.service
sudo systemctl daemon-reload
sudo systemctl enable telegram-assist-boot.service

# اجرای دستی همان مسیر بدون Reboot (اختیاری)
sudo TAB_INSTANCE_ROOT=/opt/telegram-assist-bot/instances \
  /usr/local/bin/telegram-assist-boot-recovery
```

رفتار مورد انتظار: Containerهای در حال اجرا دست‌نخورده می‌مانند، Containerهای
متوقف (از جمله توقف عمدی به‌دلیل خطای دائمی) یک‌بار در هر Reboot بالا می‌آیند و
در صورت تداوم خطای دائمی دوباره تمیز متوقف می‌شوند؛ یعنی بازیابی Reboot وجود دارد
ولی حلقهٔ بی‌پایان برنمی‌گردد. مسیر Instanceها با متغیر
`TAB_INSTANCE_ROOT` قابل تغییر است.

## Reconciliation هویت‌های Outbox تحویل

هویت تحویل هر Post دقیقاً در لحظهٔ آماده‌شدن پایدار در `approval_deliveries`
ساخته می‌شود و polling تحویل فقط همین Collection را می‌خواند. برای نصب‌های
قدیمی که آماده‌های بدون هویت دارند، `approval-bot` یک catch-up محدود و
watermark-محور اجرا می‌کند:

| کلید Config | پیش‌فرض | توضیح |
|---|---|---|
| `telegram.bot.approval_outbox_reconcile_batch_size` | `200` | سقف رکورد در هر batch (۱ تا ۱۰۰۰) |
| `telegram.bot.approval_outbox_reconcile_interval_seconds` | `300` | فاصلهٔ بین passها (۳۰ تا ۳۶۰۰) |
| `telegram.bot.approval_outbox_reconcile_pause_seconds` | `2` | مکث بین batchها در یک catch-up بزرگ |
| `telegram.bot.approval_outbox_reconcile_guard_seconds` | `300` | پنجرهٔ re-verify دنباله؛ watermark هرگز داخل این پنجره جلو نمی‌رود |

`approval_outbox_reconcile_guard_seconds` یک پنجرهٔ امنیتی است: هر آماده‌شدنی
که جدیدتر از `now - guard` باشد در Reconciliation فعلی اسکن نمی‌شود و فقط وقتی
از پنجره بیرون رفت اجازهٔ اسکن می‌گیرد. اگر نوشتن درون‌خطی هویت تحویل بلافاصله
پس از آماده‌شدن شکست بخورد، همان Marker همچنان جلوتر از watermark می‌ماند و در
یکی از passهای بعدی ترمیم می‌شود؛ پس هیچ تأییدیه‌ای گم نمی‌شود و در عین حال اسکن
همیشه bounded می‌ماند. اگر مقدار guard بزرگ‌تر شود و watermark داخل/جلوتر از
پنجرهٔ جدید بیفتد، همان pass یک‌بار watermark را به مرز پنجره برمی‌گرداند و از
آن نقطه به بعد پنجره را می‌بیند.

پیشرفت در سند `content_preparation_outbox` در Collection `approval_outbox_state`
ذخیره می‌شود، پس restart دوباره از ابتدا اسکن نمی‌کند و هیچ pass دوره‌ای کلیدی
جز کلیدهای تازه‌آماده‌شده نمی‌بیند. لاگ این مسیر فقط aggregate است
(`scanned_count`، `created_count`، `existing_count`، `watermark`، `duration_seconds`)
و یک pass بدون کار هیچ eventی ثبت نمی‌کند. هیچ‌گاه داده تاریخی MongoDB را دستی
پاک یا بازنویسی نکنید؛ Backfill از همین مسیر انجام می‌شود.

## توصیهٔ Resource Guardrails

خدمات پس‌زمینه به‌طور پیش‌فرض هیچ CPU/Memory/PID محدودیتی ندارند و Compose این
Repository نیز محدودیت اعمال نمی‌کند. علت اصلی حادثهٔ CPU اشباع، الگوریتم polling
بود و نه نبود limit؛ بنابراین limit جایگزین رفع باگ نیست. اگر پس از رفع باگ،
سخت‌گیرانه‌تر کردن مرزها لازم بود، از یک override محلی استفاده کنید و مقادیر را
محافظه‌کارانه نگه دارید:

```yaml
# compose.override.yaml (اختیاری، فقط در همان Host)
services:
  runtime:
    cpus: "2.0"
    mem_limit: 2g
    pids_limit: 512
  approval-bot:
    cpus: "2.0"
    mem_limit: 1536m
    pids_limit: 512
  media-cleanup-worker:
    cpus: "1.5"
    mem_limit: 1g
    pids_limit: 256
```

این اعداد فقط توصیه‌اند: مقادیر کوچک‌تر از ظرفیت واقعی می‌تواند پردازش Media،
تحویل Telegram یا `runtime check` را در burstهای قانونی کند یا متوقف کند. سقف
PID تنها زمانی مفید است که ده‌ها Process موازی غیرمنتظره وجود داشته باشد؛
صورت‌حساب اصلی همیشه تعداد عملیات MongoDB و الگوریتم پردازش است. MongoDB را
محدود نکنید مگر اندازهٔ مجموعه و الگوی دسترسی آن به‌صورت مستقل اندازه‌گیری شده
باشد؛ محدودیت تنگ روی MongoDB کل سرویس را از کار می‌اندازد.

Healthcheck دیتابیس هر ۳۰ ثانیه (`start_interval: 2s` فقط در بازهٔ Startup) یک
`mongosh ping` کوتاه اجرا می‌کند و `init: true` نیز Processهای کوتاه‌عمر را
reap می‌کند تا Zombie انباشته نشود. اگر Host شما Docker قدیمی‌تر از پشتیبانی
رسمی این Repository دارد، پیش از Deploy با `tabctl doctor` سازگاری را بررسی کنید.

## ایمنی و مخرب‌ها

- هیچ فرمانی `docker volume prune` یا `docker system prune` را اجرا نمی‌کند.
- حذف Image فقط روی repository همان پروژه و با تأیید است.
- `media clear`، `session reset`، `purge`، `backup delete` و restore روی
  instance موجود، تأیید صریح (در منو: تایپ نام instance) می‌خواهند.
- Restore/Backup شامل Session و `.env` است؛ دسترسی فایل‌ها `0600` است.

## Troubleshooting سریع

| نشانه | اقدام |
|---|---|
| `tabctl` منو باز نمی‌شود | `bash -n install.sh` و نصب مجدد با `install.sh --update` |
| Docker در دسترس نیست | منوی ۱۶ Doctor؛ رفع دسترسی و `logout/login` |
| Config نامعتبر | `tabctl --instance X config check` |
| Login ناقص | منوی ۲: `stop`، سپس login، سپس `start` |
| Container متوقف شده و event `startup_failed_permanently` دارد | علت دائمی را رفع کنید (مثلاً Premium یا Authorization)، سپس سرویس را با `tabctl --instance X start` بالا بیاورید |
| Container پر `restart` است | `tabctl --instance X logs --tail 200` و جست‌وجوی `startup_failed_transient`/`startup_failed_permanently` برای تفکیک خطای گذرا از دائمی |
| Backup خراب | `tabctl --instance X backup verify ID` پیش از restore |
| Update ناموفق | `tabctl --instance X update --rollback` |

## Background recovery and cleanup shutdown (T101)

- `approval_background_task_retry` records the task, safe failure class/type,
  attempt and backoff. After three lifetime starts, or a declared permanent
  error, `approval_background_task_suspended` is CRITICAL. The Bot keeps polling;
  fix the cause and use `tabctl --instance X service restart approval-bot`.
- `approval_sync_content_unavailable` warns that an absent original post was
  retired with an atomic guard (`terminal=true`); `terminal=false` means a newer
  version, owner or lease superseded that claim. No publication history is deleted.
  Inspect the additive `sync_terminal_reason`/`sync_terminal_at` fields read-only.
  Restoring content does not automatically re-enable a retired approval.
- `media_cleanup_worker_started` records the configured interval/batch limit.
  `media_cleanup_cycle_failed` records safe retry metadata; cycles wait 5/10
  seconds before retry and exit 3 after exhaustion. Docker retains `on-failure:20`.
- `media_cleanup_worker_stopped` now records `shutdown_reason`, `signal_number`
  and `exit_code`. The reported legacy incident (four completed cycles, exit 0,
  no OOM and no restarts) was a graceful stop, consistent with signal shutdown;
  the old logs cannot identify the sender. SIGINT/SIGTERM now drain and exit 3,
  making external signals restartable. An explicit Compose stop suppresses
  Docker restart; an injected application stop event exits 0.
- No live instance, session or volume is modified by this patch. Deploy the
  verified image and recreate the affected services using the normal update
  workflow; do not delete volumes to apply this fix.

## History CPU incident recovery (T102)

### Runtime behavior and observability

- `history_crawl_summary` reports created/existing/skipped-completed messages,
  failed/oversized media, retry/page/acknowledgement counts, duration, process CPU,
  RSS/peak RSS and Mongo command attempts. Process/DB/stage deltas can include
  concurrent live work; they are not exclusive channel or container measurements.
- `history_crawl_continuation` advances a page-limited source from its durable
  cursor after giving other sources a turn. `history_crawl_failed` isolates a
  source after bounded operation retries; `history_crawl_suspended` indicates no
  durable progress, not an automatic whole-history replay.
- Media dispositions are in `content_preparations` documents prefixed
  `media-operation:`. `media_operation.category` is `oversized`, `permanent` or
  `retry_exhausted` for terminal operations. Metadata includes identity, item,
  filename/type/group, declared/observed size, maximum, attempts and last failure
  category. Source text/entities are not logged.
- A sudden process death can leave a one-hour ingestion lease. Graceful shutdown
  releases it; do not clear another worker's live lease manually. Historical
  albums remain held until their snapshot finishes; newer live albums are not
  held by an older snapshot.

### Explicit media retry

Policy changes do not reopen rejected media. After reviewing the rejection and
policy, a trusted local operator can queue exactly one persisted source message:

```bash
python -m telegram_assist_bot media-retry \
  --config /app/config/configuration.json \
  --source-channel-id -1001648659094 --source-message-id 68568 \
  --admin-user-id AUTHORIZED_CONFIGURED_ADMIN_ID
```

This command opens MongoDB only, never Telegram or a runtime/session owner. The
actor must be active, have `approval.toggle`, and be allowed on every enabled
destination routed from that enabled source. The ID is a local-operator audit
identity, not Telegram identity authentication; do not expose this CLI through an
untrusted remote endpoint. Retry audit preserves the original rejection and
actor/time. A future FloodWait cannot be shortened by reset. The bounded recovery
worker consumes the saved original DTO after an authorized runtime start,
including older-day messages, provided the original Post/snapshot has not expired.
An expired original cannot be retried by this command.

### Additive database migration

No manual data rewrite, collection deletion, media deletion or volume migration.
First authorized startup creates:

- `history_checkpoints` and `history_failures`, each keyed by unique `_id`.
- `ix_media_operation_recovery_v1`, `ix_ingestion_recovery_v1`,
  `ix_history_album_hold_v1` on `content_preparations`.
- `ttl_ingestion_message_snapshot_v1` on `ingestion_snapshot_expires_at`,
  `expireAfterSeconds=0`. Only new dedicated `ingestion-message:<post_id>` source
  snapshots carry this date, exactly equal to the canonical Post expiry.
  Existing preparation, rejection audit and publication history never receive
  this TTL field.

Existing completed preparations get their durable outbox acknowledgement repaired
on first encounter, then skip expensive stages. Legacy incomplete Posts without
a saved DTO are recovered when eligible history/live redelivers them; this patch
cannot reconstruct old missing media descriptors from text-only legacy Posts.

The cleanup reader must understand `ingestion_completed` on non-anchor album
members. Older cleanup images can retain those files indefinitely because the
members deliberately have no `ready_at`. Plan an explicitly authorized update of
`media-cleanup-worker` to the same patch as runtime; no approval-bot or MongoDB
recreation is required. Do not add fake readiness markers to work around an old
cleanup image.

### Deployment and rollback — explicit approval required

The incident runtime must remain stopped with restart policy `no` until the owner
authorizes deployment **and** restart. Do not run a whole-stack installer/update,
boot recovery, `compose up -d` without a service name, or change unrelated restart
policies. No production command was executed during implementation.

After authorization:

1. Publish the verified source through CI under a new immutable image tag/digest;
   do not overwrite deployed `1.1.4`. Record the currently deployed image digest,
   runtime command/mounts and restart policy. Back up MongoDB and the matching
   configuration/session/media using the existing backup procedure.
2. In `/opt/telegram-assist-bot/instances/admin1`, prepare a reviewed Compose
   override containing only `services.runtime.image` (the new digest),
   `services.runtime.restart: "no"` and the same patched
   `services.media-cleanup-worker.image`. Validate it with `docker compose
   --env-file .env -f compose.yaml -f runtime-t102.override.yaml config --quiet`.
3. Only after explicit deployment/restart approval for these two related workers,
   run `docker compose --env-file .env -f compose.yaml -f runtime-t102.override.yaml
   up -d --no-deps runtime media-cleanup-worker`. This must not recreate
   `telegram-assist-kingofilter-mongodb-1`,
   `telegram-assist-kingofilter-approval-bot-1`, or unrelated containers.
4. Check the runtime's source summaries, source `-1001648659094` / message `68568`
   rejection, other-channel progress and checkpoint completion. Observe `docker
   stats --no-stream telegram-assist-kingofilter-runtime-1` repeatedly while idle;
   confirm no repeated media opens or whole-source failures. Verify live
   ingestion, album completeness, approval availability and publication claims.
   Leave `restart: "no"` during observation; restoring the normal bounded policy
   requires a separately reviewed operational decision.
5. On regression, stop **only** the runtime, keep `restart: "no"`, select the
   recorded previous image digest and recreate **stopped** with `docker compose
   --env-file .env -f compose.yaml -f runtime-t102.override.yaml create --no-deps
   runtime`. Do not drop additive state or restore whole volumes merely to roll
   back code. `1.1.4` still contains the original crawl defect: do not restart it
   against the incident source without a separately approved mitigation.
   Keep the compatible cleanup worker patched when rolling back only runtime.
   Rolling cleanup back too can restore indefinite album-member retention.
