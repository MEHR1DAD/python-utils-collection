import os
import json
import glob
from collections import defaultdict
from datetime import datetime, timezone

HISTORY_FILE = 'backend/data/trend_history.json'
PARTIAL_FILES = 'backend/data/trend_history_part_*.json'

def main():
    print("Starting Merge Process...")
    
    files = glob.glob(PARTIAL_FILES)
    if not files:
        print("No partial files found!")
        return

    print(f"Found {len(files)} partial files: {files}")

    total_30d = defaultdict(int)
    total_24h = defaultdict(int)

    # Merge Counts
    for fname in files:
        try:
            with open(fname, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                parts_30d = data.get('word_counts_30d', {})
                parts_24h = data.get('word_counts_24h', {})
                
                for w, c in parts_30d.items():
                    total_30d[w] += c
                    
                for w, c in parts_24h.items():
                    total_24h[w] += c
                    
        except Exception as e:
            print(f"Error reading {fname}: {e}")

    print(f"Merged Total: {len(total_30d)} unique words.")

    # Calculate Rates
    hours_30d = 30 * 24
    hours_24h = 24
    
    baselines = {}
    
    # Filter noise: Word must appear at least 15 times in 30 days (0.5 per day) globally
    for word, count_30d in total_30d.items():
        if count_30d < 15: continue
        
        count_24h = total_24h.get(word, 0)
        
        rate_long = count_30d / hours_30d
        rate_short = count_24h / hours_24h
        
        baselines[word] = {
            "rate_7d": round(rate_long, 4), # Valid long-term baseline
            "rate_24h": round(rate_short, 4), 
            "raw_30d": count_30d
        }

    # Save Final
    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "trained": True,
        "baselines": baselines
    }
    
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        
    print(f"Successfully saved merged history to {HISTORY_FILE}")
    
    # Cleanup partials
    for fname in files:
        os.remove(fname)
    print("Cleaned up partial files.")

if __name__ == '__main__':
    main()
