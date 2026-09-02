# T096 — First-class Backup and Restore

## وضعیت

Completed

## هدف

تبدیل Backup فعلی (Config+metadata+MongoDB) به Backup/Restore درجه‌یک برای
migration بین سرورها: حالت‌های core/full، archive پورتابل، رمزنگاری اختیاری
AES-256-CBC با passphrase، restore با plan/conflict/rollback و restore در
instance دیگر.

## ارجاع به نیازمندی‌ها

- عملیات Backup/Restore و Migration Release (بخش `18` و `19`).

## وابستگی‌ها

- T095، T088، T087.

## محدوده

- حالت‌ها: `core` (config+metadata+mongodump) و `full` (core + `.env` +
  `compose.yaml` + session.tar.gz + media.tar.gz) با `--exclude-media`.
- رمزنگاری اختیاری هر فایل مؤلفه با OpenSSL pbkdf2؛ manifest شامل checksum و
  metadata رمزنگاری؛ passphrase از `TAB_BACKUP_PASSPHRASE` یا hidden prompt.
- `backup export/import` (archive tar.gz یک‌فایله).
- `backup restore [--to-instance NAME]`: verify، plan، conflict، پیش‌backup،
  توقف سرویس، بازگردانی فایل/Volume/MongoDB، health check و rollback.
- تار Volumeها با container یک‌بارمصرف root؛ هرگز `rm -rf` مستقیم.

## خارج از محدوده

- رمزنگاری اجباری؛ migration خودکار legacy preview؛ dashboard وضعیت.

## فایل‌ها و ماژول‌های مورد انتظار

- `deploy/tabctl.py` (create/verify/restore/export/import و helpers حجم).
- تست‌های Unit/Deployment و سناریوی destroy→restore در `v1_acceptance.sh`.

## نکات پیاده‌سازی

- checksumهای ذخیره‌شده روی فایل‌های stored و checksumهای plaintext پس از
  decrypt بررسی می‌شوند.
- restore در instance دیگر فقط config/session/media/db را بازمی‌گرداند؛
  `.env` و هویت مقصد حفظ می‌شوند (چاپ `env_skipped`).
- همهٔ عملیات خارجی timeout محدود دارند؛ عدم تطابق نام instance بدون
  `--to-instance` رد می‌شود.

## معیارهای پذیرش عینی

1. `backup create` پیش‌فرض full شامل هر هفت مؤلفه با checksum معتبر باشد.
2. چرخهٔ encrypt → verify (با passphrase درست/غلط) قطعی باشد.
3. export یک archive و import آن در instance دیگر verify شود.
4. restore پس از `down --volumes` داده و health را بازگرداند.
5. restore با نام instance ناهم‌خوان فقط با `--to-instance` ممکن باشد.

## Unit Testهای الزامی

- ترکیب مؤلفه‌های core/full، checksum، رمزنگاری roundtrip، conflict restore،
  export/import و عدم افشای Secret.

## Integration Testهای الزامی

- backup→destroy→restore→health روی instance یک‌بارمصرف در acceptance.

## فرمان‌های راستی‌آزمایی

```bash
uv run pytest tests/unit/deployment -q
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
git diff --check
```

## به‌روزرسانی‌های مستندات

- `docs/OPERATIONS.md`، README، CODE_MAP، ROADMAP و STATUS.

## تعریف انجام‌شدن

Backup/Restore migration-safe باشد (manifest، checksum، رمزنگاری، conflict و
rollback)، acceptance با destroy→restore سبز باشد و Gateها موفق باشند.

## نتیجهٔ راستی‌آزمایی

- تست‌های Unit رمزنگاری، export/import و conflict موفق شدند.
- سناریوی full backup→destroy→restore→health در Docker acceptance با helper
  لینوکس ایزوله و daemon disposable موفق شد؛ MongoDB، session/media volume،
  config ownership و health پس از restore بررسی شدند.