"""Editorial source for the Persian/English site. Build with tools/build_docs.py."""


def bi(fa, en):
    return {'fa': fa, 'en': en}


def paragraph(fa, en):
    return dict(type='paragraph', text=bi(fa, en))


def heading(identifier, fa, en):
    return dict(type='heading', id=identifier, text=bi(fa, en))


def code(label, value, language='python'):
    return dict(type='code', label=label, language=language, value=value.strip())


def note(fa, en, tone='info'):
    return dict(type='note', text=bi(fa, en), tone=tone)


def table(headers_fa, headers_en, rows):
    return dict(type='table', headers=bi(headers_fa, headers_en), rows=rows)


def page(identifier, group, fa, en, description_fa, description_en, blocks):
    return dict(id=identifier, group=group, title=bi(fa, en),
                description=bi(description_fa, description_en), blocks=blocks)


PAGES = [
    page('overview', 'start', 'ایتا، با زبان پایتون.', 'Eitaa, in Python.',
         'از اتصال به حساب تا پیام، مدیا و رویدادها؛ یک مسیر روشن برای ساخت ابزارهای خودت.',
         'From your account to messages, media and events. A clear path to building your own tools.', [
        heading('one-client', 'از یک سشن شروع کن', 'Start with a session'),
        paragraph('Fnc Eitaa یک کتابخانهٔ async برای کار با حساب واقعی ایتاست. با `AccountClient` وارد می‌شوی، سشن را نگه می‌داری و به پیام‌ها، گفتگوها، رسانه و رویدادها دسترسی داری. وضعیت فعلی نسخهٔ `0.1.0` آزمایشی است و محدودیت‌های تأییدشده در صفحهٔ اعتبارسنجی آمده‌اند.',
                  'Fnc Eitaa is an async library for real Eitaa accounts. Use `AccountClient` to authenticate, keep a session, and work with messages, dialogs, media and events. Version `0.1.0` is experimental; see Validation for the observed scope and remaining limits.'),
        code('hello_eitaa.py', '''import asyncio
from fnceitaa import AccountClient

async def main():
    # A missing session starts terminal login before this block runs.
    async with AccountClient("account.session") as client:
        me = await client.get_me()
        print(me["id"])
        messages = await client.get_history("self", limit=5)
        for message in messages:
            print(message.id, message.text)

asyncio.run(main())'''),
        note('async with ابتدا سشن را بررسی می‌کند؛ در نبود سشن یا رد صریح آن، ورود تعاملی در ترمینال شروع می‌شود. پس از دریافت شماره و تأیید درخواست کد، ورود کامل می‌شود و بدنهٔ نمونه فقط اطلاعات را می‌خواند. auto_login=False این رفتار تعاملی را غیرفعال می‌کند.',
             'async with checks the session first. A missing or explicitly rejected session starts terminal login: enter your phone, confirm the code request and complete authentication before the example reads data. Use auto_login=False to disable interactive login.'),
        heading('capabilities', 'چه چیزی در اختیارت است؟', 'What can you build?'),
        table(['قابلیت', 'رابط اصلی'], ['Capability', 'Entry point'], [
            [bi('ورود و سشن مستقل', 'Login and persistent sessions'), '`AccountClient`'],
            [bi('گفتگوها و پیدا کردن مقصد', 'Dialogs and peer resolution'), '`resolve_peer` · `iter_dialogs`'],
            [bi('متن، ریپلای و تاریخچه', 'Text, replies and history'), '`send_message` · `get_history`'],
            [bi('ویرایش، حذف، فوروارد و کپی', 'Edit, delete, forward and copy'), '`edit_message` · `forward_messages`'],
            [bi('سند، عکس، ویس، ویدیو و آلبوم', 'Documents, photos, voice, video and albums'), '`send_file` · `send_album`'],
            [bi('رویداد جدید، ویرایش و حذف', 'New, edit and delete events'), '`client.updates()`'],
            [bi('صف پایدار دریافت و ارسال', 'Durable inbox and outbox'), '`client.queue()`'],
            [bi('اطلاعات کامل و اعضای قابل مشاهده', 'Full profiles and visible members'), '`client.profiles()`'],
        ]),
        heading('choose-client', 'کلاینت مناسب را انتخاب کن', 'Choose your client'),
        paragraph('این مستندات عمدتاً دربارهٔ `AccountClient` است. `Client` قدیمی برای ارسال با توکن ایتایار و خواندن صفحات عمومی وجود دارد؛ توکن ایتایار جای سشن حساب شخصی را نمی‌گیرد. جزئیات این مسیر در صفحهٔ «توکن و صفحات عمومی» آمده است.',
                  'This documentation primarily covers `AccountClient`. The earlier `Client` supports Eitaayar token sending and public-page reads. An Eitaayar token does not replace a personal account session. See Token & public pages for that separate interface.'),
    ]),
    page('installation', 'start', 'نصب و شروع سریع', 'Installation & quickstart',
         'محیط را بساز، کتابخانه را از مخزن نصب کن و اولین اتصال را بررسی کن.',
         'Set up your environment, install from the repository and verify your first connection.', [
        heading('requirements', 'پیش‌نیازها', 'Requirements'),
        paragraph('حداقل نسخهٔ تعریف‌شدهٔ پایتون `3.10` است. تست‌های فعلی روی Python 3.13 ویندوز اجرا شده‌اند. دستورهای زیر را در ریشهٔ مخزن اجرا کن. نصب از مخزن، مسیر مستندشدهٔ فعلی است؛ وجود بسته در PyPI تأیید نشده است.',
                  'The declared minimum Python version is `3.10`. The current suite was run on Windows with Python 3.13. Run these commands at the repository root. Installation from source is the documented route; PyPI availability has not been verified.'),
        code('Windows · PowerShell', r'''python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .''', 'powershell'),
        code('Linux / macOS', '''python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .''', 'bash'),
        heading('first-login', 'اولین ورود', 'First login'),
        code('Run from repository root', 'python -m examples.account_login --live', 'powershell'),
        paragraph('شمارهٔ موبایل ایران را با `09` و در مجموع ۱۱ رقم وارد کن؛ لازم نیست خودت `+98` بنویسی. ارقام فارسی و عربی هم پذیرفته می‌شوند. نمونه شماره، کد و رمز احتمالی را مخفی دریافت می‌کند و قبل از درخواست کد تأیید می‌خواهد. پس از پیام ذخیرهٔ موفق سشن، همین دستور را دوباره اجرا کن؛ خروجی `Session reconnected` اتصال بدون کد تازه را نشان می‌دهد.',
                  'Enter an 11-digit Iranian mobile number starting with `09`; you do not need to type `+98`. Persian and Arabic digits are also accepted. The example reads your phone, code and optional password with hidden terminal input and asks before requesting a code. After the session is saved, run the same command again. `Session reconnected` confirms reconnection without a new code.'),
        note('هنگام تایپ شماره، کد و رمز هیچ کاراکتری نمایش داده نمی‌شود؛ این رفتار طبیعی ورودی مخفی است. برای تأیید درخواست کد، واژهٔ `yes` را با حروف انگلیسی وارد کن. شماره را در دستور یا کد برنامه ننویس؛ در ترمینال وارد کن.',
             'Hidden phone, code and password input displays no characters as you type. Type the English word `yes` to confirm a code request. Enter your number at the terminal prompt rather than in a command or source file.'),
        heading('first-read', 'اولین خواندن', 'Your first read'),
        code('Dialogs', 'python -m examples.account_dialogs --live --show-titles', 'powershell'),
        code('Public channel history', 'python -m examples.account_messages history --live --peer "@eitaa" --limit 5 --show-text', 'powershell'),
        note('در PowerShell نام کاربری با `@` را داخل کوتیشن بنویس: `--peer "@eitaa"`. بدون `--live`، نمونه‌های اکانت در حالت آفلاین می‌مانند.',
             'Quote usernames containing `@` in PowerShell: `--peer "@eitaa"`. Account examples remain offline without `--live`.'),
        heading('package-vs-examples', 'پکیج و نمونه‌ها', 'Package versus examples'),
        paragraph('نام نصب و نام import هر دو `fnceitaa` هستند. ماژول‌های `examples` برای اجرای مستقیم از مخزن‌اند و داخل wheel نصب نمی‌شوند. در برنامهٔ خودت از `from fnceitaa import AccountClient` استفاده کن.',
                  'Both the distribution and import name are `fnceitaa`. The `examples` modules run from the source checkout and are not included in the installed wheel. In your own application use `from fnceitaa import AccountClient`.'),
    ]),
    page('sessions', 'start', 'ورود و سشن', 'Login & sessions',
         'ورود صریح، اتصال مجدد و نگهداری درست فایل حساب.',
         'Explicit authentication, reconnection and session file handling.', [
        heading('lifecycle', 'چرخهٔ عمر اتصال', 'Connection lifecycle'),
        paragraph('`async with` متد `start()` را اجرا می‌کند: ابتدا سشن موجود از سرور بررسی می‌شود؛ اگر فایل نباشد یا سرور صریحاً سشن را باطل اعلام کند، روند ورود تعاملی شروع می‌شود. شماره، کد و رمز دوم احتمالی مخفی گرفته می‌شوند. پس از ورود، سشن ذخیره می‌شود و بدنهٔ برنامه ادامه پیدا می‌کند. هنگام خروج، disconnect اتصال محلی را می‌بندد.',
                  '`async with` calls `start()`: it checks an existing session against the server, then starts terminal login if the file is missing or the server explicitly rejects the session. Phone, code and any two-step password use hidden input. After login the session is saved and your application block runs. Exiting disconnects locally.'),
        code('existing_session.py', '''import asyncio
from fnceitaa import AccountClient

async def main():
    async with AccountClient("account.session") as client:
        print((await client.get_me())["id"])

asyncio.run(main())'''),
        heading('phone-formats', 'فرمت شمارهٔ ورود', 'Accepted phone formats'),
        table(['ورودی نمونه', 'شمارهٔ ارسالی به سرور'], ['Example input', 'Number sent to the server'], [
            ['`09120000000`', '`+989120000000`'],
            ['`۰۹۱۲۰۰۰۰۰۰۰`', '`+989120000000`'],
            ['`٠٩١٢٠٠٠٠٠٠٠`', '`+989120000000`'],
            ['`(0912) 000-0000`', '`+989120000000`'],
            ['`+989120000000` / `00989120000000`', '`+989120000000`'],
        ]),
        paragraph('شماره‌ها صرفاً نمونه‌اند؛ شمارهٔ کامل خودت را وارد کن. تبدیل `09` مخصوص موبایل ایران و دقیقاً ۱۱ رقم است؛ شمارهٔ ثابت و شمارهٔ ناقص پذیرفته نمی‌شوند. برای کشورهای دیگر از `+` یا `00` و کد کشور استفاده کن. تبدیل در `request_code()` هم انجام می‌شود و هویت دستگاه و سشن برای دو شکل شماره یکسان می‌ماند.',
                  'These are example numbers; enter your own complete number. Local conversion accepts only 11-digit Iranian mobile numbers starting with `09`; local landlines and incomplete numbers are rejected. For other countries use `+` or `00` followed by the country code. `request_code()` performs the same conversion, preserving the same device and session identity for both formats.'),
        heading('explicit-login', 'ورود برنامه‌نویسی‌شده', 'Programmatic login'),
        code('Inside an async function', '''import getpass
from fnceitaa import AccountClient, PasswordRequiredError

async with AccountClient("new-account.session", auto_login=False) as client:
    if not client.is_authorized:
        phone = getpass.getpass("Phone (09... or +country code): ")
        if input("Request one login code? [yes/NO]: ") != "yes":
            return
        challenge = await client.request_code(phone)
        print(challenge.delivery, challenge.length)
        try:
            me = await client.sign_in(getpass.getpass("Code: "))
        except PasswordRequiredError:
            me = await client.sign_in_password(
                getpass.getpass("Two-step password: ")
            )
    else:
        me = await client.get_me()
    print(me["id"])'''),
        note('برای حالت تعاملی همان async with کافی است؛ نمونهٔ examples.account_login هم مسیر ورود مستقل را ارائه می‌کند. برای GUI، سرویس یا ورود برنامه‌ای، auto_login=False بده و متدهای ورود را خودت فراخوانی کن. connect() همیشه سطح پایین و بدون prompt است؛ start() مسیر آمادهٔ اتصال و ورود است. ورودی ترمینال در زمان ورود روی نخ فراخواننده خوانده می‌شود تا Ctrl+C آن را قطع کند.',
             'For terminal login, async with is sufficient; examples.account_login also provides a standalone flow. For a GUI, service or custom authentication use auto_login=False and call the login methods yourself. connect() always remains low-level and prompt-free; start() combines connection and login. Terminal prompts run on the calling thread so Ctrl+C can interrupt them.'),
        heading('session-behavior', 'رفتار فایل سشن', 'Session file behavior'),
        table(['موضوع', 'رفتار'], ['Topic', 'Behavior'], [
            [bi('مسیر پیش‌فرض', 'Default path'), '`account.session`'],
            [bi('فرمت', 'Format'), bi('JSON؛ رمزگذاری‌شده نیست.', 'JSON; not encrypted.')],
            [bi('کد و رمز ورود', 'OTP and password'), bi('در سشن ذخیره نمی‌شوند.', 'Not stored in the session.')],
            [bi('خروج از context', 'Leaving the context'), bi('قطع اتصال محلی؛ فایل و ورود راه‌دور حفظ می‌شوند.', 'Disconnects locally; retains the file and remote login.')],
            [bi('هر حساب', 'Per account'), bi('یک فایل جدا؛ هر فایل در اختیار یک پردازش.', 'A separate file; one process per session file.')],
            [bi('مدت اعتبار', 'Validity'), bi('تا زمانی که سرور بپذیرد؛ دائمی تضمین نمی‌شود.', 'As long as the server accepts it; no permanent validity guarantee.')],
        ]),
        paragraph('رد صریح سشن هنگام start در حالت پیش‌فرض، ورود دوباره به همان حساب را آغاز می‌کند؛ تا پیش از موفقیت پاسخ ورود، فایل قبلی حفظ می‌شود. ورود به حساب دیگر روی فایل سشن قبلی پذیرفته نمی‌شود. با auto_login=False خطای SessionRevokedError به برنامه برمی‌گردد. خطای شبکه، decode، فایل خراب و نتیجهٔ نامعلوم ارسال باعث ورود مجدد یا retry عملیات نمی‌شوند. در محیط بدون ترمینال، نبود سشن با AuthenticationError روشن متوقف می‌شود.',
                  'An explicit session rejection during start triggers login to the same account by default; the prior file is retained until the authorization response succeeds. Logging into a different account through that saved session is rejected. With auto_login=False, SessionRevokedError propagates. Network failures, decoding errors, corrupt files and uncertain sends do not trigger login or replay. Missing sessions in a noninteractive environment raise a clear AuthenticationError.'),
        note('فایل سشن، فایل‌های SQLite و نسخه‌های پشتیبانشان خصوصی‌اند. متن پیام و access hash در صف ذخیره می‌شود. این فایل‌ها را در Git یا گزارش خطا منتشر نکن.',
             'Session files, queue databases and their backups are private. Queues contain message text and access hashes. Keep these files out of Git and issue reports.', 'warning'),
    ]),
    page('peers', 'account', 'مقصدها و گفتگوها', 'Peers & dialogs',
         'شناسه، نوع مقصد و صفحه‌بندی گفتگوها را درست مدیریت کن.',
         'Work with peer identities, destination types and paginated dialogs.', [
        heading('resolve', 'پیدا کردن مقصد', 'Resolve a peer'),
        code('Inside an authorized client', '''me = await client.resolve_peer("self")
channel = await client.resolve_peer("@eitaa")
print(channel.kind.value, channel.id, channel.title)

async for dialog in client.iter_dialogs(limit=100, page_size=50):
    print(dialog.peer.kind.value, dialog.peer.id, dialog.peer.title)'''),
        table(['ورودی', 'شرط'], ['Input', 'Requirement'], [
            ['`"self"` / `"me"`', bi('حساب جاری', 'Current account')],
            ['`"@username"`', bi('resolve از سرور', 'Resolved through the server')],
            ['`"channel:12345"` / `"user:12345"`', bi('هویت کامل باید در کش همین کلاینت باشد.', 'A full identity must already be cached in this client.')],
            ['`"chat:12345"`', bi('گروه پایه؛ دسترسی سرور همچنان لازم است.', 'Basic group; server access is still required.')],
            ['`12345`', bi('شناسهٔ کش‌شده و بدون ابهام', 'An unambiguous cached identity')],
            ['`AccountPeer`', bi('مدل متعلق به همان حساب با اطلاعات دسترسی کامل', 'A full identity belonging to the same account')],
        ]),
        heading('private-peer', 'کانال خصوصی و کش مقصد', 'Private channels and the peer cache'),
        paragraph('کش مقصد در حافظهٔ همان کلاینت است. برای استفاده از شناسهٔ کانال خصوصی پس از اجرای تازه، ابتدا گفتگوها را بخوان. اگر گفتگوها چند صفحه باشند، یک صفحه ممکن است کافی نباشد. پرچم `is_megagroup` کانال عادی را از سوپرگروه جدا می‌کند.',
                  'The peer cache belongs to the current client in memory. Before using a private channel ID in a fresh run, read dialogs first. A single page may be insufficient for accounts with many dialogs. `is_megagroup` distinguishes a broadcast channel from a supergroup.'),
        code('Load identities before resolving a private ID', '''async for dialog in client.iter_dialogs(page_size=100):
    pass

peer = await client.resolve_peer("channel:YOUR_CHANNEL_ID")
print(peer.title)'''),
        heading('dialog-pages', 'صفحه‌بندی گفتگوها', 'Dialog pagination'),
        code('Explicit cursor', '''page = await client.get_dialogs_page(limit=50, folder_id=0)
for dialog in page.dialogs:
    print(dialog.peer.id, dialog.unread_count, dialog.pinned)

if page.next_cursor is not None:
    next_page = await client.get_dialogs_page(
        limit=50, cursor=page.next_cursor, folder_id=0
    )'''),
        paragraph('`folder_id=None` همه، `0` پوشهٔ اصلی و `1` بایگانی است. در ادامهٔ cursor همان folder را حفظ کن. اندازهٔ هر صفحه از ۱ تا ۱۰۰ است. `get_dialogs(limit=...)` فهرست محدود و `iter_dialogs(...)` پیمایش تدریجی می‌دهد.',
                  '`folder_id=None` selects all, `0` the main folder and `1` the archive. Keep the same folder when continuing a cursor. Page size is 1–100. `get_dialogs(limit=...)` returns a bounded collection; `iter_dialogs(...)` iterates lazily.'),
        note('`YOUR_CHANNEL_ID` جایگزین است؛ آن را با شناسهٔ واقعی از خروجی گفتگوها عوض کن. ساختن `AccountPeer` با یک شناسهٔ تنها، access hash معتبر تولید نمی‌کند.',
             '`YOUR_CHANNEL_ID` is a placeholder: replace it with an ID from your dialog output. Constructing an `AccountPeer` from a bare ID does not generate a valid access hash.'),
    ]),
    page('messages', 'account', 'پیام، ریپلای و تاریخچه', 'Messages, replies & history',
         'متن بفرست، پیام مشخصی را بخوان و تاریخچه را صفحه‌بندی کن.',
         'Send text, read a specific message and paginate history.', [
        heading('send', 'ارسال و ریپلای', 'Send and reply'),
        code('Inside an authorized client · sends two messages', '''sent = await client.send_message("self", "سلام از پایتون")
print(sent.id, sent.random_id)

reply = await client.send_message(
    "self", "پاسخ آزمایشی", reply_to_msg_id=sent.id
)
print(reply.id, reply.reply_to_msg_id)'''),
        paragraph('خروجی `AccountMessage` است. شناسهٔ `id` پیام به مقصد مربوط است؛ برای خواندن یا ریپلای همان مقصد را نگه دار. متن باید غیرخالی و حداکثر ۴۰۹۶ واحد UTF-16 باشد. ارسال مستقیم در صف SQLite ثبت نمی‌شود؛ برای این کار از `client.queue()` استفاده کن.',
                  'The result is an `AccountMessage`. Its `id` belongs to that destination; keep the same peer for reads and replies. Text must be nonempty and no longer than 4096 UTF-16 units. Direct sends do not enter the SQLite outbox; use `client.queue()` for durable sends.'),
        table(['پارامتر', 'پیش‌فرض', 'کاربرد'], ['Parameter', 'Default', 'Purpose'], [
            ['`reply_to_msg_id`', '`None`', bi('شناسهٔ پیام مبدأ ریپلای در همین گفتگو', 'Message to reply to in the same conversation')],
            ['`silent`', '`False`', bi('درخواست ارسال بی‌صدا', 'Request silent delivery')],
            ['`no_webpage`', '`False`', bi('درخواست حذف پیش‌نمایش لینک', 'Request suppression of a link preview')],
            ['`random_id`', '`None`', bi('شناسهٔ عملیات؛ در نبود مقدار تولید می‌شود.', 'Operation identity; generated when omitted.')],
        ]),
        heading('read', 'خواندن یک پیام', 'Read one message'),
        code('Readback', '''message = await client.get_message("self", sent.id)
if message is None:
    print("Message unavailable")
else:
    print(message.id, message.text, message.reply_to_msg_id)'''),
        paragraph('`None` یعنی پیام در این گفتگو در دسترس نیست. خواندن تاریخچه یا پیام، آن را به‌عنوان خوانده‌شده علامت نمی‌زند.',
                  '`None` means the message is unavailable in this conversation. Reading a message or history does not mark it as read.'),
        heading('history', 'تاریخچه از جدید به قدیم', 'History, newest first'),
        code('History helpers', '''messages = await client.get_history("self", limit=20)

async for message in client.iter_history("self", limit=200, page_size=50):
    print(message.id, message.text)

page = await client.get_history_page("@eitaa", limit=10)
if page.next_offset_id is not None:
    older = await client.get_history_page(
        "@eitaa", limit=10, offset_id=page.next_offset_id
    )'''),
        note('در نتیجهٔ نامعلوم، `SendMessageUncertainError.random_id` را برای بررسی نگه دار. timeout یا خطای decode به‌تنهایی ثابت نمی‌کند پیام ارسال نشده است؛ ارسال خودکار تکرار نمی‌شود.',
             'Keep `SendMessageUncertainError.random_id` when an outcome is uncertain. A timeout or decoding failure does not prove that a message was not sent. The SDK does not resend automatically.', 'warning'),
    ]),
    page('actions', 'account', 'ویرایش، حذف و فوروارد', 'Edit, delete & forward',
         'عملیات پیام با مقصد روشن و رسید قابل بررسی.',
         'Message operations with explicit destinations and inspectable receipts.', [
        heading('edit', 'ویرایش متن', 'Edit text'),
        code('Inside an authorized client · modifies messages', '''sent = await client.send_message("self", "متن اولیه")
edited = await client.edit_message("self", sent.id, "متن جدید")
print(edited.id, edited.text)'''),
        heading('forward', 'فوروارد و کپی', 'Forward and copy'),
        code('Destination comes first', '''forwarded = await client.forward_message(
    "self", "self", sent.id
)
copied = await client.copy_message(
    "self", "self", sent.id
)

# Multiple source messages, with one random ID per message.
results = await client.forward_messages(
    "self", "@your_source_channel", [101, 102]
)'''),
        paragraph('ترتیب آرگومان‌ها `to_peer, from_peer, message_id` است. `forward_message` و `copy_message` خروجی `ForwardedMessage` می‌دهند؛ `forward_messages` یک tuple از آن‌ها می‌دهد. کپی، درخواست `drop_author` به سرور است و تأیید محتوای متنی به‌تنهایی نمایش بدون نویسنده را ثابت نمی‌کند. مجوزهای سرور و محدودیت محتوای محافظت‌شده رعایت می‌شوند.',
                  'Argument order is `to_peer, from_peer, message_id`. `forward_message` and `copy_message` return `ForwardedMessage`; `forward_messages` returns a tuple of them. Copy requests server-side `drop_author`; reading back the text alone does not verify author attribution. Server permissions and protected-content restrictions still apply.'),
        heading('delete', 'حذف پیام‌ها', 'Delete messages'),
        code('Explicit deletion scope', '''receipt = await client.delete_messages(
    "self", [sent.id], revoke=False
)
print(receipt.message_ids, receipt.revoke)

# Shared channel deletion requires explicit revoke=True.
# await client.delete_messages(channel, [message_id], revoke=True)'''),
        paragraph('`message_ids` باید شامل ۱ تا ۱۰۰ شناسهٔ یکتای مثبت باشد. `revoke=False` درخواست حذف محلی است. حذف کانال اشتراکی است و باید صریحاً `revoke=True` بدهی؛ داشتن مجوز لازم است. رسید حذف، تأیید RPC است؛ وضعیت نهایی را با خواندن دوباره بررسی کن.',
                  '`message_ids` must contain 1–100 unique positive IDs. `revoke=False` requests local deletion. Channel deletion is shared and requires explicit `revoke=True` plus the necessary permission. A deletion receipt acknowledges the RPC; verify the resulting state with a separate read.'),
        note('`MessageOperationUncertainError` نام عملیات و شناسه‌های پیام و random ID را نگه می‌دارد. هیچ عملیات تغییردهنده پس از نتیجهٔ نامعلوم، خودکار تکرار یا به چند درخواست تقسیم نمی‌شود.',
             '`MessageOperationUncertainError` retains the operation, message IDs and random IDs. Mutations are not automatically repeated or split after an uncertain outcome.', 'warning'),
    ]),
    page('media', 'account', 'فایل، عکس و آلبوم', 'Files, photos & albums',
         'ارسال و دانلود رسانه با مشخصات صریح و خروجی قابل بررسی.',
         'Upload and download media with explicit metadata and verifiable results.', [
        heading('files-photos', 'سند و عکس', 'Documents and photos'),
        code('Inside an authorized client · uploads and sends', '''document = await client.send_file(
    "self", "report.pdf", caption="گزارش"
)
photo = await client.send_photo(
    "self", "photo.jpg", caption="عکس آزمایشی"
)
print(document.id, photo.id)'''),
        paragraph('`send_file` به‌صورت پیش‌فرض سند می‌فرستد؛ برای نمایش عکس از `send_photo` استفاده کن. مسیر محلی، bytes با filename، یا `UploadedFile` همان حساب در ارسال مستقیم پذیرفته می‌شود. کپشن حداکثر ۱۰۲۴ واحد UTF-16 است. سرور ممکن است عکس را بازفشرده کند؛ برابر نبودن هش عکس به‌تنهایی خطا نیست.',
                  '`send_file` sends a document by default; use `send_photo` for photo presentation. Direct sends accept a local path, bytes with a filename, or an `UploadedFile` from the same account. Captions allow up to 1024 UTF-16 units. The server may recompress photos, so a different photo hash alone is not a failure.'),
        heading('voice-video', 'ویس و ویدیو', 'Voice and video'),
        code('Supply the real media metadata', '''voice = await client.send_voice(
    "self", "voice.ogg", duration=4
)
video = await client.send_video(
    "self", "clip.mp4", duration=2, width=464, height=848,
    caption="ویدیو"
)'''),
        paragraph('مدت برحسب ثانیه و ابعاد برحسب پیکسل است؛ این‌ها را از فایل واقعی بگیر. کتابخانه مدت یا ابعاد را حدس نمی‌زند و تبدیل فرمت انجام نمی‌دهد. برای ویس از فایل Ogg/Opus مناسب و برای ویدیو از فایل قابل پخش در ایتا استفاده کن.',
                  'Duration is in seconds and dimensions in pixels; supply values from the actual file. The library does not infer them or transcode media. Use an appropriate Ogg/Opus voice file and a video playable in Eitaa.'),
        heading('albums', 'آلبوم', 'Albums'),
        code('A two-photo album', '''from fnceitaa import MediaItem, MediaKind

messages = await client.send_album("self", [
    MediaItem("first.jpg", kind=MediaKind.PHOTO, caption="آلبوم من"),
    MediaItem("second.jpg", kind=MediaKind.PHOTO),
])
for message in messages:
    print(message.id, message.grouped_id)'''),
        paragraph('آلبوم ۲ تا ۱۰ عضو دارد. هر `MediaItem` کپشن و مشخصات خودش را دارد. موارد قبل از ارسال نهایی آپلود می‌شوند؛ تأیید ناقص شناسه‌ها نتیجه را نامعلوم می‌کند. آلبوم دو عکس در حساب واقعی بررسی شده است؛ انواع دیگر هنوز تأیید زنده ندارند.',
                  'An album contains 2–10 items. Each `MediaItem` has its own caption and metadata. Items are uploaded before the final send; incomplete ID correlation leaves the outcome uncertain. A two-photo album was verified live; other album types remain unverified live.'),
        heading('downloads', 'دانلود و بررسی محتوا', 'Download and verify'),
        code('Choose the destination path explicitly', '''downloaded = await client.download_media(
    "self", document.id, "downloads/report.pdf"
)
print(downloaded.path, downloaded.size, downloaded.sha256)'''),
        paragraph('پوشهٔ مقصد را پیشاپیش بساز. دانلود از مسیر انتخابی تو استفاده می‌کند و به‌صورت پیش‌فرض فایل موجود را بازنویسی نمی‌کند. `overwrite=True` بازنویسی را صریح می‌کند. فایل دانلودی پس از تکمیل در مقصد قرار می‌گیرد و SHA256 برمی‌گردد.',
                  'Create the destination directory first. Downloads use your explicit path and refuse to overwrite an existing file by default. Set `overwrite=True` explicitly to replace it. The completed download is placed at the destination and returns a SHA256 digest.'),
        heading('upload-options', 'آپلود جداگانه و گزینه‌ها', 'Separate uploads and options'),
        code('Reuse an uploaded file in the same client/account', '''uploaded = await client.upload_file("photo.jpg", peer="self")
message = await client.send_photo("self", uploaded, caption="آپلود قبلی")

def progress(done, total):
    print(done, total)

message = await client.send_file(
    "self", "report.pdf", progress=progress,
    reply_to_msg_id=123, silent=False
)'''),
        note('آپلود و ارسال خودکار retry ندارند. ادامهٔ پایدار آپلود نیمه‌تمام، تبدیل فرمت و مهاجرت CDN/DC پیاده نشده‌اند. سقف محلی کد ۴۰۰۰ قطعهٔ ۵۱۲ KiB است؛ پشتیبانی واقعی فایل بزرگ هنوز تأیید نشده است.',
             'Uploads and sends do not retry automatically. Durable partial-upload resume, transcoding and CDN/DC migration are not implemented. The code allows up to 4000 parts of 512 KiB; actual large-file operation remains unverified.', 'warning'),
    ]),
    page('updates', 'automation', 'رویدادها و دریافت پیام', 'Events & polling',
         'پیام جدید، ویرایش، حذف و ادامه از checkpoint.',
         'New messages, edits, deletions and checkpoint-based continuation.', [
        heading('poll', 'دریافت و تأیید دستی', 'Poll and acknowledge'),
        code('listen.py', '''import asyncio
from fnceitaa import AccountClient

async def main():
    async with AccountClient("account.session") as client:
        updates = client.updates()
        await updates.initialize()
        while True:
            batch = await updates.poll()
            for event in batch.events:
                print(event.kind, event.event_id, event.message_ids)
                if event.message is not None:
                    print(event.message.text)
            # Acknowledge only after your processing succeeds.
            await updates.ack(batch)
            if not batch.has_more:
                await asyncio.sleep(5)

asyncio.run(main())'''),
        table(['نوع رویداد', 'معنی'], ['Event kind', 'Meaning'], [
            ['`new`', bi('پیام جدید', 'New message')],
            ['`edit`', bi('ویرایش پیام', 'Message edited')],
            ['`delete`', bi('حذف شناسه‌های پیام؛ متن پیام الزامی نیست.', 'Message IDs deleted; a message body is not required.')],
            ['`raw`', bi('به‌روزرسانی بدون تبدیل به سه نوع پیام بالا', 'An update not normalized into the three message event types')],
        ]),
        heading('watch-channel', 'دریافت رویداد کانال', 'Watch a channel'),
        code('After initialize(), before polling', '''channel = await client.resolve_peer("@your_channel")
await updates.watch_channel(channel)
# Or explicitly watch accessible channel dialogs:
# await updates.watch_dialogs()'''),
        paragraph('کانال‌ها مسیر difference جدا دارند و باید watch شوند. برای کانال خصوصی ابتدا گفتگوها را بارگذاری کن. رویداد `raw` به‌تنهایی نشانهٔ دریافت پیام متنی نیست؛ برای متن `event.message` را بررسی کن. در حذف‌های عمومی ممکن است `peer` نامعلوم و برابر None باشد.',
                  'Channels have separate difference streams and must be watched. Load dialogs first for private channels. A `raw` event alone does not establish receipt of a text message; inspect `event.message`. Some global deletion events have no known peer and use None.'),
        heading('router', 'هندلر با Router', 'Handlers with Router'),
        code('Inside an authorized client', '''from fnceitaa import Router

router = Router()

@router.on("new")
async def on_new(event):
    if event.message is not None:
        print(event.event_id, event.message.text)

updates = client.updates()
await updates.run_polling(router, interval=5, replay_pts=0)'''),
        paragraph('نام رویداد اکانت `new` است؛ میانبر قدیمی `on_message()` برای نام `message` ثبت می‌شود و جایگزین آن نیست. Router هر هندلر را با یک آرگومان event فراخوانی می‌کند. خطای هندلر مانع ack همان batch می‌شود.',
                  'Account message events are named `new`. The earlier `on_message()` shortcut registers `message`, which is a different name. Router calls each handler with one event argument. A handler failure prevents acknowledgment of that batch.'),
        heading('recovery', 'ادامه و محدودیت بازیابی', 'Continuation and recovery limits'),
        paragraph('شروع تازه از وضعیت جاری است. checkpoint پیش‌فرض `account.session.updates` است و متن پیام ذخیره نمی‌کند. فقط پس از پردازش موفق ack کن. تحویل حداقل یک بار است؛ برای اثرهای جانبی از `event_id` به‌عنوان کلید یکتا استفاده کن. برای نگهداری متن رویدادها، Inbox SQLite را انتخاب کن.',
                  'A fresh consumer starts at current state. The default checkpoint is `account.session.updates` and does not store message bodies. Acknowledge after successful processing. Delivery is at least once; use `event_id` to deduplicate effects. Choose the SQLite inbox to persist event bodies.'),
        note('بازیابی پیام زمان خاموشی با `replay_pts=1` تأیید شده؛ بازیابی عادی پس از توقف بدون آن هنوز تأیید نشده است. replay محدود به داده‌ای است که سرور برمی‌گرداند. `UpdateGapError` checkpoint را جلو نمی‌برد و به تصمیم صریح برای بازیابی نیاز دارد.',
             'Offline-message recovery was verified with `replay_pts=1`; ordinary restart catch-up without it remains unverified. Replay is bounded by server-retained data. `UpdateGapError` leaves the checkpoint unchanged and requires an explicit recovery decision.', 'warning'),
    ]),
    page('queue', 'automation', 'صف پایدار SQLite', 'Durable SQLite queues',
         'ثبت عملیات، رسید ارسال و پردازش رویدادها پس از اجرای مجدد.',
         'Persist operations, send receipts and event-processing state across restarts.', [
        heading('outbox', 'ثبت و ارسال از Outbox', 'Enqueue and dispatch'),
        code('Inside an authorized client · explicitly sends one queued message', '''with client.queue() as queue:
    job = await queue.enqueue_message(
        "self", "سلام از صف", key="welcome-v1"
    )
    print(job.id, job.state)  # pending, or the existing job for this key
    result = await queue.dispatch_one(job.id)
    if result is not None:
        print(result.state, result.receipt)
    else:
        print(queue.store.get_job(job.id).state)'''),
        paragraph('ثبت در صف پیام نمی‌فرستد؛ `dispatch_one` ارسال را آغاز می‌کند. کلید ثابت برای همان نیت، job قبلی را با همان random ID برمی‌گرداند. همان key با payload متفاوت خطای تعارض دارد. این کلید در همان پایگاه داده معنا دارد؛ برای عملیات تازه key تازه بساز.',
                  'Enqueueing does not send a message; `dispatch_one` initiates dispatch. Reusing a key for the same intent returns the existing job and its original random ID. Reusing it with a different payload is a conflict. Keys are scoped to that database; use a new key for a new operation.'),
        table(['وضعیت', 'معنی'], ['State', 'Meaning'], [
            ['`pending`', bi('آمادهٔ ارسال صریح', 'Ready for explicit dispatch')],
            ['`in_flight`', bi('claim ثبت شده و عملیات در حال انجام است.', 'A durable claim exists and dispatch is in progress.')],
            ['`confirmed`', bi('رسید منطبق سرور دریافت و ذخیره شده است.', 'A correlated server receipt was received and saved.')],
            ['`failed`', bi('رد صریح یا شکست مشخص؛ retry فقط با تصمیم صریح.', 'An explicit rejection or known failure; retry requires an explicit decision.')],
            ['`uncertain`', bi('ممکن است عملیات انجام شده باشد؛ خودکار ارسال نمی‌شود.', 'The operation may have taken effect; it is not resent automatically.')],
            ['`cancelled`', bi('کار pending پیش از dispatch لغو شده است.', 'A pending job was cancelled before dispatch.')],
        ]),
        heading('durable-inbox', 'Inbox با ack پردازش', 'Inbox processing acknowledgment'),
        code('Inside an authorized client', '''with client.queue() as queue:
    await queue.updates.initialize()
    await queue.poll_once()  # commits events and cursor together

    item = queue.store.claim_inbox()
    if item is not None:
        try:
            print(item.event.kind, item.event.event_id)
            if item.event.message is not None:
                print(item.event.message.text)
        except Exception:
            queue.store.finish_inbox(item, success=False)
            raise
        else:
            queue.store.finish_inbox(item)'''),
        paragraph('`poll_once` رویدادها و checkpoint را در یک تراکنش ثبت می‌کند. سپس از `claim_inbox` پردازش کن؛ موفقیت آن را done و شکست آن را pending می‌کند. برای دریافت پیوسته، poll را با فاصله و رعایت `has_more` تکرار کن. fresh checkpoint از حالا شروع می‌شود و JSON قبلی خودکار وارد نمی‌شود.',
                  '`poll_once` commits events and their checkpoint in one transaction. Process through `claim_inbox`: success marks done, failure returns pending. For continuous reception, repeat polling with an interval and respect `has_more`. A fresh checkpoint starts now; an old JSON checkpoint is not imported automatically.'),
        heading('other-operations', 'صف عملیات دیگر و مدیا', 'Queue other operations and media'),
        code('Queue API · these calls enqueue only', '''with client.queue() as queue:
    edit_job = await queue.enqueue_edit("self", 123, "متن جدید", key="edit-v1")
    forward_job = await queue.enqueue_forward(
        "self", "self", [123], key="forward-v1"
    )
    file_job = await queue.enqueue_file(
        "self", "report.pdf", caption="گزارش", key="report-v1"
    )
    print(queue.store.stats())'''),
        paragraph('در صف مدیا فقط مسیر فایل ماندگار پذیرفته می‌شود. اندازه و SHA256 هنگام ثبت ذخیره و پیش از ارسال بررسی می‌شوند. فایل تغییرکرده یا حذف‌شده پیش از ارسال شکست می‌خورد. فایل را تا پایان dispatch تغییر نده. `enqueue_album` و `enqueue_delete` هم در دسترس‌اند؛ کپی با `enqueue_forward(..., copy=True)` ثبت می‌شود.',
                  'Queued media requires a persistent local path. Size and SHA256 are saved at enqueue time and checked before dispatch. A changed or missing file fails before sending. Keep the file unchanged until dispatch completes. `enqueue_album` and `enqueue_delete` are also available; use `enqueue_forward(..., copy=True)` for copies.'),
        heading('recovery', 'بازیابی و تصمیم دستی', 'Recovery and manual decisions'),
        paragraph('claim پیش‌فرض ۳۰۰ ثانیه است و heartbeat ندارد؛ برای عملیات طولانی `lease_seconds` مناسب انتخاب کن. `recover_expired()` فقط claim منقضی را برمی‌گرداند: ارسال به uncertain و پردازش inbox به pending. `retry_failed(job_id)` بعد از رعایت تأخیر FLOOD_WAIT فقط کار failed را pending می‌کند؛ خودش ارسال نمی‌کند.',
                  'The default lease is 300 seconds with no heartbeat; select a suitable `lease_seconds` for long operations. `recover_expired()` only handles expired claims: outbox becomes uncertain, inbox becomes pending. After any FLOOD_WAIT delay, `retry_failed(job_id)` moves only a failed job back to pending; it does not dispatch.'),
        code('Only after independent verification of the remote outcome', '''with client.queue() as queue:
    queue.store.resolve_uncertain(
        job_id, state="confirmed", note="Verified by independent readback",
        receipt={"message_ids": [verified_message_id]},
    )'''),
        note('SQLite متن پیام‌ها، raw رویدادها و اطلاعات مقصد را بدون رمزگذاری ذخیره می‌کند. پایگاه پیش‌فرض `account.session.queue.sqlite3` به همان حساب متصل است. قبل از کپی سادهٔ پایگاه، همهٔ اتصال‌ها را ببند. اثر راه‌دور و ثبت محلی تراکنش مشترک ندارند؛ تضمین ارسال دقیقاً یک بار ارائه نمی‌شود.',
             'SQLite stores message text, raw events and peer data without encryption. The default `account.session.queue.sqlite3` is bound to that account. Close all connections before a simple file-copy backup. Remote effects and local receipts do not share a transaction; exactly-once sending is not guaranteed.', 'warning'),
    ]),
    page('profiles', 'account', 'اطلاعات کانال و اعضا', 'Profiles & members',
         'اطلاعات کامل، شمار اعضا و نقش‌ها در محدودهٔ دسترسی حساب.',
         'Full profiles, member counts and roles within your account permissions.', [
        heading('full-profiles', 'اطلاعات کامل', 'Full profiles'),
        code('Inside an authorized client · read-only', '''user = await client.get_full_user("self")
channel = await client.get_full_channel("@eitaa")
print(user.common_chats_count)
print(channel.member_count, channel.can_view_participants)

count = await client.get_member_count("@eitaa")
profile = await client.get_chat_info("@eitaa")'''),
        paragraph('`get_chat_info` براساس نوع مقصد مدل `AccountUserFull` یا `AccountChatFull` می‌دهد. فیلدهای شمارش اختیاری ممکن است None باشند؛ این مقدار به معنی صفر نیست. نبود دسترسی به اعضا هم به معنی خالی بودن کانال نیست.',
                  '`get_chat_info` returns `AccountUserFull` or `AccountChatFull` according to peer kind. Optional count fields may be None, which does not mean zero. A hidden participant list does not mean an empty channel.'),
        heading('members', 'اعضا و نقش حساب', 'Members and account roles'),
        code('Replace the private channel placeholder after loading dialogs', '''async for dialog in client.iter_dialogs():
    pass

peer = await client.resolve_peer("channel:YOUR_CHANNEL_ID")
member = await client.get_member(peer, "self")
if member is not None:
    print(member.role, member.is_member, member.role_source)

async for member in client.iter_members(peer, limit=50, page_size=25):
    print(member.peer.id, member.role)'''),
        paragraph('نقش‌ها می‌توانند owner، admin، member، banned، left یا unknown باشند. برای نقش خود در کانال، پاسخ مبهم با اطلاعات کامل همان کانال تکمیل می‌شود؛ دادهٔ ناکافی به unknown منجر می‌شود. مشاهدهٔ اعضا تابع مجوز سرور است و راهی برای دورزدن فهرست مخفی وجود ندارد.',
                  'Roles may be owner, admin, member, banned, left or unknown. For your own channel membership, an ambiguous response is supplemented by that channel’s full profile; insufficient evidence stays unknown. Participant visibility is controlled by the server; hidden lists cannot be bypassed.'),
        heading('member-pages', 'صفحه و فیلتر اعضا', 'Participant pages and filters'),
        code('Read a bounded page', '''page = await client.get_members_page(
    peer, limit=50, offset=0, filter="recent"
)
print(page.total_count, page.next_offset)

# Filters depend on peer type and server permissions.
admins = await client.get_members_page(peer, filter="admins", limit=20)'''),
        note('اطلاعات حساب خود، کانال عمومی، کانال خصوصی، فهرست اعضای کانال خصوصی و نقش owner در تست واقعی تأیید شده‌اند. گروه‌ها و صفحه‌بندی چندصفحه‌ای هنوز پذیرش زنده ندارند.',
             'Live checks cover your own full profile, public and private channels, a private channel participant list and the owner role. Groups and multiple live participant pages remain unverified.'),
    ]),
    page('reference', 'reference', 'مرجع متدها', 'API reference',
         'امضاهای استخراج‌شده از کد؛ کلاس را انتخاب و نام متد را جست‌وجو کن.',
         'Signatures extracted from source. Select a class and filter by method name.', [
        paragraph('امضای هر متد از فایل‌های همین نسخه تولید می‌شود. `async` یعنی هنگام فراخوانی از await استفاده کن؛ iteratorهای async را با async for بخوان. گزینه‌های `**kwargs` در wrapperهای مدیا و پروفایل به helper متناظر منتقل می‌شوند؛ کلاس helper را هم بررسی کن.',
                  'Each method signature is generated from this version’s source. Use await for async calls; consume async iterators with async for. Media and profile wrappers pass `**kwargs` to their helper; inspect the helper class for those options.'),
        dict(type='api'),
    ]),
    page('models', 'reference', 'مدل‌ها و خروجی‌ها', 'Models & return values',
         'فیلدهای مدل‌های اکانت، صفحه‌ها، رویدادها و صف.',
         'Fields for account identities, pages, events and queue records.', [
        paragraph('مدل‌های زیر dataclass هستند؛ فیلدها و نوع‌ها مستقیماً از سورس استخراج شده‌اند. مخفی بودن یک فیلد در repr به معنی رمزگذاری آن نیست. `get_me()` برخلاف مدل‌های این صفحه یک dict از user سرور برمی‌گرداند.',
                  'These models are dataclasses; field names and types are extracted directly from source. Hiding a field from repr does not encrypt it. Unlike these models, `get_me()` returns the server user as a dict.'),
        dict(type='models'),
    ]),
    page('errors', 'reference', 'خطاها و عیب‌یابی', 'Errors & troubleshooting',
         'خطای قطعی، نتیجهٔ نامعلوم و مشکلات متداول اجرای نمونه‌ها.',
         'Handle explicit failures, uncertain outcomes and common example issues.', [
        heading('rpc-errors', 'خطاهای RPC و محدودیت نرخ', 'RPC failures and rate limits'),
        code('Inspect the error; decide on retry explicitly', '''from fnceitaa import AccountRPCError, SendMessageUncertainError

try:
    sent = await client.send_message("self", "سلام")
except AccountRPCError as error:
    print(error.code, error.rpc_type)
    if error.retry_after is not None:
        print("Wait at least", error.retry_after, "seconds")
except SendMessageUncertainError as error:
    print("Check the destination before retrying:", error.random_id)'''),
        table(['خطا', 'اقدام'], ['Error', 'Action'], [
            ['`PeerResolveError`', bi('نام کاربری را resolve کن یا گفتگوها را در همان کلاینت بخوان.', 'Resolve a username or load dialogs in the same client.')],
            ['`AuthenticationError`', bi('اتصال و is_authorized را بررسی کن.', 'Check connection state and is_authorized.')],
            ['`SessionRevokedError`', bi('با auto_login=False خطا را مدیریت و ورود را صریح آغاز کن؛ start در حالت پیش‌فرض ورود دوباره را پیشنهاد می‌کند.', 'With auto_login=False, handle the error and initiate login explicitly; default start begins terminal login again.')],
            ['`AccountRPCError`', bi('code و rpc_type را بخوان؛ FLOOD_WAIT دارای retry_after است.', 'Inspect code and rpc_type; FLOOD_WAIT provides retry_after.')],
            ['`SendMessageUncertainError` / `MessageOperationUncertainError`', bi('ممکن است اثر راه‌دور انجام شده باشد؛ قبل از retry بررسی کن.', 'The remote effect may exist; verify before retrying.')],
            ['`UploadInterruptedError`', bi('یک قطعه تأیید نشده؛ RPC ارسال پیام شروع نشده است.', 'An upload part was not acknowledged; message send was not started.')],
            ['`QueueConflictError`', bi('تعارض key، claim یا cursor؛ وضعیت تازه را از DB بخوان.', 'Key, claim or cursor conflict; reload the database state.')],
            ['`UpdateGapError`', bi('بازیابی از cursor فعلی ممکن نیست؛ بدون بررسی آن را حذف نکن.', 'The current cursor cannot be continued; inspect before replacing it.')],
            ['`TLCodecError`', bi('از fnceitaa.protocol؛ جزئیات ساختاری را نگه دار و دادهٔ خصوصی را حذف کن.', 'Import from fnceitaa.protocol; retain structural details and redact private data.')],
        ]),
        heading('powershell', 'مشکلات رایج ترمینال', 'Common terminal issues'),
        table(['نشانه', 'راه‌حل'], ['Symptom', 'Fix'], [
            [bi('هنگام ورود چیزی تایپ نمی‌شود', 'Nothing appears while typing login input'), bi('ورودی مخفی است؛ شمارهٔ ۱۱رقمی 09، کد یا رمز را وارد کن و Enter بزن.', 'Input is hidden; type your 11-digit 09 number, code or password and press Enter.')],
            [bi('فرمت شماره رد می‌شود', 'Phone format is rejected'), bi('موبایل ایران: ۱۱ رقم با 09؛ کشورهای دیگر: + یا 00 و کد کشور. ستاره یا سه‌نقطه وارد نکن.', 'Iranian mobile: 11 digits starting with 09. Other countries: + or 00 and country code. Do not type masking stars or ellipses.')],
            ['`ModuleNotFoundError: fnceitaa`', bi('با همان پایتونی که برنامه را اجرا می‌کند python -m pip install -e . بزن و مفسر IDE را هماهنگ کن.', 'Run python -m pip install -e . using the same Python as your application and select that interpreter in your IDE.')],
            ['`Activate.ps1`', bi('اگر فعال‌سازی محیط مجاز نیست، مستقیماً ./.venv/Scripts/python.exe را به جای python اجرا کن.', 'If activation is unavailable, invoke ./.venv/Scripts/python.exe directly instead of python.')],
            ['`argument --peer: expected one argument`', bi('مقدار @username را کوتیشن بگذار.', 'Quote the @username argument.')],
            ['`ModuleNotFoundError: examples`', bi('از ریشهٔ مخزن اجرا کن؛ examples داخل wheel نیست.', 'Run from the repository root; examples are not in the wheel.')],
            ['`Unknown peer ID`', bi('شناسهٔ تنها کافی نیست؛ resolve یا بارگذاری همهٔ صفحه‌های گفتگوها لازم است.', 'A bare ID is insufficient; resolve or load all relevant dialog pages.')],
            [bi('فایل دانلود از قبل هست', 'Download target already exists'), bi('مسیر تازه بده یا overwrite=True را صریح انتخاب کن.', 'Choose another path or explicitly set overwrite=True.')],
            [bi('رویداد فقط raw است', 'Only raw events appear'), bi('پس از آماده شدن listener پیام تازه بساز؛ کانال را watch کن.', 'Generate a fresh message after the listener is ready; watch channels explicitly.')],
        ]),
        heading('cancellation', 'لغو عملیات', 'Cancellation'),
        paragraph('`SendMessageCancelledError`، `MessageOperationCancelledError` و `UploadCancelledError` زیرکلاس asyncio.CancelledError هستند. لغو پس از شروع نوشتن ممکن است نتیجهٔ نامعلوم داشته باشد. semantics لغو asyncio را با گرفتن کلی BaseException پنهان نکن.',
                  '`SendMessageCancelledError`, `MessageOperationCancelledError` and `UploadCancelledError` inherit from asyncio.CancelledError. Cancellation after a write starts may leave an uncertain outcome. Preserve asyncio cancellation semantics instead of swallowing BaseException.'),
    ]),
    page('examples', 'start', 'دستورهای آماده', 'CLI examples',
         'دستورهای مرحله‌ای برای تست با حساب خودت، از ریشهٔ مخزن.',
         'Step-by-step commands for your own account, from the source checkout.', [
        heading('read-commands', 'خواندن بدون ارسال پیام', 'Read without sending messages'),
        code('Login / reconnect', 'python -m examples.account_login --live', 'powershell'),
        code('Dialogs', 'python -m examples.account_dialogs --live --show-titles', 'powershell'),
        code('History', 'python -m examples.account_messages history --live --peer self --limit 5 --show-text', 'powershell'),
        code('Profile', 'python -m examples.account_profiles info --live --peer "@eitaa"', 'powershell'),
        heading('write-commands', 'ارسال و عملیات با تأیید', 'Confirmed writes'),
        paragraph('دستورها را یکی‌یکی اجرا کن و شناسه‌های خروجی خودت را جایگزین کن. نمونه‌های تغییردهنده پیش‌نمایش و تأیید yes دارند؛ کد SDK مستقیم چنین promptی ندارد.',
                  'Run commands one at a time and substitute your own returned IDs. Mutation examples show a preview and ask for yes; direct SDK calls do not prompt.'),
        code('Send to yourself', 'python -m examples.account_messages send --live --peer self', 'powershell'),
        code('Reply · replace MESSAGE_ID', 'python -m examples.account_messages send --live --peer self --reply-to MESSAGE_ID', 'powershell'),
        code('Edit · replace MESSAGE_ID', 'python -m examples.account_actions edit --live --peer self --message-id MESSAGE_ID', 'powershell'),
        code('Photo', 'python -m examples.account_media photo --live --peer self --file photo.jpg --caption "آزمایش"', 'powershell'),
        heading('queue-commands', 'صف، قدم‌به‌قدم', 'Queues, step by step'),
        code('1. Enqueue only', 'python -m examples.account_queue enqueue --live --peer self --key my-test-1', 'powershell'),
        code('2. Reopen and inspect', 'python -m examples.account_queue status --live', 'powershell'),
        code('3. Dispatch the returned JOB_ID', 'python -m examples.account_queue send --live --job-id JOB_ID', 'powershell'),
        code('4. Capture events · Ctrl+C to stop', 'python -m examples.account_queue poll --live --show-text', 'powershell'),
        code('5. Inspect persistent events', 'python -m examples.account_queue inbox --live --show-text', 'powershell'),
        code('6. Mark one event processed', 'python -m examples.account_queue ack --live --event-id EVENT_ID', 'powershell'),
        note('برای دیدن همهٔ گزینه‌ها از `python -m examples.account_media --help` یا `python -m examples.account_queue --help` استفاده کن. `--session` مسیر سشن متفاوت را مشخص می‌کند. بدون --live هیچ نمونهٔ اکانت درخواست زنده نمی‌فرستد.',
             'Use `python -m examples.account_media --help` or `python -m examples.account_queue --help` for all options. `--session` selects another session path. Without --live the account examples make no live request.'),
    ]),
    page('validation', 'project', 'وضعیت و محدودیت‌ها', 'Validation & limits',
         'پیاده‌سازی‌شده، تست محلی و مشاهدهٔ واقعی را جدا بخوان.',
         'Distinguish implementation, local testing and observed live behavior.', [
        heading('observed', 'آنچه در حساب واقعی بررسی شده', 'Observed live behavior'),
        note('نسخهٔ آمادهٔ انتشار شامل پوشهٔ tests و ابزارهای تست زندهٔ توسعه نیست. ارجاع یادداشت‌های مرحله‌ای به آن فایل‌ها تاریخی است؛ نسخهٔ قبلی آن‌ها در تاریخچهٔ Git موجود است. نمونه‌های کاربردی examples همچنان همراه مخزن هستند.',
             'The release source tree omits the development tests directory and live acceptance tools. References in phase notes describe historical coverage; earlier files remain in Git history. Runnable examples remain in the repository.'),
        note('وضعیت این جدول گزارش بررسی‌های قبلی است؛ تغییر مستندات یا موفقیت تست آفلاین به معنی تأیید زندهٔ جدید نیست. پوشش یک متد در schema هم تضمین پشتیبانی کامل آن در SDK نیست.',
             'This table records earlier checks; documentation changes or passing offline tests do not establish new live acceptance. A method appearing in the schema does not establish full SDK support.'),
        paragraph('این صفحه بر پایهٔ تست‌های کاربر و شواهد محلی در ۱۷ اکتبر ۲۰۲۶ است. تأیید یک مسیر در پیام‌های ذخیره‌شده، تأیید همهٔ مقصدها و مجوزها نیست.',
                  'This page reflects user-run checks and local evidence as of October 17, 2026. Verifying a path in Saved Messages does not verify every destination or permission.'),
        table(['بخش', 'تأییدشده', 'هنوز باقی است'], ['Area', 'Verified', 'Still outstanding'], [
            [bi('ورود', 'Login'), bi('OTP، ذخیره و reconnect بدون OTP', 'OTP, persistence and reconnect without OTP'), bi('رمز دوم، ابطال و انقضای زنده', 'Live password, revocation and expiry paths')],
            [bi('گفتگوها', 'Dialogs'), bi('فهرست و resolve عمومی', 'Listing and public resolution'), bi('چند صفحه و بایگانی زنده', 'Multiple live pages and archive')],
            [bi('پیام', 'Messages'), bi('ارسال، ریپلای، خواندن و تاریخچهٔ خود', 'Self-send, reply, readback and history'), bi('نوشتن در مقصدهای دیگر', 'Writes to other destination types')],
            [bi('رویداد', 'Events'), bi('new/edit/delete خود و بازیابی با replay=1', 'Self new/edit/delete and recovery with replay=1'), bi('بازیابی عادی restart و رویداد کانال', 'Ordinary restart catch-up and channel events')],
            [bi('عملیات', 'Actions'), bi('ویرایش، فوروارد، کپی و حذف در چت خود', 'Self edit, forward, copy and deletion'), bi('نمایش بدون نویسنده و مقصدهای دیگر', 'Copy attribution and other destinations')],
            [bi('مدیا', 'Media'), bi('سند، عکس، ویس، ویدیو و آلبوم دو عکس', 'Document, photo, voice, video and two-photo album'), bi('فایل بزرگ، آلبوم‌های دیگر و کانال', 'Large files, other albums and channels')],
            [bi('اعضا', 'Members'), bi('کانال عمومی/خصوصی، لیست خصوصی و نقش owner', 'Public/private channel info, private list and owner role'), bi('گروه و صفحه‌های متعدد زنده', 'Groups and multiple live pages')],
            [bi('صف', 'Queues'), bi('ماندگاری، ارسال و رسید، عدم ارسال مجدد confirmed، inbox و done', 'Persistence, dispatch/receipt, confirmed non-resend, inbox and done'), bi('قطع ناگهانی و شبکه در اجرای زندهٔ کاربر', 'User-machine live crash and network recovery')],
        ]),
        heading('local-tests', 'تست‌های محلی', 'Local test coverage'),
        paragraph('آخرین اجرای مجموعهٔ اصلی: ۳۵۵ تست؛ ۳۵۴ موفق و یک skip به‌دلیل نبود مجوز symlink ویندوز. ۴۸ تست مربوط به صف و ۲۴ تست مربوط به شروع و ورود تعاملی است. سه تست فرایند فرزند را در مرز تراکنش متوقف می‌کنند. ساخت wheel و import آن در محیط ایزوله هم بررسی شده است. این اعداد شاهد تست ثبت‌شده‌اند و شمارندهٔ زنده نیستند.',
                  'The last recorded main-suite run executed 355 tests: 354 passed and one skipped because Windows could not create symlinks. Queue coverage contains 48 tests and startup/interactive login has 24 tests. Three tests terminate child processes at transaction boundaries. Wheel build and isolated imports were also checked. These are recorded observations, not live counters.'),
        heading('limits', 'محدودیت‌های فعلی', 'Current limitations'),
        paragraph('رابط اصلی async است؛ wrapper همگام، مدیر چندحساب و ورود تعاملی خودکار روی سازنده ارائه نشده‌اند. صف و سشن رمزگذاری داخلی ندارند. آپلود نیمه‌تمام ادامهٔ پایدار ندارد. تضمین ارسال دقیقاً یک بار، بازیابی نامحدود تاریخچه و تأیید همهٔ RPCهای schema وجود ندارد.',
                  'The main interface is async. There is no sync wrapper, account-pool manager or constructor-driven interactive login. Sessions and queues have no built-in encryption. Partial uploads have no durable resume. Exactly-once sending, unlimited history recovery and verification of all schema RPCs are not claimed.'),
        dict(type='resources', links=[
            dict(path='ROADMAP.md', label=bi('نقشهٔ راه و پذیرش هر فاز', 'Roadmap and per-phase acceptance')),
            dict(path='ACCOUNT_STEP_10.md', label=bi('جزئیات تراکنش‌ها و بازیابی صف', 'Queue transaction and recovery details')),
            dict(path='PROTOCOL.md', label=bi('یادداشت پروتکل', 'Protocol notes')),
        ]),
    ]),
    page('token', 'reference', 'توکن و صفحات عمومی', 'Token & public pages',
         'رابط جداگانهٔ Client برای ایتایار و خواندن صفحات عمومی.',
         'The separate Client interface for Eitaayar and public-page reading.', [
        heading('public', 'خواندن عمومی بدون سشن اکانت', 'Read public pages without an account session'),
        code('Public-page reader', '''import asyncio
from fnceitaa import Client

async def main():
    async with Client() as client:
        info = await client.get_chat_info("@eitaa")
        print(info)
        message = await client.get_message("@eitaa", 100)
        if message is not None:
            print(message.text)

asyncio.run(main())'''),
        heading('token-send', 'ارسال با توکن ایتایار', 'Send with an Eitaayar token'),
        code('Token from your own environment · sends one message', '''import asyncio
import os
from fnceitaa import Client

async def main():
    async with Client(token=os.environ["EITAAYAR_TOKEN"]) as client:
        sent = await client.send_message("YOUR_DESTINATION", "سلام")
        print(sent.id)

asyncio.run(main())'''),
        paragraph('مقصد باید برای همان توکن معتبر باشد. این مسیر به OTP و فایل account.session وصل نیست. خواندن عمومی به username نیاز دارد و شناسهٔ عددی تنها کافی نیست. حذف با backend توکن پشتیبانی نمی‌شود. ارسال با توکن هنوز تست زندهٔ ثبت‌شده ندارد.',
                  'The destination must be valid for that token. This path does not use OTP or account.session. Public reads require a username, not just a numeric ID. Deletion is unsupported by the token backend. Token sending has no recorded live acceptance yet.'),
        note('متدهای AccountClient، مدل‌های AccountMessage و رویدادهای اکانت را با Client و Message قدیمی مخلوط نکن. کلاس Client در مرجع API جداگانه فهرست شده است.',
             'Do not mix AccountClient, AccountMessage or account events with the earlier Client and Message models. Client has its own class filter in the API reference.'),
    ]),
    page('publishing', 'project', 'این سایت و توسعهٔ مستندات', 'This site & contributing',
         'اجرای محلی، به‌روزکردن مرجع و آماده‌سازی برای GitHub Pages.',
         'Preview locally, regenerate the catalog and prepare for GitHub Pages.', [
        heading('preview', 'اجرای محلی', 'Local preview'),
        code('Serve only the documentation folder', 'python -m http.server 8765 --bind 127.0.0.1 --directory docs', 'powershell'),
        paragraph('سپس نشانی `http://127.0.0.1:8765` را در مرورگر باز کن. سایت به فرایند build جاوااسکریپت، CDN یا backend نیاز ندارد. فایل index.html را هم می‌توان مستقیم باز کرد؛ برای clipboard و بررسی واقعی مسیرها، سرور محلی مناسب‌تر است.',
                  'Open `http://127.0.0.1:8765` in your browser. The site needs no JavaScript build process, CDN or backend. You can also open index.html directly; a local server is preferable for clipboard support and realistic path checks.'),
        heading('regenerate', 'تغییر محتوا و بازتولید', 'Edit and regenerate'),
        code('Generate / check', '''python tools/build_docs.py
python tools/build_docs.py --check''', 'powershell'),
        paragraph('متن دوزبانه در `docs/site_content.py`، ظاهر در `docs/assets/styles.css` و رفتار صفحه در `docs/assets/app.js` است. اسکریپت سازنده امضای متدها و فیلد مدل‌ها را از AST می‌خواند و content.js را تولید می‌کند؛ نه سشن را باز می‌کند و نه به ایتا وصل می‌شود.',
                  'Bilingual text lives in `docs/site_content.py`, styles in `docs/assets/styles.css`, and interaction in `docs/assets/app.js`. The generator reads method signatures and model fields through AST and produces content.js; it neither opens sessions nor connects to Eitaa.'),
        heading('github-pages', 'آماده برای GitHub Pages', 'Ready for GitHub Pages'),
        paragraph('برای انتشار، فایل‌های سایت را در مخزن commit و push کن؛ سپس در Settings → Pages، منبع Deploy from a branch را انتخاب کن و شاخهٔ موردنظرت با پوشهٔ /docs را بده. فایل .nojekyll همراه سایت است. مسیرها نسبی‌اند و routeهایی مثل #fa/overview و #en/overview بدون تنظیم سرور کار می‌کنند. ساخت سایت به معنی انتشار آن نیست.',
                  'To publish, commit and push the site files, then choose Deploy from a branch under Settings → Pages and select your branch with the /docs folder. The site includes .nojekyll. Relative asset paths and routes such as #fa/overview and #en/overview require no server rewrite. Preparing the site does not publish it.'),
        heading('report', 'گزارش خطا', 'Report an issue'),
        paragraph('نسخهٔ پایتون، نسخهٔ بسته، نام متد، دستور اجرا و خطای پاک‌سازی‌شده را بنویس. سشن، توکن، کد ورود، access hash، فایل پایگاه داده و متن خصوصی پیام‌ها را نفرست. برای ادعای قابلیت تازه، بین تست محلی و مشاهدهٔ زنده تفاوت بگذار.',
                  'Include your Python and package versions, method name, command and a redacted error. Do not attach sessions, tokens, login codes, access hashes, databases or private message text. Distinguish local tests from observed live behavior when documenting a new capability.'),
    ]),
]

