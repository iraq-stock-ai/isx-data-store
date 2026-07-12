import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.isc.gov.iq"
LIST_URL = f"{BASE_URL}/news?category=7"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-IQ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE_URL,
}

MAX_PAGES = 5

# ==============================================================================
# قاموس الشركات المدرجة في سوق العراق للأوراق المالية (اسم -> رمز التداول)
# ملاحظة: هذه القائمة قد لا تكون شاملة لكل الشركات المدرجة، وقد تُضاف
# شركات جديدة لاحقاً بالسوق. لتحديث القائمة، أضف السطر هنا مباشرة.
# ==============================================================================
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
    "الشرق الاوسط لانتاج وتسويق الاسماك": "AMEF",
    "الشركة العراقية لانتاج وتسويق اللحوم": "AIPM",
    "الصنائع الكيمياوية العصرية": "IMCI", "الصناعات الالكترونية": "IELI",
    "الصناعات الخفيفة": "ITLI", "الصناعات المعدنية والدراجات": "IMIB",
    "العراقية لانتاج البذور": "AISP", "العراقية لانتاج وتسويق التمور": "IIDP",
    "العراقية لصناعات الكارتون": "IICM", "العراقية للاعمال الهندسية": "IIEW",
    "العراقية للسجاد والمفروشات": "IITC", "العراقية للمنتجات الزراعية": "AIRP",
    "العراقية للنقل البري": "SILT", "العراقية للنقل النفطية والبضائع": "SIGT",
    "الفلوجة لانتاج المواد الانشائية": "IFCM", "الكندي لانتاج اللقاحات": "IKLV",
    "المدينة السياحية في سد الموصل": "HTVM", "المصرف الاهلي العراقي": "BNOI",
    "المصرف التجاري العراقي الاسلامي": "BCOI", "المصرف الدولي الاسلامي": "BINT",
    "المصرف العراقي الاسلامي": "BIIB", "المصرف المتحد": "BUND",
    "المصرف الوطني الاسلامي": "BNAI", "المعمورة للاستثمارات العقارية": "SMRI",
    "المنصور للصناعات الدوائية": "IMAP", "الموصل لمدن الالعاب": "SMOF",
    "النخبة للمقاولات العامة والاستثمارات": "SNUC", "الهلال الصناعية": "IHLI",
    "الوئام للاستثمار المالي": "VWIF", "الوطنية لصناعات الاثاث المنزلي": "IHFI",
    "الوطنية للاستثمارات السياحية": "HNTI",
    "الوطنية للصناعات الكيمياوية والبلاستيكية": "INCP",
    "بابل للانتاج الحيواني والنباتي": "ABAP",
    "بغداد العراق للنقل العام والاستثمارات العقارية": "SBPT",
    "بغداد لصناعة مواد التغليف": "IBPM", "بغداد للمشروبات الغازية": "IBSD",
    "بين النهرين للاستثمار": "VMES", "دار السلام للتأمين": "NDSA",
    "مصرف زين العراق الاسلامي للاستثمار والتمويل": "BZII",
    "مصرف سومر التجاري": "BSUC", "مصرف عبر العراق للاستثمار": "BTRI",
    "مصرف كوردستان الدولي": "BKUI",
    "مصرف نور العراق الاسلامي للاستثمار والتمويل": "BINI",
    "مصرف الوركاء للاستثمار والتمويل": "BWAI", "مصرف ايلاف الاسلامي": "BELF",
    "مصرف بابل": "BBAY", "مصرف بغداد": "BBOB",
    "مصرف جيهان للاستثمار الاسلامي والتمويل": "BCIH",
    "مصرف حمورابي التجاري": "BHAM",
    "مصرف القابض الاسلامي للتمويل والاستثمار": "BQAB",
    "مصرف القرطاس الاسلامي للاستثمار والتمويل": "BQUR",
    "مصرف المال الاسلامي للاستثمار": "BMAL",
    "مصرف المستشار الاسلامي للاستثمار والتمويل": "BMUI",
    "مصرف المشرق العربي الاسلامي للاستثمار": "BAMS", "مصرف المنصور": "BMNS",
    "مصرف الموصل للتنمية والاستثمار": "BMFI", "مصرف السنام الاسلامي": "BSAN",
    "مصرف الشرق الاوسط للاستثمار": "BIME", "مصرف الشمال": "BNOR",
    "مصرف الطيف الاسلامي للاستثمار والتمويل": "BTIB",
    "مصرف العالم الاسلامي للاستثمار والتمويل": "BWOR",
    "مصرف العربية الاسلامي": "BAAI", "مصرف العطاء الاسلامي": "BLAD",
    "مصرف الاقليم للاستثمار والتمويل": "BRTB", "مصرف الانصاري الاسلامي": "BANS",
    "مصرف التنمية للاستثمار": "BIDB", "مصرف الثقة الدولي الاسلامي": "BTRU",
    "مصرف الجنوب الاسلامي للاستثمار والتنمية": "BJAB",
    "مصرف الخليج التجاري": "BGUC",
    "مصرف الراجح الاسلامي للاستثمار والتمويل": "BRAJ",
    "مصرف اربيل للاستثمار والتمويل": "BERI",
    "مصرف اسيا العراق الاسلامي للاستثمار والتمويل": "BAIB",
    "مصرف اشور الدولي": "BASH", "مصرف الائتمان العراقي": "BROI",
    "مصرف الاتحاد العراقي": "BUOI", "مصرف الاستثمار العراقي": "BIBI",
    "مصرف الاقتصاد للاستثمار": "BEFI", "فندق المنصور": "HMAN",
    "فندق بابل": "HBAY", "فندق بغداد": "HBAG", "فندق فلسطين": "HPAL",
    "مدينة العاب الكرخ السياحية": "SKTA",
    "مصرف امين العراق الاسلامي للاستثمار": "BAME",
    "شركة رحاب كربلاء للتجارة والمقاولات العامة": "HKAR",
    "صناعة المواد الانشائية الحديثة": "IMCM", "فنادق عشتار": "HISH",
    "فندق اشور": "HASH", "فندق السدير": "HSAD",
    # أسماء بديلة/مختصرة شائعة (alias) لتحسين نسبة المطابقة
    "خاتم": "TZNI", "آسيا سيل": "TASC",
}

