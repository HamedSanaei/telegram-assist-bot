# وضعیت فعلی

- **Current milestone:** Milestone 0 — پایه قابل اجرا
- **Active task:** [T003 — مدل Domain و چرخهٔ عمر Post](tasks/T003-post-domain-lifecycle.md)
- **Last completed task:** [T002 — Configuration و Secret Validation](tasks/T002-configuration-system.md)
- **Known blockers:** هیچ‌کدام؛ وابستگی T003 یعنی T001 کامل است و T002 نیز برای مصرف Taskهای بعدی آماده است.
- **Failing tests:** هیچ‌کدام؛ ۲۰۱ تست روی Python 3.12.13 و 3.13.14 با branch coverage برابر ۹۲٫۷۳٪ موفق شدند و Ruff، format، mypy، UTF-8، Secret، Lock و Packaging Gateها عبور کردند.
- **Last verified commit:** `795b9e3`؛ Worktree تکمیل‌شدهٔ T002 روی این Commit محلی راستی‌آزمایی شده و هنوز Commit جدید ساخته نشده است.
- **Next recommended action:** پس از review و ثبت تغییرات T002، فقط T003 را طبق فایل Task آن آغاز کنید؛ Branch مربوط به T003 هنوز ساخته نشده است.
