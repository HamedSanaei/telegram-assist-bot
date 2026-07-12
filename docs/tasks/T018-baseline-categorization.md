# T018 — دسته‌بندی پایه و Override

## وضعیت

Completed

## هدف

ایجاد دسته‌بندی deterministic غیر-AI بر اساس override دستی، قواعد keyword و دستهٔ پیش‌فرض Source، همراه مدل نتیجه و precedence صریح برای استفاده تا زمان T044.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.11 دسته‌بندی پست`، روش‌های پیش‌فرض، keyword و manual.
- `docs/REQUIREMENTS.md`، بخش `5.2 کانال‌های مبدا`، دسته‌بندی پیش‌فرض.
- `docs/REQUIREMENTS.md`، بخش `15. تست‌ها`، تست رفتار Application.
- `docs/ARCHITECTURE.md`، بخش `5. Use Caseهای Application`، `CategorizePost`.
- `docs/ARCHITECTURE.md`، بخش `16. مرز اولیه و توسعه آینده`، AI بعدی.

## وابستگی‌ها

- T002 — Configuration و Secret Validation؛ باید Completed باشد.
- T003 — مدل Domain و چرخه عمر Post؛ باید Completed باشد.

## محدوده

- تعریف شناسه/نام Category typed و مدل `CategorizationResult` با method، rule/version، timestamp و optional reason.
- اعتبارسنجی catalog دسته‌ها، default هر Source و keyword ruleها در Configuration.
- precedence صریح: manual override معتبر، سپس keyword rule deterministic، سپس Source default.
- تعریف tie-break deterministic برای چند rule هم‌امتیاز با priority/order صریح.
- match keyword با policy محدود و مستند برای Persian/case/whitespace، بدون semantic inference.
- Use Case pure برای انتخاب دسته و transition/assignment Domain در حد قرارداد T003.
- قابلیت جایگزینی نتیجه baseline توسط manual override بعدی بدون حذف audit قبلی.

## خارج از محدوده

- AI categorization و fallback Provider؛ T044.
- UI/Callback انتخاب دستی مدیر؛ Taskهای Milestone 3.
- پیشنهاد خودکار category catalog یا multi-label categorization مگر Requirement اصلاح شود.
- stemming، NLP پیچیده، embedding یا translation.
- persistence Mongo اختصاصی؛ فقط استفاده از Port موجود در Task ادغامی بعدی.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/categories/models.py`
- `src/telegram_assist_bot/application/categorize_post.py`
- توسعهٔ مدل‌های Configuration دسته/keyword.
- `tests/unit/application/test_categorize_post.py`
- `tests/unit/shared/config/test_category_validation.py`

## نکات پیاده‌سازی

- Category identity از display name جدا باشد تا متن فارسی قابل تغییر نمایشی، contract ذخیره‌شده را نشکند.
- match policy و version در result ثبت شود؛ original text تغییر نکند.
- **ریسک Configuration:** category/rule ID یکتا، reference default معتبر، priority deterministic و empty keyword رد شود.
- **ریسک Migration:** rename ID یا حذف category نیازمند migration Postهای موجود است؛ این Task rewrite تاریخی نمی‌کند.
- **ریسک Compatibility:** manual result باید با قرارداد آینده Presentation/AI سازگار ولی provider field نداشته باشد.
- **ریسک Concurrency:** categorization با expected version assignment شود یا pure result برگرداند؛ manual override هم‌زمان نباید با baseline overwrite شود.
- **ریسک Security:** keyword/reason/payload کامل Log نشود؛ config ورودی محدودیت طول/تعداد داشته باشد.

## معیارهای پذیرش عینی

1. manual override معتبر همیشه بر keyword/default مقدم است.
2. keywordهای matchشده طبق priority/tie-break دقیق نتیجهٔ ثابت دارند.
3. نبود match به default همان Source می‌رسد.
4. category/rule/default نامعتبر در Startup با مسیر فیلد رد می‌شود.
5. متن فارسی و casing/whitespace طبق policy صریح match می‌شوند و ZWNJ ناخواسته حذف نمی‌شود.
6. assignment baseline، manual جدیدتر را overwrite نمی‌کند.
7. نتیجه method/version/rule ID لازم را ثبت می‌کند و هیچ AI فراخوانی نمی‌شود.

## Unit Testهای الزامی

- precedence manual/keyword/default.
- tie-break چند rule، priority و order مستقل از ترتیب map.
- Persian keyword، ZWNJ، casing لاتین و boundary false positive.
- Config category/rule/reference validation.
- concurrent/stale assignment در قرارداد Domain.
- audit/result model و immutability متن.

## Integration Testهای الزامی

N/A. این Task فقط مدل، Configuration validation و تصمیم pure baseline دارد؛ persistence و pipeline در T019 Integration Test می‌شوند و هیچ Adapter خارجی تازه‌ای وجود ندارد.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_categorize_post.py tests/unit/shared/config/test_category_validation.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

بازبینی manual Persian fixtures، Persian diff، Config نمونه و `git diff --check` الزامی است.

## نتایج نهایی راستی‌آزمایی

- فرمان واقعی متمرکز: `uv run pytest tests/unit/application/test_categorize_post.py tests/unit/shared/config/test_category_validation.py --basetemp .pytest-tmp/m2-t018-final-20260712-100830-998 -q`؛ نتیجه `4 passed` و `0 skipped` بود. Integration assignment نیز در اجرای مشترک `m2-focused-final-20260712-100724-136` برابر `1 passed` بود.
- precedence manual/keyword/default، tie-break، Persian/ZWNJ/Latin case، reference validation و حفاظت manual assignment پاس شدند؛ هیچ AI فراخوانی نشد.
- Suite نهایی دو بار `702 passed` و `0 skipped`؛ Branch Coverage برابر `90.17%` است.

## به‌روزرسانی‌های مستندات

- ثبت Status/verification و به‌روزرسانی T018 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن category model/Use Case/config paths به `docs/CODE_MAP.md`.
- ثبت precedence، match policy و مرز AI در `docs/ARCHITECTURE.md`.
- ثبت تصمیم category identity/multi-label در `docs/DECISIONS.md` اگر تصمیم معماری مهم است.
- به‌روزرسانی Configuration نمونه با categoryهای مصنوعی امن.

## تعریف انجام‌شدن

- precedence/config/Persian tests پاس شده‌اند و نتیجه deterministic است.
- Quality Gate و UTF-8 پاس شده‌اند.
- manual baseline overwrite race کنترل شده است.
- AI و UI دستی خارج Scope باقی مانده‌اند.
