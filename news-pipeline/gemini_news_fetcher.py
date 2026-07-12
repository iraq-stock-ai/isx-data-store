import os
import sys
import json
import urllib.parse
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# مهم جداً: Google News يحظر الطلبات بدون User-Agent يشبه متصفح حقيقي (403)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

OUTPUT_FILE = "isx_news.json"

# كلمات مفتاحية موسّعة: مصطلحات سوقية عامة + أسماء شركات مدرجة معروفة
KEYWORDS = [
    "سوق العراق للأوراق المالية",
    "أسهم العراق",
    "بورصة بغداد",
    "مؤشر ISX60",
    "مصرف بغداد اسهم",
    "مصرف المنصور اسهم",
    "آسيا سيل",
    "الشرق الاوسط للاستثمار المالي",
    "بورصة العراق تداول",
]

# حد أقصى للأخبار الجديدة بكل تشغيلة (بعد التصفية)
MAX_NEW_ARTICLES_PER_RUN = 30

TRUSTED_SOURCES = [
    "رويترز", "reuters", "cnbc", "الجزيرة", "العربية",
    "مستقل", "حكومي", "الوكالة", "واع", "shafaq", "ina",
]


def normalize_title(title: str) -> str:
    """توحيد شكل العنوان لتسهيل كشف التكرار (إزالة مسافات زائدة واسم المصدر اللاحق)"""
    if not title:
        return ""
    # Google News يضيف غالباً "- اسم المصدر" بآخر العنوان
    cleaned = title.split(" - ")[0].strip()
    return " ".join(cleaned.split()).lower()


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
    """تحميل الملف الحالي إن وجد، لدمج البيانات الجديدة معه"""
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

    # مجموعة العناوين الموحّدة الموجودة مسبقاً بالملف، لمنع التكرار عبر التشغيلات
    existing_titles = {
        normalize_title(item.get("title", ""))
        for item in existing_data["general_news"]
    }

    raw_articles = fetch_raw_articles()

    new_general_news = []
    seen_this_run = set()

    for art in raw_articles:
        norm_title = normalize_title(art["title"])
        if not norm_title:
            continue
        # تجاهل لو الخبر موجود سابقاً بالملف، أو تكرر بنفس التشغيلة الحالية
        if norm_title in existing_titles or norm_title in seen_this_run:
            continue

        seen_this_run.add(norm_title)
        
        # التعديل هنا: تم إزالة الجزء الخاص بالروابط لتقليل حجم البيانات والذاكرة
        new_general_news.append({
            "date": art["date"],
            "source": art["source"],
            "title": art["title"],
            "content": f"تقرير إخباري خارجي تم رصده من منصة {art['source']} يتحدث عن: {art['title']}.",
        })

        if len(new_general_news) >= MAX_NEW_ARTICLES_PER_RUN:
            break

    # الدمج التراكمي: الجديد يُضاف بأول القائمة (الأحدث أولاً)
    combined_general_news = new_general_news + existing_data["general_news"]

    # social_signals متروك مؤقتاً كما هو موجود بالملف (بدون تحديث حالياً)
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
