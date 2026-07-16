import os
import sys
import json
import urllib.parse
import requests
from bs4 import BeautifulSoup
from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

OUTPUT_FILE = "isx_news.json"

# ==============================================================================
# مرحلة 1: كلمات البحث (موسّعة عمداً لتغطية احتمالات كافية)
# ==============================================================================
KEYWORDS = [
    "سوق العراق للأوراق المالية",
    "أسهم العراق",
    "بورصة بغداد",
    "مؤشر ISX60",
    "مصرف بغداد اسهم",
    "مصرف المنصور اسهم",
    "اسياسيل للاتصالات",
    "بورصة العراق تداول",
]

MAX_NEW_ARTICLES_PER_RUN = 30

# ==============================================================================
# مرحلة 2: فلتر الرفض الصارم
# ==============================================================================

# أسماء شركات عراقية مدرجة معروفة (لو ذُكر أي منها بالعنوان، الخبر يُقبل
# دائماً بغض النظر عن باقي الفلتر - لأنه مرتبط مباشرة بسهم محدد)
COMPANY_NAME_HINTS = [
    "مصرف بغداد", "مصرف المنصور", "اسياسيل", "آسيا سيل", "الخاتم للاتصالات",
    "مصرف بابل", "دار السلام للتأمين", "الخياطة الحديثة", "فندق بغداد",
    "المصرف المتحد", "مصرف اربيل", "الهلال الصناعية", "الشرق الاوسط لانتاج",
    # ملاحظة: "الشرق الاوسط للاستثمار المالي" اسم غامض يتشارك مع شركات
    # خليجية أخرى (كويتية خصوصاً) - لذلك استُبعد من هذه القائمة عمداً؛
    # سيُفلتر عبر الكلمات الجغرافية أدناه بدلاً من ذلك.
]

# كلمات تدل على حدث مؤثر فعلي على السهم (قرار، قضية، توسع...) - إذا وُجدت
# مع سياق عراقي، الخبر يُقبل
IMPACT_KEYWORDS = [
    "قرار", "قضية", "دعوى", "توسع", "فرع جديد", "استحواذ", "اندماج",
    "إفلاس", "افلاس", "تحقيق", "غرامة", "عقوبة", "ترخيص", "توقيع اتفاقية",
    "شراكة", "مشروع جديد", "تغيير الإدارة", "تعيين مدير", "استقالة",
]

# كلمات "ضجيج" شائعة تدل على محتوى روتيني عام بدون قيمة تحليلية مباشرة
NOISE_KEYWORDS = [
    "سعر صرف الدولار", "أسعار الدولار", "صرف الدولار", "الدولار في بغداد",
    "الدولار في اربيل", "إغلاق التداول.. ارتفاع", "مزاد العملة",
]

# كلمات تدل على أسواق أو دول أخرى غير العراق (تُستبعد إلا لو فيها اسم
# شركة عراقية معروفة بنفس الوقت)
FOREIGN_MARKET_KEYWORDS = [
    "الكويت", "الإمارات", "الامارات", "السعودية", "قطر", "البحرين",
    "مصر", "الأردن", "الاردن", "بورصة دبي", "سوق أبوظبي", "الرياض",
]

TRUSTED_SOURCES = [
    "رويترز", "reuters", "cnbc", "الجزيرة", "العربية",
    "مستقل", "حكومي", "الوكالة", "واع", "shafaq", "ina",
]


def normalize_title(title: str) -> str:
    """توحيد شكل العنوان لتسهيل كشف التكرار (إزالة مسافات زائدة واسم المصدر اللاحق)"""
    if not title:
        return ""
    cleaned = title.split(" - ")[0].strip()
    return " ".join(cleaned.split()).lower()


