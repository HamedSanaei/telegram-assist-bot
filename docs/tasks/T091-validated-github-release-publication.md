# T091 — Validated GitHub Release Publication

## وضعیت

Active

## هدف

ایجاد یا تکمیل idempotent صفحهٔ GitHub Release و Assetهای نسخه پس از Build موفق
Package، انتشار GHCR و ساخت Bundle، برای Tag push و dispatch دستی Tag موجود.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بندهای `18.7` و `18.8`.

## وابستگی‌ها

- T090.

## محدوده

- Trigger با Tag push و `workflow_dispatch.inputs.tag`.
- اعتبارسنجی وجود Tag، SemVer دقیق و تطبیق با نسخهٔ `pyproject.toml`.
- Build و Artifact مستقل wheel، sdist و Bundle نصب.
- انتشار GHCR از Commit واقعی Tag و بررسی Image نسخه.
- ایجاد یا تکمیل GitHub Release با `gh release create/upload`.
- checksum همهٔ Assetها و Summary قابل audit.

## خارج از محدوده

- ایجاد، حذف یا جابه‌جایی Tag واقعی.
- Push، اجرای Workflow، انتشار Image یا ساخت Release واقعی در این Task.
- تغییر Application، Compose، Installer یا قرارداد Runtime.

## فایل‌ها و ماژول‌های مورد انتظار

- `.github/workflows/release.yml`
- `tests/unit/deployment/test_release_contract.py`
- `README.md`
- `docs/RELEASE_CHECKLIST.md`
- `docs/STATUS.md`
- `docs/ROADMAP.md`
- `docs/ARCHITECTURE.md`
- `docs/CODE_MAP.md`

## نکات پیاده‌سازی

- dispatch دستی Workflow را از Branch جاری می‌خواند ولی Source، Version،
  Package، Image و Bundle را از Tag اعتبارسنجی‌شده checkout می‌کند.
- انتشار تکراری Tag، Release جدید نمی‌سازد؛ Assetها با `--clobber` تکمیل یا
  جایگزین می‌شوند و Tag بدون mutation باقی می‌ماند.
- Job انتشار فقط پس از موفقیت Validate، Package، GHCR و Release files اجرا
  می‌شود.

## معیارهای پذیرش عینی

1. Tag push و dispatch با `tag` هر دو پشتیبانی شوند.
2. Tag ناموجود، غیر SemVer یا ناسازگار با Package پیش از انتشار fail شود.
3. wheel، sdist، Bundle و `SHA256SUMS` به Release متصل شوند.
4. Release جدید public و دارای notes خودکار باشد؛ Release موجود duplicate نشود.
5. Image نسخه در GHCR inspect و Tag/SHA/URL/Image/Digest/Assetها Summary شوند.

## Unit Testهای الزامی

- parse معتبر YAML و قرارداد Trigger/permission.
- قرارداد validation Tag و نسخه.
- dependencyهای `Publish GitHub Release` و رفتار create/update.

## Integration Testهای الزامی

- اجرای واقعی GitHub Actions پس از Push توسط مالک؛ انتشار خارجی در Suite محلی
  مجاز نیست.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/deployment/test_release_contract.py -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --changed
uv run python scripts/check_text_integrity.py --all
uv run detect-secrets-hook --no-verify --baseline .secrets.baseline <tracked-files>
git diff --check
```

## به‌روزرسانی‌های مستندات

- README، Release checklist، STATUS، ROADMAP، ARCHITECTURE و CODE_MAP.

## تعریف انجام‌شدن

- Workflow و contract تست‌ها موفق، اسناد همگام، diff محدود و Commit محلی ساخته
  شده باشد؛ هیچ اثر خارجی Release رخ نداده باشد.

## نتیجهٔ راستی‌آزمایی

- Release Run شمارهٔ
  [`30331348535`](https://github.com/HamedSanaei/telegram-assist-bot/actions/runs/30331348535)
  پیش‌نیازهای Validate، Package، GHCR و Release files را موفق اجرا کرد، اما
  Job انتشار به‌دلیل نبود checkout مخزن شکست خورد؛ تأیید نهایی منتظر Push و
  اجرای مجدد Workflow است.
- Workflow از Tag push و dispatch دستی Tag موجود پشتیبانی می‌کند.
- `Publish GitHub Release` به Validate، Build package، GHCR و Bundle وابسته است.
- Tag موجود `v1.1.0` به Commit
  `9ec75450fa376ea9d95e2365c4270a6e1aefda7b` resolve و با نسخهٔ Package
  `1.1.0` منطبق شد.
- تست‌های policy و release contract برابر `44 passed` هستند؛ YAML واقعاً parse
  و Triggerها، validation، dependencyها، create/update و Summary بررسی شدند.
- lock، Ruff، format، mypy، text integrity changed/all، detect-secrets و
  `git diff --check` موفق‌اند.
- Build محلی دقیقاً یک wheel و یک sdist نسخهٔ `1.1.0` ساخت و Distribution check
  موفق شد.
