import os
import sys
import json
import time
import requests
from datetime import datetime

MODEL_NAME = "gemini-3.5-flash"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"
API_KEY = os.environ.get("GEMINI_API_KEY")

if not API_KEY:
    print("❌ خطأ: لم يتم العثور على متغير البيئة GEMINI_API_KEY.")
    sys.exit(1)

def fetch_gemini_news_radar():
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # صياغة توجيه صارم لجميناي ليعمل كـ رادار وباحث خارجي حصراً
    prompt_text = (
        f"اليوم هو {current_date}. بصفتك رادار مالي ذكي وخبير في أسواق المال العربية والعالمية، "
        "قم بالبحث التام واستخدام محرك البحث المدمج لديك لتجميع أحدث الأخبار والتحليلات والإشارات الحية "
        "عن سوق العراق للأوراق المالية (ISX)، والشركات العراقية المدرجة (مثل مصرف بغداد، آسيا سيل، مصرف المنصور، إلخ) "
        "من مواقع البورصات العالمية، الأخبار الاقتصادية الحكومية والعالمية، ومواقع التواصل الاجتماعي.\n\n"
        "شروط التجميع مفرزة كالتالي:\n"
        "1. قسم (general_news): بنسبة 60%، ويشمل الأخبار الاقتصادية الرسمية، مقالات التحليل المالي العالمي، أو القرارات الحكومية المؤثرة خارج الإفصاحات الروتينية.\n"
        "2. قسم (social_signals): بنسبة 40%، ويشمل نبض الشارع الاستثماري، التوجهات أو التحليلات المنتشرة على منصات التواصل الاجتماعي والمنتديات المالية بخصوص الأسهم العراقية.\n\n"
        "يجب أن تكون المخرجات باللغة العربية الفصحى الإخبارية الرصينة، ومصاغة بصيغة JSON نقية 100% وبدون أي علامات markdown (لا تضع ```json في البداية والنهاية).\n\n"
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
        # تفعيل أداة البحث عن طريق قوقل للذكاء الاصطناعي ليأتي بأخبار حية حقيقية خارج حدوده الذاتية
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    try:
        print(f"🔄 جميناي يبدأ تشمشم الويب بحثاً عن أخبار أسهم العراق الحية...")
        response = requests.post(f"{GEMINI_API_URL}?key={API_KEY}", headers=headers, json=payload, timeout=120)
        if response.status_code == 200:
            res_json = response.json()
            ai_text = res_json['candidates'][0]['content']['parts'][0]['text']
            return json.loads(ai_text)
        else:
            print(f"❌ فشل سيرفر جميناي: كود {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ خطأ اتصال أو معالجة مع جميناي: {e}")
        return None

def main():
    radar_data = fetch_gemini_news_radar()
    if not radar_data:
        print("⚠️ لم يتم جلب بيانات جديدة من جميناي.")
        sys.exit(1)

    # حفظ مخرجات جميناي المستقلة في ملفه المخصص
    output_file = "isx_news.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(radar_data, f, ensure_ascii=False, indent=2)
        
    print(f"🎉 نجاح تام! جميناي شمشم الويب وحفظ الأخبار الخارجية والإشارات بنجاح في {output_file}!")

if __name__ == "__main__":
    main()
