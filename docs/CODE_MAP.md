# نقشه کد

## وضعیت

T001 پایهٔ قابل نصب و Quality Gateها را ایجاد کرد، T002 سامانهٔ typed و
immutable Configuration را افزود و T003 مدل خالص Post و چرخهٔ عمر حداقلی آن را
پیاده کرد. T004 اکنون Port مستقل Post و Adapter async MongoDB با Persistence
یکتا، TTL و Transition اتمیک را فراهم کرده است. T005 نیز taxonomy خطا، context
هم‌زمان، redaction، Logging ساختاریافته و Retry محدود را به Foundation افزود.
T006 نیز Composition Root، CLI و lifecycle واقعی Config/Logging/MongoDB را با
readiness و shutdown امن متصل کرده است. Milestone 1 (T007–T012) اکنون login و
Session محافظت‌شده، validation حساب/کانال، crawl روز جاری، Listener زنده، مسیر
واحد ingest/claim اتمیک و restart recovery را فراهم می‌کند. Milestone 2
(T013–T019) نیز ذخیره و پاک‌سازی خصوصی Media، Album پایدار، duplicate دقیق،
محتوای مستقل مقصد، دسته‌بندی پایه و pipeline قابل‌بازیابی را افزوده است. AI،
هنوز پیاده نشده است. Milestone 3 تعامل Bot API/Approval و Milestone 4 انتشار
User API، idempotency، صف مقصد، Worker leaseدار و لغو/recompaction را کامل کرد.

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
│   │   ├── authenticate_telegram_session.py
│   │   ├── validate_telegram_session.py
│   │   ├── crawl_today_text_posts.py
│   │   ├── handle_live_message.py
│   │   ├── ingest_post_idempotently.py
│   │   ├── text_ingestion.py
│   │   └── ports/
│   │       ├── __init__.py
│   │       ├── clock.py
│   │       ├── post_repository.py
│   │       └── telegram_source_gateway.py
│   ├── infrastructure/
│   │   ├── __init__.py
│   │   ├── persistence/
│   │       ├── __init__.py
│   │       └── mongodb/
│   │           ├── __init__.py
│   │           ├── client.py
│   │           ├── errors.py
│   │           ├── indexes.py
│   │           ├── post_mapper.py
│   │           └── post_repository.py
│   │   └── telegram/user/
│   │       ├── session_adapter.py
│   │       ├── message_mapper.py
│   │       ├── history_adapter.py
│   │       ├── live_adapter.py
│   │       └── text_ingestion_gateway.py
│   ├── presentation/__init__.py
│   ├── workers/
│   │   ├── crawl_once.py
│   │   └── live_text_listener.py
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
│       ├── runtime.py
│       ├── telegram_login.py
│       ├── telegram_validation.py
│       └── text_ingestion.py
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
    ├── contract/telegram/
    ├── e2e/test_text_ingestion_restart.py
    └── fixtures/persian_utf8.json
