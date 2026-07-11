import argparse
import json
import os
import re
import sys
import time  # 💡 تم إضافة مكتبة الوقت هنا
from datetime import datetime, timezone, timedelta

import requests

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)

SANITY_MAX_ITEMS = 20  # سقف معقول لسوق صغير مثل ISX؛ تجاوزه مشبوه

# نفس قائمة الرموز الـ106 من القسم 2️⃣.2 بالوثيقة
KNOWN_SYMBOLS = {
    "AREB", "TASC", "IRMC", "SAEI", "VAMF", "NAME", "AAHP", "NAHF", "VBAT",
    "SBAG", "AMAP", "NHAM", "TZNI", "IKHC", "NGIR", "IMOS", "VKHF", "VZAF",
    "AMEF", "AIPM", "IMCI", "IELI", "ITLI", "IMIB", "AISP", "IIDP", "IICM",
    "IIEW", "IITC", "AIRP", "SILT", "SIGT", "IFCM", "IKLV", "HTVM", "BNOI",
    "BCOI", "BINT", "BIIB", "BUND", "BNAI", "SMRI", "IMAP", "SMOF", "SNUC",
    "IHLI", "VWIF", "IHFI", "HNTI", "INCP", "ABAP", "SBPT", "IBPM", "IBSD",
    "VMES", "NDSA", "BZII", "BSUC", "BTRI", "BKUI", "BINI", "BWAI", "BELF",
    "BBAY", "BBOB", "BCIH", "BHAM", "BQAB", "BQUR", "BMAL", "BMUI", "BAMS",
    "BMNS", "BMFI", "BSAN", "BIME", "BNOR", "BTIB", "BWOR", "BAAI", "BLAD",
    "BRTB", "BANS", "BIDB", "BTRU", "BJAB", "BGUC", "BRAJ", "BERI", "BAIB",
    "BASH", "BROI", "BUOI", "BIBI", "BEFI", "HMAN", "HBAY", "HBAG", "HPAL",
    "SKTA", "BAME", "HKAR", "IMCM", "HISH", "HASH", "HSAD",
}

SYSTEM_PROMPT = """You are an expert financial data extraction tool specialized in the Iraq Stock Exchange (ISX).

Task: Perform a live web search for GENERAL ECONOMIC NEWS and SOCIAL MEDIA SIGNALS related to Iraq's financial sector and ISX-listed companies over the past 24 hours. Do NOT search for official regulatory disclosures — those come from a separate official source.

Split results into two categories:
- "general_news": economic news from established news sites (not social media)
- "social_signals": mentions or discussions from social media platforms (Twitter/X, Facebook) that could plausibly affect market sentiment

CRITICAL RULES:
- You MUST respond ONLY with a raw JSON object. No markdown code blocks, no conversational text.
- Every item MUST include a real "url" you actually found via search. If you cannot find a real source URL for a claim, DO NOT include that item at all — do not use null or a placeholder URL.
- Only include "related_symbols" from this exact list of valid ISX ticker symbols: {symbols}
- If a news item does not clearly relate to any specific company from that list, leave related_symbols as an empty array (it may still be relevant as general market/macro news).
- Do not fabricate, estimate, or infer information not explicitly found in your search results.

Output Format (JSON object, exactly this structure):
{{
  "general_news": [
    {{
      "title": "Arabic title",
      "content": "Arabic summary, 2-3 sentences",
      "source": "site name",
      "url": "real URL found via search",
      "date": "ISO date",
      "related_symbols": ["TASC"],
      "sentiment": "Positive" | "Negative" | "Neutral",
      "impact_score": "Very High" | "High" | "Medium" | "Low"
    }}
  ],
  "social_signals": [
    {{
      "title": "Arabic title",
      "content": "Arabic summary",
      "source": "platform name (e.g. Twitter/X, Facebook)",
      "url": "real URL",
      "date": "ISO date",
      "related_symbols": [],
      "sentiment": "Positive" | "Negative" | "Neutral",
      "impact_score": "Very High" | "High" | "Medium" | "Low"
    }}
  ]
}}
""".format(symbols=", ".join(sorted(KNOWN_SYMBOLS)))


class QualityGateError(Exception):
    pass


