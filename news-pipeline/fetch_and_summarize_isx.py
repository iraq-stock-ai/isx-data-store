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

try:
    import pytesseract
    from pdf2image import convert_from_path
    OCR_SUPPORT = True
except ImportError:
    OCR_SUPPORT = False

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

MAX_GAP_ATTEMPTS = 15

MODEL_NAME = "gemini-3.1-flash-lite"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"
API_KEY = os.environ.get("GEMINI_DISCLOSURES_API_KEY")


def clean_text(txt: str) -> str:
    if txt is None:
        return ""
    txt = str(txt).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", txt).strip()


def fetch_story_by_type(story_id: str, story_type: int) -> dict:
    """يجلب تفاصيل حدث واحد بنوع محدد. يرجع None إذا كانت الصفحة فارغة."""
    url = f"{STORY_DETAILS_URL}?storyId={story_id}&type={story_type}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"      تحذير: فشل فتح storyId={story_id} type={story_type}: {e}")
        return None

    soup = BeautifulSoup(resp.content, "html.parser")
    page_text = clean_text(soup.get_text())

    if "not available in english" in page_text:
        return None

    date_match = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", page_text)
    if not date_match:
        return None

    date_str = date_match.group(1)

    title = f"عنصر رقم {story_id}"
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
        "type": "إفصاح" if story_type == 1 else "خبر_سوق",
        "title": title,
        "date": date_str,
        "related_symbols": related_symbols,
        "pdf_url": pdf_link,
        "url": url,
    }


def fetch_story(story_id: str) -> dict:
    result = fetch_story_by_type(story_id, 1)
    if result:
        return result
    return fetch_story_by_type(story_id, 2)


def find_next_new_item(last_known_id: int, start_id: int = None) -> tuple:
    """يبحث عن أول عنصر جديد بعد آخر رقم معروف. يرجع (item, highest_id_tried)."""
    current_id = (int(last_known_id) + 1) if last_known_id is not None else start_id
    consecutive_failures = 0
    highest_tried = current_id - 1

    while consecutive_failures < MAX_GAP_ATTEMPTS:
        print(f"  تجربة storyId={current_id}...")
        detail = fetch_story(str(current_id))
        highest_tried = current_id

        if detail is None:
            consecutive_failures += 1
            print(f"    (فارغ - فجوة، محاولة {consecutive_failures}/{MAX_GAP_ATTEMPTS})")
            current_id += 1
            time.sleep(1)
            continue

        print(f"    ✅ [{detail['type']}] {detail['title'][:60]}")
        return detail, highest_tried

    return None, highest_tried


def extract_pdf_text_via_ocr(pdf_path: str) -> str:
    """
    خطة بديلة: يحوّل صفحات PDF لصور، ثم يقرأ النص منها بصرياً عبر Tesseract
    (بدعم اللغة العربية). أبطأ بكثير من pdfplumber لكنه يعمل حتى مع
    الصفحات الممسوحة ضوئياً (scanned) أو ذات الترميز غير القياسي.
    """
    if not OCR_SUPPORT:
        return ""

    try:
        # دقة أعلى (300 بدل 200) لتحسين قراءة الأرقام الدقيقة بالجداول
        # المالية، والتي غالباً ما تُفقد أو تتشوه بدقة أقل رغم نجاح قراءة
        # النص العام للصفحة
        images = convert_from_path(pdf_path, dpi=300)
    except Exception as e:
        print(f"      تحذير: فشل تحويل PDF لصور: {e}")
        return ""

    extracted_pages = []
    # حد أقصى 5 صفحات لتفادي إبطاء التشغيلة كثيراً (أغلب الإفصاحات قصيرة)
    for i, image in enumerate(images[:5]):
        try:
            # psm=6 (بلوك نص موحد) عادة يعطي نتائج أفضل مع صفحات القوائم
            # المالية والجداول مقارنة بالإعداد الافتراضي
            page_text = pytesseract.image_to_string(image, lang="ara", config="--psm 6")
            if page_text:
                extracted_pages.append(page_text)
        except Exception as e:
            print(f"      تحذير: فشل OCR بالصفحة {i + 1}: {e}")

    return clean_text(" ".join(extracted_pages))


