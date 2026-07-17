# معماری پیشنهادی Telegram Admin Assistant

## 1. وضعیت و دامنه سند

این سند معماری پیاده‌شده و بخش‌های صریحاً برنامه‌ریزی‌شده برای Milestone فعال را
توصیف می‌کند. منبع حقیقت نیازمندی‌های محصول فایل `docs/REQUIREMENTS.md` است و
Taskها باید فقط به همین مسیر ارجاع دهند.

معماری برای تحویل تدریجی طراحی شده است:

- **پیاده‌سازی اولیه:** فاز اول؛ دریافت، پردازش، تأیید و انتشار محتوای کانال‌ها، به‌همراه زیرساخت AI لازم.
- **گام بعدی قطعی:** فاز دوم؛ تبلیغات زمان‌بندی‌شده.
- **قابلیت‌های آینده و نیازمند بازتعریف:** فازهای سوم تا پنجم. این بخش‌ها در `docs/REQUIREMENTS.md` پیشنهادی‌اند و پیش از پیاده‌سازی باید معیار پذیرش و اولویت محصولی پیدا کنند.

اصل راهنما، یک برنامه ماژولار و قابل استقرار به‌صورت یک واحد در شروع است. مرزها از روز نخست رعایت می‌شوند، اما سرویس‌های جدا، Message Broker، Event Sourcing یا Framework داخلی عمومی تا زمانی که نیاز عملی ایجاد نشده افزوده نمی‌شوند.

## 2. اهداف معماری

- منطق کسب‌وکار مستقل از Telegram، MongoDB، فایل‌سیستم، کتابخانه زمان‌بندی و Providerهای AI باشد.
- هر رفتار خارجی پشت یک Port قابل جایگزینی و قابل Fake شدن قرار گیرد.
- عملیات قابل تکرار، Idempotent و عملیات هم‌زمان، Atomic یا Lease-based باشند.
- Restart هیچ Session معتبر، پست ذخیره‌شده، Job زمان‌بندی یا AI Job را از بین نبرد.
- نسخه اصلی متن و Entityها دست‌نخورده بماند و خروجی هر مقصد جداگانه ساخته شود.
- مسیر سادهٔ اولیه حفظ شود و قابلیت‌های آینده بدون بازنویسی Domain/Application قابل افزودن باشند.

## 3. لایه‌ها و جهت وابستگی

```text
Domain
  ↑
Application (Use Cases + Ports)
  ↑                         ↑
Presentation / Workers      Infrastructure Adapters
              \             /
               Composition Root
```

قواعد وابستگی:

1. **Domain** فقط Python استاندارد و مدل‌های خود را می‌شناسد.
2. **Application** به Domain وابسته است و Portها را تعریف می‌کند؛ نام یا نوع کتابخانه‌های Telegram، MongoDB، HTTP و Scheduler در آن دیده نمی‌شود.
3. **Infrastructure** Portهای Application را برای MongoDB، Telegram User API، Telegram Bot API، Media Storage، AI و Logging پیاده می‌کند.
4. **Presentation** فرمان‌ها و Callbackهای مدیران را به Command/DTOهای Application تبدیل می‌کند و سیاست کسب‌وکار را در Handlerها قرار نمی‌دهد.
5. **Workers** محرک Use Caseها هستند؛ Crawl، Listener، Due Job و AI Job را اجرا می‌کنند اما تصمیم دامنه‌ای نمی‌گیرند.
6. **Composition Root** تنها محل ساخت Adapterها، اتصال آن‌ها به Use Caseها و مدیریت چرخه عمر Process است.

هیچ Import از `domain` یا `application` به `infrastructure`، `presentation` یا SDKهای خارجی مجاز نیست.

### Composition Root و Startup پیاده‌شده

Package `telegram_assist_bot.bootstrap` تنها محل import هم‌زمان Configuration،
Observability، Port و Adapterهای concrete MongoDB/Telethon است. `__main__.py` فقط تابع CLI را
زیر guard اجرا می‌کند؛ import هیچ Config، Environment، Logger global یا resource
خارجی را باز نمی‌کند.

ترتیب واقعی lifecycle T006:

```text
--config PATH > TAB_CONFIG_PATH > config/configuration.json
    -> load_configuration(path, environ snapshot)
    -> configured application logger + unfiltered lifecycle audit logger
    -> create_mongodb_client و ثبت فوری ownership
    -> verify_mongodb_connection با timeout Config
    -> get_posts_collection
    -> initialize_post_indexes موجود T004
    -> MongoPostRepository
    -> application_ready
    -> shutdown_begun -> close_mongodb_client -> resource_closed
```

Readiness تا پایان ping و Index setup false است. client تنها resource مستقل
مالکیت‌دار است؛ Collection و Repository close جدا ندارند. failure یا cancellation
پس از ساخت client همان shutdown task مشترک را اجرا می‌کند. Waiter لغوشده cleanup
را با `shield` تا پایان join می‌کند و سپس cancellation عبور می‌کند. shutdown
ترتیبی یا هم‌زمان close را دقیقاً یک‌بار اجرا می‌کند و فراخوانی هنگام
`STARTING` برای جلوگیری از race با Startup رد می‌شود.

Application logger Level تنظیم‌شده را حفظ می‌کند، ولی lifecycle audit logger با
همان Sink، correlation و Secretهای resolveشده در Redactor، eventهای الزامی را
حتی در Levelهای `ERROR` و `CRITICAL` ثبت می‌کند. خروجی CLI JSON خطی UTF-8 روی
stderr است. exit codeهای پایدار `0` برای success، `2` برای Configuration و `3`
برای Infrastructure هستند. command پیش‌فرض همان Startup check T006 است و پس از
readiness فوراً shutdown می‌شود. command صریح `login` تنها مسیر prompt ورود است.
command عمومی `ingest` و alias سازگار `ingest-text` پس از Foundation و validation،
subscription را پیش از crawl می‌سازند تا gap حداقل شود. همان Telethon client/session
برای History، Listener و `TelethonMediaSource` reuse می‌شود. هر DTO از مسیر مشترک
`RuntimeMessageIngestor` به Post canonical، دانلود/reuse محدود Media، Album پایدار و
`PreparePostPipeline` می‌رسد. یک task محدود deadlineهای Album را از MongoDB poll می‌کند؛
timer حافظه‌ای منبع حقیقت نیست. shutdown taskها، subscription، client/session lock و
MongoDB را در ترتیب معکوس و دقیقاً یک‌بار می‌بندد. command `media-cleanup` نیز یک batch
محدود و one-shot را با همان Config، repository و storage اجرا می‌کند.
command قدیمی `schedule-worker` پیش از بازکردن Session به‌شکل fail-closed متوقف
می‌شود. رکوردهای قدیمی `scheduled_publications` با `action=scheduled` برای audit و
سازگاری بدون migration یا execution دست‌نخورده می‌مانند.

فرمان `runtime` همین ingestion، اجرای publication فوری و زمان‌بندی بومی را زیر یک
مالک lifecycle و یک Telethon client قفل‌شده جمع می‌کند. publisher از همان client
بازشدهٔ ingestion ساخته می‌شود و native scheduler نیز با همان client، Scheduled
Messages مقصد را می‌خواند و `schedule=due_at` یا حذف بومی را اجرا می‌کند. Session
دوم وجود ندارد. `approval-bot` lifecycle مستقل
Bot API + MongoDB است، outbox تحویل و sync را poll می‌کند و هیچ User API adapter یا
Session کاربر را باز نمی‌کند. lock Session مانع رقابت فرمان‌های سازگار قدیمی با
`runtime` می‌شود.

polling انتشار فوری و polling/reconciliation زمان‌بندی بومی دو task حیاتی مستقل
هستند؛ timeout یا کندی Scheduled Messages نمی‌تواند iteration بعدی immediate را
متوقف کند. poll immediate حداکثر یک ثانیه است. هر دو مسیر قبل از `send_file` از
یک serializer مشترک استفاده می‌کنند که مسیر خصوصی را محصور، فایل را با نام اصلی
یا fallback غیرhash آپلود و برای Photo/Video/Animation/Document یک InputMedia
نوع‌صحیح می‌سازد. Album همان ترتیب payload و caption/entityهای canonical را حفظ
می‌کند.

Process `runtime` یک heartbeat MongoDB با فیلدهای محدود `instance_id`،
`started_at`، `last_seen_at` و `status` نگه می‌دارد. approval bot heartbeat تازه را
active و heartbeat stale/stopped را offline تفسیر می‌کند؛ این فقط presentation است
و job فوری یا زمان‌بندی‌شدهٔ پایدار را تغییر نمی‌دهد. `due_at` در persistence به UTC
می‌ماند و کارت کنترل آن را در timezone typed برنامه نمایش می‌دهد.

