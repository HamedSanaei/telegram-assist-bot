# نقشه کد

## وضعیت

T001 پایهٔ قابل نصب و Quality Gateهای پروژه را ایجاد کرده است. Packageهای
معماری فعلاً فقط Scaffold و importable هستند؛ هیچ رفتار محصولی، اتصال خارجی یا
Entry Point اجرایی وجود ندارد. توسعهٔ رفتار از T002 به بعد انجام می‌شود.

## ساختار فعلی

```text
.
├── .github/workflows/quality.yml
├── .editorconfig
├── .gitattributes
├── .gitignore
├── .secrets.baseline
├── README.md
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
│   ├── shared/__init__.py
│   └── bootstrap/__init__.py
└── tests/
    ├── unit/
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
| `shared/` | محل رزروشده برای primitiveهای واقعاً مشترک |
| `bootstrap/` | محل Composition Root آینده؛ هنوز Process راه‌اندازی نمی‌کند |
| `py.typed` | اعلام typed بودن Package به مصرف‌کننده‌ها |

هیچ Import از لایه‌های داخلی به Infrastructure، Presentation یا SDK خارجی
وجود ندارد. T001 هیچ runtime dependency تعریف نکرده است.

## Tooling و Quality Gateها

| مسیر | مسئولیت |
|---|---|
| `pyproject.toml` | metadata، Python `>=3.12,<3.14`، Hatchling و تنظیم pytest/Ruff/mypy/coverage |
| `uv.lock` | نسخه‌های دقیق dependencyهای توسعه و build backend |
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
- `integration/`، `contract/` و `e2e/`: convention و Marker آماده‌اند، اما چون
  T001 Adapter یا رفتار محصولی ندارد عمداً تست اجرایی در آن‌ها نیست.

اجرای پیش‌فرض هیچ شبکه، MongoDB، Telegram، Credential یا سرویس زنده لازم
ندارد و Marker `live` را کنار می‌گذارد.
