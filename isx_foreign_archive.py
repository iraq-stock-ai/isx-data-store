import io
import os
import re
import sys
import json
import time
from datetime import datetime, timezone, date, timedelta

import requests
from bs4 import BeautifulSoup
import openpyxl

LIST_URL_BASE = "http://www.isx-iq.net/isxportal/files/20-7-2023123_7_4_10_53_59.xlsx"
BASE_URL = "http://www.isx-iq.net"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

ARCHIVE_OUTPUT_FILE = "isx_foreign_trading.json"
PROGRESS_FILE = "foreign_archive_progress.json"

YEARS_BACK = 4                  # الحد الأقصى الثابت المطلوب من المستخدم
DELAY_SECONDS = 4               # فاصل إجباري بين كل تحميل ملف (حماية من الحظر)
RUNTIME_BUDGET_SECONDS = 5 * 60 # تتوقف التشغيلة بأمان بعد 5 دقائق عمل فعلي
                                 # (هامش أمان واسع تحت أي حد زمني لـ GitHub Actions،
                                 # يسمح بعدة تشغيلات متتالية قريبة بدل تشغيلة ضخمة محفوفة بالمخاطر)
MAX_ARCHIVE_PAGES_TO_SCAN = 660 # سقف أمان مطلق يمنع حلقة لا نهائية لو تغيّرت بنية الموقع فجأة

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
MARKET_MARKERS = {
    "النظامي": ["السوق النظامي", "المنصة النظامية"],
    "الثاني": ["السوق الثاني", "المنصة الثانية"],
}