تحویل پیشنهاد content-first و مرحله‌ای است: `pending`، `content_sending`،
`content_sent`، `control_sending` و `completed`. شناسه‌های تمام پیام‌های محتوا پیش
از ارسال control card ثبت می‌شوند و control card به اولین پیام محتوا reply می‌شود؛
در Album نیز اولین عضو anchor است. restart با دیدن `content_sent` فقط control card
ناقص را می‌فرستد و reference کامل دوباره تحویل نمی‌شود.
اگر Bot API فقط reply association کارت کنترل با پیام رسانه‌ای را رد کند، کارت یک
بار بدون reply و بلافاصله پس از محتوا ارسال می‌شود؛ رد خود keyboard یا ارسال دوم
همچنان failure واقعی است و reference فعال جعلی ساخته نمی‌شود.
خطاهای موقت شبکه، rate limit و پاسخ‌های 5xx سرور Telegram در Aiogram adapter به
نتیجهٔ transient امن تبدیل می‌شوند و exception خام SDK از مرز Infrastructure
عبور نمی‌کند. در مسیر ویرایش کارت، این نتیجه درخواست sync پایدار را برای retry
نگه می‌دارد؛ بنابراین اختلال موقت Bot API، task حیاتی `approval-sync` یا polling
Approval Bot را متوقف نمی‌کند.

فرمان `publication-queue` projection محدود و read-only روی صف دارد و payload یا
مسیر Media را بار نمی‌کند. `publication-cancel` فقط job ID صریح را از مسیر
`CancelScheduledPost` و policy موجود لغو می‌کند؛ هیچ command بازرسی Session را باز
نمی‌کند.

Outbox تحویل approval از watermark زمان شروع برای تفکیک backlog تاریخی و کار جدید
استفاده می‌کند. مقدار سازگار `approval_delivery_max_per_startup` اندازهٔ batch
تاریخی است؛ پس از تکمیل موفق همان تعداد Post، مکث bounded انجام و batch بعدی بدون
restart آغاز می‌شود. claim ناموفق، retry، deferred و permanent failure سهمیه را
مصرف نمی‌کنند و Postهای بعد از watermark پیش از هر کار تاریخی claim می‌شوند.
هر retry دارای `claim_due_at` است و claimها به‌ترتیب زمان due، زمان ایجاد و شناسه
مرتب می‌شوند تا یک پیشنهاد خراب پیشنهادهای سالم را گرسنه نگذارد. نتیجه و backoff
هر مدیر زیر همان outbox جدا ثبت می‌شود، درحالی‌که referenceهای content/control
موفق منبع idempotency باقی می‌مانند. `approval-queue` فقط projection امن می‌خواند و
`approval-retry` تنها مدیران terminal همان Post صریح را آزاد می‌کند.

ترتیب startup عملیاتی پس از validation و بازشدن همان Telethon client چنین است:
subscriptionها ساخته می‌شوند؛ heartbeat اولیه و publication polling حیاتی شروع و
ready می‌شوند؛ live listenerها شروع می‌شوند؛ `operational_runtime_ready` صادر
می‌شود؛ سپس history crawl در task غیرحیاتی و retryشونده آغاز می‌شود. poll مؤثر
runtime حداکثر یک ثانیه است، ولی durable truth همچنان MongoDB و claim/lease موجود
است. crawl کامل‌شده می‌تواند task خود را خاتمه دهد؛ failure آن فقط safe category/type
ثبت و retry می‌شود. lifecycle با stop event صریح فقط taskهای واقعاً بلندمدت شامل
heartbeat، publication، consumer زنده، Album finalizer و signal قطع همان client را
supervise می‌کند؛ listener registration و crawl تک‌مرحله‌ای lifetime نیستند. gateway
روی همان client بازشده `disconnected` را await می‌کند و client یا Session دومی
نمی‌سازد. بازگشت عادی، cancellation یا failure task حیاتی پیش از shutdown با event
`runtime_task_completed_unexpectedly` و فقط `task_name`، `completion_kind` و
`failure_type` امن ثبت می‌شود. علت shutdown یکی از `requested`،
`critical_task_failed`، `telethon_disconnected` یا `startup_failed` است. shutdown
همهٔ taskها را پیش از بستن gateway و Foundation cancel/gather می‌کند.

خود client مالک با `auto_reconnect` فعال و تعداد تلاش/فاصلهٔ محدود برگرفته از
`telegram.ingestion.max_reconnect_attempts` و
`reconnect_initial_delay_seconds` ساخته می‌شود. در قطع موقت transport همان client،
Session lock و event handlerها حفظ می‌شوند؛ signal `disconnected` فقط پس از پایان
واقعی اتصال یا تمام‌شدن تلاش‌های محدود به supervision حیاتی می‌رسد.
Validation اولیه و `open` همان Session نیز با همین `RetryPolicy` محدود اجرا می‌شوند؛
خطاهای transient/timeout قابل retry هستند، ولی configuration، authorization و
permission بدون retry شکست می‌خورند. هر تلاش validation snapshot کامل کانال‌ها را
دوباره می‌سازد و هیچ client هم‌زمان یا Session owner دومی نگه نمی‌دارد.

مرز mapping زنده فقط `MessageMediaPhoto` و `MessageMediaDocument` را Media
قابل‌دانلود می‌داند؛ `MessageMediaWebPage` متن عادی همراه Entityهای اصلی باقی
می‌ماند و هرگز به downloader نمی‌رسد. Media source پس از resolve مجدد فقط شیء
concrete `message.photo` یا `message.document` را stream می‌کند. خطاهای امن mapping،
Domain و Media در مرز هر پیام skip/defer می‌شوند و مصرف همان subscription برای
پیام بعدی ادامه می‌یابد؛ فقط failure واقعی subscription یا اتصال وارد reconnect و
supervision Runtime می‌شود. لاگ این مسیر فقط شناسه، category و type امن دارد و
exception message یا payload را ثبت نمی‌کند.

زمان‌بندی جدید collectionهای مستقل `native_schedule_commands` و
`native_schedule_destination_leases` دارد. Callback فقط command نسخه‌دار
`post + destination + selection version` می‌سازد. Runtime برای هر مقصد lease
انحصاری می‌گیرد، تمام Scheduled Messages تلگرام (از جمله موارد خارجی) را می‌خواند
و `due_at = max(now_utc, latest_telegram_due_at) + 5 minutes` را در UTC پایدار
می‌کند. مرز `request_started` غیرقابل‌تکرار است؛ expiry پس از آن به
`outcome_unknown` می‌رود. لغو هم‌زمان با schedule با `cancel_after_schedule`
receipt را ابتدا ذخیره و سپس همان IDها را حذف می‌کند. reconciliation حضور کامل،
لغو خارجی، ناپدیدشدن پس از due و Album ناقص را بدون ادعای قطعی انتشار تفکیک می‌کند.

پیش‌نمایش Approval رسانه را از DTO شامل نوع واقعی، مسیر نسبی، MIME و نام اصلی
می‌گیرد. Infrastructure مسیر را زیر root canonical و بدون symlink/traversal
اعتبارسنجی می‌کند و Aiogram method متناظر نوع را با timeout آپلود مستقل فراخوانی
می‌کند. فایل Document پیش از ساخت `FSInputFile` از نظر وجود، regular-file، حجم و
خالی/خوانا نبودن اعتبارسنجی می‌شود و نام اصلی، از جمله پسوند `.npvt`، فقط به‌عنوان
نام upload استفاده می‌شود. Entityهای UTF-16 معتبر حفظ می‌شوند؛ metadata ناقص
`text_url`/`text_mention` و Custom Emoji فقط در preview ادمین حذف می‌شود و متن
قابل‌مشاهده و payload canonical انتشار تغییر نمی‌کند. خطای ۴۰۰ قطعی entity برای
Document دقیقاً یک بار بدون entity و با upload تازه retry می‌شود؛ خطای مبهم یا
timeout هرگز این fallback را اجرا نمی‌کند. reason امن در delivery هر مدیر ذخیره
می‌شود و raw Bot error، caption، مسیر و نام کامل فایل وارد log نمی‌شوند. Album
تنها برای preview به اولین Photo محدود است؛ Publication payload کامل تغییر
نمی‌کند. فرمان `approval-recover-documents` فقط Documentهای terminal با
`media_rejected` را با شناسهٔ دقیق یا بازهٔ زمانی محدود، dry-run و سقف ۱۰۰ مورد
آزاد می‌کند و delivery موفق مدیران را دست‌نخورده می‌گذارد.

### Destination `text_url` safety

