# گام ۸ — مدیای اکانت

اکانت دارای سشن اکنون API ارسال فایل، عکس، ویدیو، ویس، آلبوم و دانلود مدیای پیام
دارد. کاربردش ساخت ربات کاربری، آرشیو فایل و پاسخ‌دادن با رسانه است. این قابلیت‌ها
بر اساس پروتکل پین‌شدهٔ وب ایتا پیاده شده‌اند و تست محلی دارند؛ پذیرش زندهٔ مدیا
برای همهٔ حالت‌ها انجام نشده است. کاربر در چت خود ارسال سند/ویس/ویدیو، خواندن
مشخصات، دانلود و برابری SHA256 با فایل اصلی را تأیید کرده است. ارسال، خواندن،
دانلود و نمایش عکس و پخش ویس/ویدیو نیز تأیید شده‌اند. آلبوم دو عکس با grouped ID
مشترک، کپشن عکس اول و نمایش گروهی صحیح تأیید شد. فایل بزرگ، آلبوم ویدیو/سند و
مقصدهای دیگر هنوز پذیرش مستقل می‌خواهند.

## API

همهٔ متدها async و روی `AccountClient` هستند؛ مقصد همان ورودی‌های `resolve_peer`
را می‌پذیرد. فایل ورودی مسیر محلی یا `bytes` است. متد ارسال تکی می‌تواند یک
`UploadedFile` از همین سشن نیز بگیرد.

| متد | کاربرد |
| --- | --- |
| `upload_file(file, *, chat_id='self', filename=None, progress=None)` | فقط آپلود؛ خروجی `UploadedFile`؛ هیچ پیام چتی نمی‌فرستد |
| `send_file(chat_id, file, caption, **options)` | ارسال سند؛ خروجی `AccountMessage` |
| `send_photo(chat_id, photo, caption, **options)` | ارسال عکس JPEG/PNG |
| `send_video(chat_id, video, caption, *, duration=None, width=None, height=None, **options)` | مشخصات تعیین‌نشده از خود فایل خوانده می‌شوند |
| `send_voice(chat_id, voice, caption, *, duration=None, **options)` | ارسال ویس Ogg/Opus با خواندن خودکار مدت |
| `send_album(chat_id, items, caption=None, *, random_ids=None, silent=False, reply_to_msg_id=None)` | ارسال ۲ تا ۱۰ مورد `MediaItem`؛ کپشن مشترک روی عضو اول؛ خروجی tuple از پیام‌ها به ترتیب ورودی |
| `download_media(chat_id, message_id, destination, *, overwrite=False, max_size=...)` | دانلود به مسیر صریح؛ خروجی `DownloadedFile(path, size, sha256)` |

در ارسال تکی `chat_id`، فایل و `caption` اجباری‌اند؛ برای بدون کپشن `""` بدهید.
گزینه‌های اختیاری مشترک ارسال تکی: `filename`، `silent`، `reply_to_msg_id`،
`random_id` و `progress`. `mime_type` برای سند/ویدیو قابل تعیین است. `send_file`
پارامتر `kind` نیز می‌پذیرد، ولی متدهای تخصصی خواناترند. کپشن تا ۱۰۲۴ واحد UTF-16
و متن ساده است. کالبک `progress(sent_bytes, total_bytes)` می‌تواند sync یا async
باشد و بیرون قفل درخواست اجرا می‌شود؛ خطای آن ارسال نهایی را متوقف می‌کند.

```python
from pathlib import Path
from fnceitaa import AccountClient, MediaItem

async def use_media():
    async with AccountClient('account.eitaa') as client:
        message = await client.send_file('self', Path('examples/media_sample.txt'), caption='')
        downloaded = await client.download_media('self', message.id, 'downloaded_sample.txt')
        print(downloaded.size, downloaded.sha256)
        # هر دستور زیر یک ارسال جداست؛ فقط مورد موردنیاز را اجرا کنید.
        await client.send_photo('self', 'photo.jpg', caption='توضیح عکس')
        await client.send_voice(chat_id='self', voice='voice.ogg', caption='ویس')
        await client.send_video(chat_id='self', video='clip.mp4', caption='ویدیو')
        await client.send_album('self', [MediaItem('one.jpg'), MediaItem('two.jpg')], caption='کپشن آلبوم')
```

کپشن سطح آلبوم اختیاری است. `caption=None` کپشن هر عضو را حفظ می‌کند؛ مقدار
رشته‌ای فقط کپشن عضو اول را جایگزین می‌کند و رشتهٔ خالی آن را پاک می‌کند.
کپشن سایر اعضا و اشیای ورودی تغییر نمی‌کنند. `enqueue_album` نیز همین رفتار را
دارد و کپشن را در صف پایدار ذخیره می‌کند. سقف کپشن ۱۰۲۴ واحد UTF-16 است.

