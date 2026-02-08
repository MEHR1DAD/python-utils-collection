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
        """Check for spike patterns."""
        alerts = []
        now = time.time()
        
        # Update history with new occurrences
        # self.state['history'] structure: { "keyword": [timestamp1, timestamp2...] }
        # We need to clean up old history (> 24h)
        
        clean_history = {}
        window_24h = 24 * 3600
        min_time = now - window_24h
        
        # Load existing history to memory dict for easier processing
        history = self.state.get('history', {})
        
        current_batch_counts = {} # Whats happening RIGHT NOW (in these new messages)
        
        for msg in messages:
            text = msg['text']
            # Normalize complex Persian chars
            text = text.replace('ي', 'ی').replace('ك', 'ک')
            
            # Check patterns
            found_patterns = []
            for pattern in self.config['patterns']:
                if pattern in text:
                    found_patterns.append(pattern)
                    
                    # Record occurrence
                    if pattern not in history: history[pattern] = []
                    history[pattern].append(now)
                    
                    current_batch_counts[pattern] = current_batch_counts.get(pattern, 0) + 1
            
            if len(found_patterns) >= 2:
                # Multiple keywords in one message? High priority signal
                pass

        # Cleanup History & Calculate Baselines
        alerts_to_send = []
        
        for pattern, timestamps in history.items():
            # Keep only recent
            valid_timestamps = [t for t in timestamps if t > min_time]
            if not valid_timestamps: continue
            
            clean_history[pattern] = valid_timestamps
            
            # Spike Detection Logic
            # 1. Calculate Baseline (Rate per 5 mins over last 24h)
            # Total count / (24*12) slots? 
            # Better: Average count in 5-min windows? 
            # Simple Baseline: Total count / 288 (5-min slots in 24h)
            
            total_count = len(valid_timestamps)
            baseline_rate = max(total_count / 288, 0.5) # Minimum baseline 0.5 to avoid noise
            
            # 2. Calculate Current Rate (Last 10 mins)
            short_window = 10 * 60
            recent_count = len([t for t in valid_timestamps if t > (now - short_window)])
            
            # Thresholds
            spike_threshold = self.config['thresholds']['latency_spike'] # e.g. 5x
            absolute_threshold = self.config['thresholds']['packet_loss'] # e.g. 3 (renamed from packet_loss)
            
            # Check Spike
            # If recent_count is high AND much higher than baseline
            if recent_count >= absolute_threshold and (recent_count / 2) > (baseline_rate * spike_threshold):
                # DIV by 2 because recent_count is 10 mins, baseline is 5 mins rate? 
                # Let's normalize. 
                # Current Rate (per 5 min) = recent_count / 2
                
                current_rate = recent_count / 2.0
                ratio = current_rate / baseline_rate
                
                alerts_to_send.append({
                    'pattern': pattern,
                    'count': recent_count,
                    'ratio': ratio,
                    'baseline': baseline_rate
                })

        self.state['history'] = clean_history
        return alerts_to_send, messages

    def report_status(self, alerts, all_messages):
        """Send Telegram Alert."""
        if not alerts or not BOT_TOKEN or not CHAT_ID:
            if alerts: print(f"ALER! But no token. {alerts}")
            return

        # dedupe alerts
        patterns = [a['pattern'] for a in alerts]
        patterns_str = " | ".join(patterns)
        
        # Find relevant messages
        relevant_msgs = []
        seen_ids = set()
        for msg in all_messages:
            for p in patterns:
                if p in msg['text'] and msg['id'] not in seen_ids:
                    relevant_msgs.append(msg)
                    seen_ids.add(msg['id'])
                    if len(relevant_msgs) >= 3: break
            if len(relevant_msgs) >= 3: break
            
        links = "\n".join([f"- [{m['node']}]({m['link']})" for m in relevant_msgs])
        
        text = (
            f"🚨 **Network Anomaly Detected**\n\n"
            f"Patterns: {patterns_str}\n"
            f"Intensity: {len(relevant_msgs)} occurrences (Spike Detected)\n\n"
            f"Sources:\n{links}\n\n"
            f"#SystemHealth #Monitor"
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

    def export_metrics(self, current_alerts):
        """Export public-facing metrics for frontend."""
        # 1. Top Trends (from history)
        trends = []
        now = time.time()
        min_time = now - (24 * 3600)
        
        for pattern, timestamps in self.state.get('history', {}).items():
            count = len([t for t in timestamps if t > min_time])
            if count > 0:
                trends.append({"text": pattern, "count": count})
        
        trends.sort(key=lambda x: x['count'], reverse=True)
        
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
            "top_nodes": trends[:20], # Top 20 trends
            "active_incidents": incidents
        }
        
        metrics_file = os.path.join(LOG_DIR, 'system_metrics.json')
        with open(metrics_file, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    monitor = NetworkMonitor()
    monitor.run()