def extract_pdf_text(pdf_url: str) -> dict:
    """يحمّل PDF ويستخرج نصه. يرجع dict فيه النص وحالة الاستخراج."""
    if not PDF_SUPPORT:
        return {"text": "", "status": "pdfplumber_not_installed"}

    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        return {"text": "", "status": f"download_failed: {e}"}

    tmp_path = "/tmp/_isx_temp_pdf.pdf"
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

        if full_text and len(full_text) >= 10:
            return {"text": full_text, "status": "success"}

        # pdfplumber لم يستخرج نصاً كافياً (على الأغلب صفحة ممسوحة ضوئياً أو
        # ترميز خط غير قياسي) - نجرب OCR كخطة بديلة قبل الاستسلام
        print("      ⚠️ pdfplumber لم يستخرج نصاً كافياً، تجربة OCR كخطة بديلة...")
        ocr_text = extract_pdf_text_via_ocr(tmp_path)

        if ocr_text and len(ocr_text) >= 10:
            return {"text": ocr_text, "status": "success_via_ocr"}

        return {"text": "", "status": "empty_or_scanned_ocr_also_failed"}

    except Exception as e:
        return {"text": "", "status": f"extraction_failed: {e}"}
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def summarize_with_gemini(title: str, raw_text: str, max_retries: int = 3) -> dict:
    """يرسل النص لـ Gemini بطلب بسيط مباشر (بدون أدوات بحث) لاستخراج ملخص منظم."""
    if not API_KEY:
        print("❌ خطأ: لم يتم العثور على متغير البيئة GEMINI_DISCLOSURES_API_KEY.")
        return None

    if not raw_text or len(raw_text) < 10:
        print("    ⚠️ لا يوجد نص كافٍ لإرساله لـ Gemini (PDF فارغ أو غير مستخرج).")
        return None

    prompt_text = (
        "أنت محلل مالي متخصص بسوق العراق للأوراق المالية. فيما يلي نص إفصاح "
        f"رسمي بعنوان: \"{title}\".\n\n"
        f"النص المستخرج من ملف PDF المرفق:\n{raw_text[:6000]}\n\n"
        "⚠️ تحذيرات مهمة جداً قبل الاستخراج:\n\n"
        "1. بعض هذه الإفصاحات صادرة من شركات وساطة مالية، وتحتوي ترويسة "
        "الخطاب أحياناً على معلومات مالية تخص شركة الوساطة نفسها لا علاقة "
        "لها بالشركة موضوع الإفصاح. ميّز بدقة بين الشركة موضوع الإفصاح "
        "الفعلي وأي طرف ثالث مذكور فقط كناقل أو منفّذ للإجراء.\n\n"
        "2. المستندات الطويلة (مثل محاضر اجتماعات الهيئة العامة) قد تحتوي "
        "عدة نسب مئوية بمعانٍ مختلفة تماماً - مثل نسبة حضور المساهمين "
        "بالاجتماع (نصاب الحضور)، أو نسبة عجز متراكم من رأس المال، أو نسبة "
        "ملكية بالتصويت. لا تفترض أن أي نسبة مئوية بالنص هي بالضرورة نسبة "
        "توزيع أرباح. اكتب قيمة لحقل dividend_percentage فقط إذا وجدت عبارة "
        "صريحة تربط النسبة تحديداً بكلمة 'توزيع' أو 'أرباح موزعة' أو مشابه "
        "بنفس الجملة أو الجملة المجاورة مباشرة. إن لم تجد هذا الربط الصريح، "
        "اترك الحقل null حتى لو رأيت نسبة مئوية أخرى بالنص.\n\n"
        "3. المستندات الطويلة متعددة المواضيع (محاضر اجتماعات فيها عدة "
        "بنود: حسابات ختامية، عجز متراكم، قضايا قانونية، انتخاب مجلس "
        "إدارة...) يصعب تلخيصها بجملتين. في هذه الحالة، اجعل الملخص أكثر "
        "شمولاً (حتى 5 جمل) يغطي أهم البنود المالية الفعلية المذكورة فعلاً "
        "بالنص (كالعجز المتراكم، أي قضايا قانونية بمبالغ محددة)، بدل التركيز "
        "على بند واحد فقط.\n\n"
        "استخرج المعلومات التالية إن وُجدت صراحة بالنص، والتزم بعدم اختلاق "
        "أي رقم أو معلومة غير مذكورة صراحة (اترك الحقل null إن لم تجده، ولا "
        "تخمّن أو تستنتج رقماً من سياق غير مؤكد).\n\n"
        "أجب حصراً بصيغة JSON نقية 100% وبدون أي علامات markdown، بالهيكل التالي:\n"
        "{\n"
        '  "disclosure_type": "توزيع_ارباح | زيادة_راس_المال | قوائم_مالية | '
        'اجتماع_هيئة_عامة | قرار_رفض_او_عدم_موافقة | حركة_تداول_خاصة | اخرى",\n'
        '  "company_name": "اسم الشركة موضوع الإفصاح فقط (وليس شركة الوساطة إن وُجدت) أو null",\n'
        '  "raas_al_mal": "رقم رأس المال الخاص بالشركة موضوع الإفصاح تحديداً بالدينار العراقي، أو null إن لم يُذكر صراحة لهذه الشركة بالذات",\n'
        '  "net_profit": "صافي الربح بالدينار العراقي أو null",\n'
        '  "total_debts": "إجمالي الديون/المطلوبات أو العجز المتراكم بالدينار العراقي أو null",\n'
        '  "traded_shares_quantity": "عدد الأسهم المتداولة/المباعة/المشتراة بالصفقة إن وُجد (رقم فقط) أو null",\n'
        '  "dividend_per_share": "قيمة التوزيع للسهم الواحد بالدينار أو null",\n'
        '  "dividend_percentage": "نسبة توزيع الأرباح فقط (وليس أي نسبة أخرى مثل نسبة الحضور) أو null",\n'
        '  "dividend_date": "تاريخ توزيع/استحقاق الأرباح إن ذُكر أو null",\n'
        '  "other_important_figures": "أي أرقام مالية جوهرية أخرى غير مغطاة بالحقول أعلاه (مثل مبالغ قضايا قانونية، نسبة عجز من رأس المال) كنص حر، أو null",\n'
        '  "confidence_note": "إن كان أي رقم أعلاه مستنتجاً من سياق غير قاطع الوضوح، اذكر هنا أيهما وسبب عدم اليقين. إن كانت كل الأرقام واضحة وصريحة بالنص، اكتب null",\n'
        '  "summary": "ملخص شامل بالعربية الفصحى الرصينة، بطول جملتين للمستندات البسيطة أو حتى 5 جمل للمستندات الطويلة متعددة البنود"\n'
        "}"
    )

    headers = {"Content-Type": "application/json"}
    # طلب بسيط ومباشر: بدون أي أدوات بحث مدمجة، بدون تعقيد إضافي
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    delay = 5
    for attempt in range(1, max_retries + 1):
        try:
            print(f"    🔄 إرسال لـ Gemini (طلب بسيط)... محاولة {attempt}/{max_retries}")
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
                print(f"       تفاصيل الرد: {response.text[:300]}")
                time.sleep(delay)
                delay *= 2
                continue
            else:
                print(f"    ❌ فشل سيرفر Gemini: كود {response.status_code}")
                print(f"       تفاصيل: {response.text[:300]}")
                return None
        except Exception as e:
            print(f"    ❌ خطأ اتصال/تحليل: {e}")
            time.sleep(delay)
            delay *= 2

    return None


