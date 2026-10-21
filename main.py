import asyncio
from fnceitaa import AccountClient


async def main():
    async with AccountClient("account.eitaa") as client:
        sent = await client.send_message("@test_eitaaa", "سلام!")
        print(sent.id)


if __name__ == "__main__":
    asyncio.run(main())