```

اسناد حافظهٔ پروژه در `docs/` و مشخصات Taskها در `docs/tasks/` قرار دارند.

## Package و مرزها

| مسیر | مسئولیت فعلی |
|---|---|
| `src/telegram_assist_bot/__init__.py` | metadata عمومی Package و نسخه `0.1.0` |
| `src/telegram_assist_bot/__main__.py` | Entry point بدون side effect برای `python -m telegram_assist_bot` |
| `bootstrap/cli.py` | parsing امن `--config`، commandهای runtime/approval و inspection/cancellation صریح صف، و exit codeهای پایدار |
| `bootstrap/runtime.py` | Composition Root concrete، lifecycle async، readiness، eventهای audit و ownership/cleanup دقیق Mongo client |
| `bootstrap/telegram_login.py` | Composition Root ورود صریح و prompt امن بدون Secret در CLI/log |
| `bootstrap/telegram_validation.py` | اتصال validation غیرتعاملی Session/Premium/channel به Startup |
| `bootstrap/text_ingestion.py` | orchestration یک Session؛ retry محدود validation/open، stop event، supervision taskهای حیاتی، heartbeat/publication/live پیش از crawl background و shutdown معکوس |
| `bootstrap/approval_bot.py` | long polling، delivery/sync worker، `/start`، callback و cleanup دقیق Bot/MongoDB |
| `bootstrap/publication_queue.py` | projection امن و read-only صف، لغو صریح، و recovery دقیق dry-run/clear/requeue برای failure اثبات‌شدهٔ pre-send همراه با درخواست sync صفحه‌کلید، بدون Telegram Session |
| `bootstrap/approval_queue.py` | projection امن صف approval، retry صریح و recovery محدود/dry-run فقط برای Documentهای `media_rejected` بدون reset مدیران موفق |
| `application/operational_approval.py` | delivery content-first و recovery هر مدیر؛ callback-to-command با لغو idempotent terminal failure، جبران CAS ناموفق و sync canonical بدون Telegram SDK |
| `application/ports/operational_approval.py` | DTO/Portهای outbox approval و loader محتوای آماده |
| `application/ports/native_scheduling.py` | DTO، state و Portهای command/receipt/lease و gateway زمان‌بندی بومی |
| `application/native_scheduling.py` | claim، Slot پنج‌دقیقه‌ای، cancellation و reconciliation بومی restart-safe |
| `presentation/bot/runtime_handlers.py` | handlerهای SDK-independent برای `/start` و callback عملیاتی |
| `infrastructure/persistence/mongodb/operational_approval_repository.py` | claim/lease منصفانه با `claim_due_at`، وضعیت retry/permanent هر مدیر، heartbeat Runtime، status/due/sync outbox و loader تأیید |
| `bootstrap/media_cleanup.py` | Composition Root یک‌مرحله‌ای cleanup محدود Media با reuse تنظیمات، repository و storage موجود |
| `bootstrap/scheduling.py` | Composition Root legacy؛ CLI عمومی آن پیش از Session fail-closed است |
| `bootstrap/__init__.py` | API عمومی Composition Root و CLI بدون اجرای Startup هنگام import |
| `domain/posts/models.py` | `PostId`، هویت منبع، محتوای اصلی و aggregate frozen `Post` با انقضای ۱۴روزه |
| `domain/posts/entities.py` | Entity مستقل از SDK با مختصات UTF-16 و metadata محدود Custom Emoji |
| `domain/posts/status.py` | Enumها، جدول immutable Transition و history recordهای UTC |
| `domain/posts/errors.py` | Exceptionهای Domain برای invariant، زمان، transition، version و تغییر محتوای اصلی |
| `domain/posts/__init__.py` | API عمومی و مستند قرارداد Post Domain |
| `domain/media/` | هویت و metadata immutable فایل Media بدون وابستگی به Filesystem/Telegram |
| `domain/duplicates/` | نتیجهٔ نسخه‌دار duplicate دقیق و reference تطبیق‌یافته |
| `domain/categories/` | هویت دسته و نتیجهٔ auditپذیر دسته‌بندی پایه/دستی |
| `domain/publication.py` و `domain/scheduling.py` | stateهای Publication/Schedule، identity نسخه‌دار، lease، failure و audit due |
| `application/ports/post_repository.py` | insert یکتا با canonical ID/Conflict، claim اتمیک مرحلهٔ بعد، read/list و CAS مستقل از driver |
| `application/ports/telegram_source_gateway.py` | DTO، Port، result و errorهای application-owned برای auth، validation، History و subscription |
| `application/ports/media.py` | Portهای Stream/Storage/Persistence و DTOهای Media، Album، duplicate، category، artifact و readiness |
| `application/ports/publication.py` و `scheduling.py` | Portهای Publisher، payload loader، claim Publication، certainty مرز send و صف پایدار |
| `application/publication/` | انتشار idempotent متن/Media/Album با retry پیش‌ارسال و `OutcomeUnknown` |
| `application/scheduling/` | رزرو Slot، اجرای Job due و لغو policyدار |
| `application/authenticate_telegram_session.py` | reuse Session معتبر و flow کد/2FA فقط با ورودی تعاملی تزریق‌شده |
| `application/validate_telegram_session.py` | تجمیع Premium، resolve canonical channel و access/permission issueها |
| `application/crawl_today_text_posts.py` | محاسبه بازهٔ نیمه‌باز روز محلی با Clock، pagination محدود و ingest متن/Caption |
| `application/handle_live_message.py` | فیلتر و ingest مشترک رویداد زنده بدون payload logging |
| `application/ingest_post_idempotently.py` | تنها write path Crawl/Listener؛ Created/AlreadyExists/Conflict و claim اتمیک |
| `application/download_post_media.py` | دانلود stream محدود، ثبت metadata و بازیابی file-commit/database-failure |
| `application/cleanup_expired_media.py` | پاک‌سازی batchدار و idempotent با recheck reference و containment |
| `application/assemble_media_group.py` | ثبت arrival پیش از دانلود، عضوگیری replay-safe و quiet/max deadline پایدار Album |
| `application/text_normalization.py` و `detect_exact_duplicate.py` | normalization حداقلی نسخه ۱ و hash قطعی نسخه ۱ در پنجرهٔ دقیق ۱۴روزه |
| `application/content/` و `prepare_destination_content.py` | تبدیل خالص مقصد با edit span و rebasing Entityهای UTF-16؛ policy نسخه ۱ |
| `application/categorize_post.py` | precedence دستی، keyword قطعی و default منبع؛ policy نسخه ۱ |
| `application/prepare_post_pipeline.py` | resume مرحله‌ای از MongoDB، artifact مستقل مقصد و readiness اتمیک |
| `application/runtime_ingestion.py` | مسیر مشترک History/Live؛ observation پیش‌دانلود، claim/lease و anchor canonical Album، isolation خطای هر گروه و preparation idempotent |
| `application/ports/__init__.py` | API عمومی Port و قراردادهای Persistence پست |
| `infrastructure/persistence/mongodb/client.py` | ساخت `AsyncMongoClient` از Config/Secret، timeout محدود، Stable API و بررسی حداقل MongoDB 7.0؛ دسترسی به collection پایدار `posts` |
| `infrastructure/persistence/mongodb/indexes.py` | تعریف، ساخت تکرارشونده و Fail-fast دو Index دقیق `uq_posts_source_identity_v1` و `ttl_posts_expires_at_v1` |
| `infrastructure/persistence/mongodb/post_mapper.py` | Schema `1`، round-trip Domain/UTC/Entity و markerهای افزایشی claim با backward read |
| `infrastructure/persistence/mongodb/post_repository.py` | insert/duplicate/canonical conflict، claim اتمیک، query غیرمنقضی و CAS |
| `infrastructure/persistence/mongodb/content_repository.py` | Media و preparation به‌همراه mapper سازگار legacy و claim/lease/retry/permanent state برای Album |
| `infrastructure/persistence/mongodb/publication_repository.py` | unique index، claim/lease اتمیک Publication و Schedule، cancel/recompact |
| `infrastructure/persistence/mongodb/native_schedule_repository.py` | outbox مستقل native schedule، receipt ID، request boundary و lease مقصد |
| `infrastructure/persistence/mongodb/publication_payload_loader.py` | بازسازی payload آمادهٔ متن/Media/Album و metadata اختیاری `text_url` بدون binary در MongoDB |
| `infrastructure/media/local_storage.py` | ذخیره خصوصی content-addressed با stream/hash/size، temp یکتا و rename اتمیک |
| `infrastructure/telegram/user/media_adapter.py` | resolve reference و stream فقط Photo/Document concrete تلگرام، با رد امن WebPage/Media نامعتبر |
| `infrastructure/telegram/user/session_adapter.py` | Adapter Telethon برای Session lock/path/permission، login، Premium، channel access، auto-reconnect محدود و await کردن disconnect نهایی همان client مالک |
| `infrastructure/telegram/user/message_mapper.py` | mapping بدون normalization متن/Caption/Entityهای UTF-16 و نگه‌داشتن WebPage preview به‌صورت متن عادی |
| `infrastructure/telegram/user/history_adapter.py` | pagination و query bounded History بدون token SDK در Application |
| `infrastructure/telegram/user/live_adapter.py` | subscription bounded، backpressure، خطای mapping امن per-message و unsubscribe cancellation-safe |
| `infrastructure/telegram/user/text_ingestion_gateway.py` | facade یک client برای validation، History، Listener، MediaSource و lifetime signal همان client |
| `infrastructure/telegram/media_serializer.py` | upload مشترک immediate/native با filename امن، InputMedia نوع‌صحیح و Album مرتب |
| `infrastructure/telegram/user_publisher.py` | mapping Entity/Custom Emoji، نرمال‌سازی شناسهٔ BSON مقصد و ارسال متن/Media/Album با serializer مشترک Telethon |
| `infrastructure/telegram/native_scheduler.py` | خواندن Scheduled Messages خارجی و `schedule=due_at` با serializer مشترک همان client Runtime |
| `infrastructure/telegram/bot/adapter.py` | تحویل content/control با Bot API، upload نوع‌صحیح Media زیر root محصور و نگاشت امن network/rate-limit/server failures به retry Application |
| `infrastructure/persistence/mongodb/errors.py` | خطاهای داخلی، ثابت و redacted اتصال، Index و Document؛ هیچ exception مربوط به driver از Infrastructure خارج نمی‌شود |
| `presentation/` | Scaffold Handlerها و View modelهای مدیریتی آینده |
| `workers/crawl_once.py` | محرک نازک Use Case crawl تک‌اجرا |
| `workers/live_text_listener.py` | consumer محدود با isolation خطای هر پیام، reconnect فقط برای stream/connection و shutdown امن |
| `workers/scheduled_publication_worker.py` | loop polling محدود بدون نگهداری صف در حافظه |
| `shared/config/models.py` | Schema نسخهٔ ۱ و مدل‌های Pydantic frozen برای همهٔ بخش‌های Config |
| `shared/config/loader.py` | خواندن UTF-8/JSON، تجمیع validation، resolve امن Environment/Local Secret و API واحد Composition Root |
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
    -> Created | AlreadyExists | Conflict + canonical PostId
    -> claim_next_stage با filter اتمیک markerهای خالی
    -> Claimed | AlreadyClaimed

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
    -> Environment یا Local Secret resolution
    -> LoadedConfiguration(ApplicationConfig, ResolvedSecrets)
    -> Composition Root T006/T012
```

