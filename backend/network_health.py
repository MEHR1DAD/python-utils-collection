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
            
            # 1. Identify what this message contains
            found_incidents = []
            found_locations = []
            
            if isinstance(config_patterns, dict):
                for inc in config_patterns.get('incidents', []):
                    if inc in text: found_incidents.append(inc)
                for loc in config_patterns.get('locations', []):
                    if loc in text: found_locations.append(loc)
                for sta in config_patterns.get('status', []):
                    if sta in text: found_incidents.append(sta) # Treat status as incident
            else:
                 # Fallback for old list config check
                 pass

            # 2. Record simple history for ALL detected keywords
            for p in found_incidents + found_locations:
                if p not in history: history[p] = []
                history[p].append(now)
                current_batch_counts[p] = current_batch_counts.get(p, 0) + 1

            # 3. Record Context (Location <-> Incident Link)
            for loc in found_locations:
                if loc not in location_context: location_context[loc] = {}
                for inc in found_incidents:
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
        
        # --- Spike Detection & Smart Alerting ---
        
        # We need to detect spikes in BOTH Locations and Incidents.
        # But an alert should ideally be a combination.
        
        # 1. Detect ALL Spiking Keywords (SMART ANALYSIS)
        spiking_keywords = set()
        keyword_stats = {} # {word: {count, ratio}}
        
        # Load Baselines
        baselines = self.load_baselines()
        
        # Analyze last 15 minutes of data for spikes
        short_window = 15 * 60
        
        for pattern, timestamps in clean_history.items():
             recent_count = len([t for t in timestamps if t > (now - short_window)])
             
             if recent_count < 2: continue # Ignore noise
             
             # Calculate Current Rate (Mentions per hour)
             # recent_count is in 15 mins (0.25h) -> Rate = count * 4
             current_rate = recent_count * 4.0
             
             # Get Historical Baseline Rate
             baseline_data = baselines.get(pattern)
             historical_rate = 0.5 # Default fallback
             
             if baseline_data:
                 # Use 7d rate for stability
                 historical_rate = baseline_data.get('rate_7d', 0.5)
                 if historical_rate < 0.1: historical_rate = 0.1
             
             # Calculate SPIKE RATIO (Z-Score approximation)
             ratio = current_rate / historical_rate
             
             # Thresholds:
             # - Spike Ratio > 5.0 (500% increase!)
             # - Absolute Minimum: 3 mentions (to avoid 1->5 fake spikes)
             
             if ratio > 5.0 and recent_count >= 3:
                  spiking_keywords.add(pattern)
                  keyword_stats[pattern] = {'count': recent_count, 'ratio': ratio}


        # 2. Construct Smart Alerts
        # Priority: Location + Incident Co-occurrence
        
        sent_patterns = set()
        
        # A. Check Spiking Locations for Associated Incidents
        for loc in (set(config_patterns.get('locations', [])) & spiking_keywords):
            # This location is spiking. Why?
            # Check context from current batch first
            context = location_context.get(loc, {})
            
            # Find the most frequent incident associated with this location in this batch
            top_incident = None
            max_assoc = 0
            
            for inc, count in context.items():
                if count > max_assoc:
                    max_assoc = count
                    top_incident = inc
            
            # If we found a strong link in this batch
            # START FIX: Require significant correlation
            # We don't want a single "UAV" mention in a flood of "Tehran" news to label the whole event "UAV in Tehran".
            
            is_strong_link = False
            if top_incident:
                 # 1. Absolute minimum co-occurrences (at least 2 messages must link them)
                 if max_assoc >= 2:
                     is_strong_link = True
                 # 2. Or if the batch is small, it must be the dominant topic (> 30%)
                 elif max_assoc >= 1 and (max_assoc / len(messages)) > 0.3:
                      is_strong_link = True
            
            if is_strong_link:
                alert_title = f"{top_incident} در {loc}"
                alerts.append({
                    'pattern': alert_title,
                    'count': keyword_stats[loc]['count'],
                    'keywords': [loc, top_incident]
                })
                sent_patterns.add(loc)
                sent_patterns.add(top_incident)
            else:
                # Location is spiking but no strong incident link
                alerts.append({
                    'pattern': f"رویداد مهم در {loc}",
                    'count': keyword_stats[loc]['count'],
                    'keywords': [loc]
                })
                sent_patterns.add(loc)

        # B. Check Spiking Incidents (that weren't already covered by Location alerts)
        for inc in (set(config_patterns.get('incidents', []) + config_patterns.get('status', [])) & spiking_keywords):
            if inc in sent_patterns: continue
            
            # Is this incident associated with any spiking location we missed?
            # We already checked locations. So this must be a general incident (e.g. "Earthquake" everywhere).
            
            alerts.append({
                'pattern': inc, # Just "Explosion"
                'count': keyword_stats[inc]['count'],
                'keywords': [inc]
            })
            sent_patterns.add(inc)

        return alerts, messages

    def report_status(self, alerts, all_messages):
        """Send Telegram Alert."""
        if not alerts or not BOT_TOKEN or not CHAT_ID:
            if alerts: print(f"ALER! But no token. {alerts}")
            return

        for alert in alerts:
            title = alert['pattern']
            keywords = alert.get('keywords', [])
            
            # Find sample messages containing AT LEAST ONE of the keywords
            relevant_msgs = []
            seen_ids = set()
            
            for msg in all_messages:
                # If alert has multiple keywords (Explosion, Tehran), favor messages having ALL
                # Otherwise, messages having ANY.
                
                text = msg['text']
                matches = [k for k in keywords if k in text]
                
                if matches and msg['id'] not in seen_ids:
                    # Score match quality? 
                    # For now just grab first 3
                    relevant_msgs.append(msg)
                    seen_ids.add(msg['id'])
                    if len(relevant_msgs) >= 3: break
            
            links = "\n".join([f"- [{m['node']}]({m['link']})" for m in relevant_msgs])
            
            text = (
                f"🚨 **{title}**\n\n"
                f"Intensity: {alert['count']} (Spike Detected)\n\n"
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
            time.sleep(1) # Polite delay
            
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
        self.export_metrics(alerts if 'alerts' in locals() else [])

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

    def export_metrics(self, current_alerts):
        """Export public-facing metrics for frontend."""
        # Load latest baselines (in case they updated)
        baselines = self.load_baselines()
        
        # 1. Top Trends (Smart Analysis)
        trends = []
        now = time.time()
        
        # We look at the last 3 hours for "Current Activity" to capture developing trends
        # But we weight 1h more heavily? Let's stick to 1h for "Hot" trends.
        window_1h = 3600
        min_time_1h = now - window_1h
        
        history_data = self.state.get('history', {})
        
        for pattern, timestamps in history_data.items():
            # Count recent mentions (Last 1 Hour)
            count_1h = len([t for t in timestamps if t > min_time_1h])
            
            if count_1h < 2: continue # Ignore one-offs
            
            # Get Baseline Rate (Mentions per hour over last 24h/7d)
            baseline = baselines.get(pattern)
            
            score = 0
            if baseline:
                # Use 7d rate as primary stable baseline, fallback to 24h
                rate = baseline.get('rate_7d') or baseline.get('rate_24h', 0.5)
                # Avoid division by zero
                if rate < 0.1: rate = 0.1 
                
                # SCORE = Current Rate (count/1h) / Historical Rate
                score = count_1h / rate
            else:
                # New word (not in history)?
                # If it's new and frequent -> High Score
                score = count_1h * 2 # Artificial boost for novelty
                
            # Filter: meaningful trends only
            # If Score > 2.0 (Twice as frequent as normal) -> TRENDING
            if score > 2.0:
                 trends.append({
                     "text": pattern, 
                     "count": count_1h, 
                     "score": round(score, 2),
                     "is_new": not bool(baseline)
                 })
        
        # Sort by SCORE (Relative Burstiness) not raw count
        trends.sort(key=lambda x: x['score'], reverse=True)
        
        # --- Sentiment Analysis (New Phase 13) ---
        # Only analyze top 5 trends to save API calls
        top_5_trends = trends[:5]
        try:
            from ai_service import analyze_sentiment_batch
            trend_texts = [t['text'] for t in top_5_trends]
            if trend_texts:
                sentiments = analyze_sentiment_batch(trend_texts)
                for t in top_5_trends:
                    t['sentiment'] = sentiments.get(t['text'], 'neutral')
        except ImportError:
            print("AI Service not found or dependencies missing.")
        except Exception as e:
            print(f"Sentiment Analysis failed: {e}")
        
        # 2. Active Incidents (Alerts)
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
            "top_nodes": top_5_trends + trends[5:20], # Top 5 have sentiment, rest don't
            "active_incidents": incidents
        }
        
        metrics_file = os.path.join(LOG_DIR, 'system_metrics.json')
        with open(metrics_file, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    monitor = NetworkMonitor()
    monitor.run()