entity نوع `text_url` در ingestion همراه URL اختیاری خود وارد مدل مستقل از SDK
می‌شود و همان metadata در Post، artifact مقصد، rebasing و publication payload
حفظ می‌شود. Publisher پس از کنترل UTF-16 آن را به `MessageEntityTextUrl` تبدیل
می‌کند. برای artifactهای legacy فاقد URL فقط entity لینک حذف و متن قابل‌مشاهده
حفظ می‌شود و event امن `publication_entity_omitted` ثبت می‌گردد. validationهای
پیش از `send_message`/`send_file` خطای typed و قطعی می‌سازند؛ uncertainty واقعی
شبکه/RPC همچنان ambiguous است. `publication-recover-presend` تنها با Post ID دقیق
و proof gate روی failure قدیمی `ValueError` پیش از send، job را idempotent برمی‌گرداند.

## 4. مدل Domain

مدل‌ها مستقل از Documentهای MongoDB و Objectهای SDK تلگرام‌اند. قراردادهای
پیاده‌شده در `domain.posts` عبارت‌اند از:

| مدل | مسئولیت پیاده‌شده |
|---|---|
| `PostId` | شناسهٔ داخلی opaque و مستقل از نوع شناسهٔ پایگاه‌داده |
| `SourceMessageIdentity` | کلید Idempotency برابر `(source_channel_id, source_message_id)`؛ شناسه کانال مبدا در startup از username resolve می‌شود |
| `TelegramEntity` | offset و length بر حسب UTF-16 code unit، نوع Entity و `custom_emoji_id` اختیاری |
| `OriginalPostContent` | متن، Caption و tupleهای مستقل Entityهای هرکدام، بدون normalization |
| `Post` | snapshot immutable هویت، اطلاعات منبع، محتوای اصلی، زمان‌ها، وضعیت، version و history |
| `PostStatus` | وضعیت کلی حداقلی Post در Milestone 0 |
| `StatusTransition` | وضعیت قبلی/جدید، زمان UTC، دسته Actor، دلیل و Correlation ID اختیاری |

مدل‌های `Publication` و `ScheduledPublication` اکنون state، lease، attempt،
Message ID، due time و audit جابه‌جایی را بدون نوع SDK/Driver نگه می‌دارند. مدل‌های
AI و Advertisement هنوز فقط برای Taskهای صریح بعدی برنامه‌ریزی شده‌اند.

تا پایان Milestone 3، `Administrator`، `CallbackClaims`، `ApprovalReference` و
`DestinationSelection` در `domain.admin_approval` پیاده شده‌اند. role یکتا
`admin`، permissionهای `approval.view` و `approval.toggle` و حالت‌های مستقل
`none/immediate/scheduled` هستند. Selection با history امن و version افزایشی
immutable است؛ hook پس از commit، orchestration فوری/زمان‌بندی را dispatch می‌کند.

### چرخهٔ عمر Post در Milestone 0

نام و مقدار persistence-facing وضعیت‌های فعلی دقیقاً `Discovered`، `Stored` و
`Expired` است. Post با `Discovered`، `version = 0` و history خالی ساخته می‌شود.
جدول کامل Transitionهای مجاز:

| وضعیت قبلی | وضعیت جدید | قید زمانی |
|---|---|---|
| `Discovered` | `Stored` | `received_at <= occurred_at < expires_at` |
| `Discovered` | `Expired` | `occurred_at >= expires_at` |
| `Stored` | `Expired` | `occurred_at >= expires_at` |
| `Expired` | — | Terminal |

هر Transition باید `expected_version` جاری را دریافت کند، یک snapshot تازه
برگرداند، version را دقیقاً یک واحد افزایش دهد و رکورد immutable جدید را به
انتهای history پیوست کند. Transition تکراری، عقب‌گرد، زمان نامعتبر، version
کهنه یا chain ناسازگار با Exception صریح Domain رد می‌شود. اعمال اتمیک شرط
version در MongoDB مسئولیت T004 است.

قواعد دیگر قرارداد فعلی:

- برابری و hash خود `Post` با `PostId` پایدار است؛ Idempotency دریافت فقط با
  `SourceMessageIdentity` سنجیده می‌شود.
- همهٔ timestampها aware هستند، هنگام ساخت به UTC تبدیل می‌شوند و
  `expires_at` دقیقاً `received_at + 14 days` است.
- محتوای اصلی، ترتیب Entityها، فارسی، نیم‌فاصله، خط‌شکست و Emoji بدون تغییر
  نگهداری می‌شوند. متن و Caption tupleهای Entity جدا دارند.
- offset و length مدل Entity واحد UTF-16 دارند؛ Adapter آینده مسئول تبدیل
  Objectهای SDK است. `custom_emoji_id` یک مقدار opaque است و فقط برای نوع
  `custom_emoji` پذیرفته می‌شود.
- snapshotها frozen هستند و دادهٔ اصلی با artifact پردازش‌شده یا مقصدی مخلوط
  نمی‌شود. وضعیت آیندهٔ هر `Post × Destination` نیز مستقل از `PostStatus`
  خواهد بود.
- reason و Correlation ID فقط رشته‌های builtin محدود و غیرخالی‌اند و همراه
  محتوای اصلی از `repr` حذف می‌شوند؛ object یا metadata دلخواه پذیرفته نمی‌شود.

## 5. Use Caseهای Application

### دریافت و پردازش

پیاده‌شده تا پایان Milestone 2:

- `AuthenticateTelegramSession` و `ValidateTelegramSession`
- `CrawlTodayTextPosts` و `HandleLiveMessage`
- `IngestPostIdempotently`
- `DownloadPostMedia` و `AssembleMediaGroup`
- `CleanupExpiredMedia`
- `DetectExactDuplicate` و `PrepareDestinationContent`
- `CategorizePost` و `PreparePostPipeline`

### تأیید و انتشار

- `AuthorizeAdminAction`
- `SendPostForApproval`
- `ToggleDestinationSelection`
- `SynchronizeApprovalMessages`
- `PublishPostImmediately`
- `SchedulePost`، `CancelScheduledPost` و `RunDuePublication`

### AI

- `EnqueueAIJob` و `ClaimAIJob`
- `ExecuteAIWithFallback`
- `DetectAdvertisement`
- `DetectSemanticDuplicate`
- `CategorizeWithAI`
- `UpdatePostScore`

### تبلیغات

- `FetchAdvertisementSource`
- `ExpandAdvertisementSlots`
- `PublishAdvertisementSlot`
- `ResolvePublicationCollision`
- `ReportAdvertisementRuns`

Use Caseها تراکنش یا Atomic Operation لازم را از Port اختصاصی درخواست می‌کنند؛ Transaction syntax و Queryهای MongoDB وارد Application نمی‌شوند.

## 6. Portها و Interfaceها

| Port | قرارداد مورد انتظار |
|---|---|
| `PostRepository` | insert یکتا با نتیجهٔ `Created/AlreadyExists/Conflict` و canonical ID، claim اتمیک مرحلهٔ بعد، دریافت، فهرست محدود غیرمنقضی و Transition اتمیک با expected version/status |
| `ApprovalRepository` | نگهداری Referenceها و وضعیت همگام‌سازی |
| `PublicationRepository` | ایجاد/Claim/تکمیل Idempotent انتشار |
| `ScheduleRepository` | محاسبه Slot اتمیک، Claim با Lease، لغو و بازیابی |
| `AIJobRepository` | Enqueue یکتا، اولویت، Lease، Retry و نتیجه |
| `ProviderStateRepository` | Reservation اتمیک ظرفیت و ثبت outcome سلامت برای هر Provider/Model |
| `AdvertisementRepository` | Campaign، Slot یکتا و گزارش اجرا |
| `TelegramSourceGateway` | احراز Session، Resolve کانال، History، Listener و دریافت URL |
| `TelegramPublisherGateway` | انتشار متن/مدیا/آلبوم با User API و Entityهای اصلی |
| `AdminMessagingGateway` | ارسال/ویرایش پیام تأیید و پاسخ Callback با Bot API |
| `MediaStorage` | ذخیره Stream، خواندن، Hash، حذف و مسیر خصوصی |
| `ContentPreparationRepository` | metadata Media، Album پایدار، duplicate/category/artifact و readiness اتمیک |
| `AIProvider` | اجرای یک Task روی یک Provider/Model و بازگرداندن پاسخ خام استانداردشده |
| `Clock` | زمان UTC و تبدیل مبتنی بر ZoneInfo برای تست قطعی |
| `IdGenerator` | شناسه‌های غیرقابل حدس برای Callback/Correlation |
| `UnitOfWork/Atomic Ports` | فقط در Use Caseهایی که چند تغییر باید یکپارچه باشند |

Port عمومی و مبهم مانند `Repository[T]` ایجاد نمی‌شود؛ قراردادها بر اساس عملیات واقعی هر Use Case شکل می‌گیرند.

## 7. مسئولیت Telegram User API

User API با حساب دارای Premium تنها مسئول این موارد است:

