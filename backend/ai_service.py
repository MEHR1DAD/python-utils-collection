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
    # We will combine them into a single prompt to save API calls and time.
    
    prompt = "Classify the sentiment of the following Persian news keywords as POSITIVE, NEGATIVE, or NEUTRAL.\n"
    prompt += "Strictly follow this format: 'KEYWORD: SENTIMENT'\n\n"
    
    for t in texts:
        prompt += f"- {t}\n"
        
    prompt += "\nResponse:"

    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL}"
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    payload = {
        "messages": [
            {"role": "system", "content": "You are a sentiment analysis assistant for Persian news context. You ONLY output the classification."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()
        
        if data.get('success'):
            raw_output = data['result']['response']
            # Parse the output
            # Expected format:
            # - Keyword1: POSITIVE
            # - Keyword2: NEGATIVE
            
            lines = raw_output.strip().split('\n')
            for line in lines:
                parts = line.split(':')
                if len(parts) >= 2:
                    key = parts[0].strip().replace('- ', '')
                    sentiment = parts[1].strip().lower()
                    
                    # Normalize sentiment
                    if 'positive' in sentiment: val = 'positive'
                    elif 'negative' in sentiment: val = 'negative'
                    else: val = 'neutral'
                    
                    # Find matching original text (fuzzy check needed?)
                    # For now assume exact match or close enough
                    results[key] = val
                    
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
