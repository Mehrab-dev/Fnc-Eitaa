import asyncio
from fnceitaa import AccountClient


async def main():
    async with AccountClient("account.session") as client:
        sent = await client.send_message("@test_eitaaa", "سلام!")
        print(sent.id)


asyncio.run(main())