- ورود تعاملی اولیه، ذخیره Session خارج از Git و استفاده مجدد از آن؛
- اعتبارسنجی Session، Premium بودن حساب و دسترسی کانال‌ها؛
- Resolve کانال‌های عمومی، خزش History از ابتدای روز محلی تا اکنون و Listener زنده؛
- دریافت متن، Caption، همه Entityها، Media و Media Group؛
- دریافت پست تبلیغاتی از URL؛
- انتشار نهایی متن، Media و Album در کانال مقصد با حفظ Custom Emoji.

Adapter پیاده‌شده از `Telethon 1.44.0` استفاده می‌کند، اما Application فقط DTOها،
Portها و خطاهای application-owned را می‌شناسد. Session زیر `var/sessions` و خارج
از Git است؛ login صریح با lock محدود mutation هم‌زمان را رد می‌کند و startup عادی
هرگز prompt نمی‌زند. validation غیرتعاملی Account Premium، canonical numeric ID،
خواندن Source و permission انتشار Destination را بدون test message تجمیع می‌کند.

History بازهٔ نیمه‌باز `[شروع روز در timezone پیکربندی‌شده، Clock.now)` را به UTC
می‌برد و pagination محدود را پشت DTO page پنهان می‌کند. mapper متن/Caption، ZWNJ،
line break، emoji، timestamp و UTF-16 entity offset/length را بدون normalization
نگه می‌دارد و service/empty/media-only را رد می‌کند. Listener queue محدود و
backpressure دارد؛ source نامرتبط را رد، FloodWait/reconnect را محدود و هنگام
cancellation ابتدا unsubscribe/close را کامل می‌کند و سپس لغو را عبور می‌دهد.

## 8. مسئولیت Telegram Bot API

Bot API فقط کانال مدیریتی است:

- دریافت Command و Callback؛
- احراز Admin و مجوز مقصد پیش از هر عملیات؛
- تولید هدر، محتوای پیشنهادی و Keyboard؛
- ارسال پیام تأیید و ذخیره شناسه هر نسخه؛
- ویرایش هدر/Keyboard تمام مدیران به‌صورت best-effort و ثبت Retry مستقل؛
- گزارش نتیجه عملیات و گزارش تبلیغات.

Bot API برای انتشار نهایی به کانال مقصد استفاده نمی‌شود. Callback Data باید کوتاه و غیرقابل سوءاستفاده باشد؛ طرح اولیه، Token تصادفی opaque و رکورد server-side با انقضا است. HMAC کوتاه فقط در صورت اثبات کفایت محدودیت اندازه جایگزین می‌شود.

Adapter پیاده‌شده `aiogram==3.29.1` است و فقط private chat مدیران Config‌شده را
می‌پذیرد. Callback قالب `c1_<base64url>`، دارای ۱۲۸ بیت randomness، عمر ۱۴روز و
claimهای کاملاً server-side است. هر use، actor/permission/Post/Destination جاری را
دوباره اعتبارسنجی می‌کند. برای هر مدیر header canonical و content مستقل ارسال
می‌شود؛ header هرگز وارد artifact انتشار نمی‌شود.

Keyboard برای هر مقصد مجاز یک ردیف scheduled/immediate و حداکثر ۲۰ مقصد دارد؛
overflow fail-fast است. Toggle فقط selection آینده را با CAS تغییر می‌دهد و هیچ
Job یا انتشار نمی‌سازد. Sync از آخرین state، best-effort و version-aware است؛
not-modified موفق، deleted دائمی و خطای موقت حداکثر سه attempt با lease اتمیک است.

## 9. MongoDB و مدل ماندگاری

MongoDB منبع حقیقت پردازش است. Collection پیاده‌شدهٔ فعلی:

- `posts`: سند دقیق `schema_version = 1`، Unique Index روی
  `source_channel_id + source_message_id` و TTL تک‌فیلدی روی `expires_at`.

Collectionهای پیاده‌شدهٔ Milestone 2:

- `media`: metadata امن، hash، مسیر خصوصی نسبی و expiry بدون دادهٔ باینری؛
- `media_groups`: عضوهای idempotent و مرتب، arrivalهای ثبت‌شده پیش از دانلود،
  deadlineهای quiet/max-wait بر پایهٔ زمان مشاهده، و state اتمیک
  claim/lease/retry/permanent-failure برای finalization؛
- `content_preparation`: نتیجه‌های نسخه‌دار duplicate/category، artifact مستقل هر
  مقصد و marker یکتای readiness.

Collectionهای بعدی فقط برای Taskهای صریح آینده برنامه‌ریزی شده‌اند:

- `approvals`: Reference پیام‌های تأیید و وضعیت آخرین Sync؛
- `approval_deliveries`: outbox منطقی آماده‌ها، claim/lease تحویل، retry، وضعیت امن
  مقصدها و درخواست پایدار همگام‌سازی UI؛
- `publications`: Unique Idempotency Key برای هر تصمیم انتشار؛
- `scheduled_publications`: Index روی `status + due_at`، Unique Key و فیلدهای Lease؛
- `native_schedule_commands`: outbox نسخه‌دار Scheduled Messages بومی با receipt،
  UTC due، cancellation و outcome-unknown؛
- `native_schedule_destination_leases`: serialization انحصاری Slot هر مقصد؛
- `ai_jobs` و `ai_results`: Job پایدار، نتیجه، Prompt/Schema version و Claim اتمیک؛
- `provider_state`: وضعیت پایدار مستقل هر Provider/Model شامل Circuit، Cooldown،
  پنجره ثابت شمارش درخواست، version و آرایهٔ Reservationهای دارای owner/expiry؛
- `ai_result_cache`: نتیجه استاندارد نسخه‌دار با کلید یکتا و TTL روی سند disposable؛
- `ai_audit_events`: eventهای sanitized و append-only با event ID یکتا و TTL فقط در صورت retention صریح؛
- `ai_provider_metrics`: شمارنده‌ها و مجموع latency پایدار با هویت یکتای Provider/Model و بدون TTL؛
- `advertisement_campaigns` و `advertisement_slots`: تعریف و اجرای یکتای Slot؛
- `callback_tokens`: Token کوتاه، Actor/Action/Post/Destination و زمان انقضا.

### قرارداد پیاده‌شدهٔ `posts`

Application مالک `PostRepository`، resultها و exceptionهای مستقل از driver
است. Adapter `MongoPostRepository` و همهٔ نوع‌های PyMongo/BSON در Infrastructure
می‌مانند. Mapper فقط Schema دقیق نسخهٔ ۱ را می‌پذیرد و این داده‌ها را ذخیره
می‌کند:

- `_id` برابر مقدار opaque `PostId`، به‌همراه هویت یکتای منبع و metadata کانال؛
- `original_content` شامل متن، Caption و Entityهای جداگانه و مرتب هرکدام، بدون
  Unicode normalization؛
- زمان انتشار منبع، دریافت و انقضا به UTC، به‌همراه remainder لازم برای
  بازسازی دقیق میکروثانیه؛
- markerهای افزایشی `next_stage_claimed_at` و
  `next_stage_claim_correlation_id` برای claim اتمیک T011؛ سندهای قدیمی فاقد هر
  دو marker همچنان خوانده می‌شوند؛
- وضعیت، version و تمام transition history دامنه.

BSON زمان را با دقت میلی‌ثانیه نگه می‌دارد. زمان‌های عادی رو به پایین و
`expires_at` رو به بالا ذخیره می‌شوند و remainder جداگانه Mapper مقدار دقیق را
بازسازی می‌کند؛ بنابراین TTL هیچ سندی را پیش از لحظهٔ Domain حذف نمی‌کند.
readها افزون بر شرط coarse در Query، مرز exact `expires_at > as_of` را روی Post
بازسازی‌شده اعمال می‌کنند.

Indexهای مالکیت‌شده و نام‌های پایدار آن‌ها عبارت‌اند از:

| نام | کلید | option |
|---|---|---|
| `uq_posts_source_identity_v1` | `source_channel_id: 1, source_message_id: 1` | `unique: true` |
| `ttl_posts_expires_at_v1` | `expires_at: 1` | `expireAfterSeconds: 0` |

Initializer در هر Startup قابل تکرار است، تعریف واقعی را پیش و پس از ساخت
inspect می‌کند و Index هم‌نام یا هم‌کلید ناسازگار را بدون drop یا migration
خودکار Fail-fast رد می‌کند.

درج idempotent مستقیماً `insert_one` را فراخوانی می‌کند؛ check-then-insert وجود
ندارد. نتیجه canonical Post ID را برمی‌گرداند و محتوای متفاوت برای همان هویت
`Conflict` است. فقط DuplicateKey دقیق هویت منبع `AlreadyExists` است. رقابت `_id_` فقط پس
از خواندن رکورد و اثبات همان هویت منبع idempotent محسوب می‌شود؛ collision
نامرتبط Data Error است و دادهٔ موجود overwrite نمی‌شود. Transition دامنه‌ای با
یک `find_one_and_update` مشروط به `_id + schema_version + version + status`
اعمال می‌شود و نبود رکورد از writer کهنه تفکیک می‌شود.

