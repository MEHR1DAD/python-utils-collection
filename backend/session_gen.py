import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
import os

# Install telethon if needed: pip install telethon

async def generate_session_async(name):
    print(f"\n--- Generating Session: {name} ---")
    api_id = input("Enter API ID: ").strip()
    api_hash = input("Enter API HASH: ").strip()
    
    async with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        session_str = client.session.save()
        print(f"\n✅ SUCCESS! Here is your {name} session string:\n")
        print(session_str)
        print("\n------------------------------------------------\n")
        return session_str

async def main():
    print("We need to generate 2 separate sessions to allow parallel downloads.")
    print("You can use the SAME phone number for both, just run this twice or login twice.")
    
    print("\n1. Session for Vahid (Fast Updates)")
    await generate_session_async("TELEGRAM_SESSION_VAHID")
    
    print("\n2. Session for Others (Crypto, Tech, History)")
    await generate_session_async("TELEGRAM_SESSION_GENERAL")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception as e:
        print(f"\nError: {e}")