# Short descriptions shown next to source-derived signatures, not another API.
SUMMARIES = {
    '__init__': bi('ساخت نمونه با گزینه‌های مشخص‌شده.', 'Construct an instance with the listed options.'),
    'connect': bi('بازکردن اتصال؛ در اکانت، بررسی سشن موجود بدون OTP خودکار.', 'Connect; AccountClient checks an existing session without automatic OTP.'),
    'start': bi('اتصال و بررسی سشن؛ ورود تعاملی در نبود سشن یا رد صریح آن، مگر با auto_login=False.', 'Connect/check the session and log in interactively if missing or explicitly rejected, unless auto_login=False.'),
    'disconnect': bi('بستن اتصال محلی؛ در اکانت فایل سشن حفظ می‌شود.', 'Disconnect locally; AccountClient retains its saved session.'),
    'is_authorized': bi('وضعیت مجاز بودن کلاینت جاری؛ property است.', 'Whether the current client is authorized; a property.'),
    'request_code': bi('یک درخواست OTP صریح؛ خروجی CodeRequest.', 'Explicitly request one OTP; returns CodeRequest.'),
    'resend_code': bi('درخواست مجدد صریح برای چالش جاری.', 'Explicitly request another code for the current challenge.'),
    'sign_in': bi('تکمیل ورود با کد؛ ممکن است PasswordRequiredError بدهد.', 'Sign in with a code; may raise PasswordRequiredError.'),
    'sign_in_password': bi('تکمیل مرحلهٔ رمز دوم؛ خروجی dict کاربر.', 'Complete two-step authentication; returns a user dict.'),
    'get_me': bi('خواندن هویت حساب؛ خروجی dict از user.', 'Read the current account identity; returns a user dict.'),
    'resolve_peer': bi('تبدیل مقصد به AccountPeer با اطلاعات معتبر همین حساب.', 'Resolve a destination to an AccountPeer for this account.'),
    'get_dialogs_page': bi('یک DialogPage با next_cursor اختیاری.', 'Read one DialogPage with an optional next_cursor.'),
    'iter_dialogs': bi('async iterator از AccountDialog؛ با async for.', 'Async iterator of AccountDialog; consume with async for.'),
    'get_dialogs': bi('مجموعهٔ محدود گفتگوها.', 'Read a bounded collection of dialogs.'),
    'get_message': bi('خواندن یک پیام؛ در اکانت AccountMessage یا None.', 'Read one message; AccountClient returns AccountMessage or None.'),
    'get_history_page': bi('یک HistoryPage با offset بعدی.', 'Read one HistoryPage with the next offset.'),
    'iter_history': bi('async iterator پیام‌ها از جدید به قدیم.', 'Async iterator of messages, newest first.'),
    'get_history': bi('مجموعهٔ محدود AccountMessage از جدید به قدیم.', 'Read a bounded collection of AccountMessage, newest first.'),
    'send_message': bi('ارسال مستقیم متن؛ در اکانت خروجی AccountMessage.', 'Send text directly; AccountClient returns AccountMessage.'),
    'edit_message': bi('ویرایش متن؛ خروجی AccountMessage.', 'Edit text; returns AccountMessage.'),
    'delete_messages': bi('حذف با scope صریح؛ در اکانت خروجی DeletedMessages.', 'Delete with explicit scope; AccountClient returns DeletedMessages.'),
    'forward_message': bi('فوروارد یک پیام؛ خروجی ForwardedMessage. مقصد اول است.', 'Forward one message; returns ForwardedMessage. Destination comes first.'),
    'forward_messages': bi('فوروارد چند پیام؛ tuple از ForwardedMessage.', 'Forward several messages; returns a tuple of ForwardedMessage.'),
    'copy_message': bi('درخواست کپی بدون نویسنده؛ خروجی ForwardedMessage.', 'Request a copy without attribution; returns ForwardedMessage.'),
    'media': bi('helper مدیای همین کلاینت؛ AccountMedia.', 'Create the AccountMedia helper for this client.'),
    'profiles': bi('helper اطلاعات کامل و اعضا؛ AccountProfiles.', 'Create the AccountProfiles helper.'),
    'updates': bi('consumer رویداد با checkpoint جدا؛ AccountUpdates.', 'Create an AccountUpdates consumer with a separate checkpoint.'),
    'queue': bi('بازکردن AccountQueue؛ پیش از client ببند.', 'Open an AccountQueue; close it before the client.'),
    'upload_file': bi('آپلود بدون ارسال پیام؛ خروجی UploadedFile.', 'Upload without sending a message; returns UploadedFile.'),
    'send_file': bi('ارسال فایل؛ سند پیش‌فرض است. گزینه‌ها در AccountMedia.', 'Send a file; document is the default. See AccountMedia for options.'),
    'send_photo': bi('ارسال عکس؛ خروجی AccountMessage.', 'Send a photo; returns AccountMessage.'),
    'send_voice': bi('ارسال ویس با مدت صریح برحسب ثانیه.', 'Send voice with explicit duration in seconds.'),
    'send_video': bi('ارسال ویدیو با مدت و ابعاد صریح.', 'Send video with explicit duration and dimensions.'),
    'send_album': bi('ارسال ۲ تا ۱۰ MediaItem؛ tuple از AccountMessage.', 'Send 2–10 MediaItem objects; returns a tuple of AccountMessage.'),
    'download_media': bi('دانلود به مسیر انتخابی؛ خروجی DownloadedFile.', 'Download to an explicit path; returns DownloadedFile.'),
    'get_chat_info': bi('اطلاعات مقصد؛ در اکانت مدل کامل کاربر یا چت.', 'Read peer information; AccountClient returns a full profile model.'),
    'get_full_user': bi('اطلاعات کامل کاربر؛ AccountUserFull.', 'Read a full user profile; AccountUserFull.'),
    'get_full_group': bi('اطلاعات کامل گروه پایه؛ AccountChatFull.', 'Read a basic-group profile; AccountChatFull.'),
    'get_full_channel': bi('اطلاعات کامل کانال یا سوپرگروه؛ AccountChatFull.', 'Read a channel or supergroup profile; AccountChatFull.'),
    'get_member_count': bi('شمار اعضا؛ int یا None در نبود داده.', 'Read member count; int or None when unavailable.'),
    'get_members_page': bi('یک MemberPage با فیلتر و offset.', 'Read one MemberPage using a filter and offset.'),
    'iter_members': bi('async iterator از AccountMember در محدودهٔ مجوز.', 'Async iterator of permitted AccountMember records.'),
    'get_member': bi('خواندن نقش یک عضو؛ AccountMember یا None در نبود عضویت.', 'Read one participant’s role; AccountMember or None if not found.'),
    'initialize': bi('بارگذاری checkpoint یا شروع از وضعیت جاری.', 'Load the checkpoint or initialize at current state.'),
    'watch_channel': bi('افزودن کانال به دریافت difference اختصاصی.', 'Watch a channel’s separate difference stream.'),
    'watch_dialogs': bi('افزودن کانال‌های قابل دسترس از گفتگوها.', 'Watch accessible channels from dialogs.'),
    'poll': bi('دریافت یک UpdateBatch بدون ack.', 'Fetch one UpdateBatch without acknowledging it.'),
    'ack': bi('ثبت checkpoint batch پس از پردازش موفق.', 'Commit a batch checkpoint after successful processing.'),
    'run_polling': bi('اجرای پیوسته؛ امضا براساس کلاس متفاوت است.', 'Run continuous polling; the signature depends on the class.'),
    'stop': bi('درخواست توقف حلقهٔ رویداد اکانت.', 'Request the account polling loop to stop.'),
    'close': bi('بستن اتصال محلی SQLite.', 'Close the local SQLite connection.'),
    'enqueue_message': bi('ثبت متن در صف بدون ارسال؛ OutboxJob.', 'Enqueue text without sending; returns OutboxJob.'),
    'enqueue_edit': bi('ثبت ویرایش در صف بدون اجرا.', 'Enqueue an edit without executing it.'),
    'enqueue_delete': bi('ثبت حذف در صف با revoke صریح.', 'Enqueue deletion with an explicit revoke scope.'),
    'enqueue_forward': bi('ثبت فوروارد یا copy=True بدون اجرا.', 'Enqueue forwarding or copy=True without execution.'),
    'enqueue_file': bi('ثبت فایل ماندگار با snapshot اندازه و هش.', 'Enqueue a persistent file with its size and hash snapshot.'),
    'enqueue_album': bi('ثبت آلبوم از مسیرهای ماندگار.', 'Enqueue an album using persistent paths.'),
    'dispatch_one': bi('claim و اجرای یک pending؛ OutboxJob یا None.', 'Claim and dispatch one pending job; returns OutboxJob or None.'),
    'poll_once': bi('ثبت اتمی رویدادها و checkpoint در SQLite.', 'Commit events and checkpoint atomically to SQLite.'),
    'import_checkpoint': bi('ورود صریح JSON در checkpoint SQLite استفاده‌نشده.', 'Explicitly import JSON into an unused SQLite checkpoint.'),
    'get_job': bi('خواندن وضعیت یک job محلی.', 'Read one local job.'),
    'list_jobs': bi('فهرست jobها بدون dispatch.', 'List jobs without dispatching.'),
    'enqueue': bi('API سطح پایین؛ معمولاً از AccountQueue استفاده کن.', 'Low-level storage API; usually prefer AccountQueue.'),
    'claim_job': bi('claim اتمی کار pending با lease؛ بدون شبکه.', 'Atomically claim a pending job with a lease; no network.'),
    'finish_job': bi('ثبت نتیجه فقط با claim معتبر.', 'Persist a result using a valid claim token.'),
    'retry_failed': bi('تبدیل صریح failed به pending بعد از تأخیر لازم.', 'Explicitly return failed to pending after any required wait.'),
    'cancel_job': bi('لغو کار pending پیش از ارسال.', 'Cancel a pending job before dispatch.'),
    'resolve_uncertain': bi('ثبت تصمیم پس از بررسی مستقل؛ نیازمند note.', 'Record an independently verified decision; requires a note.'),
    'recover_expired': bi('بازیابی claim منقضی؛ هیچ پیامی ارسال نمی‌کند.', 'Recover expired claims; sends no messages.'),
    'stats': bi('شمار وضعیت‌های inbox و outbox.', 'Count inbox and outbox states.'),
    'list_inbox': bi('فهرست رویدادهای ذخیره‌شده با وضعیت پردازش.', 'List persisted events and processing states.'),
    'claim_inbox': bi('claim رویداد pending برای پردازش.', 'Claim a pending event for processing.'),
    'finish_inbox': bi('موفقیت به done؛ success=False به pending.', 'Success marks done; success=False returns pending.'),
    'on': bi('دکوراتور ثبت هندلر یک‌آرگومانی.', 'Register a one-argument handler with a decorator.'),
    'on_message': bi('میانبر نام message؛ برای اکانت از on("new") استفاده کن.', 'Shortcut for message; use on("new") for account events.'),
    'emit': bi('فراخوانی هندلرهای رویداد و انتشار خطای پردازش.', 'Invoke registered event handlers and propagate processing failures.'),
    'get_info': bi('نام جایگزین get_chat_info در Client توکن.', 'Alias for get_chat_info in the token Client.'),
    'stop_polling': bi('درخواست توقف polling کلاینت توکن.', 'Request token-client polling to stop.'),
}
