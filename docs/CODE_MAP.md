# نقشه کد

## وضعیت

T001 پایهٔ قابل نصب و Quality Gateها را ایجاد کرد و T002 سامانهٔ typed و
immutable Configuration را افزود. هنوز هیچ اتصال خارجی، Persistence، Telegram
Adapter، AI Adapter یا Entry Point اجرایی وجود ندارد.

## ساختار فعلی

```text
.
├── .github/workflows/quality.yml
├── .editorconfig
├── .gitattributes
├── .gitignore
├── .secrets.baseline
├── README.md
├── config/configuration.example.json
├── pyproject.toml
├── uv.lock
├── scripts/
│   ├── __init__.py
│   ├── check_distribution.py
│   └── check_text_integrity.py
├── src/telegram_assist_bot/
│   ├── __init__.py
│   ├── py.typed
│   ├── domain/__init__.py
│   ├── application/__init__.py
│   ├── infrastructure/__init__.py
│   ├── presentation/__init__.py
│   ├── workers/__init__.py
│   ├── shared/
│   │   ├── __init__.py
│   │   └── config/
│   │       ├── __init__.py
│   │       ├── errors.py
│   │       ├── loader.py
│   │       └── models.py
│   └── bootstrap/__init__.py
└── tests/
    ├── unit/
    │   ├── shared/config/
    │   │   ├── conftest.py
    │   │   ├── test_loader.py
    │   │   ├── test_secret_resolution.py
    │   │   └── test_validation.py
    │   ├── test_package_import.py
    │   ├── test_repository_policy.py
    │   └── test_text_integrity.py
    ├── integration/.gitkeep
    ├── contract/.gitkeep
    ├── e2e/.gitkeep
    └── fixtures/persian_utf8.json
```

اسناد حافظهٔ پروژه در `docs/` و مشخصات Taskها در `docs/tasks/` قرار دارند.

## Package و مرزها

| مسیر | مسئولیت فعلی |
|---|---|
| `src/telegram_assist_bot/__init__.py` | metadata عمومی Package و نسخه `0.1.0` |
| `domain/` | Scaffold قوانین و مدل‌های خالص آینده؛ بدون dependency خارجی |
| `application/` | Scaffold Use Caseها و Portهای آینده؛ فقط وابسته به Domain |
| `infrastructure/` | Scaffold Adapterهای MongoDB، Telegram، AI و Storage آینده |
| `presentation/` | Scaffold Handlerها و View modelهای مدیریتی آینده |
| `workers/` | Scaffold محرک‌های Worker آینده |
| `shared/config/models.py` | Schema نسخهٔ ۱ و مدل‌های Pydantic frozen برای همهٔ بخش‌های Config |
| `shared/config/loader.py` | خواندن UTF-8/JSON، تجمیع validation، resolve امن Secret و API واحد Composition Root |
| `shared/config/errors.py` | Exceptionها و issueهای immutable، pathدار و secret-safe |
| `shared/config/__init__.py` | سطح عمومی مدل‌ها، خطاها و `load_configuration` |
| `bootstrap/` | محل Composition Root آینده؛ هنوز Process راه‌اندازی نمی‌کند |
| `py.typed` | اعلام typed بودن Package به مصرف‌کننده‌ها |

هیچ Import از Domain/Application به Config، Infrastructure، Presentation یا SDK
خارجی وجود ندارد. Config فقط به Pydantic v2 و دادهٔ IANA بستهٔ `tzdata` وابسته
است و هیچ Adapter را Import نمی‌کند.

## جریان Configuration

```text
configuration.local.json (ignored, UTF-8) + Environment Mapping
    -> load_configuration(...)
    -> strict JSON/schema/semantic/reference validation
    -> SecretReference resolution
    -> LoadedConfiguration(ApplicationConfig, ResolvedSecrets)
    -> Composition Root آینده
```

`ApplicationConfig` فقط Environment Variable nameها را نگه می‌دارد؛ مقدارهای
resolveشده در container جدا و redacted هستند. Loader هیچ Session file، Socket،
MongoDB، Telegram یا AI endpoint را لمس نمی‌کند.

## Tooling و Quality Gateها

| مسیر | مسئولیت |
|---|---|
| `pyproject.toml` | metadata، Python `>=3.12,<3.14`، Hatchling و تنظیم pytest/Ruff/mypy/coverage |
| `uv.lock` | نسخه‌های دقیق runtime، توسعه و build backend |
| `.github/workflows/quality.yml` | اجرای Gateها روی Python 3.12 و 3.13 بدون Secret یا سرویس زنده |
| `.editorconfig` و `.gitattributes` | UTF-8، LF و قواعد پایدار متن |
| `.gitignore` | جلوگیری از Track عادی Secret، Session، Config محلی، Runtime data و Artifact |
| `.secrets.baseline` | policy خالی و بازبینی‌شده برای Secret scanner آفلاین |
| `scripts/check_text_integrity.py` | اسکن read-only فایل‌های changed/all برای UTF-8 و corruption |
| `scripts/check_distribution.py` | اعتبارسنجی دقیق Wheel، sdist و metadata ساخت |
| `README.md` | workflow رسمی نصب، Build و Quality Gateها |

Build رسمی CI از `hatchling==1.31.0` موجود در گروه قفل‌شدهٔ توسعه و گزینه
`--no-build-isolation` استفاده می‌کند. `uv 0.11.28` نسخهٔ ابزار الزامی است.

## تست‌ها

- `test_package_import.py`: import همهٔ لایه‌ها و تطبیق نسخه Package/Distribution.
- `test_text_integrity.py`: UTF-8 سخت‌گیرانه، BOM، Mojibake، allowlist محدود،
  path discovery و round-trip دقیق متن فارسی/Emoji/نیم‌فاصله.
- `test_repository_policy.py`: رفتار واقعی `.gitignore` برای مسیرهای حساس،
  generated، template و fixture.
- `shared/config/test_loader.py`: نمونهٔ امن، parsing UTF-8/JSON، Schema، نبود
  اتصال خارجی، immutability و Exception context امن.
- `shared/config/test_validation.py`: strict type، Enum/Range/ZoneInfo، تجمیع
  structural/semantic، یکتایی و destination/provider referenceها و حفظ دقیق
  فارسی، نیم‌فاصله، line break و Emoji.
- `shared/config/test_secret_resolution.py`: resolve تزریق‌پذیر Environment،
  missing/empty/invalid Secret، snapshot و redaction بدون نشت sentinel.
- `integration/`، `contract/` و `e2e/`: convention و Marker آماده‌اند، اما چون
  T002 هیچ Adapter یا I/O خارجی ندارد عمداً تست اجرایی در آن‌ها نیست.

اجرای پیش‌فرض هیچ شبکه، MongoDB، Telegram، Credential یا سرویس زنده لازم
ندارد و Marker `live` را کنار می‌گذارد.
