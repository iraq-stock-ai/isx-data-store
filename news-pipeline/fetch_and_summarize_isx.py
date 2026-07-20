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
        pdf_link = (
            pdf_href if pdf_href.startswith("http") else f"http://www.isx-iq.net{pdf_href}"
        )

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
    """
    يبحث عن أول عنصر جديد بعد آخر رقم معروف. يرجع (item, highest_successful_id).
    """
    current_id = (int(last_known_id) + 1) if last_known_id is not None else start_id
    consecutive_failures = 0

    while consecutive_failures < MAX_GAP_ATTEMPTS:
        print(f"  تجربة storyId={current_id}...")
        detail = fetch_story(str(current_id))

        if detail is None:
            consecutive_failures += 1
            print(f"    (فارغ - فجوة، محاولة {consecutive_failures}/{MAX_GAP_ATTEMPTS})")
            current_id += 1
            time.sleep(1)
            continue

        print(f"    ✅ [{detail['type']}] {detail['title'][:60]}")
        return detail, current_id

    print(
        f"    ⚠️ لم يُعثر على عنصر جديد بعد {MAX_GAP_ATTEMPTS} محاولة. "
        f"سيُعاد فحص نفس النطاق بالكامل بالتشغيلة القادمة (قد تُنشر الإفصاحات لاحقاً)."
    )
    return None, None


def extract_pdf_text_via_ocr(pdf_path: str) -> str:
    if not OCR_SUPPORT:
        return ""

    try:
        images = convert_from_path(pdf_path, dpi=300)
    except Exception as e:
        print(f"      تحذير: فشل تحويل PDF لصور: {e}")
        return ""

    extracted_pages = []
    for i, image in enumerate(images[:5]):
        try:
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
    """يرسل النص لـ Gemini بطلب بسيط مباشر لاستخراج ملخص منظم على شكل Dictionary."""
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
        "الخطاب أحيانات على معلومات مالية تخص شركة الوساطة نفسها لا علاقة "
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
        '  "company_name": "اسم الشركة موضوع الإفصاح فقط أو null",\n'
        '  "raas_al_mal": "رقم رأس المال بالدينار العراقي، أو null",\n'
        '  "net_profit": "صافي الربح بالدينار العراقي أو null",\n'
        '  "total_debts": "إجمالي الديون/المطلوبات أو العجز المتراكم أو null",\n'
        '  "traded_shares_quantity": "عدد الأسهم المتداولة أو null",\n'
        '  "dividend_per_share": "قيمة التوزيع للسهم الواحد أو null",\n'
        '  "dividend_percentage": "نسبة توزيع الأرباح فقط أو null",\n'
        '  "dividend_date": "تاريخ توزيع/استحقاق الأرباح أو null",\n'
        '  "other_important_figures": [],\n'
        '  "confidence_note": "ملاحظات أو null",\n'
        '  "summary": "ملخص شامل بالعربية الفصحى الرصينة"\n'
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
            print(f"    🔄 إرسال لـ Gemini (طلب بسيط)... محاولة {attempt}/{max_retries}")
            response = requests.post(
                f"{GEMINI_API_URL}?key={API_KEY}", headers=headers, json=payload, timeout=30
            )

            if response.status_code == 200:
                res_json = response.json()
                ai_text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                
                # تنظيف نصوص ماركداون إضافية إن وُجدت
                if ai_text.startswith("```json"):
                    ai_text = ai_text[7:]
                if ai_text.startswith("```"):
                    ai_text = ai_text[3:]
                if ai_text.endswith("```"):
                    ai_text = ai_text[:-3]
                ai_text = ai_text.strip()

                try:
                    parsed_data = json.loads(ai_text)
                    if isinstance(parsed_data, dict):
                        return parsed_data
                    elif isinstance(parsed_data, str):
                        return {"summary": parsed_data}
                    else:
                        return {"summary": str(parsed_data)}
                except Exception as json_err:
                    print(f"    ⚠️ فشل تحويل نص Gemini إلى JSON: {json_err}")
                    return {"summary": ai_text}

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
        return {"items": [], "last_known_story_id": None}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            return {"items": [], "last_known_story_id": None}
    data.setdefault("items", [])
    data.setdefault("last_known_story_id", None)
    return data