def call_gemini(api_key: str) -> dict:
    """بوابة 1: يستدعي Gemini مع Search Grounding، ويتعامل ذكياً مع خطأ الحظر 429 عبر إعادة المحاولة تلقائياً."""
    payload = {
        "contents": [{"parts": [{"text": SYSTEM_PROMPT}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1},
    }
    url = f"{GEMINI_API_URL}?key={api_key}"

    max_retries = 5
    backoff_factor = 15  # الانتظار الأولي بالثواني عند مواجهة حظر من سيرفرات جوجل
    result = None

    for attempt in range(max_retries):
        try:
            print(f"جاري إرسال الطلب إلى Gemini API (محاولة {attempt + 1} من {max_retries})...")
            resp = requests.post(url, json=payload, timeout=90)  # رفع مهلة الانتظار لأن جلب نتائج البحث يأخذ وقتاً
            
            # في حال واجهنا ضغط طلبات وحظر مؤقت من جوجل
            if resp.status_code == 429:
                sleep_time = backoff_factor * (2 ** attempt)
                print(f"⚠️ تنبيه: واجهنا خطأ 429 (Too Many Requests). سننتظر {sleep_time} ثانية كاستراحة لفتح الحظر ثم نعيد المحاولة...")
                time.sleep(sleep_time)
                continue
                
            resp.raise_for_status()
            result = resp.json()
            break  # نجح الطلب، نكسر الحلقة ونكمل التنفيذ
            
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise QualityGateError(f"فشل الاتصال بـ Gemini API نهائياً بعد {max_retries} محاولات: {e}")
            sleep_time = backoff_factor * (2 ** attempt)
            print(f"⚠️ حدث خطأ غير متوقع: {e}. إعادة المحاولة بعد {sleep_time} ثانية...")
            time.sleep(sleep_time)
    else:
        raise QualityGateError("فشل تنفيذ السكريبت بسبب استمرار خطأ الحظر 429 من سيرفرات جوجل المجانية.")

    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise QualityGateError(f"شكل رد Gemini غير متوقع: {result}")

    text = re.sub(r"^```json\s*|\s*```$", "", text.strip())

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise QualityGateError(f"رد Gemini ليس JSON صالحاً: {e}\nالنص المستلم: {text[:500]}")

    print("[بوابة 1] ✅ نجحت — رد Gemini هو JSON صالح.")
    return parsed


def validate_and_clean_items(items: list, category: str) -> list:
    """بوابات 2 و3: يرفض أي عنصر بلا رابط حقيقي، وينظف الرموز غير المعروفة."""
    valid = []
    for item in items:
        url = item.get("url")
        if not url or not isinstance(url, str) or not url.startswith("http"):
            print(f"    [بوابة 2] رفض عنصر بلا رابط مصدر حقيقي: {item.get('title', '؟')[:50]}")
            continue

        original_symbols = item.get("related_symbols", [])
        cleaned_symbols = [s for s in original_symbols if s in KNOWN_SYMBOLS]
        if len(cleaned_symbols) != len(original_symbols):
            dropped = set(original_symbols) - set(cleaned_symbols)
            print(f"    [بوابة 3] حُذفت رموز غير معروفة من '{item.get('title', '؟')[:40]}': {dropped}")
        item["related_symbols"] = cleaned_symbols

        valid.append(item)

    print(f"[بوابة 2+3] {category}: {len(valid)} سليم من أصل {len(items)}.")
    return valid


def check_sanity_limit(general_news: list, social_signals: list):
    """بوابة 4: سقف أقصى معقول لعدد العناصر الجديدة باليوم."""
    total = len(general_news) + len(social_signals)
    if total > SANITY_MAX_ITEMS:
        raise QualityGateError(
            f"عدد العناصر المستلمة ({total}) يتجاوز الحد المعقول "
            f"({SANITY_MAX_ITEMS}) لسوق بحجم ISX — يُشتبه بتكرار، "
            f"يُرفض الرد كاملاً لمراجعة يدوية."
        )
    print(f"[بوابة 4] ✅ نجحت — {total} عنصر إجمالي، ضمن الحد المعقول.")


def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {"official_disclosures": [], "general_news": [], "social_signals": []}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for key in ("official_disclosures", "general_news", "social_signals"):
        data.setdefault(key, [])
    return data


def save_json(data: dict, path: str):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def dedupe_against_recent(new_items: list, existing_items: list, days: int = 14) -> list:
    """بوابة 5: يستبعد أي عنصر جديد له نفس url موجود مسبقاً بآخر N يوم."""
    recent_urls = set()
    for item in existing_items:
        recent_urls.add(item.get("url"))

    result = [item for item in new_items if item.get("url") not in recent_urls]
    skipped = len(new_items) - len(result)
    if skipped:
        print(f"[بوابة 5] تخطي {skipped} عنصر مكرر (رابط موجود مسبقاً).")
    return result


def main():
    parser = argparse.ArgumentParser(description="جلب الأخبار العامة وإشارات التواصل عبر Gemini")
    parser.add_argument("--existing", default="isx_news.json")
    parser.add_argument("--output", default="isx_news.json")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ متغير البيئة GEMINI_API_KEY غير موجود. تأكد من إعداده في GitHub Secrets.", file=sys.stderr)
        sys.exit(1)

    try:
        raw_result = call_gemini(api_key)

        general_news = validate_and_clean_items(raw_result.get("general_news", []), "general_news")
        social_signals = validate_and_clean_items(raw_result.get("social_signals", []), "social_signals")

        check_sanity_limit(general_news, social_signals)

    except QualityGateError as e:
        print(f"\n❌ توقف التنفيذ — فشلت بوابة تحقق: {e}", file=sys.stderr)
        print("لن يتم تعديل isx_news.json. الملف بالمستودع يبقى كما هو.", file=sys.stderr)
        sys.exit(1)

    data = load_existing(args.existing)

    new_general = dedupe_against_recent(general_news, data["general_news"])
    new_social = dedupe_against_recent(social_signals, data["social_signals"])

    if not new_general and not new_social:
        print("\nلا توجد أخبار جديدة فعلياً بعد إزالة التكرار. لن يُعدَّل الملف.")
        sys.exit(0)

    for item in new_general:
        data["general_news"].insert(0, item)
    for item in new_social:
        data["social_signals"].insert(0, item)

    save_json(data, args.output)

    print(f"\n✅ تم بنجاح:")
    print(f"   - أخبار عامة جديدة: {len(new_general)}")
    print(f"   - إشارات تواصل اجتماعي جديدة: {len(new_social)}")


if __name__ == "__main__":
    main()
