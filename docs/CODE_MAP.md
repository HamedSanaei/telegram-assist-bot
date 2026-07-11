# نقشه کد

## وضعیت

T001 پایهٔ قابل نصب و Quality Gateها را ایجاد کرد، T002 سامانهٔ typed و
immutable Configuration را افزود و T003 مدل خالص Post و چرخهٔ عمر حداقلی آن را
پیاده کرد. T004 اکنون Port مستقل Post و Adapter async MongoDB با Persistence
یکتا، TTL و Transition اتمیک را فراهم کرده است. T005 نیز taxonomy خطا، context
هم‌زمان، redaction، Logging ساختاریافته و Retry محدود را به Foundation افزود.
T006 نیز Composition Root، CLI و lifecycle واقعی Config/Logging/MongoDB را با
readiness و shutdown امن متصل کرده است. هنوز Telegram Adapter، AI Adapter،
Worker یا Presentation handler وجود ندارد.

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
│   ├── __main__.py
│   ├── py.typed
│   ├── domain/
│   │   ├── __init__.py
│   │   └── posts/
│   │       ├── __init__.py
│   │       ├── entities.py
│   │       ├── errors.py
│   │       ├── models.py
│   │       └── status.py
│   ├── application/
│   │   ├── __init__.py
│   │   └── ports/
│   │       ├── __init__.py
│   │       └── post_repository.py
│   ├── infrastructure/
│   │   ├── __init__.py
│   │   └── persistence/
│   │       ├── __init__.py
│   │       └── mongodb/
│   │           ├── __init__.py
│   │           ├── client.py
│   │           ├── errors.py
│   │           ├── indexes.py
│   │           ├── post_mapper.py
│   │           └── post_repository.py
│   ├── presentation/__init__.py
│   ├── workers/__init__.py
│   ├── shared/
│   │   ├── __init__.py
│   │   ├── errors.py
│   │   ├── observability/
│   │   │   ├── __init__.py
│   │   │   ├── context.py
│   │   │   ├── logging.py
│   │   │   └── redaction.py
│   │   ├── retry/
│   │   │   ├── __init__.py
│   │   │   ├── executor.py
│   │   │   └── policy.py
│   │   └── config/
│   │       ├── __init__.py
│   │       ├── errors.py
│   │       ├── loader.py
│   │       └── models.py
│   └── bootstrap/
│       ├── __init__.py
│       ├── cli.py
│       └── runtime.py
└── tests/
    ├── integration/
    │   ├── conftest.py
    │   ├── test_foundation_startup.py
    │   └── infrastructure/persistence/
    │       ├── conftest.py
    │       └── test_mongodb_post_repository.py
    ├── unit/
    │   ├── test_bootstrap.py
    │   ├── application/ports/test_post_repository_contract.py
    │   ├── domain/posts/
    │   │   ├── test_domain_architecture_policy.py
    │   │   ├── test_post_expiration.py
    │   │   ├── test_post_identity.py
    │   │   ├── test_post_lifecycle.py
    │   │   └── test_telegram_entity.py
    │   ├── infrastructure/persistence/
    │   │   ├── test_mongodb_post_repository_unit.py
    │   │   └── test_post_mapper.py
    │   ├── shared/config/
    │   │   ├── conftest.py
    │   │   ├── test_loader.py
    │   │   ├── test_secret_resolution.py
    │   │   └── test_validation.py
    │   ├── shared/observability/
    │   │   ├── test_redaction.py
    │   │   └── test_structured_logging.py
    │   ├── shared/retry/
    │   │   ├── test_executor.py
    │   │   └── test_policy.py
    │   ├── shared/test_foundation_architecture_policy.py
    │   ├── test_package_import.py
    │   ├── test_repository_policy.py
    │   └── test_text_integrity.py
    ├── contract/.gitkeep
    ├── e2e/.gitkeep
    └── fixtures/persian_utf8.json