Client رسمی `PyMongo AsyncMongoClient` است. عملیات شبکه‌ای ping، بررسی سازگاری،
Index و read/write با timeout محدود Configuration اجرا می‌شوند و cleanup
database تست نیز deadline محدود دارد. MongoDB 7.0 حداقل نسخهٔ پشتیبانی‌شده
است، Stable API v1 به حالت strict استفاده می‌شود و retry داخلی driver برای
read/write غیرفعال است؛ سیاست retry قابل مشاهده و طبقه‌بندی‌شده به T005 و
Taskهای عملیاتی بعدی تعلق دارد.

قواعد ماندگاری:

- ایجاد Indexها بخشی از Startup/Migration صریح است و خطای آن Fail-fast می‌شود.
- Unique insert و `findOneAndUpdate` با شرط نسخه/وضعیت برای Idempotency و Optimistic Concurrency به‌کار می‌روند.
- TTL MongoDB حذف آنی را تضمین نمی‌کند؛ `expires_at` در Queryهای Application نیز اعمال می‌شود.
- حذف TTL سند، فایل محلی را حذف نمی‌کند؛ Cleanup Worker مستقل Mediaهای منقضی و Orphan را پاک می‌کند.
- Migrationهای سازگار با عقب و ثبت نسخه Schema لازم‌اند؛ راهکار دقیق در زمان Bootstrap انتخاب می‌شود.

## 10. ذخیره Media

نسخهٔ پیاده‌شده از Storage محلی خصوصی پشت `MediaStorage` استفاده می‌کند. فایل‌ها
زیر root پیکربندی‌شده و ignored، بدون URL عمومی ذخیره می‌شوند. مسیر نهایی از
هویت داخلی و hash ساخته می‌شود و نام ورودی فقط metadata پاک‌سازی‌شده است؛ absolute،
traversal و symlink escape رد می‌شوند.

فرایند:

1. Stream با timeout و سقف حجم محدود به temp یکتا نوشته و هم‌زمان hash/size محاسبه می‌شود.
2. فایل کامل با rename اتمیک commit می‌شود؛ failure/cancellation temp را حذف می‌کند.
3. metadata پس از commit ثبت می‌شود و restart فایل سالم موجود را بدون truncate بازیابی می‌کند.
4. permissionهای POSIX به‌صورت best-effort محدود می‌شوند؛ در Windows محرمانگی
   کامل به ACL دایرکتوری runtime وابسته است.
5. Cleanup Worker در batch محدود candidate می‌گیرد، reference را دوباره بررسی
   می‌کند و فقط expired/orphan خارج از grace را در همان root حذف می‌کند.

Album با کلید canonical source + media-group ID ساخته می‌شود. arrival هر عضو پیش
از دانلود Media ثبت می‌شود تا دانلود کند یا history قدیمی quiet window را دور نزند.
finalizer هر گروه due را با claim/lease اتمیک می‌گیرد، همهٔ memberها را از نظر
source channel و Telegram grouped ID تطبیق می‌دهد و قدیمی‌ترین member بر حسب
`source_date + source_message_id` را به‌عنوان anchor پایدار انتخاب می‌کند. هویت
source، `PostId` و preparation همگی از Post همان anchor می‌آیند و نوع Photo، Video،
Document یا Animation در هویت دخالت ندارد. گروه ناقص با `next_attempt_at` و شمارندهٔ
bounded آزاد می‌شود؛ گروه malformed پس از سقف تلاش فقط خودش `permanent_failed`
می‌شود. رکورد legacy در صورت امکان از media identity و group key بازیابی می‌شود.
خطای داده‌ای یک گروه درون loop مصرف می‌شود، اما خطای Infrastructure از loop خارج
می‌شود تا supervisor بحرانی Runtime را متوقف کند. گروه completed پس از restart
دوباره claim نمی‌شود.

Duplicate دقیق از normalization حداقلی نسخه ۱ و serialization/hash نسخه ۱ استفاده
می‌کند. هیچ تبدیل `ی/ي`، `ک/ك`، ZWNJ، punctuation، URL یا Emoji انجام نمی‌شود و
hashهای Media به ترتیب وارد payload می‌شوند. query فقط پنجرهٔ غیرمنقضی ۱۴روزه را
می‌بیند و assignment اتمیک/restart-safe است.

تبدیل مقصد policy نسخه ۱، خالص و immutable است: reference منبع را جایگزین، reference
مقصد را محافظت و سایر username/linkهای Telegram را با spanهای non-overlap حذف
می‌کند. rebasing با واحد UTF-16 انجام می‌شود؛ URLهای غیرتلگرامی، فارسی، RTL، ZWNJ،
Emoji و Entity ناشناخته حفظ می‌شوند. دسته‌بندی policy نسخه ۱ به ترتیب manual
override معتبر، keyword قطعی با tie-break مستقل از ترتیب mapping، سپس default منبع
است و نتیجهٔ جدید override دستی تازه‌تر را بازنویسی نمی‌کند.

`PreparePostPipeline` state پایدار MongoDB را پیش از هر مرحله reload می‌کند،
side effect کامل‌شده را تکرار نمی‌کند، artifact مقصدها را مستقل می‌سازد و readiness
را اتمیک ثبت می‌کند. حالت AIهای هنوز پیاده‌نشده `NotRequested` است؛ فعال‌سازی
قابلیت AI فاقد پیاده‌سازی باید در validation/startup fail-fast شود.

Object Storage در Milestone 2 پیاده نشده است.

## 11. زمان‌بندی پایدار

Scheduler کتابخانه‌ای درون حافظه منبع حقیقت نیست. هر Job ابتدا در MongoDB ثبت می‌شود:

- زمان Slot هر مقصد با یک عملیات اتمیک و فاصله تنظیم‌شده محاسبه می‌شود.
- Worker، Jobهای Due را با `findOneAndUpdate` و Lease محدود Claim می‌کند.
- Unique Idempotency Key مانع اجرای دوباره پس از Restart/Retry می‌شود.
- شکست قطعی پیش از send به `WaitingForRetry` با Backoff/jitter محدود می‌رود؛
  شکست دائمی terminal و نتیجهٔ مبهم پس از send برابر `OutcomeUnknown` بدون resend است.
- لغو با شرط وضعیت انجام می‌شود تا Job در حال/تمام‌شده دوباره تغییر نکند.
- بازیابی Restart یعنی Claim دوباره Leaseهای منقضی، نه بازسازی Job از حافظه.

Slot صف خالی `now + interval` و صف غیرخالی `last_due + interval` است و queue
هر مقصد سند مستقل دارد. سیاست لغو پیش‌فرض `preserve` است. `recompact` فقط
`Pending/WaitingForRetry`های بعدی همان مقصد را جابه‌جا و old/new due، actor،
policy version، timestamp و correlation را audit می‌کند.

همین الگو برای Scheduled Publication، AI Job و Advertisement Slot استفاده می‌شود، اما Collection و سیاست هرکدام مستقل است.

## 12. Pipeline و Fallback هوش مصنوعی

Pipeline AI بخشی از فاز اول است، زیرا تشخیص تبلیغ، تکرار معنایی و امتیازدهی به آن وابسته‌اند. ترتیب اجرا:

1. درخواست به‌صورت `AIJob` یکتا و پایدار ثبت شود.
2. Worker با Lease و اولویت Claim کند.
3. Cache نسخه‌دار بر اساس Task، hash قطعی ورودی canonical، Prompt version، Schema version و زبان بررسی شود؛ Cache hit پیش از Guard و هر تماس خارجی برگردد.
4. Provider/Modelهای فعال و پشتیبان Task از Configuration انتخاب و مرتب شوند.
5. سلامت، Cooldown، Circuit و سهمیه به‌صورت اتمیک Reserve شود.
6. درخواست با Timeout و Retry داخلی محدود اجرا شود.
7. پاسخ با Schema مخصوص Task اعتبارسنجی و حداکثر یک‌بار Repair شود.
8. پاسخ نامعتبر یا شکست غیرقابل Retry به Model/Provider بعدی Fallback کند.
9. نتیجهٔ Normalizeشدهٔ معتبر با first-valid-write-wins در Cache نوشته و Attempt/Audit/Metrics امن ثبت شود؛ شکست side effect نتیجه معتبر را دور نمی‌اندازد.
10. شکست همه Providerها بدون نتیجه جعلی و طبق سیاست Task به Retry آینده، توقف، ادامه یا بررسی دستی منتهی شود.

