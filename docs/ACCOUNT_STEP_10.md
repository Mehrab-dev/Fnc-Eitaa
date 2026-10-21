# گام ۱۰ — inbox و outbox پایدار با SQLite

برنامه اکنون می‌تواند درخواست ارسال را پیش از اجرا در دیسک نگه دارد و رویدادهای
دریافتی را برای پردازش بعدی ذخیره کند. کاربرد اصلی، ادامهٔ کار پس از توقف برنامه
و تشخیص تفاوت «هنوز اجرا نشده»، «تأیید شده» و «نتیجهٔ نامشخص» است.

این فاز تست محلی و تست قطع واقعی فرایند دارد. کاربر ثبت و ماندگاری صف، ارسال و
خواندن رسید، جلوگیری از ارسال مجدد کار confirmed، دریافت و ماندگاری inbox و
حفظ وضعیت done پس از اجرای مجدد را تأیید کرده است. بازیابی قطع ناگهانی و خطای
شبکه هنوز تست زندهٔ کاربر می‌خواهد. عامل توسعه با صف جدید هیچ پیام زنده‌ای نفرستاده است.
عملیات مستقیم گام‌های قبل همچنان وجود دارند؛ فقط فراخوانی‌های API صف، وارد SQLite
می‌شوند. وابستگی تازه‌ای لازم نیست و از `sqlite3` خود پایتون استفاده شده است.

## بازکردن صف و ثبت ارسال

```python
from fnceitaa import AccountClient

async def main():
    async with AccountClient('account.eitaa') as client:
        with client.queue() as queue:
            job = await queue.enqueue_message('self', 'سلام', key='reply:example:1')
            print(job.id, job.state)  # pending؛ هنوز پیامی ارسال نشده
            result = await queue.dispatch_one(job.id)  # اجرای صریح یک عملیات
            print(result.state, result.receipt)
```

صف را پیش از بسته‌شدن client ببندید. مسیر پیش‌فرض
`account.eitaa.queue.sqlite3` است؛ `client.queue(path)` مسیر دیگری می‌گیرد.
پوشهٔ مقصد باید وجود داشته باشد. همان حساب، دستگاه و هویت اپلیکیشن باید صف را
دوباره باز کنند؛ تعویض حساب/دستگاه با همان فایل رد می‌شود. تمدید توکن همین دستگاه
مشکلی ایجاد نمی‌کند. توکن، OTP یا رمز در پایگاه صف ذخیره نمی‌شود، اما متن پیام،
رویدادها، مسیر فایل و access hash مقصد برای اجرای بعدی ذخیره می‌شوند. پایگاه
رمزگذاری نشده است؛ فایل‌های صف و sidecarهای آن در `.gitignore` پوشش داده شده‌اند.

مدل `OutboxJob` شناسهٔ job، نوع عملیات، وضعیت، تعداد تلاش، زمان مجاز retry، درخواست
ذخیره‌شده و رسید را دارد. درخواست و رسید در repr چاپ نمی‌شوند. `queue.store.get_job`
و `list_jobs` کارهای قبلی را می‌خوانند و `stats` شمارش وضعیت‌ها را برمی‌گرداند.

## API ثبت عملیات

| متد روی queue | درخواست ذخیره‌شده |
| --- | --- |
| `enqueue_message(peer, text, *, key=None, reply_to_msg_id=None, silent=False, no_webpage=False)` | ارسال متن یا ریپلای |
| `enqueue_edit(peer, message_id, text, *, key=None, no_webpage=False)` | ویرایش متن/کپشن |
| `enqueue_delete(peer, message_ids, *, key=None, revoke=False)` | حذف دسته‌ای؛ کانال نیازمند `revoke=True` است |
| `enqueue_forward(to_peer, from_peer, message_ids, *, key=None, copy=False, silent=False)` | فوروارد؛ `copy=True` برای کپی یک پیام |
| `enqueue_file(peer, file, *, key=None, kind=MediaKind.DOCUMENT, ...)` | فایل/عکس/ویدیو/ویس با گزینه‌های ارسال گام ۸ |
| `enqueue_album(peer, items, *, key=None, silent=False, reply_to_msg_id=None)` | ۲ تا ۱۰ `MediaItem` |

