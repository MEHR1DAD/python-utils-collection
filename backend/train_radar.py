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
    # Shard arguments are no longer needed for single-file output
    # parser.add_argument('--shard', type=int, default=0, help='Shard index (0-based)')
    # parser.add_argument('--total', type=int, default=1, help='Total number of shards')
    parser.add_argument('--days', type=int, default=30, help='Number of days to look back (default: 30)')
    args = parser.parse_args()

    output_file = 'backend/data/trend_history.json' # Single output file

    # Helper to Save and Exit
    def save_and_exit(data=None, error=None):
        if data is None:
            data = {"word_counts_30d": {}, "word_counts_24h": {}}
        
        if error:
            data["error"] = str(error)
            print(f"Exiting with error: {error}") # Removed shard index from print
        
        # Always ensure directory exists
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Saved output to {output_file}") # Removed shard index from print

    if not API_ID or not API_HASH or not SESSION_STRING:
        print("Error: Missing Telegram Credentials.")
        print(f"API_ID: {'Set' if API_ID else 'MISSING'}")
        print(f"API_HASH: {'Set' if API_HASH else 'MISSING'}")
        print(f"SESSION: {'Set' if SESSION_STRING else 'MISSING'}")
        save_and_exit(error="Missing Credentials")
        return

    # Load Config
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        all_nodes = config.get('nodes', [])
        my_nodes = all_nodes # Process ALL nodes
    except Exception as e:
        save_and_exit(error=f"Config Error: {e}")
        return
    
    print(f"Processing {len(my_nodes)} nodes (Concurrency: 3).") # Removed shard info, added concurrency info
    
    if not my_nodes:
        print("No nodes to process.") # Removed shard info
        save_and_exit()
        return

    from telethon.sessions import StringSession
    try:
        client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
        await client.start()
    except Exception as e:
        save_and_exit(error=f"Connection Failed: {e}")
        return

    word_counts_30d = defaultdict(int)
    word_counts_24h = defaultdict(int) 
    
    now = datetime.now(timezone.utc)
    cutoff_history = now - timedelta(days=args.days)
    cutoff_24h = now - timedelta(hours=24)
    
    print(f"Training for last {args.days} days (Cutoff: {cutoff_history.isoformat()})...")
    
    total_messages = 0
    
    # Semaphore to limit concurrent channels
    sem = asyncio.Semaphore(3)

    async def process_node(node):
        nonlocal total_messages
        async with sem:
            print(f"Processing {node}...")
            try:
                entity = await client.get_input_entity(node)
                async for msg in client.iter_messages(entity, limit=None):
                    if not msg.date: continue
                    msg_date = msg.date.astimezone(timezone.utc)
                    
                    if msg_date < cutoff_history:
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

    try:
        tasks = [process_node(node) for node in my_nodes]
        await asyncio.gather(*tasks)

        print(f"Finished. {total_messages} messages.")
        
        # Calculate Rates Here (Since we have global counts)
        hours_history = args.days * 24
        hours_24h = 24
        baselines = {}
        
        for word, count_history in word_counts_30d.items():
            # Adjust min occurrence based on duration (0.5 per day)
            min_occurrence = max(5, int(0.5 * args.days))
            if count_history < min_occurrence: continue 
            
            count_24h = word_counts_24h.get(word, 0)
            baselines[word] = {
                "rate_7d": round(count_history / hours_history, 4), # Renaming to rate_long might be better but keep legacy
                "rate_24h": round(count_24h / hours_24h, 4),
                "raw_total": count_history,
                "days_trained": args.days
            }

        # Checkpoint Output
        output = {
            "updated_at": now.isoformat(),
            "trained": True,
            "baselines": baselines
        }
        save_and_exit(data=output)
        
    except Exception as e:
        save_and_exit(error=f"Runtime Error: {e}")
    finally:
        await client.disconnect()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
