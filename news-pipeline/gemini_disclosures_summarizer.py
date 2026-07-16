import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "http://www.isx-iq.net/isxportal/portal"
STORY_LIST_URL = f"{BASE_URL}/storyList.html"
STORY_DETAILS_URL = f"{BASE_URL}/storyDetails.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-IQ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": STORY_LIST_URL,
}

MODEL_NAME = "gemini-2.0-flash-lite"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"
API_KEY = os.environ.get("GEMINI_DISCLOSURES_API_KEY")

MAX_LIST_PAGES = 5


def clean_text(txt: str) -> str:
    if txt is None:
        return ""
    txt = str(txt).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", txt).strip()


def fetch_disclosure_list(max_pages: int = MAX_LIST_PAGES) -> list:
    """يجلب قائمة الإفصاحات (activeTab=0) من isx-iq.net، الأحدث أولاً."""
    items = []
    for page in range(1, max_pages + 1):
        params = {"activeTab": 0, "page": page}
        try:
            resp = requests.get(STORY_LIST_URL, headers=HEADERS, params=params, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"  تحذير: فشل تحميل صفحة {page}: {e}")
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
            full_url = f"{STORY_DETAILS_URL}?storyId={story_id}&type=1"
            page_items.append({"story_id": story_id, "title": title, "url": full_url})

        print(f"  صفحة {page}: {len(page_items)} إفصاح.")
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