تمام enqueueها async هستند تا نام کاربری را در صورت نیاز از سرور resolve کنند؛
هیچ‌کدام عملیات چتی را اجرا نمی‌کنند. پس از resolve، نوع، شناسه و access hash واقعی
در درخواست ذخیره می‌شوند. شناسه‌های پیام و محتوای درخواست تغییر نمی‌کنند. متدهای
ویرایش/حذف/فوروارد در زمان اجرا نیز بررسی‌های گام ۷ را انجام می‌دهند.

برای ارسال‌هایی که random ID دارند، این عدد قبل از اولین درخواست شبکه در SQLite
ثبت می‌شود و در retry صریح عوض نمی‌شود. `key` یک کلید اختیاریِ منطق برنامه است:
enqueue دوبارهٔ همان عملیات و ورودی با همان key، همان job قبلی را می‌دهد؛ استفاده
از همان key برای ورودی متفاوت `QueueConflictError` است. key را مثلاً از event ID
و نوع پاسخ بسازید؛ ثبت بدون key ممکن است چند job جدا ایجاد کند.

مدیای صف‌شده باید مسیر فایل محلی ماندگار باشد؛ `bytes` و `UploadedFile` در این API
پذیرفته نمی‌شوند. اندازه و SHA256 هنگام enqueue ثبت و پیش از dispatch دوباره بررسی
می‌شوند. تغییر یا حذف فایل باعث `failed` پیش از آپلود می‌شود. خود فایل در SQLite
کپی نمی‌شود؛ فایل را تا پایان کار نگه دارید و همزمان تغییر ندهید. تبدیل فرمت یا
ادامهٔ آپلود نیمه‌کاره اضافه نشده است.

## وضعیت‌ها و بازیابی ارسال

| وضعیت | معنا و اقدام |
| --- | --- |
| `pending` | پذیرفته و ذخیره شده؛ منتظر dispatch |
| `in_flight` | worker پیش از شروع اجرا، claim را روی دیسک ثبت کرده است |
| `confirmed` | SDK پاسخ سرور را تطبیق داده و رسید در SQLite ثبت شده است |
| `failed` | رد صریح RPC یا خطای قطعی پیش از ارسال نهایی؛ نیازمند بررسی و retry صریح |
| `uncertain` | درخواست ممکن است اثر کرده باشد؛ ارسال خودکار ممنوع |
| `cancelled` | کار pending به طور صریح لغو شده است |

`await queue.dispatch_one(job_id)` فقط همان job را اجرا می‌کند. بدون شناسه، قدیمی‌ترین
pending آماده را claim می‌کند. claim با تراکنش SQL و توکن مالکیت انجام می‌شود؛ دو
worker نمی‌توانند یک pending را همزمان بگیرند. پایان کار نیز باید همان توکن را
داشته باشد. ترتیب claim بر اساس ترتیب ثبت است، نه ساعت سیستم؛ چند worker الزاماً
ترتیب پایان عملیات یکسانی ندارند. قواعد سشن گام ۳ همچنان برقرارند: برای هر فایل
سشن، یک نویسندهٔ فعال استفاده کنید.

نتیجهٔ dispatch ممکن است `None` باشد، یعنی job آماده‌ای claim نشده است. شکست RPC
یا نتیجهٔ نامشخص در مدل بازگشتی ثبت می‌شود؛ لغو async پس از ثبت وضعیت دوباره raise
می‌شود. خطای خود SQLite نیز به caller می‌رسد. اگر پاسخ موفق سرور برسد ولی ثبت رسید
شکست بخورد، job در `in_flight` باقی می‌ماند؛ این کار خودکار pending نمی‌شود.

lease پیش‌فرض ۳۰۰ ثانیه است و با `lease_seconds` قابل تنظیم است؛ heartbeat خودکار
وجود ندارد. `queue.store.recover_expired()` فقط claimهای منقضی را بازیابی می‌کند:
outbox به `uncertain` و inbox به `pending` می‌رود. برای انتقال طولانی lease مناسب
انتخاب کنید و worker فعال را بازیابی نکنید. حتی اگر فرایند پیش از ارسال شبکه مرده
باشد، از روی claim تنها نمی‌توان آن را ثابت کرد؛ بازیابی محافظه‌کارانه است.

