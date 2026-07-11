import argparse
import io
import os
import re
import sys
import json
from datetime import datetime, timezone, date, timedelta

import requests
from bs4 import BeautifulSoup
import openpyxl

LIST_URL = "http://www.isx-iq.net/isxportal/portal/uploadedFilesList.html"
BASE_URL = "http://www.isx-iq.net"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# حدود بوابة التحقق (بند 4 و6 بالتوثيق أعلاه) — قابلة للتعديل بسهولة
MIN_EXPECTED_SYMBOLS = 80
MAX_EXPECTED_SYMBOLS = 130
MAX_REJECTED_RATIO = 0.10  # 10%


class QualityGateError(Exception):
    """يُرفع عند فشل أي بوابة تحقق — يوقف السكريبت فوراً دون أي كتابة."""
    pass


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


def get_today_daily_report():
    """
    بوابة تحقق 1: يجلب أول صف 'يومي' من الصفحة الأولى (الأحدث) لأرشيف
    uploadedFilesList.html فقط — لا يتصفح صفحات إضافية، لأن هذا سكريبت
    يومي يتوقع دائماً تقريراً واحداً جديداً بالمقدمة.
    """
    print(f"[بوابة 1] جاري فحص: {LIST_URL}")
    resp = requests.get(LIST_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    table = soup.find("table")
    if table is None:
        raise QualityGateError("لم يُعثر على جدول الأرشيف بالصفحة الرئيسية.")

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
        session_date = date_match.group(1) if date_match else None

        full_url = href if href.startswith("http") else BASE_URL + href
        print(f"[بوابة 1] ✅ نجحت — تقرير يومي بتاريخ {session_date}: {full_url}")
        return {"date": session_date, "url": full_url}

    raise QualityGateError("لم يُعثر على أي صف 'التقرير اليومي' بالصفحة الأولى من الأرشيف.")


def validate_report_date(report_date_str: str):
    """
    بوابة تحقق 2: تاريخ التقرير يجب أن يكون اليوم أو الأمس (بتوقيت UTC،
    مع هامش يوم واحد لفروق التوقيت والتأخر المحتمل بنشر التقرير).

    يمنع سيناريو: الموقع يعرض بالخطأ تقريراً قديماً كـ"الأحدث" (مثلاً
    بسبب عطل بالموقع)، فيُعاد استيراد يوم قديم فوق بيانات موجودة أصلاً،
    أو الأسوأ: يُستبدل تاريخ حديث ببيانات قديمة إن وُجد تكرار بالمعالجة.
    """
    report_date = parse_date_ddmmyyyy(report_date_str)
    if report_date is None:
        raise QualityGateError(f"تعذّر تحليل تاريخ التقرير: '{report_date_str}'")

    today = datetime.now(timezone.utc).date()
    diff_days = (today - report_date).days

    if diff_days < 0:
        raise QualityGateError(f"تاريخ التقرير بالمستقبل؟! ({report_date}) — مشبوه، يوقَف التنفيذ.")
    if diff_days > 4:
        # حتى مع عطلات نهاية الأسبوع (الجمعة/السبت بالعراق)، فجوة أكبر من
        # 4 أيام تعني على الأرجح أن الصفحة تعرض بيانات قديمة عالقة
        raise QualityGateError(
            f"تاريخ التقرير قديم جداً ({report_date}، أقدم بـ{diff_days} يوماً من اليوم) "
            f"— يُشتبه أن الموقع متوقف عن التحديث أو يعرض بيانات عالقة."
        )

    print(f"[بوابة 2] ✅ نجحت — تاريخ التقرير ({report_date}) ضمن نطاق معقول ({diff_days} يوم فرق).")
    return report_date


def download_excel(url: str):
    """بوابة تحقق 3: يفشل بوضوح (يرفع استثناء) عند أي خطأ شبكة."""
    print(f"[بوابة 3] جاري تحميل: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        raise QualityGateError(f"فشل تحميل ملف Excel: {e}")
    print(f"[بوابة 3] ✅ نجحت — تم تحميل {len(resp.content)} بايت.")
    return resp.content


def find_header_row_and_map(sheet):
    """نفس منطق isx_backfill_and_export.py المُختبَر — يقرأ صف الرأس
    الفعلي ويربط كل عمود باسمه، بدل الاعتماد على موقع نسبي ثابت."""
    HEADER_ALIASES = {
        "open": ["فتح"],
        "high": ["أعلى", "اعلى"],
        "low": ["أدنى", "ادنى"],
        "close": ["اغلاق", "إغلاق"],
        "volume": ["الأسهم المتداولة", "الاسهم المتداولة"],
        "value": ["القيمة المتداولة", "القيمة المتدوالة"],
        "trades": ["عدد الصفقات"],
    }
    SYMBOL_HEADER_ALIASES = ["الرمز", "رمز الشركة", "Symbol"]

    for row_idx, row in enumerate(sheet.iter_rows(max_row=15, values_only=True)):
        row_texts = [clean_text(c) for c in row]
        col_map = {}
        symbol_col = None
        for col_idx, text in enumerate(row_texts):
            if not text:
                continue
            for field, aliases in HEADER_ALIASES.items():
                if text in aliases:
                    col_map[field] = col_idx
            if text in SYMBOL_HEADER_ALIASES:
                symbol_col = col_idx

        core_fields = {"open", "high", "low", "close"}
        if core_fields.issubset(col_map.keys()):
            return row_idx, col_map, symbol_col

    return None, {}, None


def extract_session_date_from_excel(sheet) -> str:
    date_pattern = re.compile(r"(\d{4}/\d{1,2}/\d{1,2})")
    for row in sheet.iter_rows(max_row=5, values_only=True):
        for cell in row:
            if cell:
                match = date_pattern.search(str(cell))
                if match:
                    y, m, d = match.group(1).split("/")
                    return f"{int(d):02d}/{int(m):02d}/{y}"
    return None


def parse_daily_excel(excel_bytes: bytes) -> dict:
    """يستخرج كل الأسهم، بالاعتماد على أسماء الأعمدة الفعلية (نفس منطق
    isx_backfill_and_export.py المُختبَر مسبقاً)."""
    wb = openpyxl.load_workbook(io.BytesIO(excel_bytes), data_only=True)
    results = {}

    pure_symbol_pattern = re.compile(r"^[A-Z]{3,5}$")
    bracket_pattern = re.compile(r"\((_?[A-Z]{3,5})\)")

    session_date = None
    if wb.sheetnames:
        session_date = extract_session_date_from_excel(wb[wb.sheetnames[0]])

    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        header_row_idx, col_map, symbol_col = find_header_row_and_map(sheet)
        if header_row_idx is None:
            continue

        for row_idx, row in enumerate(sheet.iter_rows(values_only=True)):
            if row_idx <= header_row_idx:
                continue
            row_str = [clean_text(c) for c in row]

            symbol = ""
            if symbol_col is not None and symbol_col < len(row_str):
                candidate = row_str[symbol_col]
                if pure_symbol_pattern.match(candidate):
                    symbol = candidate
            if not symbol:
                for text in row_str:
                    if not text:
                        continue
                    if pure_symbol_pattern.match(text) and text not in ["ISX", "OTC", "NONE"]:
                        symbol = text
                        break
                    m = bracket_pattern.search(text)
                    if m:
                        symbol = m.group(1)
                        break

            if not symbol or symbol in results:
                continue

            def get_field(field_name):
                col = col_map.get(field_name)
                if col is None or col >= len(row_str):
                    return "-"
                v = row_str[col]
                return v if v and v != "None" else "-"

            results[symbol] = {
                "date": session_date or "",
                "open": get_field("open"),
                "high": get_field("high"),
                "low": get_field("low"),
                "close": get_field("close"),
                "volume": get_field("volume"),
                "value": get_field("value"),
                "trades": get_field("trades"),
            }

    return results


def check_symbol_count(day_data: dict):
    """بوابة تحقق 4: عدد الأسهم المستخرجة ضمن نطاق معقول."""
    count = len(day_data)
    if count < MIN_EXPECTED_SYMBOLS or count > MAX_EXPECTED_SYMBOLS:
        raise QualityGateError(
            f"عدد الأسهم المستخرجة ({count}) خارج النطاق المتوقع "
            f"({MIN_EXPECTED_SYMBOLS}-{MAX_EXPECTED_SYMBOLS}) — يُشتبه بخلل بنيوي بالملف."
        )
    print(f"[بوابة 4] ✅ نجحت — {count} سهم مستخرج (ضمن النطاق المتوقع).")


def validate_records(day_data: dict):
    """بوابة تحقق 5 و6: فحص High/Low/Close لكل سجل، ورفض السجلات
    المخالفة، مع التحقق أن نسبة المرفوض لا تتجاوز الحد الأقصى."""
    valid = {}
    rejected = []

    for symbol, r in day_data.items():
        def as_float(key):
            v = r.get(key)
            if v in (None, "-", ""):
                return None
            try:
                return float(str(v).replace(",", ""))
            except (ValueError, TypeError):
                return None

        high, low, close = as_float("high"), as_float("low"), as_float("close")
        problems = []
        if high is not None and low is not None and high < low:
            problems.append(f"High({high}) < Low({low})")
        if close is not None and high is not None and low is not None and not (low <= close <= high):
            problems.append(f"Close({close}) خارج [{low}, {high}]")

        if problems:
            rejected.append(f"{symbol}: " + " | ".join(problems))
        else:
            valid[symbol] = r

    ratio = len(rejected) / len(day_data) if day_data else 1.0
    print(f"[بوابة 5] فحص جودة السجلات: {len(valid)} سليم، {len(rejected)} مرفوض من أصل {len(day_data)}.")
    if rejected:
        for w in rejected[:10]:  # لا نطبع أكثر من 10 سطر تحذير تجنباً لإغراق السجل
            print(f"           - {w}")

    if ratio > MAX_REJECTED_RATIO:
        raise QualityGateError(
            f"نسبة السجلات المرفوضة ({ratio:.1%}) تتجاوز الحد المسموح "
            f"({MAX_REJECTED_RATIO:.0%}) — يُشتبه بخلل بنيوي (مثل انزياح أعمدة)، "
            f"وليس أخطاء بيانات معزولة."
        )
    print(f"[بوابة 5+6] ✅ نجحتا — نسبة الرفض ({ratio:.1%}) ضمن الحد المسموح.")
    return valid


def load_existing(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: str):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def main():
    parser = argparse.ArgumentParser(description="التحديث اليومي التلقائي لأرشيف أسهم ISX (لـ GitHub Actions)")
    parser.add_argument("--existing", default="isx_history_all.json")
    parser.add_argument("--output", default="isx_history_all.json")
    args = parser.parse_args()

    try:
        report = get_today_daily_report()               # بوابة 1
        validate_report_date(report["date"])             # بوابة 2
        excel_bytes = download_excel(report["url"])       # بوابة 3
        day_data = parse_daily_excel(excel_bytes)
        check_symbol_count(day_data)                      # بوابة 4
        valid_data = validate_records(day_data)            # بوابة 5+6

    except QualityGateError as e:
        print(f"\n❌ توقف التنفيذ — فشلت بوابة تحقق: {e}", file=sys.stderr)
        print("لن يتم تعديل isx_history_all.json. الملف بالمستودع يبقى كما هو.", file=sys.stderr)
        sys.exit(1)  # GitHub Actions سيقرأ هذا كفشل، ولن يعمل commit

    except Exception as e:
        print(f"\n❌ خطأ غير متوقع: {e}", file=sys.stderr)
        sys.exit(1)

    # كل البوابات نجحت — الآن فقط ندمج ونحفظ
    print("\n✅ كل بوابات التحقق نجحت. جاري الدمج مع الأرشيف الموجود...")

    data = load_existing(args.existing)
    existing_dates = {sym: {r["date"] for r in recs if r.get("date")} for sym, recs in data.items()}

    added_count = 0
    skipped_count = 0
    for symbol, record in valid_data.items():
        record_date = record.get("date") or report["date"]
        record["date"] = record_date

        if symbol not in data:
            data[symbol] = []
            existing_dates[symbol] = set()

        if record_date in existing_dates[symbol]:
            skipped_count += 1
            continue  # هذا اليوم مُضاف مسبقاً لهذا السهم (تشغيل مكرر لنفس اليوم)

        data[symbol].insert(0, record)
        existing_dates[symbol].add(record_date)
        added_count += 1

    if added_count == 0:
        print(f"لا توجد سجلات جديدة لإضافتها (كلها موجودة مسبقاً — {skipped_count} تم تخطيها). "
              f"لن يُعدَّل الملف.")
        sys.exit(0)  # نجاح، لكن بلا تغيير — GitHub Actions لن يجد فرقاً ليعمل commit له

    save_json(data, args.output)

    print(f"\n✅ تم بنجاح:")
    print(f"   - تاريخ الجلسة المضافة: {report['date']}")
    print(f"   - عدد الأسهم المحدَّثة: {added_count}")
    print(f"   - عدد الأسهم المتخطاة (مكررة): {skipped_count}")
    print(f"   - إجمالي الأسهم بالملف: {len(data)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
