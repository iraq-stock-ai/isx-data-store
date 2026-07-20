import io
import os
import re
import json
import time
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
import openpyxl

# ================== الرابط المرجعي للبحث ==================
SEARCH_URL = "http://www.isx-iq.net/isxportal/portal/uploadedFilesList.html?d-447146-p=1&reporttype=40&toDate=19%2F07%2F2026&date=19%2F07%2F2024"

# ================== الثوابت العامة ==================
BASE_URL = "http://www.isx-iq.net"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.7,en;q=0.3",
}

ARCHIVE_OUTPUT_FILE = "isx_foreign_trading.json"
PROGRESS_FILE = "archive_progress.json"
DELAY_SECONDS = 2
MAX_PAGES = 500

FOREIGN_SECTION_MARKERS = ["غير العراقيين", "غيرالعراقيين", "الأجانب", "اجانب", "أجانب"]
BUY_MARKERS = ["المشتراة", "مشتراة"]
SELL_MARKERS = ["المباعة", "مباعة"]
IGNORE_WORDS = {"ISX", "OTC", "TOTAL", "DATE", "TYPE", "BUY", "SELL", "المجموع", "مجموع"}

# ================== دوال تنظيف واستخراج البيانات ==================
def clean_text(txt) -> str:
    """تنظيف النص وإزالة رموز UTF-8 الخفية والمسافات غير المرئية."""
    if txt is None:
        return ""
    txt = str(txt)
    txt = txt.replace("\xa0", " ").replace("\u200f", "").replace("\u200e", "").replace("\ufeff", "")
    txt = txt.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt

def extract_market_label(section_title_text: str, default_market: str = "النظامي") -> str:
    """تعيين نوع السوق بدقة عالية عبر الكلمات المفتاحية."""
    txt = clean_text(section_title_text)

    if any(k in txt for k in ["غير المفصحة", "غير مفصحة", "الثالث", "otc"]):
        return "الشركات غير المفصحة"
    if any(k in txt for k in ["الثاني", "الثانية"]):
        return "الثاني"
    if any(k in txt for k in ["النظامي", "النظامية"]):
        return "النظامي"

    match = re.search(r"(?:في|منصة|سوق)\s+(.+?)(?:\s+لجلسة|\s*$)", txt)
    if match:
        raw_market = clean_text(match.group(1))
        if "ثاني" in raw_market:
            return "الثاني"
        if "مفصح" in raw_market or "ثالث" in raw_market:
            return "الشركات غير المفصحة"
        if "نظام" in raw_market:
            return "النظامي"

    return default_market

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
def fetch_reports_from_page(page_url: str, retries: int = 3) -> tuple:
    print(f"  [أرشيف] جاري فحص الصفحة: {page_url}")
    
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "html.parser")
                table = soup.find("table")
                if table is None:
                    return [], True

                reports = []
                for row in table.find_all("tr"):
                    row_text = clean_text(row.get_text())
                    if "يومي" not in row_text and "التقرير اليومي" not in row_text:
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

                return reports, False

            print(f"    [⚠] محاولة {attempt}: رمز الاستجابة من الموقع {resp.status_code}")
        except Exception as e:
            print(f"    [⚠] محاولة {attempt}: فشل الاتصال بالموقع ({e})")
        
        time.sleep(3)

    return [], False

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
    return "0"

def find_foreign_trading_blocks(wb) -> list:
    """كشف كتل الأجانب مع فحص الأسطر السابقة (للأعلى) لتحديد نوع السوق بدقة."""
    blocks = []
    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        rows = list(sheet.iter_rows(values_only=True))
        num_rows = len(rows)

        current_market = "النظامي"

        for row_idx in range(num_rows):
            curr_text = clean_text(" ".join(str(c) for c in rows[row_idx] if c is not None))

            # فحص إذا كان السطر الحالي يمثل عنوان سوق جديد
            if "السوق الثاني" in curr_text or "سوق الثاني" in curr_text:
                current_market = "الثاني"
            elif "غير المفصحة" in curr_text or "غير مفصحة" in curr_text:
                current_market = "الشركات غير المفصحة"
            elif "السوق النظامي" in curr_text or "سوق النظامي" in curr_text:
                current_market = "النظامي"

            has_foreign = any(m in curr_text for m in FOREIGN_SECTION_MARKERS)
            has_direction = any(m in curr_text for m in BUY_MARKERS + SELL_MARKERS)

            if has_foreign and has_direction:
                header_lines = []
                # التعديل الرئيسي: النظر للخلف 8 أسطر وللأمام سطرين لالتقاط أية عناوين سابقة
                start_look = max(0, row_idx - 8)
                end_look = min(row_idx + 3, num_rows)

                for w_idx in range(start_look, end_look):
                    w_text = clean_text(" ".join(str(c) for c in rows[w_idx] if c is not None))
                    header_lines.append(w_text)

                full_header = " ".join(header_lines)
                market_label = extract_market_label(full_header, default_market=current_market)

                blocks.append((sheet_name, row_idx, full_header, market_label))

    return blocks

