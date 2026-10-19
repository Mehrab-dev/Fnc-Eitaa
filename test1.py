import asyncio
from fnceitaa import AccountClient, AuthenticationError


async def main():
    try:
        async with AccountClient("account.session") as client:
            message = await client.send_message(
                "self",
                "تست ارسال پیام"
            )
            print(f"پیام ارسال شد؛ شناسه: {message.id}")

    except AuthenticationError as error:
        print(f"خطای احراز هویت: {error}")
        print("ابتدا دوباره وارد حساب شو.")


asyncio.run(main())
