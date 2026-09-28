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