# ابزار استخراج لینک از NPVT

این ابزار برای تبدیل خروجی JSON اختصاصی NapsternetV به لینک‌های استاندارد ساخته شده است.

## قابلیت‌ها

- خواندن مستقیم فایل TXT خروجی Pantegnos
- دریافت مستقیم فایل NPVT، به شرط قرار دادن `pantegnos-win.exe` کنار اسکریپت
- حذف کانفیگ‌های تکراری
- تشخیص و ساخت لینک‌های:
  - `trojan://`
  - `vless://`
  - `vmess://`
  - `ss://`
- ساخت فایل Subscription به صورت Base64
- تولید گزارش و JSON نرمال‌شده

## روش بسیار ساده در ویندوز

1. Python را نصب داشته باشید.
2. فایل `pantegnos-win.exe` را کنار این ابزار قرار دهید.
3. فایل `.npvt` یا `.txt` را روی `اجرا با کشیدن فایل.bat` بکشید و رها کنید.
4. کنار فایل ورودی یک پوشه با نام زیر ساخته می‌شود:

```text
نام‌فایل-extracted
```

داخل آن:

```text
links.txt
subscription.txt
normalized.json
report.txt
```

## اجرای دستی

برای خروجی TXT:

```powershell
py .\npvt_link_extractor.py ".\JKJK-1-2-3-4.txt"
```

برای فایل NPVT، وقتی Pantegnos کنار اسکریپت است:

```powershell
py .\npvt_link_extractor.py ".\JKJK-1-2-3-4.npvt"
```

یا با مسیر صریح Pantegnos:

```powershell
py .\npvt_link_extractor.py ".\JKJK-1-2-3-4.npvt" --pantegnos "C:\Tools\pantegnos-win.exe"
```

## نکته

بخش رمزگشایی NPVT همچنان توسط Pantegnos انجام می‌شود. این ابزار، خروجی نامنظم و JSON اختصاصی آن را به لینک‌های استاندارد تبدیل می‌کند.