```

اسناد حافظهٔ پروژه در `docs/` و مشخصات Taskها در `docs/tasks/` قرار دارند.

## Package و مرزها

| مسیر | مسئولیت فعلی |
|---|---|
| `src/telegram_assist_bot/__init__.py` | metadata عمومی Package و نسخه `0.1.0` |
| `src/telegram_assist_bot/__main__.py` | Entry point بدون side effect برای `python -m telegram_assist_bot` |
| `bootstrap/cli.py` | parsing امن `--config`، precedence مسیر، اجرای یک‌مرحله‌ای و exit codeهای `0/2/3` |
| `bootstrap/runtime.py` | Composition Root concrete، lifecycle async، readiness، eventهای audit و ownership/cleanup دقیق Mongo client |
| `bootstrap/__init__.py` | API عمومی Composition Root و CLI بدون اجرای Startup هنگام import |
| `domain/posts/models.py` | `PostId`، هویت منبع، محتوای اصلی و aggregate frozen `Post` با انقضای ۱۴روزه |
| `domain/posts/entities.py` | Entity مستقل از SDK با مختصات UTF-16 و metadata محدود Custom Emoji |
| `domain/posts/status.py` | Enumها، جدول immutable Transition و history recordهای UTC |
| `domain/posts/errors.py` | Exceptionهای Domain برای invariant، زمان، transition، version و تغییر محتوای اصلی |
| `domain/posts/__init__.py` | API عمومی و مستند قرارداد Post Domain |
| `application/ports/post_repository.py` | Protocol مستقل از driver برای insert یکتا، دریافت رکورد غیرمنقضی، فهرست محدود و CAS؛ resultها و exceptionهای امن دارای tag taxonomy |
| `application/ports/__init__.py` | API عمومی Port و قراردادهای Persistence پست |
| `infrastructure/persistence/mongodb/client.py` | ساخت `AsyncMongoClient` از Config/Secret، timeout محدود، Stable API و بررسی حداقل MongoDB 7.0؛ دسترسی به collection پایدار `posts` |
| `infrastructure/persistence/mongodb/indexes.py` | تعریف، ساخت تکرارشونده و Fail-fast دو Index دقیق `uq_posts_source_identity_v1` و `ttl_posts_expires_at_v1` |
| `infrastructure/persistence/mongodb/post_mapper.py` | Mapper صریح Schema سند `1` با validation سخت‌گیرانه و round-trip دقیق Domain/UTC/Entity |
| `infrastructure/persistence/mongodb/post_repository.py` | Adapter async شامل insert مستقیم و نگاشت DuplicateKey، query غیرمنقضی و Transition اتمیک version/status |
| `infrastructure/persistence/mongodb/errors.py` | خطاهای داخلی، ثابت و redacted اتصال، Index و Document؛ هیچ exception مربوط به driver از Infrastructure خارج نمی‌شود |
| `presentation/` | Scaffold Handlerها و View modelهای مدیریتی آینده |
| `workers/` | Scaffold محرک‌های Worker آینده |
| `shared/config/models.py` | Schema نسخهٔ ۱ و مدل‌های Pydantic frozen برای همهٔ بخش‌های Config |
| `shared/config/loader.py` | خواندن UTF-8/JSON، تجمیع validation، resolve امن Secret و API واحد Composition Root |
| `shared/config/errors.py` | Exceptionها و issueهای immutable، pathدار، secret-safe و دارای category ساختاری Configuration |
| `shared/config/__init__.py` | سطح عمومی مدل‌ها، خطاها و `load_configuration` |
| `shared/errors.py` | taxonomy پایدار ده‌گانه، classification immutable و برچسب retryable مستقل از Provider |
| `shared/observability/context.py` | `CorrelationContext` frozen و binding مبتنی بر `ContextVar` با isolation میان coroutineها |
| `shared/observability/redaction.py` | کپی و redaction بازگشتی Secret، Header، URI، Exception و محتوای کامل Telegram با marker ثابت |
| `shared/observability/logging.py` | ساخت eventهای structured دارای زمان UTC، level، نام، correlation/context و خطای redacted؛ JSON با Unicode واقعی |
| `shared/retry/policy.py` | `RetryPolicy` و قرارداد timeout خارجی immutable با attempt/backoff/cap/jitter محدود |
| `shared/retry/executor.py` | اجرای async فقط برای operation صراحتاً safe/idempotent، logging تلاش/شکست، sleeper/jitter تزریق‌شده و propagation cancellation |
| `py.typed` | اعلام typed بودن Package به مصرف‌کننده‌ها |

هیچ Import از Domain/Application به Config، Infrastructure، Presentation یا SDK
خارجی وجود ندارد. Port فقط Domain و کتابخانه استاندارد را می‌شناسد؛ PyMongo،
BSON، Collection و Query صرفاً داخل `infrastructure.persistence.mongodb`
می‌مانند. Config فقط به Pydantic v2 و دادهٔ IANA بستهٔ `tzdata` وابسته است و
هیچ Adapter را Import نمی‌کند.

## جریان Domain پست

```text
دادهٔ داخلی Adapter آینده
    -> SourceMessageIdentity + OriginalPostContent + TelegramEntity
    -> Post(Discovered, version=0, history=())
    -> transition_to(..., expected_version)
    -> snapshot تازهٔ Stored یا Expired + history افزایشی
    -> PostTransitionRequest(expected_version, expected_status)
    -> PostRepository Protocol
    -> MongoPostRepository + post_mapper
    -> collection posts (schema_version=1)
