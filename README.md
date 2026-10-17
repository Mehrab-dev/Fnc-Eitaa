# Fnc-Eitaa

کتابخانهٔ async پایتون برای ارسال با توکن **ایتایار** و خواندن صفحات عمومی ایتا.
کلاینت آزمایشی اکانت شخصی هم اضافه شده است؛ ورود واقعی آن هنوز نیازمند تأیید است.

خطای `401 INVALID_LOGIN` در درخواست کد به هدر پیش‌فرض HTTP مرتبط بود؛ هدر
`User-Agent` مخصوص SDK اضافه و با شمارهٔ خالی روی سرور بررسی شد. ورود واقعی
اکانت و reconnect همچنان باید تأیید شوند. جزئیات در راهنمای گام ۳ است.

شناخت پروتکل و هستهٔ ارتباط پیاده شده‌اند. **گام ۳، ورود و سشن**، شامل
`AccountClient`، OTP، رمز دوم، فایل سشن و reconnect با تست‌های محلی آماده است.
راهنمای اجرا و وضعیت پذیرش در [راهنمای گام ۳](docs/ACCOUNT_STEP_3.md) است.
برای بررسی ورود روی حساب خودتان، شماره و کد را فقط در ترمینال وارد کنید:

```sh
python -m examples.account_login --live
```

پس از ورود موفق، همین دستور را دوباره اجرا کنید تا reconnect بدون OTP بررسی شود.
فایل `account.session` محرمانه است. بدون `--live` نمونه فقط توضیح آفلاین می‌دهد.

## نصب

Python 3.10 یا بالاتر:

```sh
python -m pip install -e .
```

## ارسال متن و فایل

توکن را در متغیر محیطی `EITAAYAR_TOKEN` قرار دهید. ارسال نیازمند دسترسی توکن به مقصد است.

```python
import asyncio
import os
from fnceitaa import Client

async def main():
    async with Client(token=os.environ["EITAAYAR_TOKEN"]) as client:
        message = await client.send_message("@yourchannel", "سلام")
        print(message.id)
        await client.send_file("@yourchannel", "photo.jpg", caption="توضیح عکس")

asyncio.run(main())
```

`send_file` فایل محلی را به اندپوینت عمومی ارسال فایل می‌فرستد؛ نوع خروجی فعلاً
`DOCUMENT` است و ارسال تخصصی ویس یا آلبوم را پیاده نمی‌کند.

## خواندن اطلاعات عمومی

```python
async with Client() as client:
    info = await client.get_chat_info("@eitaa")
    message = await client.get_message("@eitaa", 123)
    if message is not None:
        print(message.text)
```

`get_info` نام سازگار برای `get_chat_info` است. این دو متد و `get_message`
نام کاربری عمومی می‌خواهند؛ شناسهٔ عددی چت خصوصی قابل خواندن نیست.

## دریافت پیام‌های عمومی

```python
from fnceitaa import Client, Router

router = Router()
client = Client(router=router)

@router.on_message()
async def on_message(message):
    print(message.id, message.text)

await client.run_polling(["eitaa"], interval=3)
```

نمونه‌های دارای `await` باید داخل تابع async اجرا شوند. Polling از آخرین پیام در
اولین بررسی شروع می‌شود و پیام‌های جدید حاضر در صفحه را به ترتیب شناسه تحویل می‌دهد.
پیام‌هایی که پیش از بررسی بعدی از پنجرهٔ صفحه خارج شوند ممکن است از دست بروند.
این روش رویداد ویرایش/حذف یا تاریخچهٔ اکانت خصوصی ارائه نمی‌کند.

خطای یک handler مانع اجرای بقیه نمی‌شود، اما پس از اجرای آن‌ها گزارش می‌شود.
در polling، پیام شکست‌خورده دوباره تلاش می‌شود؛ handlerها باید تکرار را تحمل کنند.
برای توقف از `client.stop_polling()` یا لغو task استفاده کنید.

## خطاها و محدودیت‌ها

- ارسال بدون توکن/اتصال، پاسخ ناموفق و شناسهٔ نامعتبر: `APIRequestError`.
- حذف پیام: `UnsupportedOperationError` تا پیاده‌سازی transport اکانت.
- `session_name` فعلاً برچسب است و فایل سشن تولید نمی‌کند.
- transport این نسخه مستقل است و به SDK ایتای دیگری وابسته نیست. درخواست‌های HTTP
  timeout اتصال/خواندن دارند. لغو task الزاماً thread درخواست را متوقف نمی‌کند.
  تغییر قالب HTML عمومی ممکن است به اصلاح parser نیاز داشته باشد.
- ارسال واقعی و سازگاری پاسخ سرور هنوز با توکن واقعی آزمایش نشده است.
- خواندن اطلاعات کانال عمومی، فهرست آخرین پیام‌ها و دریافت یک پیام با شناسه،
  در ۱۴ اکتبر ۲۰۲۶ روی کانال عمومی `eitaa` با موفقیت بررسی شد.

## تست و مسیر توسعه

راهنمای لایهٔ مقدماتی توکن و صفحات عمومی: [docs/STEP_1.md](docs/STEP_1.md).

```sh
python -m examples.step1 --help
python -m examples.step1 info eitaa
```

تست‌های خودکار بدون شبکه و بدون توکن اجرا می‌شوند:

```sh
python -m unittest discover -s tests -v
```

نقشهٔ راه کلاینت اکانت: [docs/ROADMAP.md](docs/ROADMAP.md).
یافته‌های اولیهٔ پروتکل وب: [docs/PROTOCOL.md](docs/PROTOCOL.md).
