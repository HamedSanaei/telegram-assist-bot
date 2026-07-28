# T092 — v1.1.1 Installer Update Safety Patch

## وضعیت

Completed

## هدف

رفع وابستگی permission repair به group محلی Host و جلوگیری از Telegram
Session conflict هنگام update، سپس آماده‌سازی patch release نسخهٔ `1.1.1`.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بندهای `18`، `19.1`، `19.4`، `19.7` و `19.8`.

## وابستگی‌ها

- T091.

## محدوده

- اعمال UID/GID عددی بدون `install -o/-g` در helper Linux.
- skip کردن login در update Instance موجود برای Linux و Windows.
- حفظ login اولیهٔ نصب تازه و Config check/Compose rollout در update.
- همگام‌سازی تمام سطوح فعال Package و Release با نسخهٔ `1.1.1`.
- Contract و Ubuntu acceptance برای هر دو regression.

## خارج از محدوده

- تغییر `manage.* login` یا خود Telegram login use case.
- stop/down خودکار سرویس‌ها، migration Session، Config، MongoDB یا volume.
- تغییر منطق update تراکنشی `tabctl` فراتر از default نسخهٔ Release.
- Push، Tag، GitHub Release یا انتشار GHCR.

## فایل‌ها و ماژول‌های مورد انتظار

- `deploy/permissions.sh`
- `install.sh` و `install.ps1`
- surfaceهای نسخه و Release packaging
- `tests/unit/deployment/` و `scripts/v1_acceptance.sh`
- مستندات عملیات، معماری و انتشار

## نکات پیاده‌سازی

- `install -d` فقط وجود مسیر را تضمین می‌کند؛ `chown` عددی و `chmod` نهایی
  جداگانه اجرا می‌شوند تا setgid و mode قطعی بمانند.
- وجود Config پیش از mutation، Instance موجود را تعیین می‌کند. update آن
  session-neutral است؛ مسیر فاقد Config همچنان نصب تازه و نیازمند login است.
- update هیچ session validation، stop یا down پنهان ندارد.
- Config، Session، Media، MongoDB و volumeهای موجود مهاجرت نمی‌کنند.

## معیارهای پذیرش عینی

1. repair با UID/GID فاقد passwd/group entry موفق و idempotent باشد.
2. mode و owner همهٔ مسیرهای قرارداد permission بدون تغییر محتوا حفظ شود.
3. update Instance موجود در هیچ Installer فرمان login اجرا نکند.
4. نصب تازه در هر دو Installer همچنان login اولیه داشته باشد.
5. update Config check و Compose rollout را بدون stop/down ادامه دهد.
6. Package، Image defaults، Bundle و مستندات نسخهٔ `1.1.1` داشته باشند.

## Unit Testهای الزامی

- قرارداد جداسازی `install -d`، `chown` عددی و `chmod`.
- قرارداد branch نصب تازه و update در Linux و Windows.
- قرارداد versionهای Package، Image، distribution و Release.

## Integration Testهای الزامی

- Ubuntu permission repair با UID/GID واقعاً unmapped.
- Linux update harness با Instance موجود، Session sentinel و Docker command log.
- Docker/Compose acceptance موجود بدون Telegram واقعی.

## فرمان‌های راستی‌آزمایی

```powershell
uv lock --check
uv run pytest tests/unit/deployment tests/unit/scripts/test_check_distribution.py -q
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --changed
uv run python scripts/check_text_integrity.py --all
uv run detect-secrets-hook --no-verify --baseline .secrets.baseline <tracked-files>
uv build --no-build-isolation
uv run python scripts/check_distribution.py dist
bash -n install.sh deploy/permissions.sh scripts/v1_acceptance.sh
pwsh -NoProfile -Command "[void][ScriptBlock]::Create((Get-Content ./install.ps1 -Raw))"
bash scripts/v1_acceptance.sh
git diff --check
```

## به‌روزرسانی‌های مستندات

- ROADMAP، STATUS، REQUIREMENTS، ARCHITECTURE، CODE_MAP، DECISIONS، README و
  RELEASE_CHECKLIST.

## تعریف انجام‌شدن

- هر دو regression با تست معنادار پوشش داده شوند، تمام Gateهای قابل‌اجرا موفق
  باشند، محدودیت Docker/Ubuntu صریح ثبت شود و هیچ اثر خارجی Release رخ ندهد.

## نتیجهٔ راستی‌آزمایی

- تست‌های متمرکز deployment/distribution برابر `40 passed` هستند.
- کل Suite غیرزنده با MongoDB آزمایشی برابر `1859 passed` و branch coverage
  برابر `90.11%` است.
- lock، Ruff، format، mypy، text integrity changed/all، detect-secrets،
  build نسخهٔ `1.1.1`، distribution check، clean-wheel import، Bash syntax،
  dry-run هر دو Installer و `git diff --check` موفق‌اند.
- Docker روی Host Windows در دسترس نیست؛ در نتیجه Ubuntu permission/update
  harness و Docker/Compose acceptance محلی اجرا نشدند.
- Quality Run شمارهٔ
  [`30335865206`](https://github.com/HamedSanaei/telegram-assist-bot/actions/runs/30335865206)
  برای Commit
  `f367ac31a87051ca91d92b78ed3808143f9b6715` با Python 3.12، 3.13، 3.14 و
  `Docker and installer acceptance` موفق شد و محدودیت Host را تعیین تکلیف کرد.
