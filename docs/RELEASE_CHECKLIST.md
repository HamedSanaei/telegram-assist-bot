# v1.1.2 Release Checklist

## شواهد Release پایه

- Quality Run:
  [`30329250841`](https://github.com/HamedSanaei/telegram-assist-bot/actions/runs/30329250841)
- Commit پذیرفته‌شده:
  `4a5d63c1d60305c1949adbf811d25c3d9fd319c1`
- Conclusion: `success`
- Jobهای موفق: Python `3.12`، `3.13`، `3.14` و
  `Docker and installer acceptance`
- Release Run موفق `v1.1.0`:
  [`30332245031`](https://github.com/HamedSanaei/telegram-assist-bot/actions/runs/30332245031)
  روی Commit `272e7e7162cda6508da31d4411c4041fbb384f44`.
- Quality Run موفق T092:
  [`30335865206`](https://github.com/HamedSanaei/telegram-assist-bot/actions/runs/30335865206)
  روی Commit `f367ac31a87051ca91d92b78ed3808143f9b6715`.
- Quality Run موفق T093:
  [`30398566783`](https://github.com/HamedSanaei/telegram-assist-bot/actions/runs/30398566783)
  روی Commit `837f6837f6df022c7937696c18945ef28cbd70b1`؛ Python `3.12`،
  `3.13` و `3.14` و `Docker and installer acceptance` همگی موفق بودند.

- [x] همهٔ Gateهای `quality.yml` برای نسخهٔ `1.1.2` موفق‌اند.
- [x] Workflow پذیرش Docker روی Linux برای نسخهٔ `1.1.2` موفق است.
- [x] Job دقیق `Quality / Docker and installer acceptance` دو Instance مستقل،
      mutationهای `tabctl`، rollback و cleanup منابع را با موفقیت اجرا کرده است.
- [x] نسخه در Package، Wheel، OCI label و مستندات `1.1.2` است.
- [x] دو دکمهٔ URL پروکسی منبع در متن مقصد با ترتیب و target دقیق دیده می‌شوند.
- [x] Publisher فوری و Native Scheduler هیچ keyboard از User account ارسال
      نمی‌کنند و overflow پیام/Caption پیش از Telegram رد می‌شود.
- [x] permission repair روی UID/GID فاقد passwd/group entry موفق است.
- [x] update نصب موجود در Linux و Windows هیچ Telegram login اجرا نمی‌کند.
- [x] قرارداد Bundle فقط Compose، Installerها، Config نمونه و این checklist
      را می‌پذیرد.
- [ ] Imageهای `linux/amd64` و `linux/arm64` با SBOM و provenance ساخته می‌شوند.
- [ ] Digest و `SHA256SUMS` قبل از انتشار بررسی شده‌اند.
- [x] Quality gateهای secret detection، distribution و Bundle ثابت کرده‌اند
      Artifactهای بررسی‌شده فاقد Config محلی، Session، Token، Password و
      Private Key هستند؛ Assetهای نهایی Release پس از Release workflow بررسی
      می‌شوند.
- [ ] Job `Publish GitHub Release` پس از Package، GHCR و Release files موفق
      شده و wheel، sdist، Bundle و `SHA256SUMS` را ضمیمه کرده است.
- [ ] `tabctl backup verify`، update rollback و repair dry-run روی Instance
      آزمایشی موفق‌اند.
- [ ] مسیر import/repair/backup/update از Instance آزمایشی `1.0.0` با basename
      متفاوت، Config و Session و MongoDB volume را حفظ کرده است.
- [ ] diagnostics archive فقط `diagnostics.json` redacted دارد و `.env`،
      Session یا Media در آن نیست.
- [x] Kernel 6.19+ با `mongo:7.0.32` و permission non-root در Ubuntu acceptance
      موفق است.
- [ ] Release notes تغییرات Milestone 9 و محدودیت Telegram live را بیان می‌کند.
- [ ] Tag امضاشدهٔ `v1.1.2` فقط پس از تأیید دستی ساخته می‌شود.
- [ ] GitHub Release پایدار و Tagهای GHCR `1.1.2`، `1.1`، `1` و `latest` بررسی می‌شوند.

Telegram login/send در CI عمداً اجرا نمی‌شود؛ این مرزها با Fake پوشش داده شده و
smoke زنده فقط با Credential مالک Release انجام می‌شود.

پس از موفقیت T093 و Quality، Tag جدید فقط با اقدام صریح مالک منتشر می‌شود:

```bash
gh workflow run release.yml -f tag=v1.1.2
```

موارد مربوط به Image، Digest، Asset و GitHub Release فقط پس از موفقیت همین
Workflow علامت‌گذاری می‌شوند.