`ApplicationConfig` فقط Environment Variable nameها را نگه می‌دارد؛ مقدارهای
resolveشده در container جدا و redacted هستند. Loader هیچ Session file، Socket،
MongoDB، Telegram یا AI endpoint را لمس نمی‌کند.

## جریان Startup، دریافت متن و Shutdown

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
کامل عبور می‌کند. در command `ingest` و alias `ingest-text` پس از Foundation این مسیر افزوده می‌شود:

```text
Session موجود -> validation Premium/Source/Destination
    -> subscribe bounded پیش از crawl
    -> crawl [local start-of-day, now) با pagination محدود
    -> IngestPostIdempotently -> Mongo unique insert + atomic claim
    -> consume live events تا cancellation
    -> unsubscribe -> close Telegram/session lock -> Foundation shutdown
```

هیچ Media/AI/Bot API/Scheduler یا downstream worker در graph Milestone 1 نیست.

## جریان آماده‌سازی محتوای Milestone 2

```text
Telegram Media stream -> DownloadPostMedia -> LocalMediaStorage
    -> metadata MongoDB -> Album durable assembly (در صورت group)
    -> exact duplicate v1 -> baseline category v1
    -> destination artifact v1 per destination
    -> atomic preparation readiness

CleanupExpiredMedia -> bounded candidates -> reference recheck
    -> confined idempotent delete under var/media
```

