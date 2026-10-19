# گام ۹ — اطلاعات کامل و اعضای گفتگو

در گام ۴ شناسه، نوع، نام و access hash گفتگو را می‌گرفتیم. این گام توضیحات کامل،
اطلاعات قابل‌دسترسی پروفایل، تعداد اعضا و مدیران، و عضویت و نقش اشخاص را اضافه
می‌کند. همهٔ عملیات این گام خواندنی‌اند؛ مجوزهای حساب و پاسخ سرور تعیین می‌کنند
چه اطلاعاتی قابل دریافت است.

اطلاعات کامل حساب خود و کانال عمومی ایتا با سشن موجود خوانده شده است. کاربر اطلاعات
کانال خصوصی آزمایشی و فهرست دو عضو با نقش مالک/عضو را تأیید کرده است. نقش خود حساب
پس از اصلاح پاسخ self نیز با خواندن واقعی تأیید شد. گروه معمولی، سوپرگروه و فهرست
چندصفحه‌ای همچنان پذیرش زندهٔ مستقل می‌خواهند.

## API اکانت

| متد روی `AccountClient` | خروجی و کاربرد |
| --- | --- |
| `get_chat_info(peer)` | انتخاب خودکار اطلاعات کامل کاربر، گروه یا کانال |
| `get_full_user(peer='self')` | `AccountUserFull`؛ پروفایل کامل کاربر |
| `get_full_group(peer)` | `AccountChatFull`؛ گروه معمولی یا سوپرگروه |
| `get_full_channel(peer)` | `AccountChatFull`؛ کانال یا سوپرگروه با نوع channel |
| `get_member_count(peer)` | تعداد اعضای گروه/کانال؛ `None` اگر سرور ارائه نکند |
| `get_members_page(peer, *, offset=0, limit=100, filter='recent', query='')` | یک `MemberPage` با حداکثر ۲۰۰ عضو |
| `iter_members(peer, *, limit=100, page_size=100, filter='recent', query='')` | پیمایش lazy اعضای قابل‌مشاهده تا سقف خواسته‌شده |
| `get_member(peer, participant='self')` | `AccountMember` یا `None` برای عدم عضویت مشخص |

ورودی مقصد و شخص همان ورودی‌های `resolve_peer` است: `self`، نام کاربری، مدل
`AccountPeer` و شناسهٔ نوع‌دار موجود در کش. برای شناسهٔ عددی کاربر یا کانال باید
access hash واقعی همان اکانت قبلاً از dialogs یا پاسخ‌های API گرفته شده باشد.
شناسهٔ عددی ناشناخته با access hash ساختگی ارسال نمی‌شود. `get_full_group` کانال
پخش عمومی را رد می‌کند؛ برای آن از `get_full_channel` یا `get_chat_info` استفاده کنید.

```python
from fnceitaa import AccountClient

async def inspect():
    async with AccountClient('account.session') as client:
        me = await client.get_full_user()
        print(me.common_chats_count)
        channel = await client.get_chat_info('@eitaa')
        print(channel.member_count, channel.can_view_participants)
        # فقط برای گفتگویی که حق مشاهدهٔ اعضایش را دارید:
        # async for member in client.iter_members('@your_group', limit=20):
        #     print(member.peer.id, member.role)
```

این `get_chat_info` متعلق به `AccountClient` است و به API عمومیِ `Client` و
استخراج HTML وابسته نیست. از همان سشن ذخیره‌شده استفاده می‌کند و OTP نمی‌خواهد.

## مدل‌ها و معنای اطلاعات

- `AccountUserFull`: `peer`، `about` اختیاری، `common_chats_count`، `blocked`،
  `phone_calls_available`، `phone_calls_private`، `video_calls_available` و
  `pinned_message_id`. فیلدهای تماس، پرچم‌های اعلام‌شدهٔ سرورند؛ API تماس صوتی/تصویری
  در این فاز پیاده نشده است. `blocked` وضعیت رابطهٔ همین حساب با مقصد است.
- `AccountChatFull`: `peer`، `about`، `member_count`، `admin_count`، `banned_count`،
  `kicked_count`، `online_count`، `can_view_participants`، `pinned_message_id`،
  `linked_chat_id`، `slowmode_seconds` و `participants_version` برای گروه معمولی.
  `is_creator` و `admin_rights` نقش/حقوق اکانت جاری در همان گفتگو را از هویت برگشتی
  سرور بیان می‌کنند؛ این‌ها حقوق همهٔ اعضا نیستند.
  نبودن شمارنده با صفر فرق دارد؛ نبودن فیلد اختیاری، `None` می‌دهد.
