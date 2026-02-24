import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))
from backend.ai_service import analyze_sentiment_batch

test_keywords = ["غنی سازی", "دانشگاه صنعتی", "استیو ویتکاف", "صنعتی شریف", "دهد", "حمله به تهران", "زلزله مشهد", "توافق هسته‌ای"]
print("Testing Cloudflare AI NER against test keywords:")
print(test_keywords)

print("...")
result = analyze_sentiment_batch(test_keywords)

for keyword, sentiment in result.items():
    print(f"- {keyword}: {sentiment}")
