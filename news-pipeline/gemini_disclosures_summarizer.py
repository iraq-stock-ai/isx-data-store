import os
import requests
import json
import argparse

TARGET_URL = "http://www.isx-iq.net/isxportal/portal/storyList.html?methodName=getAnnouncementStoryList"

def get_raw_html(url):
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        return response.text
    except: return ""

def ask_gemini_to_extract(html_content):
    api_key = os.getenv("GEMINI_DISCLOSURES_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key: return []
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    prompt = (
        "أنت خبير في تحليل صفحات سوق العراق للأوراق المالية (ISX). "
        "هذا محتوى الـ HTML للصفحة:\n" + html_content[:15000] + 
        "\nالمطلوب: استخرج أحدث الإفصاحات (عنوان ورابط). لا تكرر القديم. أرجع JSON فقط كقائمة: [{\"title\": \"...\", \"url\": \"...\"}]. إذا لا يوجد جديد أرجع []."
    )
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    try:
        response = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=30)
        response.raise_for_status()
        result = response.json()
        text = result['candidates'][0]['content']['parts'][0]['text']
        raw_json = text.replace('```json', '').replace('```', '').strip()
        return json.loads(raw_json)
    except Exception as e:
        print(f"Error: {e}")
        return []

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
        if not any(d['title'] == item.get('title') for d in data):
            data.append(item)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    main()