خروجی `get_message` و history در صورت پشتیبانی از نوع مدیا، `message.media` دارد:
نوع، اندازه، MIME، نام اصلی سند، ابعاد/مدت و شناسهٔ فایل. `grouped_id` شناسهٔ گروه
رسانه است. پرچم قدیمی `has_media` همچنان برقرار است؛ مدیای ناشناخته ممکن است
`has_media=True` و `media=None` داشته باشد. نام فایل، file reference و access hash
در repr این مدل‌ها چاپ نمی‌شوند. نام دریافتی از سرور هرگز مسیر خروجی دانلود نیست.
پیش‌نمایش `photoCachedSize` با بایت‌های خالی، که در پاسخ واقعی ایتا دیده شد، کنار
گذاشته می‌شود تا اندازه‌های معتبر دیگر قابل انتخاب باشند. اگر فقط پیش‌نمایش خالی
وجود داشته باشد، `media=None` است؛ نوع غیر‌باینری همچنان خطای دادهٔ نامعتبر می‌دهد.

## رفتار انتقال و محدودیت‌ها

- فایل با قطعه‌های ۵۱۲ KiB و مصرف حافظهٔ محدود خوانده می‌شود. سقف فعلی SDK برابر
  ۴۰۰۰ قطعه، یعنی ۲٬۰۹۷٬۱۵۲٬۰۰۰ بایت است؛ این سقف، تضمین محدودیت حساب/سرور نیست.
  آپلود فایل خالی رد می‌شود. از ۱۰ MiB به بالا مسیر big-file به کار می‌رود.
- به درخواست اول قطعه، `peer` و `totalFileSize` مخصوص ایتا اضافه می‌شوند. فایل بزرگ
  پیش از قطعه‌ها اعلان سهمیهٔ یک‌بایتی جداگانه دارد. ارسال فایل تکی و آماده‌سازی
  آلبوم روی نقش upload است؛ ارسال نهایی آلبوم روی نقش client انجام می‌شود.
- `client.media(upload_endpoint=..., download_endpoint=...)` امکان انتخاب صریح
  اندپوینت HTTPS مجاز ایتا را دارد. پیش‌فرض‌های role از worker پین‌شده‌اند؛ مهاجرت
  خودکار DC، انتخاب تصادفی میزبان و دریافت redirectهای CDN پیاده نشده است.
- عکس فقط با امضای JPEG/PNG پذیرفته می‌شود. اعتبارسنجی امضا معادل decode کامل عکس
  نیست. ویس باید واقعاً Ogg/Opus باشد؛ تغییر پسوند کافی نیست. PyAV مشخصات مدت و
  ابعاد تعیین‌نشده را از فایل محلی می‌خواند؛ این کار خارج از حلقهٔ async است.
  تبدیل فرمت و تولید waveform یا thumbnail انجام نمی‌شود. بازنویسی صریح مدت و
  ابعاد اختیاری است. برای `UploadedFile` ویس/ویدیو باید مشخصات را صریح بدهید یا
  مسیر اصلی فایل را برای بررسی خودکار استفاده کنید.
- آلبوم می‌تواند عکس/ویدیو یا فقط سند باشد؛ ویس و ترکیب سند با عکس/ویدیو رد می‌شود.
  هر فایل ابتدا با `messages.uploadMedia` آماده می‌شود و یک درخواست نهایی
  `messages.sendMultiMedia` پیام‌ها را می‌فرستد. شکست آماده‌سازی، پیام چتی نمی‌فرستد؛
  ممکن است فایل‌های موقت روی سرور باقی بمانند.
- آپلود و درخواست ارسال خودکار تکرار نمی‌شوند. `UploadInterruptedError` و
  `UploadCancelledError` شناسه و شمارهٔ قطعه را دارند و یعنی ارسال پیام نهایی هنوز
  شروع نشده است. خطای صریح RPC، مانند FLOOD_WAIT، مستقیم گزارش می‌شود. خطا یا لغو
  بعد از شروع ارسال نهایی با `MessageOperationUncertainError` یا
  `MessageOperationCancelledError` و random IDها گزارش می‌شود؛ پیش از تکرار، مقصد
  را بررسی کنید. رسید ناقص آلبوم به معنی موفقیت کامل محسوب نمی‌شود.
- دانلود فایل را ابتدا در `.part` کنار مقصد می‌نویسد، اندازهٔ قطعه‌ها را تطبیق
  می‌دهد و پس از flush/fsync منتشر می‌کند. خطا/لغو فایل موقت را پاک می‌کند.
  `overwrite=False` انتشار بدون جایگزینی را با hard link اتمیک انجام می‌دهد؛ سیستم
  فایل باید آن را پشتیبانی کند، مانند NTFS. `overwrite=True` از replace استفاده
  می‌کند. پوشهٔ مقصد باید وجود داشته باشد. لینک نمادین به عنوان مقصد رد می‌شود.
