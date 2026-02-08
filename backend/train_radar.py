import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from collections import defaultdict
import re

# Configuration
CONFIG_FILE = 'backend/net_config.json'
HISTORY_FILE = 'backend/data/trend_history.json'
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
# Use General Session for this (it likely has access to public channels)
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_GENERAL')

# Regex for Persian Tokenization (Same as update_trends_history.py)
def tokenize(text):
    text = text.replace('ي', 'ی').replace('ك', 'ک')
    # Remove URLs
    text = re.sub(r'http\S+', '', text)
    # Keep only Persian chars and spaces
    tokens = re.findall(r'[آ-ی]+', text)
    return [t for t in tokens if len(t) > 2] # Filter short words

async def main():
    if not API_ID or not API_HASH or not SESSION_STRING:
        print("Error: Missing Telegram Credentials (API_ID, API_HASH, SESSION).")
        return

    # Load Config to get Nodes
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    
    nodes = config.get('nodes', [])
    print(f"Loaded {len(nodes)} nodes for training.")

    from telethon.sessions import StringSession
    try:
        client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
        await client.start()
    except Exception as e:
        print(f"Failed to connect: {e}")
        return

    print("Connected to Telegram.")
    
    # Data Structure: { word: [timestamps] }
    # We will just count occurrences per hour to save memory, then aggregate
    # Actually, to compute rate_7d, we just need total count over 30 days.
    # To compute rate_24h, we need recent counts.
    # Let's count totals for the last 30 days.
    
    word_counts_30d = defaultdict(int)
    word_counts_24h = defaultdict(int) 
    
    now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)
    cutoff_24h = now - timedelta(hours=24)
    
    total_messages = 0

    for node in nodes:
        print(f"Processing {node}...")
        try:
            # Get entity
            entity = await client.get_input_entity(node)
            
            # Fetch history (reverse=True is oldest first? No, default is newest first)
            # We iterate backwards until cutoff
            
            async for msg in client.iter_messages(entity, limit=None):
                if not msg.date: continue
                
                msg_date = msg.date.astimezone(timezone.utc)
                
                if msg_date < cutoff_30d:
                    break # Reached 30 days limit
                
                if msg.text:
                    total_messages += 1
                    tokens = set(tokenize(msg.text)) # Unique per message (document frequency)
                    
                    for t in tokens:
                        word_counts_30d[t] += 1
                        if msg_date > cutoff_24h:
                            word_counts_24h[t] += 1
                            
        except Exception as e:
            print(f"Error processing {node}: {e}")
            continue

    print(f"Training Complete. Processed {total_messages} messages.")
    
    # Calculate Rates
    # Rate = Count / Hours
    hours_30d = 30 * 24
    hours_24h = 24
    
    baselines = {}
    
    # Filter noise: Word must appear at least 15 times in 30 days (0.5 per day)
    for word, count_30d in word_counts_30d.items():
        if count_30d < 15: continue
        
        count_24h = word_counts_24h.get(word, 0)
        
        # We use 7d rate concept, but verify with 30d data for stability
        # The 'rate_7d' field in our system implies "Long Term Average".
        # So Average 30d is even better.
        
        rate_long = count_30d / hours_30d
        rate_short = count_24h / hours_24h
        
        baselines[word] = {
            "rate_7d": round(rate_long, 4), # Mapping 30d avg to 'rate_7d' key for compatibility
            "rate_24h": round(rate_short, 4),
            "raw_30d": count_30d
        }
        
    # Save/Update History
    # We should merge with existing if possible, or overwrite?
    # Overwrite is better for a "Reset/Train" operation.
    
    output = {
        "updated_at": now.isoformat(),
        "trained": True,
        "baselines": baselines
    }
    
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        
    print(f"Saved baselines for {len(baselines)} words to {HISTORY_FILE}")
    await client.disconnect()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