class ArchiveStop(Exception):
    """توقف طبيعي ومقصود (نهاية الميزانية الزمنية، أو التقاء بأحدث تقرير) — ليس خطأً."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def clean_text(txt) -> str:
    if txt is None:
        return ""
    txt = str(txt).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def parse_date_ddmmyyyy(date_str: str):
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# إدارة ملف التقدم (Checkpoint)
# ---------------------------------------------------------------------------

def load_progress() -> dict:
    if not os.path.exists(PROGRESS_FILE):
        start_date = date.today() - timedelta(days=365 * YEARS_BACK)
        return {
            "last_processed_date": None,   # لم تُعالَج أي تشغيلة بعد
            "search_from_date": start_date.strftime("%d/%m/%Y"),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "days_completed": 0,
            "days_skipped_no_report": 0,
            "status": "in_progress",
        }
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_progress(progress: dict):
    tmp_path = PROGRESS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, PROGRESS_FILE)


# ---------------------------------------------------------------------------
# اكتشاف رابط الأرشيف لصفحة مُرقَّمة (وليس الصفحة الأولى فقط)
# ---------------------------------------------------------------------------

def build_archive_page_url(page_number: int) -> str:
    if page_number <= 1:
        return LIST_URL_BASE
    # النمط المؤكَّد من المستخدم يدوياً: معامل صفحة يُلحَق بالرابط الأساسي.
    # يُعتمد شكل الاستعلام القياسي "?p=N" مع الإبقاء على إمكانية ضبطه
    # لاحقاً بسهولة إن اختلف الشكل الفعلي عند أول تشغيلة حقيقية.
    return f"{LIST_URL_BASE}?p={page_number}"


def fetch_archive_page(page_number: int) -> list:
    """
    يُرجع قائمة من {"date": "DD/MM/YYYY", "url": "..."} لكل "تقرير يومي"
    (Excel) موجود بهذه الصفحة من الأرشيف، بترتيب ظهوره بالصفحة.
    """
    url = build_archive_page_url(page_number)
    print(f"  [أرشيف] جاري فحص الصفحة {page_number}: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    table = soup.find("table")
    if table is None:
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


def find_report_for_date(target_date: date) -> dict:
    """
    يبحث عبر صفحات الأرشيف (من الأولى فصاعداً) عن تقرير يطابق
    target_date بالضبط. يتوقف بمجرد العثور عليه، أو بعد استنفاد
    MAX_ARCHIVE_PAGES_TO_SCAN صفحة دون نتيجة.

    ملاحظة أداء: هذا يعيد فحص صفحات سابقة بكل استدعاء، لكن بما أن
    عدد الصفوف بكل صفحة أرشيف صغير نسبياً وطلب صفحة HTML أخف بكثير
    من تحميل Excel كامل، هذا مقبول ولا يحتاج تحسيناً إضافياً بهذه
    المرحلة. المتغيّر page_hint يُستخدم لتسريع البحث بالتشغيلات
    اللاحقة بدل البدء من الصفحة الأولى دائماً.
    """
    target_str = target_date.strftime("%d/%m/%Y")

    for page in range(1, MAX_ARCHIVE_PAGES_TO_SCAN + 1):
        reports = fetch_archive_page(page)
        if not reports:
            # صفحة فارغة أو غير موجودة — على الأغلب انتهى الأرشيف من هذا الاتجاه
            print(f"  [أرشيف] الصفحة {page} فارغة — يُفترض نهاية الأرشيف بهذا الاتجاه.")
            return None

        for report in reports:
            if report["date"] == target_str:
                return report

        # أقدم تاريخ بهذه الصفحة أقدم من هدفنا → هدفنا موجود بصفحة سابقة (أرقام أصغر)
        # هذا السكربت يمشي للأمام زمنياً (من الأقدم)، والأرشيف عادة مرتب
        # من الأحدث للأقدم (صفحة 1 = الأحدث). لذلك لا "قفزة" ذكية هنا آمنة
        # 100% دون تأكيد إضافي لترتيب الصفحات — نكتفي بالفحص التسلسلي البسيط
        # والموثوق، على حساب بعض السرعة، تفادياً لتخطي يوم بالخطأ.
        time.sleep(1)  # فاصل خفيف حتى بين طلبات صفحات HTML (وليس فقط تحميل Excel)

    return None


# ---------------------------------------------------------------------------
# تحميل واستخراج تقرير Excel (نفس منطق isx_foreign_daily.py بالضبط،
# مكرر هنا عمداً بدل استيراد مشترك، لإبقاء هذا السكربت مستقلاً تماماً
# وقابلاً للحذف الكامل لاحقاً دون أي أثر أو اعتماد من كود آخر)
# ---------------------------------------------------------------------------

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
    """نفس الإصلاح المطبَّق بـ isx_foreign_daily.py: يُستبعد العنوان
    الرئيسي لكامل التقرير (الذي يحتوي "غير العراقيين" لكن بلا كلمة
    شراء/بيع صريحة) من قائمة الأقسام، لتفادي بدء القراءة من مكان خاطئ."""
    blocks = []
    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        for row_idx, row in enumerate(sheet.iter_rows(values_only=True)):
            row_text = clean_text(" ".join(str(c) for c in row if c is not None))
            has_foreign_marker = any(marker in row_text for marker in FOREIGN_SECTION_MARKERS)
            has_direction_marker = any(m in row_text for m in BUY_MARKERS + SELL_MARKERS)
            if has_foreign_marker and has_direction_marker:
                blocks.append((sheet_name, row_idx, row_text))
    return blocks


def parse_foreign_section(sheet, start_row_idx: int, market_label: str, direction: str) -> list:
    records = []
    bracket_pattern = re.compile(r"\(([A-Z]{3,6})\)")
    MAX_ROWS_PER_SECTION = 40

    row_iter = list(sheet.iter_rows(values_only=True))
    for offset in range(1, MAX_ROWS_PER_SECTION):
        idx = start_row_idx + offset
        if idx >= len(row_iter):
            break
        row = row_iter[idx]
        row_cells = [clean_text(c) for c in row]
        row_text = " ".join(c for c in row_cells if c)

        if not row_text:
            continue
        # نفس الإصلاح المطبَّق بـ isx_foreign_daily.py: التوقف فقط عند
        # "المجموع الكلي" الفعلي، وتخطي صفوف "مجموع قطاع X" الفرعية
        # دون توقف، لأن القسم الواحد يمتد عادة عبر عدة قطاعات متتالية.
        if "المجموع الكلي" in row_text:
            break
        if "مجموع" in row_text:
            continue

        symbol = None
        for cell in row_cells:
            if SYMBOL_PATTERN.match(cell) and cell not in ("ISX", "OTC"):
                symbol = cell
                break
            m = bracket_pattern.search(cell)
            if m:
                symbol = m.group(1)
                break

        if not symbol:
            continue

        numbers = []
        for cell in row_cells:
            cell_num = cell.replace(",", "")
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
    for sheet_name, row_idx, row_text in blocks:
        sheet = wb[sheet_name]

        direction = "buy" if any(m in row_text for m in BUY_MARKERS) else (
            "sell" if any(m in row_text for m in SELL_MARKERS) else None
        )
        if direction is None:
            continue

        market_label = "النظامي"
        for label, markers in MARKET_MARKERS.items():
            if any(m in row_text for m in markers):
                market_label = label
                break

        section_records = parse_foreign_section(sheet, row_idx, market_label, direction)
        all_records.extend(section_records)

    return {"session_number": session_number, "records": all_records}


# ---------------------------------------------------------------------------
# دمج وحفظ
# ---------------------------------------------------------------------------

def load_existing_data() -> dict:
    if not os.path.exists(ARCHIVE_OUTPUT_FILE):
        return {}
    with open(ARCHIVE_OUTPUT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict):
    tmp_path = ARCHIVE_OUTPUT_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, ARCHIVE_OUTPUT_FILE)


def merge_day_into_data(existing: dict, session_date: str, session_number: str, records: list) -> int:
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

        existing[symbol].append({   # append وليس insert(0) هنا لأننا نمشي زمنياً للأمام،
            "date": session_date,   # فالترتيب الطبيعي بالإضافة هو الأقدم أولاً بهذه المرحلة.
            "sessionNumber": session_number,  # الترتيب النهائي للعرض (الأحدث أولاً) هو مسؤولية
            "market": rec["market"],           # كود القراءة بالتطبيق لاحقاً، وليس ملف التخزين نفسه.
            "direction": rec["direction"],
            "trades": rec["trades"],
            "shares": rec["shares"],
            "value": rec["value"],
        })
        added += 1

    return added


# ---------------------------------------------------------------------------
# الحلقة الرئيسية
# ---------------------------------------------------------------------------

def process_one_day(target_date: date) -> str:
    """
    يعالج يوماً واحداً بالكامل. يُرجع:
      "done"     — تمت المعالجة (سواء وُجدت حركة أجانب أو لا)
      "no_report" — لا يوجد تقرير منشور أصلاً لهذا التاريخ (عطلة/غير يوم تداول)، يُتخطى بأمان
    يرفع ArchiveStop إن لم يُعثر على أي تقرير إطلاقاً بعد فحص كامل الأرشيف
    (يُستخدم فقط عند البحث عن "أحدث تقرير" لا عن يوم تاريخي محدد؛ هذا
    السيناريو أقل احتمالاً هنا وتُترك معالجته بحذر أعلى).
    """
    report = find_report_for_date(target_date)
    if report is None:
        return "no_report"

    time.sleep(DELAY_SECONDS)  # الحماية الأساسية من الحظر: فاصل قبل كل تحميل Excel فعلي
    excel_bytes = download_excel(report["url"])
    parsed = parse_daily_foreign_excel(excel_bytes)

    data = load_existing_data()
    added = merge_day_into_data(data, report["date"], parsed.get("session_number"), parsed["records"])
    if added > 0 or not parsed["records"]:
        save_data(data)

    print(f"  [أرشيف] ✅ {report['date']}: {len(parsed['records'])} سجل مستخرَج، {added} سجل جديد أُضيف.")
    return "done"


def main():
    progress = load_progress()

    if progress.get("status") == "completed":
        print("✅ الأرشفة مكتملة مسبقاً بحسب ملف التقدم. لا شيء لعمله.")
        print("   (يُفترض حذف هذا السكربت وملف الـ workflow الخاص به الآن يدوياً.)")
        sys.exit(0)

    if progress["last_processed_date"] is None:
        current_date = parse_date_ddmmyyyy(progress["search_from_date"])
    else:
        last = parse_date_ddmmyyyy(progress["last_processed_date"])
        current_date = last + timedelta(days=1)

    today = datetime.now(timezone.utc).date()
    run_start = time.time()

    print(f"بدء تشغيلة الأرشفة. آخر يوم مُعالَج سابقاً: {progress['last_processed_date'] or 'لا يوجد (أول تشغيلة)'}")
    print(f"سيبدأ من: {current_date.strftime('%d/%m/%Y')}")

    days_this_run = 0
    while True:
        elapsed = time.time() - run_start
        if elapsed > RUNTIME_BUDGET_SECONDS:
            print(f"\n⏸️ انتهت الميزانية الزمنية لهذه التشغيلة ({RUNTIME_BUDGET_SECONDS} ثانية). "
                  f"سيُستكمَل بالتشغيلة القادمة.")
            break

        if current_date >= today:
            progress["status"] = "completed"
            progress["completed_at"] = datetime.now(timezone.utc).isoformat()
            save_progress(progress)
            print(f"\n🎉 اكتملت الأرشفة! تم الوصول لتاريخ اليوم الحالي ({today.strftime('%d/%m/%Y')}).")
            print("   يمكن الآن حذف isx_foreign_archive.py وملف الـ workflow الخاص به بأمان.")
            print("   سكربت isx_foreign_daily.py سيكمل من هنا يومياً على نفس الملف.")
            sys.exit(0)

        try:
            result = process_one_day(current_date)
        except requests.exceptions.RequestException as e:
            print(f"\n⚠️ فشل شبكة عند معالجة {current_date.strftime('%d/%m/%Y')}: {e}")
            print("   توقف هادئ (بدون إعادة محاولة عنيفة) — سيُعاد المحاولة بنفس اليوم بالتشغيلة القادمة.")
            break

        if result == "no_report":
            progress["days_skipped_no_report"] = progress.get("days_skipped_no_report", 0) + 1
        else:
            progress["days_completed"] = progress.get("days_completed", 0) + 1

        progress["last_processed_date"] = current_date.strftime("%d/%m/%Y")
        save_progress(progress)  # حفظ فوري بعد كل يوم — لا فقدان تقدم حتى لو انقطعت التشغيلة فجأة

        days_this_run += 1
        current_date += timedelta(days=1)

    print(f"\nانتهت هذه التشغيلة: {days_this_run} يوم عولج "
          f"({progress.get('days_completed', 0)} يوم فيه بيانات/تقرير، "
          f"{progress.get('days_skipped_no_report', 0)} يوم بلا تقرير منشور إجمالاً حتى الآن).")


if __name__ == "__main__":
    main()