هیچ retry خودکاری برای عملیات نوشتن وجود ندارد. برای failed از
`queue.store.retry_failed(job_id)` استفاده کنید؛ زمان FLOOD_WAIT باید گذشته باشد.
این فقط job را pending می‌کند و خودش ارسال نمی‌کند. لغو pending با
`queue.store.cancel_job(job_id)` انجام می‌شود.

برای uncertain ابتدا وضعیت مقصد را مستقل بررسی کنید. سپس در صورت داشتن نتیجهٔ
قطعی می‌توان با `resolve_uncertain(job_id, state='confirmed'|'failed', note=...,
receipt=...)` آن را ثبت کرد. تغییر وضعیت در audit باقی می‌ماند. تبدیل صریح به failed
به‌تنهایی retry نمی‌کند. صرف نگه‌داشتن random ID، تضمین حذف ارسال تکراری در سرور
نیست؛ بررسی مقصد را با حدس یا نبودن یک پیام در یک صفحه جایگزین نکنید.

## دریافت پایدار و پردازش inbox

```python
async with AccountClient('account.eitaa') as client:
    with client.queue() as queue:
        await queue.updates.initialize()
        # برای کانال موردنظر، پیش از تولید رویداد آزمایشی:
        # await queue.updates.watch_channel('@your_channel')
        await queue.poll_once(replay_pts=1)
        item = queue.store.claim_inbox()
        if item is not None:
            try:
                # نمونه: ثبت پاسخ یکتا؛ هنوز ارسال نمی‌شود.
                if item.event.kind == 'new':
                    await queue.enqueue_message('self', 'رویداد دریافت شد',
                        key='event-reply:' + item.event.event_id)
            except BaseException:
                queue.store.finish_inbox(item, success=False)
                raise
            else:
                queue.store.finish_inbox(item)
```

`queue.poll_once` فقط دریافت و ذخیره می‌کند. رویدادهای new/edit/delete/raw و
checkpoint همان batch در **یک تراکنش** ثبت می‌شوند. بعد از commit، cursor دریافت
جلو می‌رود، ولی رویداد هنوز برای handler در inbox pending است. تکرار event ID حتی
پس از خروج از کش محدود checkpoint، سطر تازه‌ای ایجاد نمی‌کند؛ tombstone وضعیت done
نیز حفظ می‌شود. دادهٔ باینری/مدیا با JSON نوع‌دار و base64 ذخیره می‌شود، بدون pickle.

برای پردازش، از `claim_inbox` استفاده کنید؛ `batch.events` به‌تنهایی صف پردازش نیست.
پس از موفقیت `finish_inbox(item)` وضعیت done می‌دهد و در شکست
`finish_inbox(item, success=False)` آن را pending می‌کند. پردازش دست‌کم یک بار است؛
اثر خارجی handler و ack آن با SQLite اتمیک نیستند. در الگوی بالا، اگر برنامه پس از
enqueue پاسخ و پیش از ack متوقف شود، key ثابت جلوی ثبت پاسخ تازه را می‌گیرد.
handlerهای دارای اثر خارجی دیگر باید راهکار idempotency خودشان را داشته باشند.

یک checkpoint تازه از وضعیت فعلی شروع می‌کند و به‌طور خودکار JSON قدیمیِ `.updates`
را وارد نمی‌کند. واردکردن صریحِ یک checkpoint موجود فقط در DB تازه ممکن است:
`await queue.import_checkpoint('account.eitaa.updates')`. فایل اصلی دست‌نخورده
می‌ماند. checkpoint SQLite و JSON را برای دریافت همان جریان همزمان جلو نبرید.
محدودیت تاریخچهٔ سرور، watched channelها، `UpdateGapError` و replay گام ۶ تغییر
نکرده‌اند؛ SQLite رویدادی را که سرور برنمی‌گرداند بازسازی نمی‌کند.

cursorها دارای revision و کنترل تعارض هستند؛ consumer قدیمی نمی‌تواند cursor جدید
را بازنویسی کند. در تعارض، consumer را از DB باز کنید. retention و پاک‌سازی خودکار
نداریم؛ DB با کارکرد برنامه رشد می‌کند. برای کپی سادهٔ فایل پایگاه، ابتدا تمام
اتصال‌ها را ببندید؛ کپی تنها فایل اصلی هنگام فعال‌بودن WAL نسخهٔ پشتیبان کامل نیست.