def is_relevant_news(title: str) -> bool:
    """
    فلتر الرفض الصارم. يرجع True فقط إذا كان الخبر يحمل قيمة تحليلية
    حقيقية لسهم عراقي، ويرفض الضجيج والأخبار البعيدة.

    منطق القرار بالترتيب:
    1. إذا ذُكرت شركة عراقية معروفة صراحة بالعنوان -> يُقبل دائماً
    2. إذا كان العنوان عن سوق خليجي/عربي آخر (بدون ذكر شركة عراقية) -> يُرفض
    3. إذا كان العنوان "ضجيج" معروف (صرف الدولار اليومي..) -> يُرفض
    4. إذا احتوى كلمة تأثير حقيقي (قرار، قضية، توسع...) -> يُقبل
    5. غير ذلك -> يُرفض (احتياطاً، لتفادي التسرب العشوائي)
    """
    if not title:
        return False

    # 1) شركة عراقية معروفة مذكورة صراحة -> قبول فوري
    if any(name in title for name in COMPANY_NAME_HINTS):
        return True

    # 2) سوق أجنبي مذكور بدون شركة عراقية -> رفض
    if any(country in title for country in FOREIGN_MARKET_KEYWORDS):
        return False

    # 3) ضجيج معروف -> رفض
    if any(noise in title for noise in NOISE_KEYWORDS):
        return False

    # 4) كلمة تأثير حقيقي موجودة + العنوان يذكر السوق/العراق بشكل ما -> قبول
    has_impact_word = any(kw in title for kw in IMPACT_KEYWORDS)
    if has_impact_word:
        return True

    # 5) افتراضياً: رفض (نفضّل تفويت خبر حدّي على قبول ضجيج)
    return False


def fetch_raw_articles():
    print("🔄 جاري تشمشم الويب عبر رادار البحث الحُر (Google News)...")
    all_articles = []
    seen_urls = set()

    for kw in KEYWORDS:
        encoded_kw = urllib.parse.quote(kw)
        rss_url = f"https://news.google.com/rss/search?q={encoded_kw}&hl=ar&gl=IQ&ceid=IQ:ar"

        try:
            resp = requests.get(rss_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                print(f"⚠️ فشل الطلب لـ ({kw}): كود {resp.status_code}")
                continue

            soup = BeautifulSoup(resp.content, "xml")
            items = soup.find_all("item")

            for item in items:
                title = item.title.text if item.title else ""
                link = item.link.text if item.link else ""
                pub_date = item.pubDate.text if item.pubDate else ""
                source = item.source.text if item.source else "مصدر عالمي"

                try:
                    dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z")
                    date_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    date_str = datetime.now().strftime("%Y-%m-%d")

                if link in seen_urls:
                    continue
                seen_urls.add(link)

                all_articles.append({
                    "title": title,
                    "url": link,
                    "date": date_str,
                    "source": source,
                })

        except Exception as e:
            print(f"⚠️ تحذير أثناء البحث عن ({kw}): {e}")

    all_articles.sort(key=lambda x: x["date"], reverse=True)
    return all_articles


def load_existing_data():
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("general_news", [])
                data.setdefault("social_signals", [])
                return data
        except Exception as e:
            print(f"⚠️ تعذّر قراءة الملف القديم ({e})، سيتم البدء بملف جديد.")
    return {"general_news": [], "social_signals": []}


def main():
    existing_data = load_existing_data()

    existing_titles = {
        normalize_title(item.get("title", ""))
        for item in existing_data["general_news"]
    }

    raw_articles = fetch_raw_articles()
    print(f"📥 إجمالي الأخبار الخام المجلوبة قبل الفلترة: {len(raw_articles)}")

    new_general_news = []
    seen_this_run = set()
    rejected_count = 0

    for art in raw_articles:
        norm_title = normalize_title(art["title"])
        if not norm_title:
            continue
        if norm_title in existing_titles or norm_title in seen_this_run:
            continue

        # تطبيق فلتر الرفض الصارم قبل القبول
        if not is_relevant_news(art["title"]):
            rejected_count += 1
            continue

        seen_this_run.add(norm_title)
        new_general_news.append({
            "date": art["date"],
            "source": art["source"],
            "title": art["title"],
            "content": (
                f"تقرير إخباري خارجي تم رصده من منصة {art['source']} "
                f"يتحدث عن: {art['title']}."
            ),
        })

        if len(new_general_news) >= MAX_NEW_ARTICLES_PER_RUN:
            break

    print(f"🚫 أخبار مرفوضة (ضجيج/بعيدة عن الهدف): {rejected_count}")
    print(f"✅ أخبار مقبولة (مرتبطة فعلياً بالسهم): {len(new_general_news)}")

    combined_general_news = new_general_news + existing_data["general_news"]

    result = {
        "general_news": combined_general_news,
        "social_signals": existing_data["social_signals"],
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(
        f"🎉 نجاح! أُضيف {len(new_general_news)} خبر جديد. "
        f"الإجمالي التراكمي الآن: {len(combined_general_news)} خبر في general_news."
    )


if __name__ == "__main__":
    main()