MongoDB منبع حقیقت resume هر مرحله است. Worker پس از restart نتیجه‌های duplicate،
category و artifact موجود را دوباره مصرف می‌کند و readiness تنها یک برنده دارد.
AIهای آینده صدا زده نمی‌شوند و state آن‌ها در این milestone صریحاً
`NotRequested` است.

## جریان تأیید Milestone 3

```text
aiogram Update -> private actor mapping -> AuthorizeAdminAction
    -> opaque callback lookup/revalidation -> destination CAS
    -> latest persisted state -> header/keyboard render
    -> best-effort edit of every active ApprovalReference
```

`domain/admin_approval.py` مدل‌های Admin، Callback، ApprovalReference و selection
نسخه‌دار را نگه می‌دارد. `application/approvals/` use caseهای authorization،
token، delivery، keyboard، toggle و sync را دارد. `application/ports/admin.py`
مرز Bot/MongoDB است. Adapterهای concrete در
`infrastructure/telegram/bot/adapter.py` و
`infrastructure/persistence/mongodb/approval_repository.py` قرار دارند؛
`presentation/bot/handlers.py` فقط mapping و dispatch مجوزمحور انجام می‌دهد و
`bootstrap/admin_approval.py` Composition Root صریح و بدون side effect import است.

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
| `pyproject.toml` | metadata، Python `>=3.12,<3.15`، PyMongo async، Telethon `1.44.0`، Hatchling و تنظیم pytest/Ruff/mypy/coverage |
| `uv.lock` | نسخه‌های دقیق runtime، توسعه و build backend |
| `.github/workflows/quality.yml` | اجرای Gateها روی Python 3.12، 3.13 و 3.14 با MongoDB موقت نسخه‌ثابت، URI آزمایشی loopback و بدون Secret |
| `.editorconfig` و `.gitattributes` | UTF-8، LF و قواعد پایدار متن |
| `.gitignore` | جلوگیری از Track عادی Secret، Session، Config محلی، Runtime data و Artifact |
| `.secrets.baseline` | policy خالی و بازبینی‌شده برای Secret scanner آفلاین |
| `scripts/check_text_integrity.py` | اسکن read-only فایل‌های changed/all برای UTF-8 و corruption |
| `scripts/check_distribution.py` | اعتبارسنجی دقیق Wheel، sdist، metadata و حضور ماژول‌های Config/Post Domain/Observability/Retry/Bootstrap |
| `README.md` | workflow رسمی نصب، Build، Quality Gateها و استفاده امن از Local Config مستقیم |

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
- `contract/telegram/`: contractهای Session، channel access و live message با SDK
  boundary مصنوعی و بدون Secret.