- SHA256 خروجی از بایت‌های دانلودشده محاسبه می‌شود؛ برای فایل ارسالی خودتان، آن را
  با هش فایل اصلی مقایسه کنید. این عدد تأیید هش مستقل از سرور نیست.
- خواندن هر قطعهٔ دانلود در خطای اتصال یا HTTP 5xx، حداکثر سه بار با مکث‌های
  ۱ و ۲ ثانیه تلاش می‌شود. خطای صریح RPC از جمله FLOOD_WAIT و RETRY_LIMIT،
  و HTTP 4xx تکرار نمی‌شود. این رفتار شامل آپلود یا ارسال پیام نیست.
- انقضای توکن در خواندن، یک بار فرصت تمدید و تکرار دارد؛ انقضای file reference
  یک بار با بازخوانی همان پیام و بررسی هویت رسانه رفع می‌شود. revoked شدن سشن
  باعث ورود خودکار یا OTP نمی‌شود. آپلود ناقص و دانلود نیمه‌کاره پس از restart
  قابل ادامه نیستند؛ سشن حساب و رسید ارسال را با checkpoint انتقال اشتباه نگیرید.

## پذیرش زنده، قدم‌به‌قدم

نتیجهٔ آزمون جامع کانال با فایل‌های مصنوعی در [گزارش تحویل](DELIVERY_CHECK.md)
ثبت می‌شود. برای آزمون دستی خودتان دستورات را
یکی‌یکی اجرا کنید؛ برای عملیات بعدی، خروجی قبلی را بررسی کنید. بدون `--live` نمونه
حتی سشن را باز نمی‌کند. در حالت live پیش‌نمایش می‌دهد و `yes` می‌خواهد.

۱. ابتدا سند کوچکِ موجود در پروژه را به خودتان بفرستید:

```powershell
python -m examples.account_media send --live --peer self --file examples/media_sample.txt
```

خروجی `Media send confirmed` و `message_id` را بررسی کنید و فایل را در ایتا ببینید.
سپس با شناسهٔ واقعی به جای `123` اطلاعات همان پیام را بخوانید:

```powershell
python -m examples.account_messages read --live --peer self --message-id 123
```

۲. همان سند را به مسیر جدید دانلود کنید:

```powershell
python -m examples.account_media download --live --peer self --message-id 123 --output downloaded_sample.txt
```

هش اصلی و خروجی باید برابر باشند:

```powershell
Get-FileHash examples/media_sample.txt, downloaded_sample.txt -Algorithm SHA256
```

۳. بعد از تأیید رفت‌وبرگشت سند، عکس واقعی، ویس واقعی، ویدیو و آلبوم را جداگانه
آزمایش کنید. مسیرهای زیر صرفاً نمونه‌اند و باید فایل‌های خودتان را جایگزین کنید؛
مدت و ابعاد نیز باید متعلق به همان فایل باشند.

```powershell
python -m examples.account_media photo --live --peer self --file photo.jpg --caption "تست عکس"
python -m examples.account_media voice --live --peer self --file voice.ogg --duration 3
python -m examples.account_media video --live --peer self --file clip.mp4 --duration 5 --width 640 --height 360
python -m examples.account_media album --live --peer self --file one.jpg two.jpg
```

نمایش صحیح نوع مدیا در ایتا، کپشن/ریپلای، grouped ID آلبوم و دانلود هر نوع باید
مستقل تأیید شوند. فایل بزرگ و مرز ۱۰ MiB نیز پذیرش زندهٔ جدا می‌خواهند.

## مبنای پروتکل و بررسی محلی

منبع عمومی پین‌شده `mtproto.worker-Zk-UnVZ2.js` با SHA256 زیر است:
`ef186db9dfa2a1e79ad30aadd805a5d455e90ee66b2827feca8006df979f4c17`.
شواهد ساختاری بدون اطلاعات حساب در
`research/protocol/account_step8_evidence.json` ثبت شده‌اند.

تست‌های `tests/test_account_media.py` شامل TL/HTTP با نقش‌های مستقل، بایت قطعه‌ها،
مرز big-file و اعلان سهمیه، مشخصات عکس/ویس/ویدیو، ترتیب و رسید ناقص آلبوم، رد ورودی
نامعتبر پیش از آپلود، تغییر فایل/هویت حساب، timeout و لغو، FLOOD_WAIT و revoked،
تمدید توکن خواندن، انقضای reference، جلوگیری از overwrite، حذف فایل موقت و هش
رفت‌وبرگشت هستند. این تست‌ها جای پذیرش واقعی سرور را نمی‌گیرند.
