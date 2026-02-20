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

    def fetch_node_logs(self, source_url):
        """Fetch latest messages from JSON data sources."""
        try:
            response = self.session.get(source_url, timeout=self.config['settings']['timeout'])
            if response.status_code != 200:
                print(f"Source {source_url} unreachable: {response.status_code}")
                return []
            
            data = response.json()
            messages = []
            # Parse JSON format (NewsItem[])
            for item in data:
                # Convert to internal format
                messages.append({
                    'id': item.get('id', 0),
                    'text': item.get('text', ''),
                    'node': item.get('source', 'Unknown'),
                    'link': item.get('url', '#'),
                    'date': item.get('date', '')
                })
            return messages
        except Exception as e:
            print(f"Connection error to {source_url}: {e}")
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

    def is_old_news(self, text):
        """
        Check if the text mentions old dates (previous months or years).
        Returns True if it's likely a memorial or repost of old news.
        """
        months_fa = [
            "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", 
            "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"
        ]
        
        # Normalized current Persian month (Bahman = 11, index 10)
        # Mid-February 2026 is mid-Bahman 1404
        # We can get this dynamically or hardcode for 1404 context
        current_month_idx = 10 
        
        # Ignore mentions of previous years (139x or 1400-1403)
        if re.search(r'(۱۳۹\d|۱۴۰[۰-۳])', text):
            return True
            
        # Check for month names
        for i, month in enumerate(months_fa):
            if month in text:
                # If explicit mention of previous months in same year
                if i < current_month_idx:
                    # User mentions "Dey" or "Azar" etc. 
                    # We should allow if it's just "yesterday" but Dey is 30 days ago.
                    return True
                    
        return False

    def match_pattern(self, text, pattern):
        """
        Check if pattern exists in text as a whole word (regex boundary).
        Handles Persian/Arabic word boundaries correctly.
        """
        try:
            # Escape the pattern to handle special chars like + or .
            esc_pattern = re.escape(pattern)
            # Use \b for boundaries. In Python re.UNICODE, this works for Persian.
            # We use a raw string for the regex.
            if re.search(r'\b' + esc_pattern + r'\b', text):
                return True
        except:
            # Fallback to simple inclusion if regex fails (unlikely)
            if pattern in text:
                return True
        return False

    
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
            
            # Temporal Filter: Block old news/memorials from triggering alerts
            if self.is_old_news(text):
                continue

            self.seen_hashes.append(msg_hash) # Add to LRU
            
            # 1. Identify what this message contains
            found_incidents = []
            found_locations = []
            
            if isinstance(config_patterns, dict):
                for inc in config_patterns.get('incidents', []):
                    if self.match_pattern(text, inc): found_incidents.append(inc)
                for loc in config_patterns.get('locations', []):
                    if self.match_pattern(text, loc): found_locations.append(loc)
                for sta in config_patterns.get('status', []):
                    if self.match_pattern(text, sta): found_incidents.append(sta) 
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

    def jaccard_similarity(self, s1, s2):
        """Calculate Jaccard Similarity between two strings."""
        set1 = set(s1.split())
        set2 = set(s2.split())
        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))
        return intersection / union if union > 0 else 0

    def report_status(self, alerts, all_messages):
        """Send Telegram Alert with Anti-Spam Check."""
        if not alerts or not BOT_TOKEN or not CHAT_ID:
            return

        for alert in alerts:
            title = alert['pattern']
            keywords = alert.get('keywords', [])
            
            # Find sample messages & Deduplicate Content
            relevant_msgs = []
            seen_ids = set()
            unique_contents = [] # List of unique message texts
            
            for msg in all_messages:
                text = msg['text']
                is_match = False
                
                # Check Match
                if len(keywords) > 1:
                     if all(self.match_pattern(text, k) for k in keywords):
                         is_match = True
                else:
                     if self.match_pattern(text, keywords[0]):
                         is_match = True
                
                if is_match and msg['id'] not in seen_ids:
                    # Content Similarity Check
                    is_duplicate = False
                    for existing_text in unique_contents:
                        if self.jaccard_similarity(text, existing_text) > 0.7:
                            is_duplicate = True
                            break
                    
                    if not is_duplicate:
                        unique_contents.append(text)
                        relevant_msgs.append(msg)
                        seen_ids.add(msg['id'])
                
                if len(relevant_msgs) >= 5: break
            
            # Anti-Spam Rule: Require at least 2 UNIQUE perspectives
            if len(unique_contents) < 2:
                print(f"Skipping alert '{title}': Only {len(unique_contents)} unique source(s) found (likely syndication).")
                continue # Skip this alert

            links = "\n".join([f"- [{m['node']}]({m['link']})" for m in relevant_msgs])
            
            text = (
                f"🚨 **{title}**\n\n"
                f"Intensity: {alert['count']} (Spike Detected)\n"
                f"Density: {len(relevant_msgs)} unique sources\n\n"
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
        
        # 0. Inject Urgent Alerts as High-Priority Trends
        for alert in current_alerts:
            # Check if this alert is already in trends? Unlikely to have exact same text.
            # We add it with a SUPER high score to ensure it survives deduplication and stays on top.
            trends.append({
                "text": alert['pattern'],
                "count": alert['count'],
                "score": 500 + alert['count'], # Priority Boost
                "is_new": True,
                "is_alert": True # Marker for frontend if needed
            })

        # 0.5 Define Aliases & Blocklist
        # 0.5 Define Aliases & Blocklist
        ALIAS_MAP = {
            # Target: Reza Pahlavi
            "شاهزاده رضا": "رضا پهلوی",
            "شاهزاده": "رضا پهلوی",
            "پرنس رضا": "رضا پهلوی",
            "شاهزاده رضا پهلوی": "رضا پهلوی",
            "رضا پهلوی": "رضا پهلوی", # Self-map
            
            # Target: Munich Security Conference
            "کنفرانس امنیتی": "کنفرانس امنیتی مونیخ", # Force context if found alone? Or map to full name? 
            # Actually, user wants "Context". Mapping "Konferans Amniyti" -> "Konferans Amniyti Munich" might be risky if it's NOT Munich.
            # But right now, it IS Munich. So hardcoding the alias is a safe bet for this week.
            "مونیخ": "کنفرانس امنیتی مونیخ"
        }
        
        # Block generic terms that aren't trends on their own (Unigrams)
        # By blocking these, we allow Bigrams (e.g. "Tajamo Tehran") to survive if they exist.
        BLOCKLIST = {
            "مردم ایران", 
            "ناو هواپیمابر",
            "ایران", 
            "جمهوری اسلامی",
            "تجمع", # Generic - requires location
            "اعتراضات", # Generic
            "تظاهرات", # Generic
            "ایالات متحده", # User requested: Too generic/constant
            "دونالد ترامپ", # User requested: Too generic/constant
            "آمریکا",
            "ترامپ",
            "اسرائیل",
            "ارسال",
            "فیلم",
            "ویدیو"
        }

        # 1. Sort by Score (Descending) - Critical for the logic below
        trends.sort(key=lambda x: x['score'], reverse=True)
        
        # 2. Smart Deduplication & Aliasing
        # Logic: 
        # - Apply Aliasing (Merge counts/scores)
        # - Filter Blocklist
        # - Deduplicate Substrings
        
        final_trends = []
        processed_aliases = set()
        
        # First Pass: Handle Aliasing & Blocking
        aliased_trends = {}
        
        for t in trends:
            text = t['text']
            
            # 1. Blocking
            if text in BLOCKLIST: continue
            
            # 2. Aliasing
            if text in ALIAS_MAP:
                target = ALIAS_MAP[text]
                if target not in aliased_trends:
                    aliased_trends[target] = {
                        "text": target,
                        "count": 0,
                        "score": 0,
                        "is_new": t['is_new']
                    }
                # Merge Stats
                aliased_trends[target]['count'] += t['count']
                aliased_trends[target]['score'] += t['score'] 
                # Keep 'is_new' if ANY source was new? Or only if target was new?
                # Let's say if it's trending, it's trending.
                continue
                
            # If not aliased, keep as candidate
            if text not in aliased_trends:
                 aliased_trends[text] = t
            else:
                 # If exact duplicate exists (unlikely given previous logic, but purely for safety)
                 aliased_trends[text]['count'] += t['count']
                 aliased_trends[text]['score'] = max(aliased_trends[text]['score'], t['score'])

        # Convert back to list
        candidates = list(aliased_trends.values())
        candidates.sort(key=lambda x: x['score'], reverse=True)

        for candidate in candidates:
            candidate_text = candidate['text']
            is_duplicate = False
            
            for accepted in final_trends:
                accepted_text = accepted['text']
                
                # Check Mutual Substring
                if candidate_text in accepted_text or accepted_text in candidate_text:
                    # Found a collision!
                    # Since we sorted by score, 'accepted' is the higher-scoring one.
                    # We discard 'candidate'.
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                final_trends.append(candidate)
        
        # Re-sort just in case, though they should be sorted
        final_trends.sort(key=lambda x: x['score'], reverse=True)
        
        # --- Extract Dynamic Trends (Unigrams/Bigrams) ---
        dynamic_candidates = {}
        if all_messages:
            vahid_sources = ['VahidOnline', 'VahidOOnLine', 'VahidHeadline']
            vahid_msgs = [m for m in all_messages if m.get('node') in vahid_sources]
            recent_msgs = vahid_msgs[-150:] if vahid_msgs else []
            
            stop_words = set([
                'در', 'به', 'از', 'که', 'می', 'این', 'است', 'را', 'با', 'های', 'برای', 'آن', 'یک', 'شود', 'شده', 'خود', 'ها', 'کرد', 'شد', 'ای', 'تا', 'کند', 'بر', 'بود', 'گفت', 'نیز', 'وی', 'هم', 'و', 'یا', 'همچنین', 'دو', 'سه', 'اول', 'دوم', 'کرده', 'اند', 'دارند', 'بودند', 'می‌شود', 'می‌کند', 'است', 'هست', 'نیست', 'دارد', 'سال', 'ماه', 'روز', 'ساعت',
                'اعلام', 'گزارش', 'خبر', 'اخبار', 'ویدیو', 'عکس', 'تصویر', 'تصاویر', 'لینک', 'کانال', 'عضویت', 'مشاهده', 'ادامه', 'مطلب', 'تبلیغات', 'پست', 'جدید', 'قدیم', 'اخیر', 'مهم', 'اصلی', 'سایر', 'بقیه', 'دیگران', 'لطفا',
                'vahidonline', 'vahidoonline', 'vahidheadline', 'bbc', 'fars', 'tasnim', 'isna', 'irna', 'thezoomit', 'digiato', 'telegram', 'instagram', 'twitter',
                'وزیر', 'دولت', 'رئیس', 'مدیر', 'معاون', 'سخنگوی', 'نماینده', 'فرمانده', 'سفیر', 'دبیر', 'حضور', 'کاهش', 'افزایش', 'رشد', 'افت', 'تغییر', 'آغاز', 'پایان',
                'دریافتی', 'شرح', 'پیکر', 'جان', 'دقایقی', 'پیش', 'فوری', 'مهمترین', 'عناوین', 'تکمیلی', 'جزئیات', 'طی', 'صد', 'هزار', 'میلیون', 'میلیارد', 'بیش', 'حدود', 'فقط', 'تنها', 'کامنت', 'ویدئو', 'فیلم', 'کلیپ', 'صوت', 'پادکست', 'درباره', 'مورد', 'براساس', 'طبق', 'گفته'
            ])
            
            unigram_counts = {}
            bigram_counts = {}
            
            for msg in recent_msgs:
                text = msg.get('text', '')
                if not text: continue
                # Basic cleanup
                text = re.sub(r'https?://\S+', '', text)
                text = text.replace('ي', 'ی').replace('ك', 'ک').replace('\u200c', ' ')
                
                # Tokenize Persian words
                tokens = [t for t in re.findall(r'[آ-ی0-9]+', text) if len(t) > 2 and not t.isdigit() and t not in stop_words]
                
                for i in range(len(tokens)):
                    w1 = tokens[i]
                    unigram_counts[w1] = unigram_counts.get(w1, 0) + 1
                    if i < len(tokens) - 1:
                        w2 = tokens[i+1]
                        bigram = f"{w1} {w2}"
                        bigram_counts[bigram] = bigram_counts.get(bigram, 0) + 1
            
            # Boost bigrams and add to candidates
            for bg, count in bigram_counts.items():
                if count >= 3:
                    dynamic_candidates[bg] = count * 3
            
            for ug, count in unigram_counts.items():
                if count >= 5 and ug not in dynamic_candidates:
                    dynamic_candidates[ug] = count

        # Sort dynamic candidates
        sorted_dynamic = [{"text": k, "count": v, "score": v, "is_new": True} for k, v in sorted(dynamic_candidates.items(), key=lambda item: item[1], reverse=True)[:15]]
        
        # Merge dynamic trends with incident trends
        for dt in sorted_dynamic:
            is_dup = False
            for ft in final_trends:
                if dt['text'] in ft['text'] or ft['text'] in dt['text']:
                    is_dup = True
                    break
            if not is_dup:
                final_trends.append(dt)
        
        # Re-sort combined trends
        final_trends.sort(key=lambda x: x['score'], reverse=True)
        top_15_trends = final_trends[:15]
        vibe_index = 50 # Default neutral
        
        try:
            from ai_service import analyze_sentiment_batch
            
            # 1. Analyze Top Trends Sentiment
            trend_texts = [t['text'] for t in top_15_trends]
            if trend_texts:
                sentiments = analyze_sentiment_batch(trend_texts)
                for t in top_15_trends:
                    t['sentiment'] = sentiments.get(t['text'], 'neutral')

                # 2. Calculate Global Vibe Index (from top trend sentiments, NOT 40 full articles)
                pos = list(sentiments.values()).count('positive')
                neg = list(sentiments.values()).count('negative')
                total = len(sentiments)
                
                if total > 0:
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
            "top_nodes": top_15_trends,
            "active_incidents": incidents
        }
        
        with open(os.path.join(LOG_DIR, 'system_metrics.json'), 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    monitor = NetworkMonitor()
    monitor.run()
