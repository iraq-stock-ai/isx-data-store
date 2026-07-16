import os
import requests
import google.generativeai as genai
import json
import argparse

# إعداد مفتاح الـ API
GEMINI_KEY = os.getenv("GEMINI_DISCLOSURES_API_KEY") or os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_KEY)

# الرابط
TARGET_URL = "http://www.isx-iq.net/isxportal/portal/storyList.html?methodName=getAnnouncementStoryList"

def get_raw_html(url):
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        return response.text
    except Exception as e:
        print(f"فشل الاتصال: {e}")
        return ""

def ask_gemini_to_extract(html_content):
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    # التعليمات (Prompt) - هنا جمناي هو المسؤول عن الفهم
    prompt = f"""
    أنت خبير في تحليل صفحات سوق العراق للأوراق المالية (ISX).
    هذا هو محتوى HTML لصفحة الإفصاحات:
    {html_content[:15000]} 

    المطلوب منك:
    1. استخرج أحدث الإفصاحات فقط (عناوين الأخبار والروابط الكاملة لها).
    2. لا تكرر الأخبار القديمة.
    3. أرجع النتيجة بتنسيق JSON فقط (قائمة تحتوي على {"title": "...", "url": "..."}).
    4. إذا لم توجد أخبار جديدة، أرجع قائمة فارغة [].
    """
    
    response = model.generate_content(prompt)
    try:
        # تنظيف الرد لاستخراج الـ JSON
        raw_json = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(raw_json)
    except:
        return []

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing")
    parser.add_argument("--output")
    args = parser.parse_args()

    html = get_raw_html(TARGET_URL)
    if not html: return

    new_items = ask_gemini_to_extract(html)
    
    # دمج مع البيانات القديمة
    data = []
    if os.path.exists(args.existing):
        with open(args.existing, 'r', encoding='utf-8') as f:
            try: data = json.load(f)
            except: data = []

    # إضافة العناصر الجديدة فقط (بدون تكرار)
    for item in new_items:
        if not any(d['title'] == item['title'] for d in data):
            print(f"تم العثور على إفصاح جديد: {item['title']}")
            data.append(item)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    main()
