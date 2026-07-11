import os
import sys
import json
import time
import requests

# 1. إعدادات الموديل والروابط بناءً على لوحة تحكم حسابك الحالية
MODEL_NAME = "gemini-3.5-flash"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"

# جلب مفتاح الـ API المخزن في Secrets داخل GitHub
API_KEY = os.environ.get("GEMINI_API_KEY")

if not API_KEY:
    print("❌ خطأ: لم يتم العثور على متغير البيئة GEMINI_API_KEY. تأكد من إضافته في Secrets.")
    sys.exit(1)

def fetch_gemini_response(prompt_text, max_retries=5):
    """
    دالة ترسل الطلب إلى Gemini مع ميزة الانتظار الذكي (Exponential Backoff)
    لمعالجة الخطأ 429 وحماية الحصة اليومية المحدودة (20 طلب).
    """
    headers = {
        "Content-Type": "application/json"
    }
    
    # إجبار الموديل على إرجاع البيانات بصيغة JSON صافية لمنع أخطاء القراءة
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt_text}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    
    delay = 15  # يبدأ الانتظار من 15 ثانية في حال ضغط السيرفر
    
    for attempt in range(1, max_retries + 1):
        try:
            print(f"🔄 محاولة إرسال الطلب إلى {MODEL_NAME} (محاولة {attempt} من {max_retries})...")
            
            # إرسال الطلب مع تمرير المفتاح برابط الـ URL لضمان استقرار الاتصال
            response = requests.post(
                f"{GEMINI_API_URL}?key={API_KEY}",
                headers=headers,
                json=payload,
                timeout=90
            )
            
            # حالة النجاح
            if response.status_code == 200:
                print("✅ تم استلام الرد بنجاح من Gemini!")
                return response.json()
            
            # حالة الحظر المؤقت أو تجاوز الحصة بالدقيقة (429)
            elif response.status_code == 429:
                print(f"⚠️ تنبيه (429): تم تجاوز حد الطلبات المسموح بها مؤقتاً.")
                if attempt < max_retries:
                    print(f"⏳ سيتم إيقاف السكريبت مؤقتاً لمدة {delay} ثانية لتصفير عداد الدقيقة الحالية...")
                    time.sleep(delay)
                    delay *= 2  # مضاعفة وقت الانتظار تلقائياً (15 -> 30 -> 60)
                    continue
                else:
                    print("❌ خطأ: تم استنفاد جميع المحاولات بسبب قيود الحصة (Quota Limits).")
                    sys.exit(1)
            
            # أي خطأ آخر من السيرفر
            else:
                print(f"❌ خطأ من السيرفر (كود {response.status_code}):")
                print(response.text)
                sys.exit(1)
                
        except requests.exceptions.RequestException as e:
            print(f"❌ خطأ في الاتصال بالشبكة: {e}")
            if attempt < max_retries:
                print(f"⏳ إعادة المحاولة بعد {delay} ثانية...")
                time.sleep(delay)
                delay *= 2
            else:
                sys.exit(1)

def main():
    print("📋 بدء معالجة الأخبار وتجهيز البيانات...")
    
    # 📝 هنا نضع الـ Prompt المخصص لسوق العراق للأوراق المالية (ISX)
    # نقوم بطلب معالجة البيانات دفعة واحدة لحفظ الـ 20 طلب اليومي الخاص بك.
    prompt = (
        "أنت خبير محترف في سوق العراق للأوراق المالية (ISX). "
        "قم بتحليل البيانات والأخبار المرفقة واستخراج الأخبار الجوهرية للشركات المدرجة. "
        "يجب أن يكون الرد بصيغة JSON صالحة ونظيفة حصراً وبدون أي مقدمات أو علامات markdown (مثل ```json). "
        "الهيكل المطلوب للملف:\n"
        "{\n"
        "  \"news\": [\n"
        "    {\"date\": \"تاريخ الخبر\", \"company\": \"اسم الشركة\", \"title\": \"عنوان الخبر\", \"content\": \"ملخص وتفاصيل الخبر الجوهري\"}\n"
        "  ]\n"
        "}\n\n"
        "الأخبار المراد تحليلها:\n"
        # [هنا السكريبت يدمج النص المجلوب تلقائياً]
    )
    
    # استدعاء دالة الاتصال المحدثة والمحمية بالـ Backoff
    result = fetch_gemini_response(prompt)
    
    if result:
        try:
            # استخراج النص الصافي للـ JSON من رد الموديل
            ai_text = result['candidates'][0]['content']['parts'][0]['text']
            
            # التحقق من صحة هيكل الـ JSON قبل الحفظ لضمان عدم تلف الملف
            news_data = json.loads(ai_text)
            
            # حفظ الملف بالاسم المطلوب في المستودع
            with open("isx_news.json", "w", encoding="utf-8") as f:
                json.dump(news_data, f, ensure_ascii=False, indent=2)
                
            print("🎉 ممتاز! تم إنشاء وتحديث ملف isx_news.json بنجاح والأكشن سيصبح أخضر ✅!")
            
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            print(f"❌ خطأ أثناء معالجة رد الذكاء الاصطناعي وتحويله لـ JSON: {e}")
            print("الرد الخام المستلم كان:")
            print(result)
            sys.exit(1)

if __name__ == "__main__":
    main()
