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
tabctl --instance X media cleanup                 # پاک‌سازی امن مرجع‌آگاه
printf '%s\n' "$TOKEN" | tabctl --instance X env set TAB_TELEGRAM_BOT_TOKEN
tabctl --instance X config set timezone Asia/Tehran
tabctl --instance X config set preview true
tabctl --instance X config set cleanup-interval 1800
tabctl --instance X backup create --mode core --encrypt
TAB_BACKUP_PASSPHRASE=... tabctl --instance X backup verify ID
```

خروجی‌های `status --json`، `backup verify` و `diagnostics` JSON هستند؛
`session status` و `media usage` خطوط `key=value` چاپ می‌کنند.

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
| Backup خراب | `tabctl --instance X backup verify ID` پیش از restore |
| Update ناموفق | `tabctl --instance X update --rollback` |