def parse_foreign_section(sheet, start_row_idx: int, market_label: str, direction: str) -> list:
    records = []
    symbol_pattern = re.compile(r"\b([A-Z]{3,6})\b")
    rows = list(sheet.iter_rows(values_only=True))
    MAX_ROWS = 60

    for offset in range(1, MAX_ROWS):
        idx = start_row_idx + offset
        if idx >= len(rows):
            break

        row = rows[idx]
        row_cells = [clean_text(c) for c in row]
        row_text = " ".join(c for c in row_cells if c)

        if not row_text:
            continue

        if any(term in row_text for term in ["المجموع الكلي", "مجموع الكلي"]):
            break
        if any(m in row_text for m in FOREIGN_SECTION_MARKERS) and any(m in row_text for m in BUY_MARKERS + SELL_MARKERS):
            break

        if "مجموع" in row_text or "قطاع" in row_text:
            continue

        symbol = None
        for cell in row_cells:
            cell_clean = cell.strip().upper()
            m = symbol_pattern.search(cell_clean)
            if m:
                cand = m.group(1)
                if cand not in IGNORE_WORDS:
                    symbol = cand
                    break

        if not symbol:
            continue

        numbers = []
        for cell in row_cells:
            cell_num = cell.replace(",", "").strip()
            if re.match(r"^-?\d+(\.\d+)?$", cell_num):
                numbers.append(cell_num)

        if len(numbers) < 3:
            continue

        trades_raw, shares_raw, value_raw = numbers[-3], numbers[-2], numbers[-1]
        try:
            trades = int(float(trades_raw))
            shares = int(float(shares_raw))
            value = int(float(value_raw))
        except ValueError:
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
    for sheet_name, row_idx, row_text, market_label in blocks:
        sheet = wb[sheet_name]

        direction = "buy" if any(m in row_text for m in BUY_MARKERS) else (
            "sell" if any(m in row_text for m in SELL_MARKERS) else None
        )
        if direction is None:
            continue

        section_records = parse_foreign_section(sheet, row_idx, market_label, direction)
        all_records.extend(section_records)

    return {"session_number": session_number, "records": all_records}

# ================== التخزين وإدارة الملفات ==================
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

        matched_idx = -1
        for idx, r in enumerate(existing[symbol]):
            if r.get("date") == session_date and r.get("direction") == rec["direction"]:
                matched_idx = idx
                break

        new_entry = {
            "date": session_date,
            "sessionNumber": session_number,
            "market": rec["market"],
            "direction": rec["direction"],
            "trades": rec["trades"],
            "shares": rec["shares"],
            "value": rec["value"],
        }

        if matched_idx != -1:
            if existing[symbol][matched_idx].get("market") != rec["market"]:
                existing[symbol][matched_idx] = new_entry
                added += 1
        else:
            existing[symbol].append(new_entry)
            added += 1

    return added

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_progress(progress: dict):
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROGRESS_FILE)

# ================== التشغيل الرئيسي ==================
def main():
    print("=" * 60)
    print("أرشفة تداول غير العراقيين (شاملة لكافة الأسواق والمنصات)")
    print("=" * 60)

    params = extract_search_params(SEARCH_URL)
    data = load_existing_data()

    if not os.path.exists(ARCHIVE_OUTPUT_FILE):
        save_data(data)

    progress = load_progress()
    last_page = progress.get("last_page", 0)

    start_page = last_page + 1
    print(f"بدء الفحص من الصفحة: {start_page}")

    total_processed = 0
    total_added = 0
    page = start_page

    while page <= MAX_PAGES:
        page_url = build_page_url(params, page)
        reports, is_end_of_archive = fetch_reports_from_page(page_url)

        if is_end_of_archive:
            print(f"  [توقف] الصفحة {page} فارغة، تم جلب الأرشيف بالكامل.")
            progress["last_page"] = page
            save_progress(progress)
            break

        if not reports:
            print(f"  [⚠] لم يتم الحصول على تقارير من الصفحة {page} (ربما تعذر الاتصال بالموقع).")
            break

        print(f"  [✓] الصفحة {page} تحتوي على {len(reports)} تقرير(ات).")

        for report in reports:
            time.sleep(DELAY_SECONDS)
            try:
                excel_bytes = download_excel(report["url"])
                parsed = parse_daily_foreign_excel(excel_bytes)
                added = merge_records(data, report["date"], parsed.get("session_number"), parsed["records"])
                total_added += added
                total_processed += 1
                print(f"      ✅ {report['date']}: {len(parsed['records'])} سجل، {added} مضاف/محدث.")
            except Exception as e:
                print(f"      ❌ فشل معالجة {report['date']}: {e}")

        save_data(data)
        progress["last_page"] = page
        save_progress(progress)

        page += 1

    print("\n" + "=" * 60)
    print(f"🎉 انتهت العملية!")
    print(f"   - التقارير المكتملة في هذه الجلسة: {total_processed}")
    print(f"   - السجلات المضافة/المحدثة: {total_added}")
    print("=" * 60)

if __name__ == "__main__":
    main()