# ترتيب الأسماء من الأطول للأقصر عشان لو فيه أي تداخل مستقبلي (اسم شركة جديدة
# يحتوي اسم شركة قديمة كجزء منه)، تنطبق الأسماء الأكثر تحديداً أولاً
SORTED_COMPANY_NAMES = sorted(
    COMPANY_NAMES_TO_SYMBOLS.keys(), key=len, reverse=True
)


def clean_text(txt: str) -> str:
    if txt is None:
        return ""
    txt = str(txt).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", txt).strip()


def fetch_disclosure_list(max_pages: int = MAX_PAGES) -> list:
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
        links = soup.find_all("a", href=re.compile(r"/news/\d+$"))

        page_items = []
        for link in links:
            href = link.get("href", "")
            title = clean_text(link.get_text())
            if not title or not href:
                continue

            full_url = href if href.startswith("http") else BASE_URL + href
            full_url = full_url.replace("/en/news/", "/news/")

            page_items.append({"title": title, "url": full_url})

        print(f"  صفحة {page}: {len(page_items)} إفصاح.")
        items.extend(page_items)
        if not page_items:
            break
        time.sleep(1)

    seen = set()
    unique_items = []
    for item in items:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique_items.append(item)
    return unique_items


def match_companies(text: str) -> list:
    """
    يرجع كل الشركات المطابقة بالنص (مو وحدة بس)، بترتيب من الأطول اسم للأقصر
    عشان نتجنب أي تطابق substring خاطئ لو انضافت شركات جديدة بأسماء متشابهة مستقبلاً.
    يرجع قائمة رموز فريدة (بدون تكرار) بترتيب ظهورها.
    """
    if not text:
        return []

    found_symbols = []
    seen_symbols = set()

    for name in SORTED_COMPANY_NAMES:
        if name in text:
            symbol = COMPANY_NAMES_TO_SYMBOLS[name]
            if symbol not in seen_symbols:
                seen_symbols.add(symbol)
                found_symbols.append(symbol)

    return found_symbols


