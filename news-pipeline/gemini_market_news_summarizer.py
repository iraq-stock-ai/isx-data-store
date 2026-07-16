import os
import requests
import google.generativeai as genai
import json
import argparse

# إعداد مفتاح API
GEMINI_KEY = os.getenv("GEMINI_MARKET_NEWS_API_KEY") or os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

TARGET_URL = "http://www.isx-iq.net/isxportal/portal/storyList.html?methodName=getNewsStoryList"

def get_raw_html(url):
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        return response.text
    except: return ""

def ask_gemini_to_extract(html_content):
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    # تم تغيير الطريقة لدمج النصوص لتجنب تداخل الأقواس
    part1 = "\nأنت خبير في تحليل صفحات سوق العراق للأوراق المالية (ISX). هذا محتوى الـ HTML لصفحة الأخبار:\n"
    part2 = "\nالمطلوب: استخرج أحدث الأخبار (عنوان ورابط). لا تكرر القديم. أرجع JSON فقط كقائمة: [{\"title\": \"...\", \"url\": \"...\"}]. إذا لا يوجد جديد أرجع []."
    
    full_prompt = part1 + html_content[:15000] + part2
    
    response = model.generate_content(full_prompt)
    try:
        raw_json = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(raw_json)
    except: return []

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing")
    parser.add_argument("--output")
    args = parser.parse_args()

    html = get_raw_html(TARGET_URL)
    if not html: return

    new_items = ask_gemini_to_extract(html)
    
    data = []
    if os.path.exists(args.existing):
        with open(args.existing, 'r', encoding='utf-8') as f:
            try: data = json.load(f)
            except: data = []

    for item in new_items:
        if not any(d['title'] == item['title'] for d in data):
            data.append(item)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    main()