## پذیرش زنده، قدم‌به‌قدم

۱. فقط ثبت متن در صف، بدون ارسال:

```powershell
python -m examples.account_queue enqueue --live --peer self --key phase10-test-1
```

متن را وارد کنید؛ پس از دیدن مقصد و متن، ذخیره با `yes` تأیید می‌شود. خروجی باید
`state=pending` و job ID بدهد. برای ثبت دوبارهٔ آزمایش متفاوت، key جدید انتخاب کنید.

۲. در اجرای تازه، ماندگاری را بررسی کنید:

```powershell
python -m examples.account_queue status --live
```

۳. فقط با شناسهٔ واقعی همان job به جای `JOB_ID`، ارسال صریح را انجام دهید:

```powershell
python -m examples.account_queue send --live --job-id JOB_ID
```

پس از بررسی پیش‌نمایش و تأیید `yes`، وضعیت باید confirmed و رسید شامل شناسهٔ پیام
باشد. دوباره status بگیرید و همان پیام را با `account_messages read` بررسی کنید.
اجرای دوبارهٔ send همان jobِ confirmed نباید درخواست ارسال دیگری بفرستد.

۴. دریافت رویداد در inbox، در ترمینال جدا:

```powershell
python -m examples.account_queue poll --live --show-text --replay-pts 1
```

بعد از پیام آماده‌شدن inbox، یک پیام آزمایشی به خودتان بفرستید. پس از مشاهدهٔ
`stored=True` با Ctrl+C متوقف کنید و در اجرای تازه بخوانید:

```powershell
python -m examples.account_queue inbox --live --show-text
```

برای کانال، `--load-dialogs --watch "channel:ID"` اضافه کنید و رویداد را پس از آماده‌شدن
watch تولید کنید. `ack --live --event-id EVENT_ID` با تأیید محلی یک رویداد را done
می‌کند. `recover --live` claimهای منقضی را بازیابی می‌کند؛ چیزی ارسال نمی‌کند.
`retry --live --job-id JOB_ID` فقط failed را صریحاً به pending می‌برد.

بدون `--live`، هیچ‌یک از دستورها حتی پایگاه یا سشن را باز نمی‌کنند. status/inbox در
حالت live فقط برای اتصال با هویت سشن، خواندن اعتبار حساب انجام می‌دهند؛ هیچ jobی
را dispatch نمی‌کنند. CLI ثبت اولیه فقط متن را می‌گیرد؛ صف مدیا و عملیات دیگر از
API پایتون قابل استفاده‌اند و send می‌تواند job آن‌ها را با پیش‌نمایش اجرا کند.

## بررسی محلی و حدود تضمین

تست‌ها در `tests/test_account_queue.py` شامل ماندگاری پس از بازکردن مجدد، کلید یکتا،
claim همزمان، FIFO با تغییر ساعت، انقضای lease، خطای ثبت رسید پس از پاسخ موفق،
خطا/لغو/محدودیت RPC، تغییر فایل، عملیات مختلف، TL/HTTP باینری و تراکنش مشترک
inbox/checkpoint هستند. سه تست فرایند فرزند را با `os._exit` هنگام enqueue، پس از
claim و بین درج رویداد و commit checkpoint متوقف می‌کنند و DB را مجدداً می‌خوانند.
این‌ها تست crash فرایندند، نه آزمایش قطع برق یا خرابی سخت‌افزار. تست symlink در
ویندوزِ فاقد مجوز ساخت symlink skip می‌شود. شاهد محلی بدون دادهٔ حساب در
`research/protocol/account_step10_evidence.json` ثبت می‌شود.

SQLite با WAL و `synchronous=FULL` تنظیم شده است. دوام commit همچنان به عملکرد
سیستم فایل و دیسک وابسته است؛ صف روی فایل محلی طراحی شده، نه پوشهٔ شبکه‌ای یا
سینک همزمان چند دستگاه. بین اجرای پیام در ایتا و commit رسید محلی تراکنش مشترکی
وجود ندارد؛ تضمین ارسال دقیقاً یک بار ارائه نمی‌شود.
