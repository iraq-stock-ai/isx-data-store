import argparse
import json
import os
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

BASE_URL = "http://www.isx-iq.net/isxportal/portal"
HOME_URL = f"{BASE_URL}/homePage.html"
STORY_LIST_URL = f"{BASE_URL}/storyList.html"
STORY_DETAILS_URL = f"{BASE_URL}/storyDetails.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-IQ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": HOME_URL,
}

MAX_LIST_PAGES = 5

# ==============================================================================
# تصنيف نوع الإفصاح بناءً على كلمات مفتاحية شائعة بعناوين isx-iq.net الفعلية
# مرتبة بحيث تُفحص الأنماط الأكثر تحديداً أولاً
# ==============================================================================
DISCLOSURE_TYPE_PATTERNS = [
    ("توزيع_ارباح", ["توزيع", "أرباح", "ارباح"]),
    ("زيادة_راس_المال", ["زيادة راس المال", "زيادة رأس المال", "زياده راس مال"]),
    ("قوائم_مالية", ["البيانات المالية", "القوائم المالية", "بيانات مالية"]),
    ("اجتماع_هيئة_عامة", ["اجتماع الهيئة العامة", "إجتماع الهيئة العامة", "الجمعية العمومية"]),
    ("قرار_رفض_او_عدم_موافقة", ["عدم الموافقة", "رفض"]),
    ("حركة_تداول_خاصة", ["أمر متقابل", "امر متقابل", "تنفيذ أمر"]),
    ("تعليق_او_ايقاف_تداول", ["ايقاف التداول", "إيقاف التداول", "تعليق التداول"]),
]


def clean_text(txt: str) -> str:
    if txt is None:
        return ""
    txt = str(txt).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", txt).strip()


def classify_disclosure(title: str) -> str:
    """يصنف نوع الإفصاح بمطابقة كلمات مفتاحية بعنوانه. يرجع 'اخرى' إذا لم يطابق أي نمط."""
    if not title:
        return "اخرى"
    for disclosure_type, keywords in DISCLOSURE_TYPE_PATTERNS:
        if any(kw in title for kw in keywords):
            return disclosure_type
    return "اخرى"


def fetch_story_list(story_type: int, max_pages: int = MAX_LIST_PAGES) -> list:
    """
    يجلب قائمة الإفصاحات (type=1) أو أخبار السوق (type=2) من صفحة القائمة.
    """
    items = []
    active_tab = 0 if story_type == 1 else 1

    for page in range(1, max_pages + 1):
        params = {"activeTab": active_tab, "page": page}
        try:
            resp = requests.get(STORY_LIST_URL, headers=HEADERS, params=params, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"  تحذير: فشل تحميل صفحة {page} (النوع {story_type}): {e}")
            break

        soup = BeautifulSoup(resp.content, "html.parser")
        links = soup.find_all("a", href=re.compile(r"storyDetails\.html\?storyId=\d+"))

        page_items = []
        for link in links:
            href = link.get("href", "")
            title = clean_text(link.get_text())
            if not title or not href:
                continue

            story_id_match = re.search(r"storyId=(\d+)", href)
            if not story_id_match:
                continue
            story_id = story_id_match.group(1)

            full_url = f"{STORY_DETAILS_URL}?storyId={story_id}&type={story_type}"
            page_items.append({"story_id": story_id, "title": title, "url": full_url})

        print(f"  صفحة {page} (النوع {story_type}): {len(page_items)} عنصر.")
        items.extend(page_items)
        if not page_items:
            break
        time.sleep(1)

    seen = set()
    unique_items = []
    for item in items:
        if item["story_id"] not in seen:
            seen.add(item["story_id"])
            unique_items.append(item)
    return unique_items


