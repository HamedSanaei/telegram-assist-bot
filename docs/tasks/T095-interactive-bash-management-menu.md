# T095 — Interactive Bash Management Menu and Manager Plumbing

## وضعیت

Completed

## هدف

تبدیل تجربهٔ عملیاتی به منوی تعاملی Bash مشابه BackPack/3x-ui: کاربر عادی
بدون دانستن Docker Compose، Python module، MongoDB یا ساختار JSON همهٔ عملیات
مدیریت را از یک منو انجام دهد. Bash فقط orchestration است و همهٔ منطق از طریق
مدیر Python موجود (`tabctl.py`) و سرویس‌های Docker اجرا می‌شود.

## ارجاع به نیازمندی‌ها

- عملیات Installation و Operations Release (بخش `18` و `19` نیازمندی‌ها).

## وابستگی‌ها

- T087 (مدیر چندinstance)، T088 (Lifecycle/Backup/Repair)، T090، T092.

## محدوده

- `deploy/menu.sh`: منوی تعاملی ۱۸ بخشه با status header، submenu سرویس‌ها،
  Session، Bot، کانال‌ها، مدیران، Config، Logs، Queueها، Media، Backup،
  Docker، Update، Instance، Diagnostics، Uninstall.
- `deploy/tabctl.sh`: wrapper سراسری که بدون آرگومان منو و با آرگومان مدیر
  Python را اجرا می‌کند.
- افزودن به `tabctl.py`: `status --json`، `session status/reset`،
  `service start|stop|restart|recreate`، `queue` و `recover`، `media usage|cleanup|clear`،
  `env list|set` (stdin، بدون argv)، `config set` و fallback تک‌instance.
- Fallback نامعین‌بودن Docker به‌صورت graceful.

## خارج از محدوده

- بازنویسی Domain/Application در Bash.
- تغییر معنای انتشار، Dedup، زمان‌بندی یا Approval concurrency.
- parity کامل PowerShell.
- Commit، Push، Tag یا Release.

## فایل‌ها و ماژول‌های مورد انتظار

- `deploy/menu.sh`، `deploy/tabctl.sh`، `deploy/tabctl.py`.
- `src/telegram_assist_bot/bootstrap/operator_config.py` و `bootstrap/cli.py`
  برای mutatorهای `timezone-set`، `preview-set`، `cleanup-interval-set` و
  `approval-chat-set`.
- تست‌های Unit/Deployment و `scripts/v1_acceptance.sh`.

## نکات پیاده‌سازی

- هر عملیات مخرب: تأیید صریح یا تایپ نام instance؛ هرگز Enter به‌تنهایی حذف نکند.
- Secretها: ورودی hidden، هرگز در argv، هرگز در خروجی.
- JSON وضعیت فقط از `tabctl status --json`؛ parse با Python نه jq.
- بدون `eval`؛ `printf` برای داده؛ `set -Eeuo pipefail`؛ رنگ فقط روی TTY.

## معیارهای پذیرش عینی

1. `tabctl` بدون آرگومان منو را باز کند و `tabctl status` و
   `tabctl --instance foo status` بدون تغییر کار کنند.
2. منو همهٔ عملیات موجود README را پوشش دهد یا به CLI مستند ارجاع دهد.
3. `--action` برای تست/اتوماسیون یک‌بار اجرا شود.
4. هیچ Secret در خروجی وضعیت/doctor/منو نرود.
5. پاک‌سازی Media پیش‌فرض امن و مرجع‌آگاه باشد؛ reset فقط با تأیید صریح.

## Unit Testهای الزامی

- dispatch وضعیت JSON، session، service، queue، media، env، config set.
- عدم افشای Secret در env list/status.
- fallback تک‌instance و خطای چندinstance.
- smoke تست‌های Bash menu (`--action`) بدون Docker.

## Integration Testهای الزامی

- سناریوهای منو در `v1_acceptance.sh` روی instance واقعی.

## فرمان‌های راستی‌آزمایی

```bash
bash -n deploy/menu.sh deploy/tabctl.sh install.sh
uv run pytest tests/unit/deployment -q
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --all
git diff --check
```

## به‌روزرسانی‌های مستندات

- ROADMAP، STATUS، CODE_MAP، DECISIONS، `docs/OPERATIONS.md`، README و
  `AGENTS.md` (سیاست هویت عامل‌ها).

## تعریف انجام‌شدن

منو تمام بخش‌های عملیاتی را پوشش دهد، همهٔ CLIهای قبلی سازگار بمانند، هیچ
Secretی درز نکند، تست‌ها و Gateها سبز باشند و Docker acceptance اجرا شده باشد.

## نتیجهٔ راستی‌آزمایی

- ۵۴ تست deployment و ۱۰ smoke تست منو موفق شدند.
- `bash -n` روی menu.sh، tabctl.sh، install.sh و v1_acceptance.sh موفق بود.
- suite کامل Python روی 3.12/3.13/3.14 محلی سبز است.
- Docker acceptance کامل در helper لینوکس ایزوله با daemon disposable و
  storage driver `vfs` موفق شد؛ منو، session/config، دو instance، isolation و
  cleanup بررسی شدند.