# T083 — Restore Repository-wide Quality Gates

## وضعیت

Completed

## هدف

بازگرداندن Gateهای repository-wide به وضعیت موفق بدون تغییر قراردادهای رفتاری
T077، T078 یا Featureهای Milestone 8.

## ارجاع به نیازمندی‌ها

- `AGENTS.md`، بخش‌های Testing، Verification و Definition of Done.
- `.github/workflows/quality.yml`.
- `pyproject.toml` و `scripts/check_distribution.py`.

## وابستگی‌ها

- T078 — Configurable Independent Media Retention؛ Completed.

## محدوده

- افزایش Branch Coverage کل Package از Baseline فعلی حدود ۸۴٪ تا حداقل ۹۰٪ با
  تست‌های معنادار.
- تعیین Scope رسمی Ruff و exclude دقیق فقط برای ابزار مستقل
  `npvt-link-extractor`.
- همگام‌سازی allowlist دقیق Distribution Check با Package عمدی فعلی.
- ثبت path-specific کانفیگ‌های عمومی VPN در detect-secrets فقط در صورت نیاز.

## خارج از محدوده

- کاهش threshold نود درصد یا حذف Branch Coverage.
- wildcard گسترده برای Ruff یا Distribution.
- تغییر، ماسک، حذف یا synthetic کردن کانفیگ‌های عمومی VPN.
- پیاده‌سازی T079 یا Feature جدید.
- بازنویسی Git history، rotation credential یا تغییر Secretهای واقعی پروژه.

## فایل‌ها و ماژول‌های مورد انتظار

- `pyproject.toml`
- `.github/workflows/quality.yml` فقط در صورت نیاز به همگام‌سازی فرمان رسمی
- `scripts/check_distribution.py`
- `.secrets.baseline` فقط برای allowlist بررسی‌شده و path-specific
- تست‌های Unit/Integration لازم برای coverage و Distribution validation
- مستندات Quality Gate.

## نکات پیاده‌سازی

- Baseline تمیز `a8ff7d8` دارای coverage برابر `84.09%`، چهل خطای Ruff فقط در
  ابزار مستقل NPVT و allowlist قدیمی Distribution است.
- تست coverage باید branch مهم و assertion رفتاری داشته باشد؛ تست صوری یا
  تکراری پذیرفته نیست.
- ماژول‌های Phase Two موجود در Wheel عمدی‌اند و allowlist باید نام دقیق آن‌ها
  را ثبت کند و ورود member ناشناخته را همچنان رد کند.
- کانفیگ‌های VPN عمومی با تصمیم صریح مالک پروژه Secret خصوصی نیستند.

## معیارهای پذیرش عینی

1. Full non-live branch coverage حداقل ۹۰٪ است.
2. فرمان رسمی `ruff check .` با exclude دقیق NPVT موفق است و سایر مسیرها را کامل
   lint می‌کند.
3. Distribution checker Wheel/sdist فعلی را می‌پذیرد و member ناخواستهٔ مصنوعی
   را رد می‌کند.
4. detect-secrets هیچ Telegram token، API hash، Session، MongoDB password،
   Private Key یا GitHub token واقعی را نادیده نمی‌گیرد.
5. CI و مستندات دقیقاً فرمان‌های یکسان دارند.

## Unit Testهای الزامی

- Branchهای معنادار ماژول‌های زیر threshold، با اولویت Workerها و pipelineهای
  Phase Two.
- پذیرش تمام memberهای مجاز Wheel.
- رد top-level یا member اضافهٔ ناخواسته.
- بررسی اینکه Ruff فقط مسیر مستقل مصوب را exclude می‌کند.

## Integration Testهای الزامی

- Full non-live suite با MongoDB loopback و Branch Coverage.
- Build واقعی Wheel/sdist، Distribution check و clean-wheel import.

## فرمان‌های راستی‌آزمایی

```powershell
uv lock --check
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-report=term-missing --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --changed
uv run python scripts/check_text_integrity.py --all
uv run detect-secrets-hook --no-verify --baseline .secrets.baseline <tracked-files>
uv build --no-build-isolation
uv run python scripts/check_distribution.py dist
git diff --check
```

## به‌روزرسانی‌های مستندات

- `docs/ROADMAP.md`
- `docs/STATUS.md`
- `docs/CODE_MAP.md` در صورت افزودن تست یا ابزار جدید
- `README.md` و CI در صورت تغییر Scope رسمی Gate.

## تعریف انجام‌شدن

- تمام معیارها و Gateهای repository-wide موفق‌اند.
- threshold یا validationها تضعیف نشده‌اند.
- T079 یا Feature دیگری پیاده‌سازی نشده است.
- T083 در Roadmap و STATUS به‌روز و پیام Commit انگلیسی پیشنهاد شده است.

## وضعیت راستی‌آزمایی

- Branch Coverage از baseline `84.09%` به `90.05%` رسید؛ Full Suite
  `1757 passed`.
- `ruff check .` با exclude دقیق فقط `npvt-link-extractor`، format و mypy موفق
  شدند.
- Wheel/sdist نسخهٔ `1.0.0` با allowlist دقیق پذیرفته و تست member مصنوعی
  ناخواسته رد شد.
- text integrity changed/all، detect-secrets روی tracked و untrackedهای قابل
  انتشار، build، clean-wheel import و `git diff --check` موفق شدند.
- کانفیگ‌های عمومی VPN طبق تصمیم مالک دادهٔ عمومی عمدی‌اند و تغییر یا mask
  نشدند؛ هیچ Secret واقعی پروژه یافت نشد.
