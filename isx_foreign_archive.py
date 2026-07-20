import io
import os
import re
import json
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
import openpyxl

# ================== الرابط المرجعي للبحث ==================
SEARCH_URL = "http://www.isx-iq.net/isxportal/portal/uploadedFilesList.html?d-447146-p=140&reporttype=40&toDate=19%2F07%2F2026&date=19%2F07%2F2024"

# ================== الثوابت العامة ==================
BASE_URL = "http://www.isx-iq.net"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

ARCHIVE_OUTPUT_FILE = "isx_foreign_trading.json"
PROGRESS_FILE = "archive_progress.json"
DELAY_SECONDS = 3
MAX_PAGES = 500

# ================ منطق استخراج "غير العراقيين" ================
SYMBOL_PATTERN = re.compile(r"^[A-Z]{3,6}$")
FOREIGN_SECTION_MARKERS = [
    "غير العراقيين",
    "غيرالعراقيين",
    "الأجانب",
    "اجانب",
    "أجانب",
]
BUY_MARKERS = ["المشتراة", "مشتراة"]
SELL_MARKERS = ["المباعة", "مباعة"]

# ================ توسيع مرادفات أسماء الأسواق ================
MARKET_NAME_NORMALIZATION = {
    "السوق النظامي": "النظامي",
    "المنصة النظامية": "النظامي",
    "السوق الثاني": "الثاني",
    "السوق الثانوي": "الثاني",          # مرادف إضافي
    "المنصة الثانية": "الثاني",
    "منصة الشركات غير المفصحة": "الشركات غير المفصحة",
    "منصة الشركات غير المخصصة": "الشركات غير المفصحة",  # مرادف إضافي
}
MARKET_EXTRACTION_PATTERN = re.compile(r"في\s+(.+?)(?:\s+لجلسة|\s*$)")

# ================== دوال مساعدة ==================
def clean_text(txt) -> str:
    if txt is None:
        return ""
    txt = str(txt).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt

def extract_market_label(section_title_text: str) -> str:
    """
    يستخرج اسم السوق/المنصة من نص عنوان القسم الكامل.
    """
    match = MARKET_EXTRACTION_PATTERN.search(section_title_text)
    if not match:
        return "غير محدد"
    raw_market_name = clean_text(match.group(1))
    if not raw_market_name:
        return "غير محدد"
    for pattern, normalized in MARKET_NAME_NORMALIZATION.items():
        if pattern in raw_market_name or raw_market_name in pattern:
            return normalized
    return raw_market_name

def extract_search_params(url: str) -> dict:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    return {
        "reporttype": params.get("reporttype", ["40"])[0],
        "toDate": params.get("toDate", [""])[0],
        "date": params.get("date", [""])[0],
    }

def build_page_url(params: dict, page: int) -> str:
    return f"http://www.isx-iq.net/isxportal/portal/uploadedFilesList.html?d-447146-p={page}&reporttype={params['reporttype']}&toDate={params['toDate']}&date={params['date']}"

# ================== استخراج روابط التقارير ==================
def fetch_reports_from_page(page_url: str) -> list:
    print(f"  [أرشيف] جاري فحص الصفحة: {page_url}")
    try:
        resp = requests.get(page_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"    [خطأ] فشل تحميل الصفحة: {e}")
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    table = soup.find("table")
    if table is None:
        print("    [⚠] لم يُعثر على جدول في الصفحة.")
        return []

    reports = []
    for row in table.find_all("tr"):
        row_text = clean_text(row.get_text())
        if "يومي" not in row_text or "التقرير اليومي" not in row_text:
            continue

        link_tag = row.find("a", href=True)
        if not link_tag:
            continue
        href = link_tag["href"]
        if ".xlsx" not in href.lower() and ".xls" not in href.lower():
            continue

        date_match = re.search(r"(\d{2}/\d{2}/\d{4})", row_text)
        if not date_match:
            continue
        session_date = date_match.group(1)

        full_url = href if href.startswith("http") else BASE_URL + href
        reports.append({"date": session_date, "url": full_url})

    return reports

# ================== تحميل واستخراج بيانات Excel ==================
def download_excel(url: str) -> bytes:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.content

def extract_session_number(sheet) -> str:
    pattern = re.compile(r"الجلسة\s*[\(\)]?\s*(\d+)")
    for row in sheet.iter_rows(max_row=10, values_only=True):
        for cell in row:
            if cell:
                match = pattern.search(clean_text(cell))
                if match:
                    return match.group(1)
    return None