def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {"items": [], "last_known_story_id": None}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            return {"items": [], "last_known_story_id": None}
    data.setdefault("items", [])
    data.setdefault("last_known_story_id", None)
    return data


def save_json(data: dict, path: str):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def main():
    parser = argparse.ArgumentParser(description="جلب وتلخيص إفصاح/خبر واحد جديد عبر Gemini لكل تشغيلة")
    parser.add_argument("--existing", default="isx_disclosures.json")
    parser.add_argument("--output", default="isx_disclosures.json")
    parser.add_argument("--start-id", type=int, default=None)
    args = parser.parse_args()

    data = load_existing(args.existing)
    last_known_id = data.get("last_known_story_id")

    if last_known_id is None and args.start_id is None:
        print("❌ خطأ: لا يوجد last_known_story_id بالملف، ولم تحدد --start-id.")
        return

    item, highest_tried = find_next_new_item(last_known_id, args.start_id)

    # نحدّث last_known_story_id دائماً لأعلى رقم جربناه (حتى لو فشل)، لتفادي
    # إعادة تجربة نفس الفجوات بكل تشغيلة قادمة
    data["last_known_story_id"] = highest_tried

    if not item:
        print("\n✅ لا يوجد عنصر جديد حالياً.")
        save_json(data, args.output)
        return

    # فقط الإفصاحات (type=1) تحتاج تلخيص مالي مفصّل عبر PDF. أخبار السوق
    # (type=2) نكتفي بحفظ العنوان والتفاصيل الأساسية بدون استدعاء Gemini،
    # توفيراً لاستهلاك الـ API على ما يستحق التحليل المالي فعلاً.
    if item["type"] == "خبر_سوق":
        print("\nℹ️ هذا خبر سوق عام (type=2)، سيُحفظ بدون تلخيص Gemini.")
        item["disclosure_type"] = None
        item["summary"] = None
        data["items"].insert(0, item)
        save_json(data, args.output)
        print(f"✅ تم الحفظ. الإجمالي: {len(data['items'])}")
        return

    if not item.get("pdf_url"):
        print("\nℹ️ لا يوجد PDF مرفق بهذا الإفصاح، سيُحفظ بدون تلخيص Gemini.")
        data["items"].insert(0, item)
        save_json(data, args.output)
        print(f"✅ تم الحفظ. الإجمالي: {len(data['items'])}")
        return

    print(f"\n📄 تحميل واستخراج نص PDF: {item['pdf_url']}")
    pdf_result = extract_pdf_text(item["pdf_url"])
    print(f"   حالة الاستخراج: {pdf_result['status']}")

    if pdf_result["status"] not in ("success", "success_via_ocr"):
        print("⚠️ فشل استخراج نص PDF. سيُحفظ الإفصاح بدون تلخيص مالي.")
        item["pdf_extraction_status"] = pdf_result["status"]
        data["items"].insert(0, item)
        save_json(data, args.output)
        print(f"✅ تم الحفظ (بدون ملخص). الإجمالي: {len(data['items'])}")
        return

    summary = summarize_with_gemini(item["title"], pdf_result["text"])

    if not summary:
        print("⚠️ فشل التلخيص عبر Gemini. سيُحفظ الإفصاح بدون تلخيص مالي (سيُعاد لاحقاً يدوياً إن لزم).")
        item["pdf_extraction_status"] = "success_but_gemini_failed"
        data["items"].insert(0, item)
        save_json(data, args.output)
        print(f"✅ تم الحفظ (بدون ملخص). الإجمالي: {len(data['items'])}")
        return

    item.update(summary)
    item["pdf_extraction_status"] = pdf_result["status"]
    data["items"].insert(0, item)
    save_json(data, args.output)

    print(f"\n🎉 نجاح كامل! تم تلخيص وحفظ الإفصاح. الإجمالي: {len(data['items'])}")


if __name__ == "__main__":
    main()