def extract_pdf_text(pdf_url: str) -> dict:
    """
    يحمّل ملف PDF ويستخرج نصه. يرجع dict فيه النص وحالة الاستخراج،
    بدل ما يفشل بصمت لو كان الملف صورة ممسوحة أو غير قابل للقراءة.
    """
    if not PDF_SUPPORT:
        return {"text": "", "status": "pdfplumber_not_installed"}

    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        return {"text": "", "status": f"download_failed: {e}"}

    tmp_path = "/tmp/_isx_temp_disclosure.pdf"
    try:
        with open(tmp_path, "wb") as f:
            f.write(resp.content)

        extracted_pages = []
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    extracted_pages.append(page_text)

        full_text = clean_text(" ".join(extracted_pages))

        if not full_text or len(full_text) < 10:
            # النص فاضي أو قصير جداً - على الأرجح PDF صورة ممسوحة (scanned)
            return {"text": "", "status": "needs_manual_review_possibly_scanned"}

        return {"text": full_text, "status": "success"}

    except Exception as e:
        return {"text": "", "status": f"extraction_failed: {e}"}
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def fetch_story_detail(url: str, story_type: int) -> dict:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"    تحذير: فشل فتح {url}: {e}")
        return {}

    soup = BeautifulSoup(resp.content, "html.parser")

    # استخراج رمز الشركة مباشرة من رابط "شركات ذات صلة" - أدق من أي مطابقة نصية
    related_symbols = []
    for link in soup.find_all("a", href=re.compile(r"companyCode=([A-Za-z0-9]+)")):
        match = re.search(r"companyCode=([A-Za-z0-9]+)", link.get("href", ""))
        if match:
            symbol = match.group(1)
            if symbol not in related_symbols:
                related_symbols.append(symbol)

    # استخراج تاريخ الإفصاح (صيغة يوم/شهر/سنة)
    page_text = clean_text(soup.get_text())
    date_match = re.search(r"(\d{2}/\d{2}/\d{4})", page_text)
    date_str = date_match.group(1) if date_match else None

    # البحث عن رابط PDF المرفق
    pdf_link = None
    pdf_tag = soup.find("a", href=re.compile(r"\.pdf$", re.IGNORECASE))
    if pdf_tag:
        pdf_href = pdf_tag.get("href", "")
        pdf_link = pdf_href if pdf_href.startswith("http") else f"http://www.isx-iq.net{pdf_href}"

    result = {
        "date": date_str,
        "related_symbols": related_symbols,
        "pdf_url": pdf_link,
        "content": "",
        "pdf_extraction_status": "no_pdf_attached",
    }

    if pdf_link:
        pdf_result = extract_pdf_text(pdf_link)
        result["content"] = pdf_result["text"]
        result["pdf_extraction_status"] = pdf_result["status"]

    return result


def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {"official_disclosures": [], "market_news": []}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            return {"official_disclosures": [], "market_news": []}
    data.setdefault("official_disclosures", [])
    data.setdefault("market_news", [])
    return data


def save_json(data: dict, path: str):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def process_story_type(story_type: int, known_ids: set, label: str) -> list:
    """يجلب ويعالج كل القصص من نوع معين (إفصاحات أو أخبار سوق)."""
    print(f"\nجاري فحص: {label}")
    listing = fetch_story_list(story_type)
    new_items = [item for item in listing if item["story_id"] not in known_ids]
    print(f"{label} جديدة مكتشفة: {len(new_items)}")

    records = []
    for item in new_items:
        print(f"  [{len(records) + 1}/{len(new_items)}] {item['title'][:60]}...")
        detail = fetch_story_detail(item["url"], story_type)
        if not detail:
            continue

        symbols = detail.get("related_symbols", [])
        if symbols:
            print(f"      ↳ شركات مرتبطة: {', '.join(symbols)}")

        pdf_status = detail.get("pdf_extraction_status", "")
        if pdf_status == "success":
            print(f"      ↳ تم استخراج نص PDF بنجاح ({len(detail['content'])} حرف)")
        elif pdf_status == "needs_manual_review_possibly_scanned":
            print(f"      ⚠️ PDF يحتاج مراجعة يدوية (ربما صورة ممسوحة)")
        elif pdf_status.startswith("download_failed") or pdf_status.startswith("extraction_failed"):
            print(f"      ⚠️ مشكلة بمعالجة PDF: {pdf_status}")

        record = {
            "id": item["story_id"],
            "title": item["title"],
            "disclosure_type": classify_disclosure(item["title"]) if story_type == 1 else None,
            "content": detail.get("content", ""),
            "source": "سوق العراق للأوراق المالية (isx-iq.net)",
            "url": item["url"],
            "pdf_url": detail.get("pdf_url"),
            "pdf_extraction_status": pdf_status,
            "date": detail.get("date") or datetime.now().strftime("%d/%m/%Y"),
            "related_symbols": symbols,
        }
        records.append(record)
        time.sleep(1.5)

    return records


def main():
    parser = argparse.ArgumentParser(description="جلب إفصاحات وأخبار سوق العراق للأوراق المالية من isx-iq.net")
    parser.add_argument("--existing", default="isx_disclosures.json")
    parser.add_argument("--output", default="isx_disclosures.json")
    args = parser.parse_args()

    if not PDF_SUPPORT:
        print("⚠️ تحذير: مكتبة pdfplumber غير مثبتة. لن يتم استخراج نصوص PDF.")
        print("   ثبّتها بـ: pip install pdfplumber")

    data = load_existing(args.existing)
    known_ids = {
        item["id"] for item in data["official_disclosures"] + data["market_news"]
        if "id" in item
    }

    new_disclosures = process_story_type(1, known_ids, "الإفصاحات الرسمية")
    new_market_news = process_story_type(2, known_ids, "أخبار السوق")

    data["official_disclosures"] = new_disclosures + data["official_disclosures"]
    data["market_news"] = new_market_news + data["market_news"]

    total_added = len(new_disclosures) + len(new_market_news)
    if total_added == 0:
        print("\nلا توجد إفصاحات أو أخبار جديدة.")
        return

    save_json(data, args.output)
    print(
        f"\n✅ تم بنجاح. أُضيف {len(new_disclosures)} إفصاح جديد و"
        f"{len(new_market_news)} خبر سوق جديد."
    )


if __name__ == "__main__":
    main()
