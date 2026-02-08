import os
import json
import asyncio
import argparse
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from collections import defaultdict
import re

# Configuration
CONFIG_FILE = 'backend/net_config.json'
# Output file determined by shard index
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_GENERAL')

def tokenize(text):
    text = text.replace('ي', 'ی').replace('ك', 'ک')
    text = re.sub(r'http\S+', '', text)
    tokens = re.findall(r'[آ-ی]+', text)
    return [t for t in tokens if len(t) > 2]

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shard', type=int, default=0, help='Shard index (0-based)')
    parser.add_argument('--total', type=int, default=1, help='Total number of shards')
    args = parser.parse_args()

    output_file = f'backend/data/trend_history_part_{args.shard}.json'

    if not API_ID or not API_HASH or not SESSION_STRING:
        print("Error: Missing Telegram Credentials.")
        return

    # Load Config
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    
    all_nodes = config.get('nodes', [])
    
    # Sharding Logic (Simple Round Robin / Modulo)
    # my_nodes = [n for i, n in enumerate(all_nodes) if i % args.total == args.shard]
    # Slicing is easier:
    my_nodes = all_nodes[args.shard::args.total]
    
    print(f"Shard {args.shard}/{args.total} processing {len(my_nodes)} nodes: {my_nodes}")
    
    if not my_nodes:
        print("No nodes to process for this shard.")
        # Create empty output to prevent workflow errors
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({"word_counts_30d": {}, "word_counts_24h": {}}, f)
        return

    from telethon.sessions import StringSession
    try:
        client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
        await client.start()
    except Exception as e:
        print(f"Failed to connect: {e}")
        return

    word_counts_30d = defaultdict(int)
    word_counts_24h = defaultdict(int) 
    
    now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)
    cutoff_24h = now - timedelta(hours=24)
    
    total_messages = 0

    for node in my_nodes:
        print(f"[{args.shard}] Processing {node}...")
        try:
            entity = await client.get_input_entity(node)
            async for msg in client.iter_messages(entity, limit=None):
                if not msg.date: continue
                msg_date = msg.date.astimezone(timezone.utc)
                
                if msg_date < cutoff_30d:
                    break 
                
                if msg.text:
                    total_messages += 1
                    tokens = set(tokenize(msg.text))
                    
                    for t in tokens:
                        word_counts_30d[t] += 1
                        if msg_date > cutoff_24h:
                            word_counts_24h[t] += 1
        except Exception as e:
            print(f"Error processing {node}: {e}")
            continue

    print(f"[{args.shard}] Finished. {total_messages} messages.")
    
    # Checkpoint Output
    output = {
        "shard": args.shard,
        "word_counts_30d": word_counts_30d,
        "word_counts_24h": word_counts_24h
    }
    
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        
    print(f"Saved partial results to {output_file}")
    await client.disconnect()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
