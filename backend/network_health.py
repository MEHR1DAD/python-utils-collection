import os
import json
import time
import requests
import re
from datetime import datetime, timedelta
from collections import deque
import statistics

# --- Stealth Configuration ---
CONFIG_FILE = 'backend/net_config.json'
STATE_FILE = 'backend/data/net_status.json'
# "Log" file is actually the state persistence
LOG_DIR = 'backend/data'

# --- Telegram Alert Config (Environment Variables) ---
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

class NetworkMonitor:
    def __init__(self):
        self.config = self.load_config()
        self.state = self.load_state()
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': self.config['settings']['user_agent']})
        
        # In-memory "metrics" (Word Counts)
        # Structure: { word: [timestamp1, timestamp2, ...] }
        self.metrics = {} 
        self.seen_hashes = deque(maxlen=2000) 
        self.seen_hashes = deque(maxlen=2000) 

    def load_config(self):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_state(self):
        if not os.path.exists(STATE_FILE):
            return {"last_checked": {}, "history": {}}
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {"last_checked": {}, "history": {}}

    def save_state(self):
        if not os.path.exists(LOG_DIR):
            os.makedirs(LOG_DIR)
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def fetch_node_logs(self, node):
        """Scrape latest messages from public channel view."""
        url = f"https://t.me/s/{node}"
        try:
            response = self.session.get(url, timeout=self.config['settings']['timeout'])
            if response.status_code != 200:
                print(f"Node {node} unreachable: {response.status_code}")
                return []
            return self.parse_logs(response.text, node)
        except Exception as e:
            print(f"Connection error to {node}: {e}")
            return []

    def parse_logs(self, html, node):
        """Parse HTML to extract text and IDs."""
        messages = []
        # Regex to find message divs in Telegram Web view
        # <div class="tgme_widget_message_text" dir="auto">...</div>
        # Also need data-post="ChannelName/123" to track IDs
        
        # Simple regex approach (can be improved with BeautifulSoup if available, but staying dependency-light)
        # Using regex for speed and fewer dependencies in this stealth script
        
        # Find message blocks
        # Pattern: data-post="Channel/123" ... <div class="tgme_widget_message_text" ...>(.*?)</div>
        
        pattern = r'data-post="([^"]+)"'
        posts = re.finditer(pattern, html)
        
        # We need to associate text with ID. 
        # Actually, let's look for the container `tgme_widget_message_wrap`
        # Because regex parsing HTML is fragile, let's try a split strategy or simpler block search.
        
        # Robust enough strategy for t.me/s/:
        # Each message is in <div class="tgme_widget_message_wrap js-widget_message_wrap">
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        wraps = soup.find_all('div', class_='tgme_widget_message_wrap')
        
        last_id = self.state['last_checked'].get(node, 0)
        new_last_id = last_id
        
        for wrap in wraps:
            msg_div = wrap.find('div', class_='tgme_widget_message')
            if not msg_div: continue
            
            data_post = msg_div.get('data-post') # e.g. "VahidOnline/12345"
            if not data_post: continue
            
            try:
                msg_id = int(data_post.split('/')[-1])
            except:
                continue
                
            if msg_id <= last_id:
                continue
                
            new_last_id = max(new_last_id, msg_id)
            
            text_div = wrap.find('div', class_='tgme_widget_message_text')
            if text_div:
                # Get raw text, replace breaks with space
                text = text_div.get_text(separator=' ').strip()
                # Clean hidden link text often found in tg web view? usually ok.
                messages.append({'id': msg_id, 'text': text, 'node': node, 'link': f"https://t.me/{data_post}"})
        
        self.state['last_checked'][node] = new_last_id
        return messages

    
    def analyze_traffic(self, messages):
        """Check for spike patterns with Context Awareness."""
        alerts = []
        now = time.time()
        
        # Helper to flatten patterns for general tracking
        config_patterns = self.config['patterns']
        all_patterns = []
        if isinstance(config_patterns, list):
             all_patterns = config_patterns
        else:
             all_patterns = (
                 config_patterns.get('incidents', []) + 
                 config_patterns.get('locations', []) + 
                 config_patterns.get('status', [])
             )
        
        # --- Update History ---
        history = self.state.get('history', {})
        
        # Track co-occurrences for this batch to build alert context
        # Structure: { "Tehran": {"Explosion": 2, "Fire": 1} }
        location_context = {} 
        
        current_batch_counts = {}

        for msg in messages:
            text = msg['text']
            # Normalize
            text = text.replace('ي', 'ی').replace('ك', 'ک')
            
            # Deduplication: Calculate simple hash of normalized text
            import hashlib
            norm_text = re.sub(r'[^\w\s]', '', text).strip()
            msg_hash = hashlib.md5(norm_text.encode('utf-8')).hexdigest()
            
            # Check if this exact message content was seen recently (in this batch or globally?)
            # Ideally globally, but state persistence is tricky. 
            # Let's check within the batch + a small LRU cache in memory.
            # For now, let's assume if it's in the same batch from different nodes, it's duplication.
            
            if msg_hash in self.seen_hashes:
                continue # Duplicate content - ignore for spike counting
            
            self.seen_hashes.append(msg_hash) # Add to LRU
            
            # 1. Identify what this message contains
            found_incidents = []
            found_locations = []
            
            if isinstance(config_patterns, dict):
                for inc in config_patterns.get('incidents', []):
                    if inc in text: found_incidents.append(inc)
                for loc in config_patterns.get('locations', []):
                    if loc in text: found_locations.append(loc)
                for sta in config_patterns.get('status', []):
                    if sta in text: found_incidents.append(sta) 
            else:
                 pass

            # 2. Record simple history for ALL detected keywords
            for p in found_incidents + found_locations:
                if p not in history: history[p] = []
                history[p].append(now)
                current_batch_counts[p] = current_batch_counts.get(p, 0) + 1

            # 3. Record Context (Location <-> Incident Link) AND Composite History
            for loc in found_locations:
                for inc in found_incidents:
                    composite = f"{inc} در {loc}"
                    if composite not in history: history[composite] = []
                    history[composite].append(now)
                    current_batch_counts[composite] = current_batch_counts.get(composite, 0) + 1
                    
                    if loc not in location_context: location_context[loc] = {}
                    location_context[loc][inc] = location_context[loc].get(inc, 0) + 1
        
        # --- Cleanup Old History ---
        clean_history = {}
        window_24h = 24 * 3600
        min_time = now - window_24h
        
        for p, timestamps in history.items():
            valid = [t for t in timestamps if t > min_time]
            if valid:
                clean_history[p] = valid
        
        self.state['history'] = clean_history
        
        # --- Spike Detection & Smart Alerting (STRICT MODE) ---
        spiking_keywords = set()
        keyword_stats = {} 
        baselines = self.load_baselines()
        short_window = 15 * 60 # 15 mins
        
        # 1. Detect Spikes (including Composites)
        for pattern, timestamps in clean_history.items():
             recent_count = len([t for t in timestamps if t > (now - short_window)])
             
             if recent_count < 2: continue 
             
             current_rate = recent_count * 4.0
             baseline_data = baselines.get(pattern)
             historical_rate = 0.5
             if baseline_data:
                 historical_rate = baseline_data.get('rate_7d') or baseline.get('rate_24h', 0.5)
                 if historical_rate < 0.1: historical_rate = 0.1
             
             ratio = current_rate / historical_rate
             
             # User Rule: "Must be repeated... in 50% of messages"
             # Ratio Check: Is it BURSTING? (> 5x normal)
             if ratio > 5.0 and recent_count >= 3:
                  spiking_keywords.add(pattern)
                  keyword_stats[pattern] = {'count': recent_count, 'ratio': ratio}

        # 2. Filter & Alert
        sent_patterns = set()
        sorted_spikes = sorted(spiking_keywords, key=lambda k: keyword_stats[k]['count'], reverse=True)
        
        for spk in sorted_spikes:
            if spk in sent_patterns: continue
            
            if " در " in spk:
                # Composite Pattern ("Incident in Location")
                parts = spk.split(" در ")
                inc_part = parts[0]
                loc_part = parts[1]
                
                # STRICT DENSITY CHECK (User Requirement: 50%)
                # Count total messages in this batch (or recent 15m) related to LOCATION?
                # No, check proportion of THIS PATTERN in recent traffic?
                # The user said: "50% of news... in period... must be related".
                # Let's interpret: If there are 10 messages in the last 15m about "Tehran", 5 must be "Flood in Tehran".
                # Or simply: The pattern itself must have a high count?
                # Let's verify against TOTAL traffic? No, total traffic is huge.
                # Let's verify against LOCATION traffic.
                
                loc_history = clean_history.get(loc_part, [])
                loc_recent_count = len([t for t in loc_history if t > (now - short_window)])
                
                this_pattern_count = keyword_stats[spk]['count']
                
                density = 0
                if loc_recent_count > 0:
                    density = this_pattern_count / loc_recent_count
                
                # User Rule: > 50%
                if density >= 0.5:
                    alerts.append({
                        'pattern': spk,
                        'count': this_pattern_count,
                        'keywords': [inc_part, loc_part]
                    })
                    sent_patterns.add(spk)
                    sent_patterns.add(inc_part)
                    sent_patterns.add(loc_part)
            
            else:
                # Naked Keyword
                is_status = spk in config_patterns.get('status', [])
                if is_status:
                    alerts.append({
                        'pattern': spk,
                        'count': keyword_stats[spk]['count'],
                        'keywords': [spk]
                    })
                    sent_patterns.add(spk)

        return alerts, messages

    def report_status(self, alerts, all_messages):
        """Send Telegram Alert."""
        if not alerts or not BOT_TOKEN or not CHAT_ID:
            return

        for alert in alerts:
            title = alert['pattern']
            keywords = alert.get('keywords', [])
            
            # Find sample messages
            relevant_msgs = []
            seen_ids = set()
            
            # Link Deduplication (Same News logic)
            # We want diverse sources if possible
            
            for msg in all_messages:
                text = msg['text']
                # For composite alerts, require BOTH keywords in the text
                if len(keywords) > 1:
                     if all(k in text for k in keywords):
                         if msg['id'] not in seen_ids:
                             relevant_msgs.append(msg)
                             seen_ids.add(msg['id'])
                else:
                     # Status or Single
                     if keywords[0] in text:
                         if msg['id'] not in seen_ids:
                             relevant_msgs.append(msg)
                             seen_ids.add(msg['id'])
                
                if len(relevant_msgs) >= 5: break
            
            if not relevant_msgs: continue

            links = "\n".join([f"- [{m['node']}]({m['link']})" for m in relevant_msgs])
            
            text = (
                f"🚨 **{title}**\n\n"
                f"Intensity: {alert['count']} (Spike Detected)\n"
                f"Density: {len(relevant_msgs)} confirmed sources\n\n"
                f"Sources:\n{links}\n\n"
                f"#NetworkAlert #StealthMonitor"
            )
            
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            try:
                requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'Markdown', 'disable_web_page_preview': True})
            except Exception as e:
                print(f"Failed to send alert: {e}")

    def run(self):
        print("Starting network diagnostic...")
        all_messages = []
        for node in self.config['nodes']:
            msgs = self.fetch_node_logs(node)
            all_messages.extend(msgs)
            time.sleep(1) 
            
        if all_messages:
            alerts, _ = self.analyze_traffic(all_messages)
            if alerts:
                print(f"Detected anomalies: {len(alerts)}")
                self.report_status(alerts, all_messages)
            else:
                print("System normal.")
        else:
            print("No new packets.")
            
        self.save_state()
        self.export_metrics(alerts if 'alerts' in locals() else [], all_messages)

    def load_baselines(self):
        history_file = 'backend/data/trend_history.json'
        if not os.path.exists(history_file):
            return {}
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('baselines', {})
        except:
            return {}

    def export_metrics(self, current_alerts, all_messages=[]):
        """Export public-facing metrics for frontend."""
        baselines = self.load_baselines()
        trends = []
        now = time.time()
        window_1h = 3600
        min_time_1h = now - window_1h
        
        history_data = self.state.get('history', {})
        
        for pattern, timestamps in history_data.items():
            count_1h = len([t for t in timestamps if t > min_time_1h])
            if count_1h < 2: continue
            
            baseline = baselines.get(pattern)
            score = 0
            if baseline:
                rate = baseline.get('rate_7d') or baseline.get('rate_24h', 0.5)
                if rate < 0.1: rate = 0.1 
                score = count_1h / rate
            else:
                score = count_1h * 2 
                
            if score > 2.0:
                 trends.append({
                     "text": pattern, 
                     "count": count_1h, 
                     "score": round(score, 2),
                     "is_new": not bool(baseline)
                 })
        
        trends.sort(key=lambda x: x['score'], reverse=True)
        
        # Deduplication for Frontend Cloud
        # If "Flood in Tehran" exists, remove "Flood" and "Tehran" from the list
        final_trends = []
        composite_keys = set()
        
        # First pass: Identify Composites
        for t in trends:
            if " در " in t['text']:
                composite_keys.add(t['text'])
                final_trends.append(t)
        
        # Second pass: Add Singles ONLY if not part of a Composite
        for t in trends:
            if " در " in t['text']: continue # Already added
            
            is_constituent = False
            for composite in composite_keys:
                if t['text'] in composite: # e.g. "Flood" in "Flood in Tehran"
                    is_constituent = True
                    break
            
            if not is_constituent:
                final_trends.append(t)
                
        # Re-sort final list
        final_trends.sort(key=lambda x: x['score'], reverse=True)
        
        # Sentiment Analysis & Vibe Index
        top_5_trends = final_trends[:5]
        vibe_index = 50 # Default neutral
        
        try:
            from ai_service import analyze_sentiment_batch
            
            # 1. Analyze Top Trends
            trend_texts = [t['text'] for t in top_5_trends]
            if trend_texts:
                sentiments = analyze_sentiment_batch(trend_texts)
                for t in top_5_trends:
                    t['sentiment'] = sentiments.get(t['text'], 'neutral')

            # 2. Calculate Global Vibe Index (from latest news)
            if all_messages:
                # Sample 40 items to avoid overwhelming the AI but get a good range
                news_sample = [m['text'] for m in all_messages[-40:]]
                news_sentiments = analyze_sentiment_batch(news_sample)
                
                pos = list(news_sentiments.values()).count('positive')
                neg = list(news_sentiments.values()).count('negative')
                total = len(news_sentiments)
                
                if total > 0:
                    # Score 0-100. (pos-neg)/total yields -1 to 1. 
                    # Map to 0-100: 50 + (pos-neg)/total * 50
                    vibe_index = int(50 + ((pos - neg) / total) * 50)
                    vibe_index = max(0, min(100, vibe_index))
        except Exception as e:
            print(f"Index Calculation Failed: {e}")
        
        incidents = []
        for alert in current_alerts:
             incidents.append({
                 "type": "spike",
                 "pattern": alert['pattern'],
                 "intensity": alert['count'],
                 "time": int(now)
             })
             
        metrics = {
            "generated_at": int(now),
            "vibe_index": vibe_index,
            "top_nodes": top_5_trends + final_trends[5:20],
            "active_incidents": incidents
        }
        
        with open(os.path.join(LOG_DIR, 'system_metrics.json'), 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    monitor = NetworkMonitor()
    monitor.run()
