import os
import requests
import json
import time

# Configuration
ACCOUNT_ID = os.environ.get('CLOUDFLARE_ACCOUNT_ID')
API_TOKEN = os.environ.get('CLOUDFLARE_API_TOKEN')
MODEL = "@cf/meta/llama-3-8b-instruct"

def analyze_sentiment_batch(texts):
    """
    Analyze sentiment for a list of texts using Cloudflare AI.
    Returns a dictionary: { "text": "positive" | "negative" | "neutral" }
    """
    if not ACCOUNT_ID or not API_TOKEN:
        print("Warning: Cloudflare credentials not found. Skipping sentiment analysis.")
        return {t: "neutral" for t in texts}

    results = {}
    
    # Cloudflare AI runs best with single prompts or small batches.
    # We will combine them into a single prompt to request a strict JSON object.
    
    prompt = "You are a financial and political news sentiment analyst. Evaluate the market sentiment (Vibe) of the following Persian keywords.\n"
    prompt += "Definitions:\n"
    prompt += "- 'negative': Words related to war, death, tension, conflict, crisis, fear, or economic crash.\n"
    prompt += "- 'positive': Words related to peace, agreements, hope, economic growth, or stability.\n"
    prompt += "- 'neutral': Proper nouns, generic terms, or anything ambiguous.\n\n"
    prompt += "You MUST respond with ONLY a valid JSON object where the keys are the exact keywords and the values are exactly one of: 'positive', 'negative', or 'neutral'. Do not include any other text.\n\n"
    prompt += "Keywords to analyze:\n"
    
    for t in texts:
        prompt += f'- "{t}"\n'
        
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL}"
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    payload = {
        "messages": [
            {"role": "system", "content": "You are a strict JSON API. You only output valid JSON dictionaries."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()
        
        if data.get('success'):
            raw_output = data['result']['response'].strip()
            
            # Remove possible conversational leftovers or markdown
            if raw_output.startswith("```json"):
                raw_output = raw_output[7:]
            if raw_output.startswith("```"):
                raw_output = raw_output[3:]
            if raw_output.endswith("```"):
                raw_output = raw_output[:-3]
                
            raw_output = raw_output.strip()
            
            try:
                # Basic cleanup just in case there's text before/after the JSON
                import re
                json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
                if json_match:
                    raw_output = json_match.group(0)
                
                parsed_json = json.loads(raw_output)
                # Ensure parsed_json is a dict
                if not isinstance(parsed_json, dict):
                    raise ValueError("Parsed JSON is not a dictionary")
                    
                for key, val in parsed_json.items():
                    sentiment = str(val).strip().lower()
                    if 'positive' in sentiment: final_val = 'positive'
                    elif 'negative' in sentiment: final_val = 'negative'
                    else: final_val = 'neutral'
                    
                    # Match the key back to the original text (allowing for quotes etc)
                    matched_key = None
                    for t in texts:
                        if t in key or key in t:
                            matched_key = t
                            break
                    if matched_key:
                        results[matched_key] = final_val

            except Exception as e:
                print(f"Failed to parse AI JSON output: {e}\nRaw Output: {raw_output}")
                # Fallback: simple text scanning
                for t in texts:
                    results[t] = "neutral" # Default to neutral if we can't parse safely
                    # It's too risky to just check if 'positive' is in the text because the AI might say "I marked this as positive"

            # Fill missing with neutral
            for t in texts:
                if t not in results:
                    # Fallback check matches
                    for k, v in results.items():
                        if t in k or k in t:
                            results[t] = v
                            break
                    if t not in results:
                        results[t] = "neutral"
                        
            return results
        else:
            print(f"Cloudflare AI Error: {data.get('errors')}")
            return {t: "neutral" for t in texts}

    except Exception as e:
        print(f"AI Request Failed: {e}")
        return {t: "neutral" for t in texts}