def fetch_disclosure_detail(url: str) -> dict:
    try:
        url = url.replace("/en/news/", "/news/")
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"    تحذير: فشل فتح {url}: {e}")
        return {}

    soup = BeautifulSoup(resp.content, "html.parser")
    full_text = clean_text(soup.get_text())

    # نبحث عن التاريخ بالنص الكامل أولاً (نحتاج full_text عشان نطابق الشركات بدقة)
    date_match = re.search(r"(\d{2}/\d{2}/\d{4})", full_text)
    date_str = date_match.group(1) if date_match else None

    # نبحث بالنص الكامل أولاً، وإذا ما لقينا شي نبحث بالرابط كخطة احتياطية
    symbols = match_companies(full_text)
    if not symbols:
        symbols = match_companies(url)

    # مهم: نشيل التاريخ من النص قبل نحفظه كـ content، عشان ما يضل ملتصق
    # بأول كلمة من الخبر (مشكلة شائعة لأن full_text يجمع كل نص الصفحة بدون
    # فواصل واضحة بين العناصر). نضيف مسافة مكانه للتأكد من الفصل.
    content_text = full_text
    if date_match:
        content_text = (
            full_text[: date_match.start()] + " " + full_text[date_match.end() :]
        )
        content_text = clean_text(content_text)

    return {
        "content": content_text[:1000],
        "date": date_str,
        "related_symbols": symbols,
    }


def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {"official_disclosures": []}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            return {"official_disclosures": []}
    if "official_disclosures" not in data:
        data["official_disclosures"] = []
    return data


def save_json(data: dict, path: str):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def main():
    parser = argparse.ArgumentParser(description="جلب الإفصاحات الرسمية بالعربية")
    parser.add_argument("--existing", default="isx_disclosures.json")
    parser.add_argument("--output", default="isx_disclosures.json")
    args = parser.parse_args()

    data = load_existing(args.existing)
    known_urls = {item["url"] for item in data["official_disclosures"] if "url" in item}

    print(f"جاري فحص أرشيف الإفصاحات العربي: {LIST_URL}")
    print(f"عدد الشركات في قاموس المطابقة: {len(COMPANY_NAMES_TO_SYMBOLS)}")
    listing = fetch_disclosure_list()

    new_items = [item for item in listing if item["url"] not in known_urls]
    print(f"إفصاحات جديدة مكتشفة: {len(new_items)}\n")

    added = 0
    for item in new_items:
        print(f"[{added + 1}/{len(new_items)}] جلب تفاصيل بالعربية: {item['title'][:60]}...")
        detail = fetch_disclosure_detail(item["url"])
        if not detail:
            continue

        symbols = detail.get("related_symbols", [])
        if symbols:
            print(f"    ↳ شركات مرتبطة: {', '.join(symbols)}")
        else:
            print("    ↳ لم يتم ربط أي شركة بهذا الإفصاح")

        record = {
            "id": item["url"].rstrip("/").split("/")[-1],
            "title": item["title"],
            "content": detail.get("content", ""),
            "source": "هيئة الأوراق المالية العراقية (isc.gov.iq)",
            "url": item["url"],
            "date": detail.get("date") or datetime.now().strftime("%d/%m/%Y"),
            "related_symbols": symbols,
            "sentiment": "Neutral",
            "impact_score": "Medium",
        }
        data["official_disclosures"].insert(0, record)
        added += 1
        time.sleep(1.5)

    if added == 0:
        print("لا توجد إفصاحات جديدة للعربية.")
        return

    save_json(data, args.output)
    print(f"\n✅ تم بنجاح تحديث ملف الإفصاحات المفرز بالعربية الكاملة.")


if __name__ == "__main__":
    main()
