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

# إضافة توجيهات المتصفح لإجبار السيرفر على فهم أننا نريد لغة عربية
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-IQ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE_URL
}

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
    "المصرف التجاري العراقي الاسلامي": "BCOI", "خاتم": "TZNI", "آسيا سيل": "TASC"
}

MAX_PAGES = 5 

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
        # اقتناص الروابط حتى لو كانت تحتوي على en بسبب التحويل التلقائي للسيرفر
        links = soup.find_all("a", href=re.compile(r"/news/\d+$"))

        page_items = []
        for link in links:
            href = link.get("href", "")
            title = clean_text(link.get_text())
            if not title or not href:
                continue
            
            full_url = href if href.startswith("http") else BASE_URL + href
            # الكبسولة السحرية: حذف مسار الإنجليزي فوراً لفتح الصفحة العربية حصراً
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

def match_company(text: str):
    for name, symbol in COMPANY_NAMES_TO_SYMBOLS.items():
        if name in text:
            return symbol
    return None

def fetch_disclosure_detail(url: str) -> dict:
    try:
        # التأكيد مرة أخرى على تنظيف رابط التفاصيل قبل استدعائه
        url = url.replace("/en/news/", "/news/")
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"    تحذير: فشل فتح {url}: {e}")
        return {}

    soup = BeautifulSoup(resp.content, "html.parser")
    full_text = clean_text(soup.get_text())

    date_match = re.search(r"(\d{2}/\d{2}/\d{4})", full_text)
    date_str = date_match.group(1) if date_match else None

    symbol = match_company(full_text) or match_company(url)

    return {
        "content": full_text[:1000],  
        "date": date_str,
        "related_symbols": [symbol] if symbol else [],
    }

def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {"official_disclosures": []}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except:
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
    listing = fetch_disclosure_list()
    
    new_items = [item for item in listing if item["url"] not in known_urls]
    print(f"إفصاحات جديدة مكتشفة: {len(new_items)}\n")

    added = 0
    for item in new_items:
        print(f"[{added + 1}/{len(new_items)}] جلب تفاصيل بالعربية: {item['title'][:60]}...")
        detail = fetch_disclosure_detail(item["url"])
        if not detail:
            continue

        record = {
            "id": item["url"].rstrip("/").split("/")[-1],
            "title": item["title"],
            "content": detail.get("content", ""),
            "source": "هيئة الأوراق المالية العراقية (isc.gov.iq)",
            "url": item["url"],
            "date": detail.get("date") or datetime.now().strftime("%d/%m/%Y"),
            "related_symbols": detail.get("related_symbols", []),
            "sentiment": "Neutral",
            "impact_score": "Medium",
        }
        data["official_disclosures"].insert(0, record)
        added += 1
        time.sleep(1.5) # زيادة طفيفة لتفادي حظر السيرفر

    if added == 0:
        print("لا توجد إفصاحات جديدة للعربية.")
        return

    save_json(data, args.output)
    print(f"\n✅ تم بنجاح تحديث ملف الإفصاحات المفرز بالعربية الكاملة.")

if __name__ == "__main__":
    main()
