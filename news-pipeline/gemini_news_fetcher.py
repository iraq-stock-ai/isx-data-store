import os
import sys
import json
import time
import requests
from datetime import datetime

# اعتماد الموديل الخفيف والسريع
MODEL_NAME = "gemini-2.0-flash-lite"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"
API_KEY = os.environ.get("GEMINI_API_KEY")

if not API_KEY:
    print("❌ خطأ: لم يتم العثور على متغير البيئة GEMINI_API_KEY.", flush=True)
    sys.exit(1)

def fetch_gemini_news_radar(max_retries=3):
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    prompt_text = (
        f"اليوم هو {current_date}. بصفتك رادار مالي ذكي وخبير في أسواق المال العربية والعالمية، "
        "قم بالبحث التام واستخدم محرك البحث المدمج لديك لتجميع أحدث الأخبار والتحليلات والإشارات الحية "
        "عن سوق العراق للأوراق المالية (ISX)، والشركات العراقية المدرجة (مثل مصرف بغداد، آسيا سيل، مصرف المنصور، إلخ) "
        "من مواقع البورصات العالمية، الأخبار الاقتصادية الحكومية والعالمية، ومنصات التواصل الاجتماعي.\n\n"
        "شروط التجميع مفرزة كالتالي:\n"
        "1. قسم (general_news): بنسبة 60%، ويشمل الأخبار الاقتصادية الرسمية، مقالات التحليل المالي العالمي، أو القرارات الحكومية المؤثرة خارج الإفصاحات الروتينية.\n"
        "2. قسم (social_signals): بنسبة 40%، ويشمل نبض الشارع الاستثماري، التوجهات أو التحليلات المنتشرة على منصات التواصل الاجتماعي والمنتديات المالية بخصوص الأسهم العراقية.\n\n"
        "يجب أن يكون الرد باللغة العربية الفصحى الإخبارية الرصينة، ومصاغاً بصيغة JSON نقية 100% وبدون أي علامات markdown (لا تضع ```json في البداية والنهاية).\n\n"
        "الهيكل المطلوب للحفظ حصراً:\n"
        "{\n"
        "  \"general_news\": [\n"
        "    {\"date\": \"تاريخ نشر الخبر الأصلي\", \"source\": \"اسم الموقع أو المصدر الخارجي العالمي/الحكومي\", \"title\": \"عنوان مالي ذكي وبصياغة عربية ممتازة\", \"content\": \"ملخص مالي شامل ومفهوم للمستثمر حول الخبر\"}\n"
        "  ],\n"
        "  \"social_signals\": [\n"
        "    {\"date\": \"تاريخ رصد الإشارة\", \"platform\": \"المنصة مثل X، تليغرام، فيسبوك.. إلخ\", \"sentiment\": \"Bullish أو Bearish أو Neutral\", \"summary\": \"ملخص للنبض أو التحليل المتداول بين المستثمرين في السوشيال ميديا\"}\n"
        "  ]\n"
        "}"
    )

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    # تقليل فترات الانتظار وعدد المحاولات ليعمل السكربت بسرعة خاطفة
    delay = 5  
    for attempt in range(1, max_retries + 1):
        try:
            print(f"🔄 جميناي يبدأ تشمشم الويب باستخدام {MODEL_NAME}... محاولة رقم {attempt} من {max_retries}", flush=True)
            
            # تقليص وقت الانتظار إلى 30 ثانية كحد أقصى لاستجابة السيرفر
            response = requests.post(f"{GEMINI_API_URL}?key={API_KEY}", headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                res_json = response.json()
                ai_text = res_json['candidates'][0]['content']['parts'][0]['text']
                ai_text = ai_text.strip().removeprefix("```json").removesuffix("```").strip()
                return json.loads(ai_text)
                
            elif response.status_code == 429:
                print(f"⏳ تنبيه (429): قيود الطلبات ممتلئة. تهدئة سريعة لمدة {delay} ثوانٍ...", flush=True)
                time.sleep(delay)
                delay *= 2  
                continue
            else:
                print(f"❌ فشل سيرفر جميناي: كود الخطأ {response.status_code}", flush=True)
                return None
        except Exception as e:
            print(f"❌ خطأ في الاتصال أو التحليل: {e}", flush=True)
            time.sleep(delay)
            delay *= 2
            
    return None

def main():
    radar_data = fetch_gemini_news_radar()
    if not radar_data:
        print("⚠️ لم يتم جلب بيانات جديدة من جميناي.", flush=True)
        sys.exit(1)

    output_file = "isx_news.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(radar_data, f, ensure_ascii=False, indent=2)
        
    print(f"🎉 نجاح تام! تم تشمشم الأخبار الخارجية بنجاح وحُفظت في {output_file}!", flush=True)

if __name__ == "__main__":
    main()