def fetch_story_raw_content(url: str) -> dict:
    """يستخرج العنوان والنص الخام المتوفر بصفحة HTML، ورابط PDF المرفق إن وجد."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"    تحذير: فشل فتح {url}: {e}")
        return {}

    soup = BeautifulSoup(resp.content, "html.parser")
    page_text = clean_text(soup.get_text())

    related_symbols = []
    for link in soup.find_all("a", href=re.compile(r"companyCode=([A-Za-z0-9]+)")):
        match = re.search(r"companyCode=([A-Za-z0-9]+)", link.get("href", ""))
        if match and match.group(1) not in related_symbols:
            related_symbols.append(match.group(1))

    date_match = re.search(r"(\d{2}/\d{2}/\d{4})", page_text)
    date_str = date_match.group(1) if date_match else None

    pdf_link = None
    pdf_tag = soup.find("a", href=re.compile(r"\.pdf$", re.IGNORECASE))
    if pdf_tag:
        pdf_href = pdf_tag.get("href", "")
        pdf_link = pdf_href if pdf_href.startswith("http") else f"http://www.isx-iq.net{pdf_href}"

    return {
        "raw_text": page_text[:3000],
        "date": date_str,
        "related_symbols": related_symbols,
        "pdf_url": pdf_link,
    }


def summarize_with_gemini(title: str, raw_text: str, pdf_url: str, max_retries: int = 3) -> dict:
    """يرسل النص الخام لـ Gemini ويطلب تلخيصاً منظماً بصيغة JSON صارمة."""
    if not API_KEY:
        print("❌ خطأ: لم يتم العثور على متغير البيئة GEMINI_DISCLOSURES_API_KEY.")
        return None

    pdf_note = (
        f"\nرابط ملف PDF مرفق بهذا الإفصاح (قد يحتوي تفاصيل إضافية): {pdf_url}"
        if pdf_url else ""
    )

    prompt_text = (
        "أنت محلل مالي متخصص بسوق العراق للأوراق المالية. فيما يلي نص إفصاح "
        f"رسمي بعنوان: \"{title}\".\n\n"
        f"النص المتوفر من صفحة الإفصاح:\n{raw_text}{pdf_note}\n\n"
        "استخرج المعلومات التالية إن وُجدت صراحة بالنص، والتزم بعدم اختلاق "
        "أي رقم أو معلومة غير مذكورة صراحة (اترك الحقل null إن لم تجده):\n\n"
        "أجب حصراً بصيغة JSON نقية 100% وبدون أي علامات markdown، بالهيكل التالي:\n"
        "{\n"
        '  "disclosure_type": "توزيع_ارباح | زيادة_راس_المال | قوائم_مالية | '
        'اجتماع_هيئة_عامة | قرار_رفض_او_عدم_موافقة | حركة_تداول_خاصة | اخرى",\n'
        '  "company_name": "اسم الشركة كما ورد بالنص أو null",\n'
        '  "raas_al_mal": "رقم رأس المال بالدينار العراقي أو null",\n'
        '  "net_profit": "صافي الربح بالدينار العراقي أو null",\n'
        '  "total_debts": "إجمالي الديون/المطلوبات بالدينار العراقي أو null",\n'
        '  "dividend_per_share": "قيمة التوزيع للسهم الواحد بالدينار أو null",\n'
        '  "dividend_percentage": "نسبة توزيع الأرباح كنص أو null",\n'
        '  "dividend_date": "تاريخ توزيع/استحقاق الأرباح إن ذُكر أو null",\n'
        '  "summary": "ملخص من جملتين إلى ثلاث جمل بالعربية الفصحى الرصينة"\n'
        "}"
    )

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    delay = 5
    for attempt in range(1, max_retries + 1):
        try:
            print(f"    🔄 إرسال لـ Gemini... محاولة {attempt}/{max_retries}")
            response = requests.post(
                f"{GEMINI_API_URL}?key={API_KEY}", headers=headers, json=payload, timeout=30
            )

            if response.status_code == 200:
                res_json = response.json()
                ai_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
                ai_text = ai_text.strip().removeprefix("```json").removesuffix("```").strip()
                return json.loads(ai_text)

            elif response.status_code == 429:
                print(f"    ⏳ (429) تهدئة {delay} ثوانٍ...")
                time.sleep(delay)
                delay *= 2
                continue
            else:
                print(f"    ❌ فشل سيرفر Gemini: كود {response.status_code}")
                return None
        except Exception as e:
            print(f"    ❌ خطأ اتصال/تحليل: {e}")
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
    parser = argparse.ArgumentParser(description="تلخيص إفصاح واحد جديد عبر Gemini لكل تشغيلة")
    parser.add_argument("--existing", default="isx_disclosures.json")
    parser.add_argument("--output", default="isx_disclosures.json")
    args = parser.parse_args()

    data = load_existing(args.existing)
    known_ids = {item["id"] for item in data["official_disclosures"] if "id" in item}

    print("جاري فحص قائمة الإفصاحات...")
    listing = fetch_disclosure_list()

    new_items = [item for item in listing if item["story_id"] not in known_ids]

    if not new_items:
        print("✅ لا يوجد إفصاح جديد. الخروج بدون استدعاء Gemini.")
        return

    # نأخذ أقدم إفصاح جديد (آخر عنصر بالقائمة المرتبة من الأحدث للأقدم)
    # لمعالجتها بالترتيب الزمني الصحيح، واحداً في كل تشغيلة
    target = new_items[-1]
    print(f"\n📌 معالجة الإفصاح: {target['title'][:70]}")
    print(f"   (متبقٍ {len(new_items) - 1} إفصاح جديد آخر بعد هذه التشغيلة)")

    detail = fetch_story_raw_content(target["url"])
    if not detail:
        print("❌ فشل استخراج محتوى الإفصاح. سيُعاد المحاولة بالتشغيلة القادمة.")
        return

    summary = summarize_with_gemini(
        target["title"], detail.get("raw_text", ""), detail.get("pdf_url")
    )

    if not summary:
        print("⚠️ فشل التلخيص عبر Gemini. سيُعاد المحاولة بالتشغيلة القادمة.")
        return

    record = {
        "id": target["story_id"],
        "title": target["title"],
        "source": "سوق العراق للأوراق المالية (isx-iq.net) - ملخص Gemini",
        "url": target["url"],
        "pdf_url": detail.get("pdf_url"),
        "date": detail.get("date") or datetime.now().strftime("%d/%m/%Y"),
        "related_symbols": detail.get("related_symbols", []),
        **summary,
    }

    data["official_disclosures"].insert(0, record)
    save_json(data, args.output)

    print(f"\n✅ تم تلخيص وحفظ الإفصاح بنجاح. الإجمالي الآن: {len(data['official_disclosures'])}")


if __name__ == "__main__":
    main()
