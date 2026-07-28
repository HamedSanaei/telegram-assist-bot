# v1.1.1 Release Checklist

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

- [ ] همهٔ Gateهای `quality.yml` برای نسخهٔ `1.1.1` موفق‌اند.
- [ ] Workflow پذیرش Docker روی Linux برای نسخهٔ `1.1.1` موفق است.
- [ ] Job دقیق `Quality / Docker and installer acceptance` دو Instance مستقل،
      mutationهای `tabctl`، rollback و cleanup منابع را با موفقیت اجرا کرده است.
- [ ] نسخه در Package، Wheel، OCI label و مستندات `1.1.1` است.
- [ ] permission repair روی UID/GID فاقد passwd/group entry موفق است.
- [ ] update نصب موجود در Linux و Windows هیچ Telegram login اجرا نمی‌کند.
- [x] قرارداد Bundle فقط Compose، Installerها، Config نمونه و این checklist
      را می‌پذیرد.
- [ ] Imageهای `linux/amd64` و `linux/arm64` با SBOM و provenance ساخته می‌شوند.
- [ ] Digest و `SHA256SUMS` قبل از انتشار بررسی شده‌اند.
- [ ] هیچ Config محلی، Session، Token، Password یا Private Key در Artifact نیست.
- [ ] Job `Publish GitHub Release` پس از Package، GHCR و Release files موفق
      شده و wheel، sdist، Bundle و `SHA256SUMS` را ضمیمه کرده است.
- [ ] `tabctl backup verify`، update rollback و repair dry-run روی Instance
      آزمایشی موفق‌اند.
- [ ] مسیر import/repair/backup/update از Instance آزمایشی `1.0.0` با basename
      متفاوت، Config و Session و MongoDB volume را حفظ کرده است.
- [ ] diagnostics archive فقط `diagnostics.json` redacted دارد و `.env`،
      Session یا Media در آن نیست.
- [ ] Kernel 6.19+ با `mongo:7.0.32` و permission non-root در Ubuntu acceptance
      موفق است.
- [ ] Release notes تغییرات Milestone 9 و محدودیت Telegram live را بیان می‌کند.
- [ ] Tag امضاشدهٔ `v1.1.1` فقط پس از تأیید دستی ساخته می‌شود.
- [ ] GitHub Release پایدار و Tagهای GHCR `1.1.1`، `1.1`، `1` و `latest` بررسی می‌شوند.

Telegram login/send در CI عمداً اجرا نمی‌شود؛ این مرزها با Fake پوشش داده شده و
smoke زنده فقط با Credential مالک Release انجام می‌شود.

پس از موفقیت T092 و Quality، Tag جدید فقط با اقدام صریح مالک منتشر می‌شود:

```bash
gh workflow run release.yml -f tag=v1.1.1
```

موارد مربوط به Image، Digest، Asset و GitHub Release فقط پس از موفقیت همین
Workflow علامت‌گذاری می‌شوند.