Adapterهای تک-Attempt مستقل `z-ai` و `deepseek` پس از ثبت تصمیم رسمی ساخته شده‌اند. هر دو فقط Port
`AIProvider` و `RawResponseEnvelope` متعلق به Application را پیاده می‌کنند و هنوز به Composition Root، Worker،
CLI یا جریان Telegram متصل نیستند. DeepSeek فقط Modelهای allowlisted و Taskهای مصوب را پیش از تماس HTTP
می‌پذیرد، redirect و میزبان production غیرمصوب را رد می‌کند و در هر فراخوانی دقیقاً یک request می‌فرستد.
Routing، Retry، Fallback، Repair و Normalization در T038 و T039 پیاده شده‌اند. T040 پیش از هر تلاش واقعی
`ProviderGuard` را اجرا می‌کند: policy کامل و صریح کاندید را می‌خواهد، Reservation را با یک update اتمیک
MongoDB می‌گیرد، آن را تا پایان درخواست نگه می‌دارد و outcome تایپ‌شده را در مسیر cancellation-safe ثبت
می‌کند. گزینهٔ موقتاً نامتاح بدون تماس Provider و بدون مصرف attempt رد می‌شود و نزدیک‌ترین زمان eligibility
را برمی‌گرداند. این اجزا همچنان از Composition Root، Worker، CLI و جریان Telegram جدا هستند.

Collection `provider_state` برای هر `provider_name + model_name` یک سند یکتا دارد. `active_reservations`
چند lease مستقل با `reservation_id`، `owner_id`، نوع normal/probe و زمان‌های UTC نگه می‌دارد؛ acquire با
update pipeline واحد، leaseهای منقضی را حذف و هم‌زمان concurrency، پنجره ثابت request-count، Cooldown و
Circuit را enforce می‌کند. State پایدار TTL ندارد تا Circuit، failure counter، پنجره و version حذف نشوند.
پس از پایان Open دقیقاً یک probe اتمیک HalfOpen مجاز است. فقط timeout، transport گذرا، server failure واجد
شرایط و Rate Limit تایپ‌شده بر سلامت اثر می‌گذارند؛ cancellation، auth، permission، config و مدل نامعتبر
failure counter را افزایش نمی‌دهند. Quota یا limit واقعی هیچ Provider حدس زده نشده و اجرای route فاقد policy
پیش از تماس خارجی با ConfigurationError امن متوقف می‌شود.

T041 کلید Cache را بدون Provider/Model از canonical JSON قطعی با UTF-8 و
`ensure_ascii=False` می‌سازد؛ در نتیجه خروجی معتبر Providerهای مختلف برای هویت ورودی
یکسان قابل اشتراک است. Lookup منقضی یا ناسازگار حتی پیش از حذف غیرهم‌زمان TTL miss
است. درج هم‌زمان first-valid-write-wins است و writer بازنده نتیجه معتبر ذخیره‌شده را
می‌پذیرد. Cache hit هیچ Reservation، تماس Provider، attempt یا fallback مصرف نمی‌کند.

Audit با event identity پایدار append-only و idempotent است و فقط metadata امن را
نگه می‌دارد. Metrics برای هر Provider/Model با incrementهای اتمیک شمارنده، tokenهای
موجود و `total_latency / latency_sample_count` ثبت می‌شود و در ترتیب Route اثری ندارد.
شکست Cache/Audit/Metrics به warning تایپ‌شده و sanitized تبدیل می‌شود و `AIResult`
معتبر را نابود نمی‌کند. این ترکیب همچنان ایزوله است و در Composition Root عملیاتی،
Worker، CLI، Telegram، Approval، Publication یا Scheduling ثبت نشده است.

T042 یک state پردازشی مستقل و افزایشی روی `Post` می‌افزاید و lifecycle پایهٔ
`Discovered/Stored/Expired` را overload نمی‌کند. `DetectAdvertisement` فقط وقتی هر دو
flag سراسری و per-source فعال باشند، AIJob یکتای
`post + advertisement_detection + prompt/schema version` را enqueue می‌کند. handler
ایزوله فقط `AIResult` نرمال‌شده و معتبر همان Job/Post/Prompt/Schema را مصرف می‌کند و
نتیجه یا شکست را با expected processing version در یک CAS MongoDB ثبت می‌کند. نتیجهٔ
تبلیغاتی به `RejectedAsAdvertisement`، نتیجهٔ غیرتبلیغاتی به
`AdvertisementCheckPassed` می‌رود؛ confidence فقط metadata است و آستانه‌ای اختراع
نشده است. نتیجهٔ cache hit دقیقاً همین قرارداد را طی می‌کند.

شکست نهایی فقط یکی از policyهای صریح `continue_processing`، `stop_processing`،
`retry_later` یا `manual_review` را اجرا می‌کند و هیچ نتیجهٔ AI/تبلیغ ساختگی نمی‌سازد.
حالت `AdvertisementManualReviewRequired` یک handoff تایپ‌شده و قابل مصرف برای جریان
Application تأیید موجود است، اما T042 هیچ Bot UX، Worker، CLI یا Runtime wiring اضافه
نمی‌کند. اسناد Post قدیمی در نبود بخش `advertisement_processing` به `NotRequested`
نسخه صفر خوانده می‌شوند و متن، Caption، Entity و Media اصلی دست‌نخورده می‌مانند.

T043 یک state مستقل `semantic_duplicate_processing` به Post افزوده است. مرحله فقط
پس از عبور Advertisement و exact duplicate اجرا می‌شود؛ exact match همچنان
short-circuit قطعی T016 است. query نامزدها projection حداقلی دارد، بازهٔ دریافت
`[now - 14 days, now]` را با expiry سخت‌گیرانهٔ `expires_at > now` اعمال می‌کند و
با ترتیب `received_at DESC, _id ASC` قطعی است. index
`ix_posts_semantic_window_v1` همین scan را پشتیبانی می‌کند.

هر Post یک AIJob نسخه‌دار semantic دارد و شناسهٔ نامزد فقط در Application به متن
مقایسه نگاشت می‌شود؛ هیچ Post ID یا metadata تلگرام به Provider داده نمی‌شود.
خروجی schema 2 شامل Boolean، `similarity` و `confidence` مستقل است. ناسازگاری
Boolean با threshold یک Provider result نامعتبر است. handler بالاترین similarity
را انتخاب و در تساوی ترتیب query را حفظ می‌کند، سپس result/state را با CAS ثبت
می‌کند. policyهای نتیجه و شکست صریح‌اند و این مسیر همچنان Worker/Runtime ندارد.

### قراردادها و مدل‌های پیاده‌سازی‌شده فاز اول

در راستای فراهم‌سازی زیرساخت هوش مصنوعی مستقل از ارائه دهنده (Provider-agnostic)، بخش‌های زیر تعریف و پیاده‌سازی شده‌اند:

1. **انواع وظایف هوش مصنوعی (`AITaskType`):**
   - `advertisement_detection`: تشخیص خودکار پست‌های تبلیغاتی.
   - `semantic_duplicate`: سنجش میزان تشابه مفهومی و معنایی پست‌ها.
   - `categorization`: دسته‌بندی موضوعی پست‌ها بر اساس کلمات کلیدی یا موضوع.
   - `scoring`: امتیازدهی به کیفیت و جذابیت پست‌ها در بازه ۱ تا ۱۰.

2. **قراردادهای داده‌ای ورودی و خروجی (Context & Output Schemas):**
   - برای هر تسک یک کلاس درخواست ورودی (Context) مستقل تعریف شده است:
     - `AdvertisementDetectionContext`: متن اصلی پست.
     - `SemanticDuplicateContext`: متن پست جدید، متن کاندید مقایسه و آستانه شباهت.
     - `CategorizationContext`: متن پست و فهرست دسته‌بندی‌های مجاز.
     - `ScoringContext`: متن پست جهت ارزیابی.
   - برای خروجی هر تسک، یک کلاس پایدار مبتنی بر Pydantic تعریف شده است؛ schemaهای عمومی نسخه ۱ باقی مانده‌اند و Semantic Duplicate به‌طور صریح به نسخه ۲ ارتقا یافته است:
     - `AdvertisementDetectionOutput`: شامل `is_advertisement` (bool)، `confidence` (float 0..1) و `reason` (str).
     - `SemanticDuplicateOutput` نسخه ۲: شامل `is_duplicate` (bool)، `similarity` و `confidence` مستقل (هر دو float 0..1) و `reason` محدود.
     - `CategorizationOutput`: شامل `category` (str)، `confidence` (float 0..1) و `reason` (str).
     - `ScoringOutput`: شامل `score` (int 1..10)، `confidence` (float 0..1) و `reason` (str).