def find_foreign_trading_blocks(wb) -> list:
    blocks = []
    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        for row_idx, row in enumerate(sheet.iter_rows(values_only=True)):
            row_text = clean_text(" ".join(str(c) for c in row if c is not None))
            has_foreign = any(m in row_text for m in FOREIGN_SECTION_MARKERS)
            has_direction = any(m in row_text for m in BUY_MARKERS + SELL_MARKERS)
            if has_foreign and has_direction:
                blocks.append((sheet_name, row_idx, row_text))
    return blocks

# ================================================================
# ⬅️ الدالة المعدلة بالكامل لقراءة البيانات من أعمدة محددة
# ================================================================
def parse_foreign_section(sheet, start_row_idx: int, market_label: str, direction: str) -> list:
    rows = list(sheet.iter_rows(values_only=True))

    # 1. البحث عن صف العناوين (في نطاق 5 صفوف قبل بداية القسم)
    header_row = None
    for offset in range(-5, 0):
        idx = start_row_idx + offset
        if idx < 0:
            continue
        row = rows[idx]
        if not row:
            continue
        row_text = " ".join(str(c) for c in row if c)
        if "رمز" in row_text or "الشركة" in row_text:
            header_row = row
            break

    if not header_row:
        print(f"    [⚠] تعذّر العثور على صف العناوين، تخطي هذا القسم.")
        return []

    # 2. تحديد مؤشرات الأعمدة بناءً على عناوينها
    symbol_idx = trades_idx = shares_idx = value_idx = None
    for i, cell in enumerate(header_row):
        if cell is None:
            continue
        cell_str = str(cell).strip()
        if "رمز" in cell_str:
            symbol_idx = i
        elif "الصفقات" in cell_str or "صفقات" in cell_str:
            trades_idx = i
        elif "الأسهم" in cell_str or "اسهم" in cell_str:
            shares_idx = i
        elif "القيمة" in cell_str:
            value_idx = i

    # التأكد من وجود عمود الرمز، وإلا لا يمكن المتابعة
    if symbol_idx is None:
        print(f"    [⚠] لم يُعثر على عمود 'رمز الشركة'، تخطي.")
        return []

    records = []
    # 3. قراءة الصفوف التالية (بدءاً من الصف التالي لصف العنوان)
    for offset in range(1, 40):
        idx = start_row_idx + offset
        if idx >= len(rows):
            break
        row = rows[idx]
        if not row or not any(c is not None for c in row):
            continue

        row_text = " ".join(str(c) for c in row if c)
        # التوقف عند صف المجموع الكلي
        if "المجموع الكلي" in row_text:
            break
        # تخطي صفوف "مجموع قطاع" لأنها لا تحمل رمز شركة
        if "مجموع" in row_text and "قطاع" in row_text:
            continue

        # استخراج الرمز من العمود المخصص له
        symbol = clean_text(row[symbol_idx]) if symbol_idx < len(row) else ""
        if not symbol or not SYMBOL_PATTERN.match(symbol):
            continue

        # استخراج الأرقام من الأعمدة المحددة
        try:
            trades_str = clean_text(row[trades_idx]).replace(",", "") if trades_idx is not None and trades_idx < len(row) else "0"
            shares_str = clean_text(row[shares_idx]).replace(",", "") if shares_idx is not None and shares_idx < len(row) else "0"
            value_str = clean_text(row[value_idx]).replace(",", "") if value_idx is not None and value_idx < len(row) else "0"

            trades = int(float(trades_str)) if trades_str and trades_str != "0" else 0
            shares = int(float(shares_str)) if shares_str and shares_str != "0" else 0
            value = int(float(value_str)) if value_str and value_str != "0" else 0
        except (ValueError, TypeError):
            continue

        # تجاهل الصفوف التي تكون قيمتها صفر (قد تكون فارغة)
        if value == 0 and shares == 0:
            continue

        records.append({
            "symbol": symbol,
            "market": market_label,
            "direction": direction,
            "trades": trades,
            "shares": shares,
            "value": value,
        })

    return records

