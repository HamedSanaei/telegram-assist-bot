# وضعیت فعلی

- **Current milestone:** Milestone 0 — پایه قابل اجرا
- **Active task:** [T004 — MongoDB و Persistence یکتای Post](tasks/T004-mongodb-idempotent-posts.md)
- **Last completed task:** [T003 — مدل Domain و چرخهٔ عمر Post](tasks/T003-post-domain-lifecycle.md)
- **Known blockers:** هیچ‌کدام؛ وابستگی‌های T004 یعنی T002 و T003 کامل‌اند.
- **Failing tests:** هیچ‌کدام؛ ۳۴۵ تست روی Python 3.12.13 و 3.13.14 با branch coverage کل ۹۵٫۲۱٪ و پوشش ۱۰۰٪ package پست موفق شدند و Ruff، format، mypy، UTF-8، Secret، Lock و Packaging Gateها عبور کردند.
- **Last verified commit:** `3befdf4`؛ Worktree تکمیل‌شدهٔ T003 روی این Commit محلی راستی‌آزمایی شده و هنوز Commit جدید ساخته نشده است.
- **Next recommended action:** پس از review، Commit و Merge تغییرات T003، فقط T004 را طبق فایل Task آن آغاز کنید؛ Branch مربوط به T004 هنوز ساخته نشده است.