- `integration/test_crawl_today_text_posts.py` و `test_live_text_listener.py`:
  round-trip متن/Entity و duplicate delivery روی MongoDB واقعی آزمایشی.
- `integration/test_concurrent_idempotent_ingestion.py`: barrier قطعی چند producer،
  یک document/canonical ID/claim و استقلال identityهای متفاوت.
- `integration/test_ingestion_recovery.py` و `e2e/test_text_ingestion_restart.py`:
  disconnect، crash boundary، Session/database مشترک و restart بدون prompt/duplicate.
- `application/test_download_post_media.py` و `infrastructure/media/`: timeout،
  cancellation، size، temp cleanup، containment/symlink، rename اتمیک و حفظ فایل سالم.
- `application/test_cleanup_expired_media.py`: مرز retention، reference/shared hash،
  orphan grace، missing file، recheck و رقابت دو cleanup worker.
- `application/test_assemble_media_group.py`: replay، ترتیب، deadline، late-member
  policy و finalization تک‌برنده.
- `application/test_detect_exact_duplicate.py` و `test_text_normalization.py`:
  fixtureهای فارسی/ZWNJ/Emoji، serialization نسخه‌دار و مرز دقیق ۱۴روزه.
- `application/content/` و `test_categorize_post.py`: spanهای UTF-16، Custom Emoji،
  immutability، pruning مقصد و precedence قطعی دسته‌بندی.
- `integration/test_content_preparation_pipeline.py` و
  `e2e/test_content_preparation_restart.py`: MongoDB واقعی، readiness اتمیک،
  restart/recovery و عدم تکرار side effectهای کامل‌شده.

Unit/Contract Suite هیچ سرویس خارجی لازم ندارد. اجرای Integrationهای MongoDB و Full Suite به
`TEST_MONGODB_URI` صریح، credential-free و loopback با
`directConnection=true` نیاز دارد؛ هیچ تست Foundation به production، Telegram یا AI
متصل نمی‌شود و نبود MongoDB باعث skip خاموش نمی‌شود. تست live Telegram اختیاری و
از suite پیش‌فرض حذف است.
