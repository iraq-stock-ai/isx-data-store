import os
import re
import json
import openpyxl

# ==============================================================================
# 1. إعدادات الثوابت والأنماط
# ==============================================================================
FOREIGN_SECTION_MARKERS = ["غير العراقيين", "غيرالعراقيين", "الأجانب", "اجانب", "أجانب"]
BUY_MARKERS = ["المشتراة", "مشتراة"]
SELL_MARKERS = ["المباعة", "مباعة"]

# كلمات يتم استبعادها عند البحث عن رمز الشركة
IGNORE_WORDS = {"ISX", "OTC", "TOTAL", "DATE", "TYPE", "BUY", "SELL", "المجموع", "مجموع"}

# ==============================================================================
# 2. دوال تنظيف النص واستخراج اسم السوق
# ==============================================================================
def clean_text(txt) -> str:
    """تنظيف النص من المسافات المخفية ورموز الاتجاه والأسطر الجديدة."""
    if txt is None:
        return ""
    txt = str(txt)
    # إزالة المسافات الخفية \xa0 ورموز الاتجاه العربي \u200f / \u200e
    txt = txt.replace("\xa0", " ").replace("\u200f", "").replace("\u200e", "").replace("\ufeff", "")
    txt = txt.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt

def extract_market_label(section_title_text: str) -> str:
    """استخراج اسم السوق بدقة عالية بناءً على الكلمات المفتاحية."""
    txt = clean_text(section_title_text)

    # 1. المطابقة بالمباشر للكلمات المفتاحية (الأعلى دقة)
    if any(k in txt for k in ["غير المفصحة", "غير مفصحة", "الثالث", "otc"]):
        return "الشركات غير المفصحة"
    if any(k in txt for k in ["الثاني", "الثانية"]):
        return "الثاني"
    if any(k in txt for k in ["النظامي", "النظامية"]):
        return "النظامي"

    # 2. محاولة استخراج عبر Regex في حال وجود صياغة غير معيارية
    match = re.search(r"(?:في|منصة|سوق)\s+(.+?)(?:\s+لجلسة|\s*$)", txt)
    if match:
        raw_market = clean_text(match.group(1))
        if "ثاني" in raw_market:
            return "الثاني"
        if "مفصح" in raw_market or "ثالث" in raw_market:
            return "الشركات غير المفصحة"
        if "نظام" in raw_market:
            return "النظامي"
        if raw_market:
            return raw_market

    # الافتراضي في سوق العراق للأوراق المالية
    return "النظامي"

# ==============================================================================
# 3. دالة اكتشاف كتل تداول غير العراقيين في شيتات العمل
# ==============================================================================
def find_foreign_trading_blocks(wb) -> list:
    """البحث عن كتل التداول مع دمج سياق الأسطر المجاورة لمنع ضياع العنوان المقسم."""
    blocks = []
    
    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        rows = list(sheet.iter_rows(values_only=True))
        num_rows = len(rows)

        for row_idx in range(num_rows):
            curr_text = clean_text(" ".join(str(c) for c in rows[row_idx] if c is not None))

            has_foreign = any(m in curr_text for m in FOREIGN_SECTION_MARKERS)
            has_direction = any(m in curr_text for m in BUY_MARKERS + SELL_MARKERS)

            if has_foreign and has_direction:
                # تجميع السطر الحالي والسطرين التاليين للوصول لاسم السوق كاملاً
                header_lines = []
                for w_idx in range(row_idx, min(row_idx + 3, num_rows)):
                    w_text = clean_text(" ".join(str(c) for c in rows[w_idx] if c is not None))
                    # إيقاف التجميع إذا وصلنا لرؤوس أعمدة الجدول
                    if any(col in w_text for col in ["رمز الشركة", "اسم الشركة", "الصفقات"]):
                        break
                    header_lines.append(w_text)

                full_header = " ".join(header_lines)
                blocks.append((sheet_name, row_idx, full_header))

    return blocks

# ==============================================================================
# 4. دالة تحليل واستخراج صفوف الجدول للكتلة المحددة
# ==============================================================================
def parse_foreign_section(sheet, start_row_idx: int, market_label: str, direction: str) -> list:
    """قراءة أسهم الشركات والصفقات والكميات والقيم داخل القسم المحدد."""
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

        # إيقاف القراءة عند الوصول للمجموع الكلي أو عنوان نشرة جديدة
        if any(term in row_text for term in ["المجموع الكلي", "مجموع الكلي", "مجموع السوق"]):
            break
        if any(m in row_text for m in FOREIGN_SECTION_MARKERS) and any(m in row_text for m in BUY_MARKERS + SELL_MARKERS):
            break

        # التجاوز عن أسطر القطاعات والمجاميع الفرعية للقطاع
        if "مجموع" in row_text or "قطاع" in row_text:
            continue

        # 1. استخراج رمز الشركة
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

        # 2. استخراج الأرقام (الصفقات، الأسهم المتداولة، القيمة المتداولة)
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
            "value": value
        })

    return records

# ==============================================================================
# 5. قراءة ملف Excel اليومي كاملاً
# ==============================================================================
def parse_daily_foreign_excel(file_path: str, session_date: str, session_number: str) -> dict:
    """فتح ملف Excel واستخراج جميع معاملات غير العراقيين لجميع الأسواق."""
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
    except Exception as e:
        print(f"❌ خطأ في فتح الملف {file_path}: {e}")
        return {"session_number": session_number, "records": []}

    blocks = find_foreign_trading_blocks(wb)
    all_records = []

    for sheet_name, row_idx, row_text in blocks:
        sheet = wb[sheet_name]
        
        # تحديد الاتجاه (شراء / بيع)
        direction = "buy" if any(m in row_text for m in BUY_MARKERS) else (
            "sell" if any(m in row_text for m in SELL_MARKERS) else None
        )
        if not direction:
            continue

        # استخراج اسم السوق
        market_label = extract_market_label(row_text)

        # استخراج السجلات
        recs = parse_foreign_section(sheet, row_idx, market_label, direction)
        all_records.extend(recs)

    return {
        "session_number": session_number,
        "records": all_records
    }

# ==============================================================================
# 6. دالة دمج وتحديث البيانات في ملف JSON
# ==============================================================================
def merge_records_smart(existing_db: dict, session_date: str, session_number: str, records: list) -> tuple:
    """دمج السجلات وتحديث البيانات القديمة الخاطئة تلقائياً."""
    added = 0
    updated = 0

    for rec in records:
        symbol = rec["symbol"]
        if symbol not in existing_db:
            existing_db[symbol] = []

        # البحث عن سجل سابق بنفس التاريخ والاتجاه
        matched_idx = -1
        for idx, r in enumerate(existing_db[symbol]):
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
            "value": rec["value"]
        }

        if matched_idx != -1:
            existing_rec = existing_db[symbol][matched_idx]
            # تحديث السجل إذا تغير اسم السوق إلى اسم أدق أو اختلفت القيم
            if existing_rec.get("market") != rec["market"] or existing_rec.get("value") != rec["value"]:
                existing_db[symbol][matched_idx] = new_entry
                updated += 1
        else:
            existing_db[symbol].append(new_entry)
            added += 1

    return added, updated