- `AccountMember`: `peer` و نقش `owner/admin/member/restricted/banned/left/unknown`، وضعیت
  `is_member`، زمان ورود در صورت ارائه، inviter/promoter، عنوان مدیر و حقوق
  `admin_rights` و `banned_rights` به شکل tuple نام پرچم‌ها. `restriction_until=0`
  همان مقدار سرور است و به تاریخ پایان ساختگی تبدیل نمی‌شود. فرد محدودشده ممکن است
  هنوز عضو باشد؛ فرد banned/left عضو فعال محسوب نمی‌شود. یک peer در نتیجهٔ عضویت
  الزاماً access hash کافی برای ارسال مستقیم ندارد.
  `role_source` منبع نقش را به صورت `participant` یا `channel_profile` مشخص می‌کند.
- `MemberPage`: `peer`، `members`، `total_count`، `next_offset`، `filter` و `query`.
  شمارنده در فیلتر جستجو/مدیران مربوط به همان نتیجه است، نه کل اعضای گفتگو.

مدل‌ها immutable هستند. توضیحات، مشخصات peer، عنوان مدیر و شناسهٔ دعوت‌کننده در
repr مدل‌های جدید نمایش داده نمی‌شوند. فایل سشن و توکن در خروجی مثال چاپ نمی‌شوند.
CLI به طور پیش‌فرض فقط شناسه‌ها، نقش‌ها و شمارنده‌ها را نشان می‌دهد؛ نام و توضیحات
با `--show-names` و `--show-about` در ترمینال خودتان قابل نمایش‌اند.

## اعضا و محدودیت دسترسی

برای کانال/سوپرگروه فیلترهای `recent`، `admins`، `bots`، `search`، `banned` و
`kicked` وجود دارند. `query` فقط برای سه مورد آخرِ جستجودار (`search/banned/kicked`)
پذیرفته می‌شود. فیلترها و دسترسی به آن‌ها را سرور اجرا می‌کند؛ کد حقوق جدیدی به
اکانت نمی‌دهد. برای گروه معمولی، `recent` فهرست فعلی برگشتی سرور و `admins`
مالک/مدیران همان فهرست است؛ اینجا ترتیب recent ادعای مرتب‌سازی بر اساس زمان نیست.
فیلترهای دیگر گروه معمولی صریحاً پشتیبانی نمی‌شوند.

در گروه معمولی، فهرست کامل از `messages.getFullChat` خوانده و سپس صفحه‌بندی می‌شود؛
صفحه‌های جدا ممکن است در زمان متفاوت خوانده شوند. اگر سرور `chatParticipantsForbidden`
بدهد، پروفایل و شمارندهٔ خلاصه در صورت وجود قابل خواندن‌اند، اما درخواست فهرست اعضا
`UnsupportedOperationError` می‌دهد. فهرست ممنوع، فهرست خالی یا عدم عضویت نیست.

`get_member` برای کانال/سوپرگروه از `channels.getParticipant` استفاده می‌کند؛ فقط
`USER_NOT_PARTICIPANT` به `None` تبدیل می‌شود. خصوصی‌بودن گفتگو، نیاز به مدیریت،
FLOOD_WAIT و خطاهای دیگر مستقیم گزارش می‌شوند و خودکار تکرار نمی‌شوند.
برای گروه معمولی، عدم حضور در فهرست کامل قابل‌دسترسی `None` می‌دهد.

در پاسخ واقعی ایتا، `getParticipant` برای خود حساب حتی در کانالی که مالک آن است،
گاهی `channelParticipantSelf` برمی‌گرداند. این پاسخ عضویت را ثابت می‌کند ولی نقش را
مشخص نمی‌کند؛ بنابراین parser آن را عضو عادی فرض نمی‌کند. `get_member` برای همین
حالت یک درخواست خواندنی دیگر به `channels.getFullChannel` می‌فرستد. فقط پرچم صریح
`creator` یا حقوق مدیریتی غیرخالیِ اکانت جاری در هویت همان کانال، نقش را به
`owner/admin` تبدیل می‌کند؛ در غیر این صورت `role=unknown` و `is_member=True` است.
خطای درخواست تکمیلی مستقیم گزارش می‌شود و نقش قطعی ساخته نمی‌شود. رکوردهای صریح
`channelParticipantCreator/Admin` در فهرست اعضا به این درخواست اضافه نیاز ندارند.
شمارندهٔ مدیران مستقلاً همان مقدار سرور باقی می‌ماند و از نقش مالک محاسبه نمی‌شود.

