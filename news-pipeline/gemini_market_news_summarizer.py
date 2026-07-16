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
API_KEY = os.environ.get("GEMINI_MARKET_NEWS_API_KEY")

MAX_LIST_PAGES = 5

# activeTab=1 هو تبويب "الأخبار" العامة بالموقع (يختلف عن activeTab=0 وهو
# "الإعلانات" الرسمية المستخدم بسكربت الإفصاحات المنفصل)
ACTIVE_TAB = 1
STORY_TYPE = 2


def clean_text(txt: str) -> str:
    if txt is None:
        return ""
    txt = str(txt).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", txt).strip()


def fetch_market_news_list(max_pages: int = MAX_LIST_PAGES) -> list:
    items = []
    for page in range(1, max_pages + 1):
        params = {"activeTab": ACTIVE_TAB, "page": page}
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
            full_url = f"{STORY_DETAILS_URL}?storyId={story_id}&type={STORY_TYPE}"
            page_items.append({"story_id": story_id, "title": title, "url": full_url})

        print(f"  صفحة {page}: {len(page_items)} خبر.")
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

    return {
        "raw_text": page_text[:2000],
        "date": date_str,
        "related_symbols": related_symbols,
    }


def summarize_with_gemini(title: str, raw_text: str, max_retries: int = 3) -> dict:
    if not API_KEY:
        print("❌ خطأ: لم يتم العثور على متغير البيئة GEMINI_MARKET_NEWS_API_KEY.")
        return None

    prompt_text = (
        "أنت محرر أخبار متخصص بسوق العراق للأوراق المالية. فيما يلي خبر من "
        f"قسم أخبار السوق بعنوان: \"{title}\".\n\n"
        f"النص المتوفر:\n{raw_text}\n\n"
        "لخص هذا الخبر بجملتين إلى ثلاث جمل بالعربية الفصحى الرصينة، دون "
        "اختلاق أي معلومة غير مذكورة صراحة بالنص.\n\n"
        "أجب حصراً بصيغة JSON نقية 100% وبدون أي علامات markdown، بالهيكل التالي:\n"
        "{\n"
        '  "summary": "الملخص هنا",\n'
        '  "topic_category": "تصنيف عام مثل: اجتماع | إعلان_عام | تنظيمي | اخرى"\n'
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
        return {"market_news": []}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            return {"market_news": []}
    data.setdefault("market_news", [])
    return data


def save_json(data: dict, path: str):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def main():
    parser = argparse.ArgumentParser(description="تلخيص خبر سوق واحد جديد عبر Gemini لكل تشغيلة")
    parser.add_argument("--existing", default="isx_market_news.json")
    parser.add_argument("--output", default="isx_market_news.json")
    args = parser.parse_args()

    data = load_existing(args.existing)
    known_ids = {item["id"] for item in data["market_news"] if "id" in item}

    print("جاري فحص قائمة أخبار السوق...")
    listing = fetch_market_news_list()

    new_items = [item for item in listing if item["story_id"] not in known_ids]

    if not new_items:
        print("✅ لا يوجد خبر سوق جديد. الخروج بدون استدعاء Gemini.")
        return

    target = new_items[-1]
    print(f"\n📌 معالجة الخبر: {target['title'][:70]}")
    print(f"   (متبقٍ {len(new_items) - 1} خبر جديد آخر بعد هذه التشغيلة)")

    detail = fetch_story_raw_content(target["url"])
    if not detail:
        print("❌ فشل استخراج محتوى الخبر. سيُعاد المحاولة بالتشغيلة القادمة.")
        return

    summary = summarize_with_gemini(target["title"], detail.get("raw_text", ""))

    if not summary:
        print("⚠️ فشل التلخيص عبر Gemini. سيُعاد المحاولة بالتشغيلة القادمة.")
        return

    record = {
        "id": target["story_id"],
        "title": target["title"],
        "source": "سوق العراق للأوراق المالية (isx-iq.net) - أخبار السوق - ملخص Gemini",
        "url": target["url"],
        "date": detail.get("date") or datetime.now().strftime("%d/%m/%Y"),
        "related_symbols": detail.get("related_symbols", []),
        **summary,
    }

    data["market_news"].insert(0, record)
    save_json(data, args.output)

    print(f"\n✅ تم تلخيص وحفظ الخبر بنجاح. الإجمالي الآن: {len(data['market_news'])}")


if __name__ == "__main__":
    main()
