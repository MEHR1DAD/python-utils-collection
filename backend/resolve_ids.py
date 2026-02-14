import os
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = os.environ.get("TELEGRAM_API_ID")
API_HASH = os.environ.get("TELEGRAM_API_HASH")
SESSION_STRING = os.environ.get("TELEGRAM_SESSION")

async def main():
    if not API_ID or not API_HASH or not SESSION_STRING:
        print("Error: Please set TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_SESSION environment variables.")
        return

    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    await client.start()

    channels = ["VahidOnline", "VahidOOnLine", "VahidHeadline"]
    print("\n--- Resolved Channel IDs ---")
    for ch in channels:
        try:
            entity = await client.get_entity(ch)
            print(f"{ch}|{entity.id}")
        except Exception as e:
            print(f"Error resolving {ch}: {e}")
            
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