پیمایش تا ۱۰هزار نتیجه در هر فراخوانی محدود است، صفحه‌های همپوشان را حذف تکرار
می‌کند و تکرار بی‌پیشرفت را خطا می‌داند. صفحهٔ خالی پایان پیمایش است حتی اگر
شمارندهٔ گزارش‌شده بزرگ‌تر باشد. `next_offset` فقط ادامهٔ نتایج قابل‌مشاهده است؛
سرور ممکن است فهرست را محدود کند و تغییر عضویت هنگام پیمایش نیز ممکن است نتیجه
را جابه‌جا کند. هیچ ادعای استخراج تضمینی تمام مشترکان یا snapshot اتمیک وجود ندارد.

افزودن/اخراج عضو، ban/unban و تغییر مدیر در این گام پیاده نشده‌اند. «عملیات اعضا»
در این نسخه به خواندن فهرست، نقش‌ها و بررسی عضویت محدود است؛ تغییر عضویت نیازمند
طراحی مجزای مجوز و رسید عملیات نوشتن است.

## تست زنده، یکی‌یکی

۱. اطلاعات حساب خودتان:

```powershell
python -m examples.account_profiles info --live --peer self
```

۲. اطلاعات کانال عمومی و تعداد اعضا:

```powershell
python -m examples.account_profiles info --live --peer "@eitaa"
```

۳. بعد از دو مورد بالا، یک گروه متعلق به خودتان را با `account_dialogs` پیدا کنید.
نوع و شناسهٔ واقعی را جایگزین مثال کنید؛ گروه معمولی `chat:ID` و سوپرگروه
`channel:ID` است. `--load-dialogs` کش مقصدها را با همان سشن آماده می‌کند:

```powershell
python -m examples.account_profiles info --live --load-dialogs --peer "chat:123"
python -m examples.account_profiles members --live --load-dialogs --peer "chat:123" --limit 5
python -m examples.account_profiles member --live --load-dialogs --peer "chat:123" --participant self
```

برای نام کاربری عمومی گروه از کوتیشن PowerShell استفاده کنید. کانال رسمی ایتا
مقصد مناسبی برای آزمون مجوز مدیریت و مشاهدهٔ مشترکان نیست؛ فهرست اعضا را روی
گروه خودتان و با مجوز همان حساب تست کنید.

بدون `--live` هیچ سشن، prompt یا درخواست شبکه‌ای باز نمی‌شود. CLI تنظیمی را
تغییر نمی‌دهد و در خطای محدودیت زمانی، مدت انتظار را اعلام می‌کند.

## شواهد و بررسی

پروتکل از worker عمومی پین‌شده با SHA256
`ef186db9dfa2a1e79ad30aadd805a5d455e90ee66b2827feca8006df979f4c17` استخراج شده است.
`users.getFullUser` در این schema پاسخ قدیمی `userFull` با `user` داخلی دارد؛
wrapper جدید `users.userFull` به طور حدسی جایگزین نشده است. شواهد ساختاری، offset
فراخوانی‌ها، پوشش محلی و وضعیت پذیرش در `research/protocol/account_step9_evidence.json`
ثبت می‌شوند؛ شناسه‌ها، نام‌ها و توضیحات خصوصی در آن نیستند.

تست‌های `tests/test_account_profiles.py` مسیر باینری TL/HTTP هر پنج RPC، انتخاب
InputUserSelf و access hash واقعی، تفاوت گروه معمولی/سوپرگروه، شمارندهٔ صفر/نامشخص،
تطبیق هویت پاسخ، نقش‌ها و حقوق، ممنوعیت فهرست، فیلترها، صفحه‌بندی و حذف تکرار،
تعویض هویت، تمدید محدود توکن، revoked، FLOOD_WAIT و لغو را پوشش می‌دهند.
