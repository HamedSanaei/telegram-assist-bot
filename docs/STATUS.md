# وضعیت فعلی

- **Current milestone:** Milestone 14 — v1.1.4 Media Cleanup Fairness and
  Approval-Expiration Patch.
- **Active task:** None.
- **Last completed task:** [T099 — v1.1.4 Media Cleanup Fairness and
  Approval-Expiration Patch](tasks/T099-v1-1-4-media-cleanup-fairness-and-approval-expiration.md).
- **Known blockers:** None.
- **Failing tests:** None.
- **Branch:** `fix/v1.1.4-media-cleanup-fairness` (دو commit منطقی: fix و
  release prep). Tag، GitHub Release و Deploy انجام نشده‌اند.
- **Last verified commit before this work:** `687c912` روی `main`.
- **Local verification (this session):** focused cleanup tests `53 passed`؛ full
  suite `1979 passed` روی MongoDB loopback با branch coverage `90.16%`
  (آستانه ۹۰)؛ `uv lock --check`، `ruff check .`، `ruff format --check .`،
  `mypy src tests scripts`، `check_text_integrity.py --all` (۵۵۶ فایل)،
  `detect-secrets-hook`، `uv build`، `check_distribution.py` و `bash -n`
  همگی موفق. Release contract tests نسخهٔ `1.1.4` را تأیید می‌کنند.
- **Production-shaped اندازه‌گیری:** Dataset با ۱۰۶٫۲۰۰ سند (۱۰۰٫۰۰۰ Media
  تازهٔ غیرمنقضی، ۵٫۰۰۰ منقضی هرگز-بررسی‌نشده، ۱٫۰۰۰ retry موعد-رسیده و ۲۰۰
  رکورد legacy). کلاس هرگز-بررسی‌نشده `keysExamined = 5202` (فقط جمعیت منقضی،
  بدون COLLSCAN) و کلاس retry `keysExamined = 100` بدون SORT روی
  `ix_media_cleanup_fairness_v4`. همین assertionها در Suite به‌صورت Test
  پیاده شده‌اند.
- **Production state (reported):** در استقرار `1.1.3` با
  `media.retention_days = 1`، ۳٫۳۰۳ رکورد `expired_uncleaned` با ۲٫۸۸۲ مسیر
  یگانه و ۱۱٫۳۱ GiB باقی مانده که ۳٫۲۵۴ Approval تمام‌شدهٔ `approval_expired =
  true` آن‌ها را قفل کرده بود؛ تفاضل این دو عدد در مجموع ۴۹ رکورد است. پس از
  Deploy نسخهٔ `1.1.4` و اجرای مسیر Cleanup خود Application (بدون حذف دستی)
  قابل آزادسازی است.
- **Next recommended action:** بازبینی و merge کردن Branch، سپس Tag دستی
  `v1.1.4` تنها پس از موفقیت Gateها و Deploy کنترل‌شده.