```

هویت Idempotency زوج شناسهٔ کانال/پیام منبع است و با `PostId` داخلی یکی نیست.
متن و Caption اصلی و Entityهای جداگانهٔ آن‌ها frozen و بدون normalization
می‌مانند. `expires_at` فقط از زمان UTC دریافت و retention ثابت ۱۴ روز محاسبه
می‌شود. Mapper زمان‌های BSON را همراه remainder میکروثانیه بازسازی می‌کند؛
`expires_at` برای جلوگیری از حذف زودهنگام TTL به بالا گرد می‌شود و query پس از
فیلتر MongoDB مرز دقیق Domain را نیز بررسی می‌کند. درج بدون check مقدماتی به
Unique Index تکیه دارد و Transition فقط با شرط ترکیبی `_id`، `schema_version`،
`version` و `status` جاری موفق می‌شود.

## جریان Persistence پست

```text
Post immutable
    -> insert_idempotently
    -> insert_one مستقیم
    -> Created | AlreadyExists بر اساس هویت منبع

Post.transition_to(...)
    -> PostTransitionRequest
    -> find_one_and_update با CAS نسخه/وضعیت
    -> snapshot بازسازی‌شده | NotFound | ConcurrencyConflict
```

در `posts`، زوج `source_channel_id + source_message_id` با Unique Index یکتا
است. Index تک‌فیلدی `expires_at` با `expireAfterSeconds=0` cleanup دیرهنگام
MongoDB را فعال می‌کند، ولی همهٔ readهای Application-facing شرط
`expires_at > as_of` و بررسی exact Domain دارند. Index initializer تعریف
ناسازگار هم‌نام یا هم‌کلید را بدون drop خودکار Fail-fast می‌کند.

## جریان Configuration

```text
configuration.local.json (ignored, UTF-8) + Environment Mapping
    -> load_configuration(...)
    -> strict JSON/schema/semantic/reference validation
    -> SecretReference resolution
    -> LoadedConfiguration(ApplicationConfig, ResolvedSecrets)
    -> Composition Root T006
```

`ApplicationConfig` فقط Environment Variable nameها را نگه می‌دارد؛ مقدارهای
resolveشده در container جدا و redacted هستند. Loader هیچ Session file، Socket،
MongoDB، Telegram یا AI endpoint را لمس نمی‌کند.

## جریان Startup و Shutdown پایه

```text
python -m telegram_assist_bot --config PATH
    -> CLI > TAB_CONFIG_PATH > default path
    -> load/validate Config پیش از I/O
    -> configured logger + lifecycle audit logger
    -> create client -> ping/hello -> existing T004 indexes
    -> MongoPostRepository -> readiness
    -> shutdown task مشترک -> close client دقیقاً یک‌بار -> exit
```

هر event Startup/Shutdown همان `CorrelationContext` را دارد و پیش از Sink از
Redactor دارای Secretهای resolveشده عبور می‌کند. failure Config exit `2` و هیچ
client نمی‌سازد؛ failure اتصال/Index exit `3` است. cancellation پس از cleanup
کامل عبور می‌کند و هیچ Telegram/AI/Media/Scheduler یا Worker در graph نیست.

## جریان Observability و Retry

```text
CorrelationContext frozen
    -> bind_log_context (ContextVar task-local)
    -> StructuredLogger.emit
    -> base fields + allowlisted context + error classification
    -> Redactor پیش از Sink/JSON
    -> structured event بدون Secret یا متن کامل Telegram

operation صراحتاً safe/idempotent
    -> execute_with_retry
    -> classify_error
    -> retry_scheduled + delay capped/jittered + sleeper تزریق‌شده
    -> success | retry_exhausted + همان Exception نهایی