3. **درگاه ارائه‌دهنده (`AIProvider`):**
   - این درگاه قرارداد لازم جهت اجرای تلاش‌ها (Attempts) روی کلاینت‌های مختلف را تعریف می‌کند. تابع `execute_attempt` درخواست را همراه با پرامپت، کانتکست ورودی، و جزئیات Timeout مستقل از فریم‌ورک‌های تلگرام یا درایور دیتابیس دریافت نموده و پاسخ خام را در قالب استاندارد `RawResponseEnvelope` بازمی‌گرداند.

4. **رجیستری پرامپت‌های نسخه‌دار (`PromptRegistry`):**
   - پرامپت‌ها در مسیر `application/ai/prompts/` به صورت فایل‌های متنی UTF-8 همراه با فرانت‌متر مشخص‌کننده‌ی ویژگی‌ها بارگذاری می‌شوند.
   - رجیستری صحت تطابق نسخه پرامپت، وظیفه (Task) و نسخه اسکیما (Schema Version) را به محض لود شدن کنترل کرده و مانع نسخه‌های تکراری می‌شود (Fail-Fast).
   - هش قطعی پرامپت‌ها با استفاده از الگوریتم SHA-256 و نرمال‌سازی انتهای خطوط (LF) محاسبه می‌شود تا پایداری هش در تمامی سیستم‌عامل‌ها حفظ گردد.

5. **صف ماندگار کارهای هوش مصنوعی (`AIJob`):**
   - **چرخه عمر و وضعیت‌ها (`AIJobStatus`):** شامل `Pending` (در انتظار پردازش)، `Processing` (در حال پردازش)، `WaitingForRetry` (در انتظار تلاش مجدد به دلیل بروز خطا)، `Completed` (تکمیل موفقیت‌آمیز)، `AllProvidersFailed` (شکست دائمی پس از اتمام تمامی تلاش‌ها و ارائه‌دهندگان کاندید)، `Cancelled` (لغو توسط مدیر) و `Expired` (انقضا به دلیل رد شدن از بازه زمانی مجاز انباشت ۱۴روزه).
   - **کلید یکتایی هوش مصنوعی (`idempotency_key`):** تولید شناسه یکتا به صورت ترکیب شناسه پست تلگرام، نوع کار هوش مصنوعی، نسخه پرامپت و نسخه اسکیما (به فرمت `post_id:task_type:prompt_version:schema_version`). این کلید به عنوان یک ایندکس یکتا در سطح MongoDB یکپارچگی درج‌های تکراری را در شرایط اجرای موازی تضمین می‌کند.
   - **اولویت‌بندی (`AIJobPriority`):** تعیین سطح اولویت‌ها به ترتیب High (۳۰)، Medium (۲۰) و Low (۱۰).
   - **دریافت اتمیک (`ClaimAIJob`):** دریافت و رزرو کار جدید توسط کارگر به صورت یک تراکنش اتمیک در دیتابیس MongoDB با مرتب‌سازی بر اساس بالاترین اولویت، اولین زمان اجرا (`next_run_at`) و در نهایت قدیمی‌ترین زمان ایجاد.
   - **مکانیزم اجاره (`lease_owner` و `lease_expires_at`):** قفل کردن کار کاندید تحت مالکیت کارگر فعال برای مدت معین (Lease) جهت پیشگیری از تداخل همزمان. در صورت بروز Crash یا اتمام مهلت، کار مجدداً در فرآیند اجرا قرار گرفته و توسط دیگر کارگرها قابل بازپس‌گیری (Reclaim) خواهد بود.
   - **کنترل همروندی خوش‌بینانه (`Optimistic Concurrency Control`):** هرگونه ثبت تغییر نهایی در وضعیت کارها با مقایسه نسخه سند (`version`) و شناسه آن انجام می‌شود.

6. **جریان پردازش و یکسان‌سازی پاسخ هوش مصنوعی (Validation, Repair, Normalization):**
   - **فراخوانی اولیه (Parse):** `ResponseParser` پاسخ خام `RawResponseEnvelope.raw_content` را بررسی و بخش کلیدی پیام دستیار هوش مصنوعی (`choices[0].message.content`) را استخراج و به عنوان JSON پارس می‌کند.
   - **سیاست اصلاح قطعی و بدون حالت (Deterministic, Stateless Repair):** در صورتی که پارس اولیه به دلیل وجود Markdown Code Fences (مانند ` ```json ... ``` `) با شکست مواجه شود، در صورتی که متن اضافه یا Prose دیگری وجود نداشته باشد (مجموعاً ۲ تگ backtick)، یک‌بار اقدام به حذف تگ‌ها و استخراج بلاک اصلی JSON می‌کند. هرگونه شکست مجدد یا ناتوانی در اصلاح، بلافاصله منجر به لغو تلاش با شکست قطعی می‌شود.
   - **اعتبارسنجی سخت‌گیرانه (Strict Validation):** ساختار نهایی استخراج‌شده توسط `ResponseValidator` با Schema متناظر تسک در کلاس‌های Pydantic اعتبارسنجی می‌شود. ویژگی `strict=True` و `extra="forbid"` بر روی تمامی خروجی‌ها فعال است تا از هرگونه Coercion نوع داده‌ای ناخواسته یا ارسال فیلدهای ناشناخته جلوگیری به عمل آید.
   - **یکسان‌سازی و ساختار خروجی استاندارد (Normalization):** پس از راستی‌آزمایی کامل، خروجی به شکل مستقل از ارائه‌دهنده در قالب `AIResult` یکسان همراه با متادیتای واقعی (شناسه مدل، ارائه‌دهنده، توکن‌های مصرفی و تاخیر) کپسوله‌سازی می‌شود.
   - **دسته‌بندی خطاها (Failure Taxonomy):** کلاس‌های استثنای مشخص و مستقل مانند `AIEmptyResponseError` ،`AIInvalidJSONError` ،`AISchemaValidationError` و `AIValidationConstraintError` (همگی زیرمجموعه `ValidationError` یا `ApplicationError`) برای استفاده کارآمد جریان‌های مسیریابی و Fallback در T039 پیاده‌سازی شده‌اند.

## 13. Configuration و Secret

- قرارداد Commitشدنی فقط `config/configuration.example.json` با
  `configuration_schema_version = 1` است و هیچ مقدار حساس ندارد. نسخهٔ عددی
  ناشناخته بدون Migration خودکار Fail-fast می‌شود.
- مدل‌های `ApplicationConfig` و زیرمدل‌ها در `shared.config` با Pydantic v2،
  `frozen=True`، scalarهای strict، tupleهای immutable و `extra="forbid"`
  ساخته می‌شوند. `tzdata` پذیرش پایدار IANA ZoneInfo از جمله `Asia/Tehran` را
  روی Windows و محیط‌های فاقد tzdb سیستم تضمین می‌کند.
- Config شامل MongoDB، Session path و ورودی Login، Bot/approval chat، Adminها،
  Source/Destinationها، Feature Flagها، Timezone، Logging، اسکلت AI routing و
  Advertisement routing است. bounds معتبر History page/pagination/timeout و
  Listener buffer/reconnect/FloodWait نیز typed و immutable هستند.
- `load_configuration(Path, environ=...)` تنها API خواندن برای Composition
  Root است و `LoadedConfiguration(settings, secrets)` برمی‌گرداند. هیچ Domain،
  Application Use Case یا Adapter فایل JSON یا Environment را مستقیم نمی‌خواند.
- Loader فایل را با `encoding="utf-8"` و JSON سخت‌گیرانه می‌خواند؛ duplicate
  key، عدد غیراستاندارد، Root نامعتبر، Enum/Range/URL/ZoneInfo نامعتبر، هویت
  تکراری و reference ناشناخته رد می‌شوند. خطاهای structural، semantic و Secret
  مستقل تا سطح field/item تجمیع و با مسیر پایدار گزارش می‌شوند.
- Config نمونه و غیرمحلی فقط `SecretReference.environment_variable` را می‌پذیرند.
  فایل `configuration.local.json` یا `configuration.<profile>.local.json` می‌تواند
  literal مستقیم برای Secretهای پشتیبانی‌شده داشته باشد؛ Loader آن را پیش از ساخت
  مدل به binding opaque تبدیل می‌کند. مقدارها در `ResolvedSecrets` مبتنی بر
  `SecretStr` نگهداری می‌شوند؛ `repr` و Exceptionها redacted هستند و context خام
  parser/validator به Exception عمومی متصل نمی‌ماند.
- Load هیچ Session path را باز یا canonicalize نمی‌کند و هیچ اتصال شبکه،
  MongoDB، Telegram یا AI ندارد. در نتیجه Timeout/Retry عملیاتی در T002 مصداق
  ندارد و فراخوانی تکراری با ورودی یکسان بدون side effect و idempotent است.

Dynamic reload فقط برای فاز پنجم مطرح است؛ نسخه اولیه برای تغییر Config نیاز به Restart کنترل‌شده دارد.

## 14. Logging، Retry، Idempotency و هم‌زمانی

### Logging

