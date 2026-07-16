import json
import os
import sys
import time
from datetime import datetime

import requests

MODEL_NAME = "gemini-3.1-flash-lite"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"
API_KEY = os.environ.get("GEMINI_MARKET_NEWS_API_KEY")

DISCLOSURES_LIST_URL = "http://www.isx-iq.net/isxportal/portal/storyList.html?activeTab=0"


def call_gemini(prompt_text: str, max_retries: int = 3) -> dict:
    """يستدعي Gemini API مع أداة googleSearch المدمجة، ويرجع JSON من الرد."""
    if not API_KEY:
        print("❌ خطأ: لم يتم العثور على متغير البيئة GEMINI_MARKET_NEWS_API_KEY.")
        return None

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    delay = 5
    for attempt in range(1, max_retries + 1):
        try:
            print(f"🔄 إرسال لـ Gemini (مع أداة البحث)... محاولة {attempt}/{max_retries}")
            response = requests.post(
                f"{GEMINI_API_URL}?key={API_KEY}", headers=headers, json=payload, timeout=60
            )

            if response.status_code == 200:
                res_json = response.json()
                candidate = res_json.get("candidates", [{}])[0]
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                ai_text = "".join(p.get("text", "") for p in parts)

                if not ai_text.strip():
                    print("⚠️ رد Gemini فارغ تماماً (لم يستطع استخراج أي محتوى).")
                    print(f"   الرد الخام الكامل للتشخيص: {json.dumps(res_json, ensure_ascii=False)[:500]}")
                    return None

                ai_text = ai_text.strip().removeprefix("```json").removesuffix("```").strip()
                return json.loads(ai_text)

            elif response.status_code == 429:
                print(f"⏳ (429) تهدئة {delay} ثوانٍ...")
                time.sleep(delay)
                delay *= 2
                continue
            else:
                print(f"❌ فشل سيرفر Gemini: كود {response.status_code}")
                print(f"   تفاصيل: {response.text[:500]}")
                return None

        except json.JSONDecodeError as e:
            print(f"❌ رد Gemini لم يكن JSON صالحاً: {e}")
            print(f"   النص المستلم: {ai_text[:500] if 'ai_text' in dir() else '(غير متاح)'}")
            return None
        except Exception as e:
            print(f"❌ خطأ اتصال/تحليل: {e}")
            time.sleep(delay)
            delay *= 2

    return None


def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {"official_disclosures": []}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            return {"official_disclosures": []}
    data.setdefault("official_disclosures", [])
    return data


def save_json(data: dict, path: str):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def main():
    output_path = "isx_disclosures.json"
    data = load_existing(output_path)
    known_ids = [item.get("id") for item in data["official_disclosures"] if "id" in item]
    known_ids_text = ", ".join(known_ids) if known_ids else "لا يوجد (هذه أول تشغيلة)"

    prompt_text = (
        "استخدم أداة البحث المتوفرة لديك لزيارة هذا الرابط وقراءة محتواه بالكامل:\n"
        f"{DISCLOSURES_LIST_URL}\n\n"
        "هذه صفحة \"اعلانات\" (إفصاحات رسمية) بسوق العراق للأوراق المالية. "
        "تحتوي قائمة بعناوين إفصاحات، كل عنصر له رقم معرّف (storyId) ضمن رابطه.\n\n"
        f"المعرّفات (storyId) التي سبق معالجتها ويجب تجاهلها: {known_ids_text}\n\n"
        "مهمتك:\n"
        "1. حدد أقدم إفصاح بالقائمة لم يُعالج سابقاً (رقم معرّفه غير موجود بالقائمة أعلاه).\n"
        "2. افتح تفاصيل هذا الإفصاح تحديداً واقرأ محتواه الكامل.\n"
        "3. استخرج المعلومات المالية الجوهرية إن وُجدت صراحة بالنص فقط، دون اختلاق أي رقم.\n\n"
        "إذا لم تجد أي إفصاح جديد بالصفحة، أو تعذّر عليك الوصول لمحتوى الصفحة "
        "فعلياً، أجب بـ: {\"found\": false, \"reason\": \"وصف موجز لسبب الفشل\"}\n\n"
        "إذا نجحت، أجب حصراً بصيغة JSON نقية 100% وبدون أي علامات markdown:\n"
        "{\n"
        '  "found": true,\n'
        '  "story_id": "المعرّف الرقمي المستخرج من رابط الإفصاح",\n'
        '  "title": "العنوان الكامل كما ورد",\n'
        '  "url": "الرابط الكامل لصفحة تفاصيل هذا الإفصاح",\n'
        '  "disclosure_type": "توزيع_ارباح | زيادة_راس_المال | قوائم_مالية | '
        'اجتماع_هيئة_عامة | قرار_رفض_او_عدم_موافقة | حركة_تداول_خاصة | اخرى",\n'
        '  "company_name": "اسم الشركة أو null",\n'
        '  "raas_al_mal": "رقم رأس المال أو null",\n'
        '  "net_profit": "صافي الربح أو null",\n'
        '  "total_debts": "إجمالي الديون أو null",\n'
        '  "dividend_per_share": "قيمة التوزيع للسهم أو null",\n'
        '  "dividend_percentage": "نسبة التوزيع أو null",\n'
        '  "dividend_date": "تاريخ التوزيع إن ذُكر أو null",\n'
        '  "date": "تاريخ الإفصاح بصيغة يوم/شهر/سنة",\n'
        '  "summary": "ملخص من جملتين إلى ثلاث بالعربية الفصحى"\n'
        "}"
    )

    result = call_gemini(prompt_text)

    if not result:
        print("❌ فشل الاتصال بـ Gemini أو معالجة الرد. سيُعاد المحاولة بالتشغيلة القادمة.")
        sys.exit(1)

    if not result.get("found"):
        reason = result.get("reason", "غير محدد")
        print(f"ℹ️ Gemini لم يجد إفصاحاً جديداً أو تعذّر الوصول. السبب المُبلّغ: {reason}")
        return

    record = {
        "id": result.get("story_id", "unknown"),
        "title": result.get("title", ""),
        "source": "سوق العراق للأوراق المالية (isx-iq.net) - ملخص Gemini المباشر",
        "url": result.get("url", DISCLOSURES_LIST_URL),
        "date": result.get("date") or datetime.now().strftime("%d/%m/%Y"),
        "disclosure_type": result.get("disclosure_type"),
        "company_name": result.get("company_name"),
        "raas_al_mal": result.get("raas_al_mal"),
        "net_profit": result.get("net_profit"),
        "total_debts": result.get("total_debts"),
        "dividend_per_share": result.get("dividend_per_share"),
        "dividend_percentage": result.get("dividend_percentage"),
        "dividend_date": result.get("dividend_date"),
        "summary": result.get("summary"),
    }

    data["official_disclosures"].insert(0, record)
    save_json(data, output_path)

    print(f"\n✅ تم تلخيص وحفظ الإفصاح بنجاح: {record['title'][:60]}")
    print(f"   الإجمالي الآن: {len(data['official_disclosures'])}")


if __name__ == "__main__":
    main()