def parse_daily_foreign_excel(excel_bytes: bytes) -> dict:
    wb = openpyxl.load_workbook(io.BytesIO(excel_bytes), data_only=True)

    session_number = None
    if wb.sheetnames:
        session_number = extract_session_number(wb[wb.sheetnames[0]])

    blocks = find_foreign_trading_blocks(wb)
    if not blocks:
        return {"session_number": session_number, "records": []}

    all_records = []
    for sheet_name, row_idx, row_text in blocks:
        sheet = wb[sheet_name]

        direction = "buy" if any(m in row_text for m in BUY_MARKERS) else (
            "sell" if any(m in row_text for m in SELL_MARKERS) else None
        )
        if direction is None:
            continue

        market_label = extract_market_label(row_text)
        if market_label == "غير محدد":
            print(f"      [⚠] تعذّر استخراج اسم السوق من: '{row_text}' — سيُحفَظ 'غير محدد' صراحة.")

        print(f"      [DEBUG] عُثر على قسم {direction} في السوق '{market_label}'")

        section_records = parse_foreign_section(sheet, row_idx, market_label, direction)
        all_records.extend(section_records)

    return {"session_number": session_number, "records": all_records}

# ================== إدارة التخزين والتقدم ==================
def load_existing_data() -> dict:
    if not os.path.exists(ARCHIVE_OUTPUT_FILE):
        return {}
    try:
        with open(ARCHIVE_OUTPUT_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, IOError):
        return {}

def save_data(data: dict):
    tmp_path = ARCHIVE_OUTPUT_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, ARCHIVE_OUTPUT_FILE)

def merge_records(existing: dict, session_date: str, session_number: str, records: list) -> int:
    added = 0
    for rec in records:
        symbol = rec["symbol"]
        if symbol not in existing:
            existing[symbol] = []

        duplicate = any(
            r.get("date") == session_date
            and r.get("market") == rec["market"]
            and r.get("direction") == rec["direction"]
            for r in existing[symbol]
        )
        if duplicate:
            continue

        existing[symbol].append({
            "date": session_date,
            "sessionNumber": session_number,
            "market": rec["market"],
            "direction": rec["direction"],
            "trades": rec["trades"],
            "shares": rec["shares"],
            "value": rec["value"],
        })
        added += 1
    return added

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  [⚠] خطأ في قراءة ملف التقدم: {e}")
            return {}
    return {}

def save_progress(progress: dict):
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROGRESS_FILE)

# ================== الحلقة الرئيسية ==================
def main():
    print("=" * 60)
    print("أرشفة تداول غير العراقيين (مع استئناف تلقائي)")
    print("=" * 60)

    params = extract_search_params(SEARCH_URL)
    print(f"المعاملات: reporttype={params['reporttype']}, toDate={params['toDate']}, date={params['date']}")

    data = load_existing_data()
    print(f"البيانات الحالية: {sum(len(v) for v in data.values())} سجل.")

    if not os.path.exists(ARCHIVE_OUTPUT_FILE):
        save_data(data)
        print(f"  [تهيئة] {ARCHIVE_OUTPUT_FILE} غير موجود — أُنشئ الآن فارغاً ({{}}) لضمان وجوده.")

    progress = load_progress()
    last_page = progress.get("last_page", 0)
    start_page = last_page + 1
    print(f"آخر صفحة مكتملة: {last_page} → سنبدأ من الصفحة {start_page}")

    total_processed = 0
    total_added = 0
    page = start_page

    while page <= MAX_PAGES:
        page_url = build_page_url(params, page)
        reports = fetch_reports_from_page(page_url)

        if not reports:
            print(f"  [توقف] الصفحة {page} فارغة أو لا تحتوي على تقارير، نعتقد أنها نهاية الأرشيف.")
            progress["last_page"] = page
            save_progress(progress)
            break

        print(f"  [✓] الصفحة {page} تحتوي على {len(reports)} تقرير(ات).")

        for report in reports:
            time.sleep(DELAY_SECONDS)
            try:
                print(f"    - تحميل {report['date']} ...")
                excel_bytes = download_excel(report["url"])
                parsed = parse_daily_foreign_excel(excel_bytes)
                added = merge_records(data, report["date"], parsed.get("session_number"), parsed["records"])
                total_added += added
                total_processed += 1
                print(f"      ✅ {report['date']}: {len(parsed['records'])} سجل، {added} جديد.")
            except Exception as e:
                print(f"      ❌ فشل معالجة {report['date']}: {e}")

        save_data(data)
        progress["last_page"] = page
        save_progress(progress)
        print(f"  [حفظ] تم تحديث الملفات (البيانات والتقدم) بعد الصفحة {page}.")

        page += 1

    print("\n" + "=" * 60)
    print(f"🎉 انتهت المعالجة.")
    print(f"   - تم تحميل {total_processed} تقرير")
    print(f"   - أُضيف {total_added} سجل جديد")
    print(f"   - آخر صفحة مكتملة: {page - 1}")
    print(f"   - ملف البيانات: {ARCHIVE_OUTPUT_FILE}")
    print("=" * 60)

if __name__ == "__main__":
    main()