Foundation پیاده‌شدهٔ T005 در `shared/observability` از logger یا state سراسری
قابل‌تغییر استفاده نمی‌کند. `StructuredLogger`، Sink و Clock را تزریق می‌گیرد،
حداقل level معتبر T002 را اعمال می‌کند و هر event تولیدشده حداقل `timestamp`
UTC، `level`، `event_name` و `correlation_id` دارد. شناسه‌های اختیاری
Task/Job/Post/Channel/Destination/Admin فقط از `CorrelationContext` frozen وارد
می‌شوند. Binding با `ContextVar` و token reset، context را در `await` نگه می‌دارد
و میان coroutineهای هم‌زمان جدا می‌کند.

`Redactor` پیش از Sink و JSON روی یک کپی بازگشتی و depth-bounded اجرا می‌شود؛
marker ثابت آن `[REDACTED]` است. sensitive key/value، Authorization، credential
داخل URI، URLهای secret-bearing، Exception و کلیدهای محتوای کامل Telegram
پوشانده می‌شوند، بدون تغییر object ورودی یا متن فارسی پیرامون Secret. Formatter
JSON از `ensure_ascii=False` و JSON سخت‌گیرانه استفاده می‌کند.

### Retry

- `shared/errors.py` ده category پایدار Validation، Configuration،
  Authorization، Permission، Permanent، Transient، Timeout، Rate-limit،
  Concurrency-conflict و Already-completed را تعریف می‌کند. فقط سه category
  Transient/Timeout/Rate-limit retryable هستند؛ خطاهای موجود Config و
  PostRepository با tag ساختاری خود، بدون برعکس‌کردن dependency، در همین taxonomy
  طبقه‌بندی می‌شوند.
- `ExternalOperationPolicy` وجود timeout مثبت و finite را به‌عنوان قرارداد
  Adapter تثبیت می‌کند؛ T005 هیچ تماس خارجی تازه‌ای نمی‌سازد.
- `RetryPolicy` حداکثر ۱۰ attempt، delay اولیه، multiplier، cap و jitter محدود را
  immutable نگه می‌دارد. Sleeper و jitter source به executor تزریق می‌شوند.
- `execute_with_retry` فقط وقتی اجرا می‌شود که caller امن/idempotent بودن operation
  را صریحاً اعلام کند. هر retry و شکست نهایی event redacted دارد؛ exhaustion همان
  Exception نهایی را حفظ می‌کند و `CancelledError` حین operation یا backoff فوراً
  عبور می‌کند.
- Retry Provider، Fallback، FloodWait adapter، Circuit Breaker، DLQ و اتصال retry
  به MongoDB/Telegram/AI خارج از T005 و در Taskهای خود باقی می‌مانند.

### Idempotency

- ingest: `source_channel_id + source_message_id`؛
- انتشار عادی: کلید درخواست پایدار برای `post + destination + action`؛
- Job AI: `post + task + prompt/schema version`؛
- تبلیغ: `campaign + destination + scheduled slot`.

### Concurrency

- Atomic update و Unique Index خط دفاع اول‌اند.
- Crawl و Listener فقط `IngestPostIdempotently` را فراخوانی می‌کنند. درج یکتا و
  claim بعدی دو primitive اتمیک MongoDB هستند؛ فقط یک producer claim را می‌برد و
  duplicate producer همان canonical Post ID را دریافت می‌کند.
- Domain هر Transition را با `expected_version` و وضعیت فعلی اعتبارسنجی می‌کند؛
  Adapter T004 همین شرط را در update اتمیک MongoDB enforce می‌کند.
- Workerها Lease دارای انقضا می‌گیرند.
- ویرایش پیام مدیران fan-out و best-effort است؛ شکست یک پیام مانع بقیه نیست.
- قفل Process-local برای صحت توزیع‌شده کافی محسوب نمی‌شود.

## 15. راهبرد تست

1. **Unit:** Domain rules، Transitionها، Normalize/Hash، Entity rebasing، Toggle،
   Slot calculation، taxonomy/retry policy، structured logging، context isolation،
   redaction امنیتی، AI schema/fallback و Permission.
2. **Integration:** Adapter MongoDB روی database یکتای آزمایشی و loopback با
   Index/Atomicity/TTL semantics؛ lifecycle پایه با Config موقت، Environment
   مصنوعی، startup تکراری و target ناموجود bounded؛ Milestone 1 همین MongoDB را
   با Gateway مصنوعی برای crawl، listener و concurrent claim مصرف می‌کند.
3. **Contract:** fixtureهای مصنوعی User API برای Session، channel access و live
   message بدون Secret واقعی؛ Bot API/Providerها در Taskهای آینده.
4. **End-to-end کنترل‌شده:** MongoDB واقعی آزمایشی و Gateway مصنوعی برای
   subscribe-before-crawl، overlap، disconnect و restart با Session/database مشترک؛
   تست Sandbox تلگرام فقط opt-in و خارج از suite پیش‌فرض است.
5. **Restart/Concurrency:** Startup/index تکراری، shutdown/cancellation lifecycle،
   خاموش‌کردن Worker پس از Claim، انقضای Lease، رویداد تکراری و چند Worker.
6. **Security:** Callback جعلی، Admin غیرمجاز، Secret redaction، Path traversal Media و Config نامعتبر.

هر Task تست‌های متمرکز خود را دارد و Taskهای Stabilization سناریوهای بین‌لایه‌ای همان Milestone را تثبیت می‌کنند. تست زنده Provider/Telegram نباید شرط اجرای Unit Suite باشد.

## 16. مرز اولیه و توسعه آینده

| موضوع | نسخه اولیه | آینده |
|---|---|---|
| استقرار | یک Package و چند Entry Point/Worker قابل اجرای مشترک | تفکیک Process/Service فقط با نیاز مقیاس |
| Media | Storage محلی خصوصی | Object Storage |
| حساب Telegram | یک حساب Premium | چند حساب |
| Config | فایل + Environment، Restart برای تغییر | پنل و Reload پویا |
| مدیریت | Bot API | Web Panel/API |
| تحلیل | نیازهای AI فاز اول و دوم | غنی‌سازی، ترجمه، تحلیل عملکرد |
| Messaging داخلی | Mongo durable jobs | Broker فقط در صورت اثبات نیاز |

## 17. ابهام‌های باز

این موارد نباید هنگام پیاده‌سازی بی‌صدا تصمیم‌گیری شوند:

1. SDK مربوط به Bot API هنوز تعیین نشده است؛ User API در ADR-014 روی Telethon تثبیت شد.
2. رفتار دکمه «فوری» هم Toggle انتخاب و هم آغاز فوری انتشار توصیف شده؛ وجود یا نبود مرحله Confirm باید روشن شود.
3. `minimum AI score` برای پیشنهاد کانال مبدا با امتیازدهی حداقل ۲۰ دقیقه بعد و امکان ارسال زودتر برای تأیید تعارض زمانی دارد.
4. Providerها، Modelها، روش Auth، Quota و Schema واقعی AI مشخص نشده‌اند.
5. بخش ۷ Fallback چندمدلی را آینده می‌نامد، اما بخش ۱۱ آن را با معیار پذیرش الزام‌آور می‌کند؛ این نقشه‌راه بخش ۱۱ را برای فاز اول ملاک گرفته است.
6. مقصد پیام تأیید می‌تواند گروه/کانال مشترک یا گفت‌وگوی جداگانه هر مدیر باشد؛ حالت‌های لازم و UX نهایی مشخص نیست.
7. آستانه «بسیار نزدیک» خارج از Semantic Duplicate T043 هنوز مقدار قطعی ندارد؛ آستانه و policy معنایی در ADR-031 حل شده‌اند.
8. Storage اولیه محیط Production (دیسک پایدار یا Object Storage)، سقف حجم Media و ظرفیت ۱۴روزه معلوم نیست.
9. رفتار با Edit/Delete پیام منبع و پیام‌های Forwardشده تعریف نشده است.
10. سیاست Collision تبلیغ و پست عادی گزینه‌ها را نام می‌برد ولی Default قطعی ندارد.
11. رفتار Cache تبلیغ پس از Edit منبع قابل تنظیم است، اما Default و بازه Refresh تعیین نشده‌اند.
12. سطح دسترسی/Roleهای Admin، Commandهای گزارش و عملیات Reject صریح تعریف نشده‌اند.
13. فازهای سوم تا پنجم پیشنهادی‌اند و معیار پذیرش، UX، داده و اولویت قطعی ندارند؛ T055 تا T057 ابتدا آن‌ها را قابل برنامه‌ریزی می‌کنند.

ابهام‌های 1 تا 13 در زمان فعال‌شدن Task مرتبط باید حل و در `docs/DECISIONS.md` یا Requirement اصلاح‌شده ثبت شوند؛ هیچ‌کدام مانع T001 نیست.
