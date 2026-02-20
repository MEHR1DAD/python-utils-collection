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
    
    prompt = "Classify the sentiment of the following Persian news keywords as 'positive', 'negative', or 'neutral'.\n"
    prompt += "You MUST respond with ONLY a valid JSON object where the keys are the exact keywords and the values are their sentiments. Do not include any markdown formatting, backticks, or intro text.\n\n"
    prompt += "Keywords to analyze:\n"
    
    for t in texts:
        prompt += f"- {t}\n"
        
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL}"
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    payload = {
        "messages": [
            {"role": "system", "content": "You are a sentiment analysis assistant for Persian news context. You ONLY output raw JSON. Never use markdown like ```json."},
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
                parsed_json = json.loads(raw_output)
                for key, val in parsed_json.items():
                    sentiment = val.strip().lower()
                    if 'positive' in sentiment: final_val = 'positive'
                    elif 'negative' in sentiment: final_val = 'negative'
                    else: final_val = 'neutral'
                    results[key] = final_val
            except json.JSONDecodeError as e:
                print(f"Failed to parse AI JSON output: {e}\nRaw Output: {raw_output}")
                # Try fallback line-by-line matching if JSON fails entirely
                for t in texts:
                    if t in raw_output:
                        if 'positive' in raw_output: results[t] = 'positive'
                        elif 'negative' in raw_output: results[t] = 'negative'
                    
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
