import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.isc.gov.iq"
LIST_URL = f"{BASE_URL}/en/news?category=7"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# نفس جدول الشركات المرجعي بالضبط من القسم 2️⃣.2 بوثيقة V1 — يجب إبقاؤه
# متطابقاً حرفياً مع الوثيقة. مختصر هنا بمتغير منفصل لسهولة الصيانة؛
# في نسخة الإنتاج الفعلية يُفضّل قراءته من ملف companies.json مشترك
# بدل تكراره داخل هذا السكريبت.
COMPANY_NAMES_TO_SYMBOLS = {
    "الريباس للدواجن والاعلاف": "AREB", "اسياسيل للاتصالات": "TASC",
    "الالبسة الجاهزة": "IRMC", "الامين للاستثمارات العقارية": "SAEI",
    "الامين للاستثمار المالي": "VAMF", "الامين للتأمين": "NAME",
    "الاهلية للانتاج الزراعي": "AAHP", "الاهلية للتأمين": "NAHF",
    "الباتك للاستثمار المالي": "VBAT", "البادية للنقل العام": "SBAG",
    "الحديثة للانتاج الزراعي والحيواني": "AMAP", "الحمراء للتأمين": "NHAM",
    "الخاتم للاتصالات": "TZNI", "الخازر لانتاج المواد الانشائية": "IKHC",
    "الخليج للتأمين واعادة التأمين": "NGIR", "الخياطة الحديثة": "IMOS",
    "الخير للاستثمار المالي": "VKHF", "الزوراء للاستثمار المالي": "VZAF",
    "مصرف الائتمان العراقي": "BROI", "مصرف الاستثمار العراقي": "BIBI",
    "مصرف بغداد": "BBOB", "المصرف الاهلي العراقي": "BNOI",
    "المصرف التجاري العراقي الاسلامي": "BCOI",
    # القائمة الكاملة (106 شركة) موجودة بالقسم 2️⃣.2 من V1 — يجب نسخها
    # كاملة هنا عند التنفيذ الفعلي. اختصرتها هنا فقط لتوضيح الآلية.
}

MAX_PAGES = 5  # يكفي لالتقاط أي إفصاح جديد؛ الأرشيف الكامل (181 صفحة) لا يُفحص يومياً


def clean_text(txt: str) -> str:
    if txt is None:
        return ""
    txt = str(txt).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", txt).strip()


def fetch_disclosure_list(max_pages: int = MAX_PAGES) -> list:
    """يجلب قائمة (عنوان، رابط، تاريخ) من صفحات الأرشيف الأولى فقط."""
    items = []
    for page in range(1, max_pages + 1):
        url = LIST_URL if page == 1 else f"{LIST_URL}&page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"  تحذير: فشل تحميل صفحة {page}: {e}")
            break

        soup = BeautifulSoup(resp.content, "html.parser")
        # كل إفصاح يظهر كرابط لصفحة تفاصيل بنمط /en/news/<id>
        links = soup.find_all("a", href=re.compile(r"/en/news/\d+$"))

        page_items = []
        for link in links:
            href = link.get("href", "")
            title = clean_text(link.get_text())
            if not title or not href:
                continue
            full_url = href if href.startswith("http") else BASE_URL + href
            page_items.append({"title": title, "url": full_url})

        print(f"  صفحة {page}: {len(page_items)} إفصاح.")
        items.extend(page_items)
        if not page_items:
            break
        time.sleep(1)

    # إزالة تكرار داخل نفس عملية الجلب (نفس الرابط قد يظهر أكثر من مرة بالصفحة)
    seen = set()
    unique_items = []
    for item in items:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique_items.append(item)
    return unique_items


def match_company(text: str):
    """يبحث عن أول اسم شركة من القائمة المرجعية يظهر داخل النص، ويرجع رمزها."""
    for name, symbol in COMPANY_NAMES_TO_SYMBOLS.items():
        if name in text:
            return symbol
    return None


def fetch_disclosure_detail(url: str) -> dict:
    """يفتح صفحة إفصاح واحدة، يستخرج المحتوى الكامل وتاريخ النشر ويحاول مطابقة الشركة."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"    تحذير: فشل فتح {url}: {e}")
        return {}

    soup = BeautifulSoup(resp.content, "html.parser")
    full_text = clean_text(soup.get_text())

    date_match = re.search(r"(\d{2}/\d{2}/\d{4})", full_text)
    date_str = date_match.group(1) if date_match else None

    symbol = match_company(full_text)

    return {
        "content": full_text[:1000],  # لا حاجة للنص الكامل الطويل بكل التنقل بالصفحة
        "date": date_str,
        "related_symbols": [symbol] if symbol else [],
    }


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


def main():
    parser = argparse.ArgumentParser(description="جلب الإفصاحات الرسمية من isc.gov.iq")
    parser.add_argument("--existing", default="isx_news.json")
    parser.add_argument("--output", default="isx_news.json")
    args = parser.parse_args()

    data = load_existing(args.existing)
    known_urls = {item["url"] for item in data["official_disclosures"] if "url" in item}

    print(f"جاري فحص أرشيف الإفصاحات: {LIST_URL}")
    listing = fetch_disclosure_list()
    print(f"إجمالي الإفصاحات بالصفحات المفحوصة: {len(listing)}")

    new_items = [item for item in listing if item["url"] not in known_urls]
    print(f"إفصاحات جديدة (غير موجودة مسبقاً بالأرشيف المحلي): {len(new_items)}\n")

    added = 0
    for item in new_items:
        print(f"[{added + 1}/{len(new_items)}] جلب تفاصيل: {item['title'][:60]}...")
        detail = fetch_disclosure_detail(item["url"])
        if not detail:
            continue

        record = {
            "id": item["url"].rstrip("/").split("/")[-1],
            "title": item["title"],
            "content": detail.get("content", ""),
            "source": "Iraqi Securities Commission (isc.gov.iq)",
            "url": item["url"],
            "date": detail.get("date"),
            "related_symbols": detail.get("related_symbols", []),
            "sentiment": "Neutral",  # الإفصاحات الرسمية لا تُصنَّف عاطفياً، فقط تُعرض كوقائع
            "impact_score": "Medium",  # تصنيف افتراضي متحفظ؛ يمكن تحسينه لاحقاً بقواعد أدق
        }
        data["official_disclosures"].insert(0, record)
        added += 1
        time.sleep(1)  # لطف مع الخادم

    if added == 0:
        print("لا توجد إفصاحات جديدة. لن يُعدَّل الملف.")
        sys.exit(0)

    save_json(data, args.output)
    print(f"\n✅ تم بنجاح: أُضيف {added} إفصاح جديد.")
    print(f"   إجمالي الإفصاحات المحفوظة: {len(data['official_disclosures'])}")


if __name__ == "__main__":
    main()
