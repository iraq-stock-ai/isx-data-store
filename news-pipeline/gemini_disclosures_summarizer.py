import argparse
import json
import os
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "http://www.isx-iq.net/isxportal/portal"
STORY_DETAILS_URL = f"{BASE_URL}/storyDetails.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-IQ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": STORY_DETAILS_URL,
}

# كم رقم storyId نحاول بعد آخر رقم ناجح، قبل ما نستسلم بهذه التشغيلة
# (يغطي الفجوات الصغيرة بالترقيم، مثل الفجوة اللي لاحظناها بين 17754 و17755)
MAX_GAP_ATTEMPTS = 15

# سقف أعلى: لا نعالج أكثر من هذا العدد من الإفصاحات الجديدة بتشغيلة واحدة
MAX_NEW_PER_RUN = 20


def clean_text(txt: str) -> str:
    if txt is None:
        return ""
    txt = str(txt).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", txt).strip()


def fetch_story_details(story_id: str) -> dict:
    """
    يجلب تفاصيل إفصاح واحد. يرجع None إذا كانت الصفحة فارغة (رقم غير
    موجود أو فجوة بالترقيم)، أو dict فيه البيانات إذا نجح.
    """
    url = f"{STORY_DETAILS_URL}?storyId={story_id}&type=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"    تحذير: فشل فتح storyId={story_id}: {e}")
        return None

    soup = BeautifulSoup(resp.content, "html.parser")
    page_text = clean_text(soup.get_text())

    # علامة صفحة فارغة: تحتوي "This content is not available in english"
    if "not available in english" in page_text:
        return None

    # الصيغة الفعلية بالصفحة: التاريخ ثم الوقت، مثال "15/07/2026 10:40"
    date_match = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", page_text)
    if not date_match:
        # لا يوجد تاريخ بهذه الصيغة = على الأغلب صفحة فارغة/فجوة بالترقيم
        return None

    date_str = date_match.group(1)

    # العنوان الفعلي يبدأ مباشرة بعد "المؤشرات المالية" (آخر عنصر بالقائمة
    # الجانبية الثابتة الموجودة بكل صفحات الموقع)، وينتهي عند التاريخ
    title = f"إفصاح رقم {story_id}"
    marker = "المؤشرات المالية"
    if marker in page_text:
        after_marker = page_text.split(marker, 1)[1]
        title_match = re.search(
            r"^\s*([^\n]{10,300}?)\s+\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}", after_marker
        )
        if title_match:
            title = clean_text(title_match.group(1))

    related_symbols = []
    for link in soup.find_all("a", href=re.compile(r"companyCode=([A-Za-z0-9]+)")):
        match = re.search(r"companyCode=([A-Za-z0-9]+)", link.get("href", ""))
        if match and match.group(1) not in related_symbols:
            related_symbols.append(match.group(1))

    pdf_link = None
    pdf_tag = soup.find("a", href=re.compile(r"\.pdf$", re.IGNORECASE))
    if pdf_tag:
        pdf_href = pdf_tag.get("href", "")
        pdf_link = pdf_href if pdf_href.startswith("http") else f"http://www.isx-iq.net{pdf_href}"

    return {
        "id": story_id,
        "title": title,
        "date": date_str,
        "related_symbols": related_symbols,
        "pdf_url": pdf_link,
        "url": url,
    }


def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {"official_disclosures": [], "last_known_story_id": None}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            return {"official_disclosures": [], "last_known_story_id": None}
    data.setdefault("official_disclosures", [])
    data.setdefault("last_known_story_id", None)
    return data


def save_json(data: dict, path: str):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def main():
    parser = argparse.ArgumentParser(description="جلب إفصاحات isx-iq.net بتجربة أرقام storyId تصاعدياً")
    parser.add_argument("--existing", default="isx_disclosures.json")
    parser.add_argument("--output", default="isx_disclosures.json")
    parser.add_argument(
        "--start-id",
        type=int,
        default=None,
        help="أول رقم storyId نبدأ منه إذا لم يوجد last_known_story_id بالملف (أول تشغيلة فقط)",
    )
    args = parser.parse_args()

    data = load_existing(args.existing)
    last_known_id = data.get("last_known_story_id")

    if last_known_id is None:
        if args.start_id is None:
            print("❌ خطأ: لا يوجد last_known_story_id بالملف، ولم تحدد --start-id.")
            print("   مرر --start-id برقم storyId معروف حديث (مثال: 17754) لأول تشغيلة.")
            return
        current_id = args.start_id
        print(f"أول تشغيلة، سنبدأ من storyId={current_id}")
    else:
        current_id = int(last_known_id) + 1
        print(f"آخر رقم مُعالج سابقاً: {last_known_id}. سنبدأ من storyId={current_id}")

    new_records = []
    consecutive_failures = 0
    highest_successful_id = int(last_known_id) if last_known_id else None

    while len(new_records) < MAX_NEW_PER_RUN and consecutive_failures < MAX_GAP_ATTEMPTS:
        print(f"  تجربة storyId={current_id}...")
        detail = fetch_story_details(str(current_id))

        if detail is None:
            consecutive_failures += 1
            print(f"    (فارغ - فجوة أو نهاية القائمة، محاولة {consecutive_failures}/{MAX_GAP_ATTEMPTS})")
        else:
            consecutive_failures = 0
            highest_successful_id = current_id
            print(f"    ✅ إفصاح موجود: {detail['title'][:60]}")
            new_records.append(detail)

        current_id += 1
        time.sleep(1)

    if not new_records:
        print("\n✅ لا يوجد إفصاح جديد بعد آخر رقم معروف.")
        return

    data["official_disclosures"] = new_records + data["official_disclosures"]
    data["last_known_story_id"] = highest_successful_id

    save_json(data, args.output)
    print(
        f"\n🎉 نجاح! أُضيف {len(new_records)} إفصاح جديد. "
        f"آخر رقم معروف الآن: {highest_successful_id}. "
        f"الإجمالي: {len(data['official_disclosures'])}"
    )


if __name__ == "__main__":
    main()