```

فقط `transient`، `timeout` و `rate_limit` retryable هستند. Validation،
Configuration، Authorization، Permission، Permanent، Conflict و Already-completed
خودکار retry نمی‌شوند. هیچ Adapter موجود به executor متصل نشده است؛ T005 فقط
قرارداد و Foundation مستقل را فراهم می‌کند.

## Tooling و Quality Gateها

| مسیر | مسئولیت |
|---|---|
| `pyproject.toml` | metadata، Python `>=3.12,<3.14`، PyMongo async، Hatchling و تنظیم pytest/Ruff/mypy/coverage |
| `uv.lock` | نسخه‌های دقیق runtime، توسعه و build backend |
| `.github/workflows/quality.yml` | اجرای Gateها روی Python 3.12 و 3.13 با MongoDB موقت نسخه‌ثابت، URI آزمایشی loopback و بدون Secret |
| `.editorconfig` و `.gitattributes` | UTF-8، LF و قواعد پایدار متن |
| `.gitignore` | جلوگیری از Track عادی Secret، Session، Config محلی، Runtime data و Artifact |
| `.secrets.baseline` | policy خالی و بازبینی‌شده برای Secret scanner آفلاین |
| `scripts/check_text_integrity.py` | اسکن read-only فایل‌های changed/all برای UTF-8 و corruption |
| `scripts/check_distribution.py` | اعتبارسنجی دقیق Wheel، sdist، metadata و حضور ماژول‌های Config/Post Domain/Observability/Retry/Bootstrap |
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
- `domain/posts/test_post_identity.py`: هویت داخلی/منبع، validation، equality،
  hash و immutability محتوای اصلی.
- `domain/posts/test_post_lifecycle.py`: جدول کامل Transition، version/history،
  optimistic conflict، rehydration و redaction.
- `domain/posts/test_post_expiration.py`: UTC-aware بودن، canonicalization،
  انقضای دقیق ۱۴روزه و boundaryهای ماه/سال/DST.
- `domain/posts/test_telegram_entity.py`: مختصات UTF-16، Custom Emoji و حفظ دقیق
  فارسی، نیم‌فاصله، خط‌شکست و Emoji.
- `domain/posts/test_domain_architecture_policy.py`: منع import بیرونی و کنترل
  API مستند و مدل‌های frozen Domain.
- `application/ports/test_post_repository_contract.py`: قرارداد result، request
  یک Transition منسجم، exceptionهای امن و استقلال Port از MongoDB/BSON.
- `infrastructure/persistence/test_post_mapper.py`: Schema دقیق، document
  نامعتبر/نسخه ناشناخته، datetime UTC، remainder میکروثانیه و round-trip کامل
  فارسی، نیم‌فاصله، خط‌شکست، Emoji، Custom Emoji و history.
- `infrastructure/persistence/test_mongodb_post_repository_unit.py`: نگاشت خطا و
  DuplicateKey، timeout، queryهای exact، CAS و redaction با Collectionهای fake.
- `integration/infrastructure/persistence/conftest.py`: پذیرش فقط URI بدون
  credential روی loopback، database تصادفی `tab_t004_*` و cleanup محافظت‌شده.
- `integration/infrastructure/persistence/test_mongodb_post_repository.py`:
  MongoDB واقعی آزمایشی برای Indexهای دقیق/تکرارشونده، درج هم‌زمان، عدم
  overwrite، query منقضی، CAS رقابتی و round-trip Unicode/Entity.
- `shared/observability/test_redaction.py`: redaction بازگشتی و non-mutating برای
  کلید/مقدار/URI/Header/Exception، cycle/depth، محتوای Telegram و حفظ متن فارسی.
- `shared/observability/test_structured_logging.py`: schema پایه، JSON UTF-8،
  سطح Config، context nested و isolation دو coroutine هم‌زمان.
- `shared/retry/test_policy.py`: همهٔ دسته‌های خطا، mapping خطاهای Config/Post
  موجود، cause، timeout contract، attempt bound، backoff، cap و jitter قطعی.
- `shared/retry/test_executor.py`: موفقیت/بازیابی/exhaustion، operation امن،
  redaction رخدادها، شکست sink و cancellation حین operation/backoff بدون sleep.
- `shared/test_foundation_architecture_policy.py`: منع dependency از Foundation
  به Telegram، MongoDB/BSON، AI/HTTP، Scheduler و لایه‌های بیرونی.
- `test_bootstrap.py`: precedence و exit code CLI، wiring order، readiness،
  event/correlation، redaction، failure میانی، shutdown دقیقاً یک‌باره، race و
  cancellation چندباره و import بدون side effect با تمام boundaryهای fake.
- `integration/test_foundation_startup.py`: Config موقت UTF-8 و Environment
  مصنوعی روی MongoDB واقعی loopback، Startup دوباره و Indexهای ثابت، Config
  نامعتبر بدون client attempt و target credential-bearing ناموجود با timeout.
- `contract/` و `e2e/`: فقط convention و Marker آماده دارند و هنوز تست اجرایی
  ندارند.

Unit Suite هیچ سرویس خارجی لازم ندارد. اجرای Integrationهای T004/T006 و Full Suite به
`TEST_MONGODB_URI` صریح، credential-free و loopback با
`directConnection=true` نیاز دارد؛ هیچ تست Foundation به production، Telegram یا AI
متصل نمی‌شود و نبود MongoDB باعث skip خاموش نمی‌شود.
