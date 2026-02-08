
import json
import os
import time
import re
from datetime import datetime, timedelta
from collections import Counter, defaultdict

# Configuration
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
NEWS_FILES = ['news_vahid.json', 'news_tech.json', 'news_crypto.json', 'news_economic.json', 'temp_news_v.json']
HISTORY_FILE = os.path.join(DATA_DIR, 'trend_history.json')

# Persian Stop Words (Simplified for backend - mirroring frontend)
STOP_WORDS = {
    'در', 'به', 'از', 'که', 'می', 'این', 'است', 'را', 'با', 'های', 'برای', 'آن', 'یک', 'شود', 'شده', 'خود', 'ها', 
    'کرد', 'شد', 'ای', 'تا', 'کند', 'بر', 'بود', 'گفت', 'نیز', 'وی', 'هم', 'کنید', 'دارد', 'ما', 'شما', 'او', 
    'باید', 'پس', 'اگر', 'همه', 'نه', 'دیگر', 'چه', 'پیش', 'یکی', 'حتی', 'مورد', 'بیش', 'بان', 'تحت', 'جز', 
    'چون', 'چند', 'دیروز', 'امروز', 'فردا', 'خیر', 'بله', 'شاید', 'اما', 'ولی', 'زیرا', 'چرا', 'نیست', 'هست',
    'http', 'https', 'com', 'ir', 'news', 't.me', 'telegram'
}

WINDOWS = {
    '1h': 1,
    '3h': 3,
    '6h': 6,
    '12h': 12,
    '24h': 24,
    '48h': 48,
    '7d': 168
}

def load_news():
    all_news = []
    # Search in parent directories if not found in current (handling various CWDs)
    search_paths = [
        '.', 
        '..', 
        '../..',
        os.path.join(os.path.dirname(__file__), '..') # backend/..
    ]
    
    for fname in NEWS_FILES:
        found = False
        for path in search_paths:
            fpath = os.path.join(path, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_news.extend(data)
                        found = True
                        break
                except Exception as e:
                    print(f"Error loading {fname}: {e}")
        if not found:
            print(f"Warning: {fname} not found in search paths.")
            
    return all_news

def clean_text(text):
    if not text: return ""
    # Remove URLs
    text = re.sub(r'https?://\S+', '', text)
    # Normalize ZWNJ
    text = text.replace('\u200c', ' ')
    # Normalize characters
    text = text.replace('ي', 'ی').replace('ك', 'ک')
    # Keep only Persian & Numbers (Simple regex)
    # text = re.sub(r'[^\w\s]', '', text) 
    return text

def tokenize(text):
    clean = clean_text(text)
    # Simple whitespace split for now, or regex for persian words
    tokens = re.findall(r'[آ-ی0-9]+', clean)
    return [t for t in tokens if len(t) > 2 and t not in STOP_WORDS]

def calculate_baselines():
    news_items = load_news()
    print(f"Loaded {len(news_items)} news items.")
    
    now = datetime.now()
    # If news items have 'date' string (e.g. "2024-05-20T10:00:00"), parse it.
    # Assuming standard format or attempting parse. 
    # Vahid news usually has 'date' field.
    
    # Store tokens with their age in hours
    token_ages = []
    
    for item in news_items:
        date_str = item.get('date') or item.get('time')
        if not date_str: continue
        
        try:
            # Attempt partial ISO parsing (YYYY-MM-DD...)
            # Taking first 19 chars: "2024-05-20T10:00:00"
            clean_date = date_str[:19].replace('T', ' ')
            item_dt = datetime.strptime(clean_date, '%Y-%m-%d %H:%M:%S')
            
            diff = now - item_dt
            age_hours = diff.total_seconds() / 3600
            
            if age_hours < 0: continue # Future news?
            if age_hours > 200: continue # Ignore very old news (>8 days) to save time
            
            tokens = tokenize(item.get('text', ''))
            for t in tokens:
                token_ages.append((t, age_hours))
                
        except Exception:
            continue
            
    # Calculate frequencies per window
    # We want average frequency PER HOUR for that window? 
    # Or just total count in that window?
    # User wants to know "Is this current 1h count normal compared to 24h average?"
    # Let's simple count occurrences in each window.
    
    window_counts = defaultdict(lambda: defaultdict(int))
    
    for token, age in token_ages:
        for w_name, w_hours in WINDOWS.items():
            if age <= w_hours:
                window_counts[token][w_name] += 1
                
    # Calculate Baseline Rates (Mentions Per Hour)
    # We use 7d and 24h as the main baselines
    
    output = {
        "updated_at": now.isoformat(),
        "baselines": {}
    }
    
    # Filter: Token must have at least 5 mentions in 7 days to be tracked (noise reduction)
    relevant_tokens = {t for t, counts in window_counts.items() if counts['7d'] >= 5}
    
    for t in relevant_tokens:
        c7d = window_counts[t]['7d']
        c24h = window_counts[t]['24h']
        
        # Rate = Count / Hours
        # We add a small epsilon to rate to avoid division by zero later
        rate_7d = c7d / 168.0
        rate_24h = c24h / 24.0
        
        output['baselines'][t] = {
            "rate_7d": round(rate_7d, 4),
            "rate_24h": round(rate_24h, 4),
            "raw_7d": c7d
        }
        
    # Ensure raw output dir
    os.makedirs(DATA_DIR, exist_ok=True)
    
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        
    print(f"Saved baselines for {len(output['baselines'])} words to {HISTORY_FILE}")

if __name__ == "__main__":
    calculate_baselines()
