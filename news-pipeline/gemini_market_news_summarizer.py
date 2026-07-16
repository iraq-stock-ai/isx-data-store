import os
import requests
import json
import argparse
import sys

# إعداد المتغيرات
parser = argparse.ArgumentParser()
parser.add_argument("--existing")
parser.add_argument("--output")
args = parser.parse_args()

api_key = os.getenv("GEMINI_MARKET_NEWS_API_KEY") or os.getenv("GEMINI_API_KEY")
if not api_key:
    sys.exit("API Key not found")

target_url = "http://www.isx-iq.net/isxportal/portal/storyList.html?methodName=getNewsStoryList"

# جلب البيانات
response = requests.get(target_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
html_content = response.text

# تجهيز الطلب
model_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
prompt = (
    "أنت خبير في تحليل صفحات سوق العراق للأوراق المالية. هذا محتوى صفحة الأخبار:\n" 
    + html_content[:15000] + 
    "\nالمطلوب: استخرج أحدث الأخبار (عنوان ورابط). أرجع JSON فقط كقائمة: [{\"title\": \"...\", \"url\": \"...\"}]. إذا لا يوجد جديد أرجع []."
)

payload = {"contents": [{"parts": [{"text": prompt}]}]}

# إرسال الطلب
gemini_response = requests.post(model_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=60)

if gemini_response.status_code == 200:
    data = gemini_response.json()
    text = data['candidates'][0]['content']['parts'][0]['text']
    raw_json = text.replace('```json', '').replace('```', '').strip()
    new_items = json.loads(raw_json)
else:
    print(f"Error: {gemini_response.status_code} - {gemini_response.text}")
    new_items = []

# حفظ البيانات
existing_data = []
if args.existing and os.path.exists(args.existing):
    with open(args.existing, 'r', encoding='utf-8') as f:
        try: existing_data = json.load(f)
        except: existing_data = []

for item in new_items:
    if not any(d['title'] == item.get('title') for d in existing_data):
        existing_data.append(item)

with open(args.output, 'w', encoding='utf-8') as f:
    json.dump(existing_data, f, ensure_ascii=False, indent=4)
