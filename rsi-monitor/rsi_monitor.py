"""
rsi_monitor.py
--------------
يُشغّل يوميًا عبر GitHub Actions بعد تحديث isx_history_all.json.

لكل سهم:
  1. يحسب RSI الحالي (Wilder، period=14) من البيانات المحدّثة.
  2. يقارنه بالقاع المحفوظ في isx_rsi_lows.json.
  3. لو RSI الحالي أعمق (أوطأ) من القاع المحفوظ:
       - يحدّث القاع تلقائيًا للرقم الجديد.
       - يرسل تنبيه "قاع تاريخي جديد".
  4. لو RSI الحالي ضمن نطاق (القاع المحفوظ + APPROACH_MARGIN):
       - يرسل تنبيه "اقتراب من القاع" (بدون تحديث القاع).
  5. يحفظ isx_rsi_lows.json المحدّث ويرسل كل التنبيهات دفعة واحدة برسالة تلغرام واحدة.

المتغيرات البيئية المطلوبة (تُضبط كـ GitHub Secrets):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

الاستخدام:
  python rsi_monitor.py [isx_history_all.json] [isx_rsi_lows.json]
"""

import json
import os
import sys
import datetime
import urllib.request
import urllib.parse

from rsi_engine import compute_symbol_rsi

RSI_PERIOD = 14
APPROACH_MARGIN = 5.0  # نطاق "الاقتراب من القاع" (بالنقاط RSI)
EXCLUDED_ZERO_WARN_THRESHOLD = 50


def count_excluded_zero(recs):
    total_dated = len([r for r in recs if r.get('date')])
    valid = 0
    for r in recs:
        if not r.get('date') or not r.get('close'):
            continue
        try:
            c = float(r['close'])
        except (ValueError, TypeError):
            continue
        if c > 0:
            valid += 1
    return total_dated - valid


def send_telegram_message(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')

    if not token or not chat_id:
        print("تحذير: TELEGRAM_BOT_TOKEN أو TELEGRAM_CHAT_ID غير مضبوطين. تخطي الإرسال.")
        print("--- محتوى الرسالة كان سيكون ---")
        print(text)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    data = urllib.parse.urlencode(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp_body = resp.read().decode('utf-8')
            result = json.loads(resp_body)
            if not result.get('ok'):
                print("فشل إرسال تلغرام:", resp_body)
                return False
            return True
    except Exception as e:
        print("خطأ أثناء إرسال تلغرام:", e)
        return False


def load_baseline(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def run_monitor(history_path, baseline_path):
    with open(history_path, 'r', encoding='utf-8') as f:
        history = json.load(f)

    baseline = load_baseline(baseline_path)
    symbols_db = baseline.setdefault('symbols', {})
    baseline.setdefault('meta', {})['rsi_period'] = RSI_PERIOD

    new_low_alerts = []
    approach_alerts = []
    new_symbols_added = []

    for sym, recs in history.items():
        result = compute_symbol_rsi(recs, period=RSI_PERIOD)
        if result is None:
            continue  # بيانات غير كافية بعد

        rsi_values = result['rsi_values']
        closes = result['closes']
        dates = result['dates']

        current_rsi = rsi_values[-1]
        current_date = dates[-1]
        current_close = closes[-1]

        if current_rsi is None:
            continue

        entry = symbols_db.get(sym)

        if entry is None:
            # سهم جديد لسه ما بالقاعدة: نحسب قاعه التاريخي الكامل ونضيفه
            min_rsi, min_date, min_idx = None, None, None
            for i, v in enumerate(rsi_values):
                if v is not None and (min_rsi is None or v < min_rsi):
                    min_rsi, min_date, min_idx = v, dates[i], i
            excluded_zero = count_excluded_zero(recs)
            symbols_db[sym] = {
                'min_rsi': round(min_rsi, 4),
                'min_rsi_date': min_date.strftime('%Y-%m-%d'),
                'close_at_min_rsi': round(closes[min_idx], 4),
                'last_rsi': round(current_rsi, 4),
                'last_rsi_date': current_date.strftime('%Y-%m-%d'),
                'last_close': round(current_close, 4),
                'records_used': len(closes),
                'excluded_zero_close': excluded_zero,
                'data_quality_warning': excluded_zero > EXCLUDED_ZERO_WARN_THRESHOLD,
                'last_updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            }
            new_symbols_added.append(sym)
            continue

        saved_min = entry['min_rsi']

        # تحديث last_rsi دائمًا (بغض النظر عن التنبيهات)
        entry['last_rsi'] = round(current_rsi, 4)
        entry['last_rsi_date'] = current_date.strftime('%Y-%m-%d')
        entry['last_close'] = round(current_close, 4)
        entry['records_used'] = len(closes)
        entry['last_updated'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        # نقارن بعد التقريب لنفس عدد الخانات المخزّنة، لتجنب اعتبار فروقات
        # تقريب دقيقة عشرية (مثل 5.18720 مقابل 5.1872 المخزّنة) "قاع جديد" وهميًا
        current_rsi_rounded = round(current_rsi, 4)

        if current_rsi_rounded < saved_min:
            # قاع تاريخي جديد أعمق -> نحدّث ونرسل تنبيه
            old_min = saved_min
            entry['min_rsi'] = round(current_rsi, 4)
            entry['min_rsi_date'] = current_date.strftime('%Y-%m-%d')
            entry['close_at_min_rsi'] = round(current_close, 4)
            new_low_alerts.append({
                'symbol': sym,
                'old_min': old_min,
                'new_min': current_rsi_rounded,
                'close': current_close,
                'date': current_date.strftime('%Y-%m-%d'),
            })
        elif current_rsi_rounded <= saved_min + APPROACH_MARGIN:
            gap = round(current_rsi_rounded - saved_min, 4)
            # نتجاهل تنبيه "اقتراب" لو الفرق صفر بالضبط وكان القاع المحفوظ
            # ناتج عن بيانات مشوهة (data_quality_warning) -- غالبًا السهم عالق
            # عند آخر يوم بيانات مشوّهة، مو اقتراب حقيقي من قاع سعري له معنى.
            is_zero_gap_bad_data = (gap == 0.0) and entry.get('data_quality_warning', False)
            if is_zero_gap_bad_data:
                pass  # نتخطى -- لا تنبيه لتكرار قاع مشوّه يوميًا
            else:
                approach_alerts.append({
                    'symbol': sym,
                    'current_rsi': current_rsi_rounded,
                    'saved_min': saved_min,
                    'gap': gap,
                    'close': current_close,
                    'date': current_date.strftime('%Y-%m-%d'),
                })
    baseline['meta']['last_run'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    baseline['meta']['symbols_count'] = len(symbols_db)

    with open(baseline_path, 'w', encoding='utf-8') as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)

    # بناء رسالة تلغرام واحدة تجمع كل التنبيهات
    if new_low_alerts or approach_alerts:
        lines = []
        today = datetime.date.today().strftime('%Y-%m-%d')
        lines.append(f"📊 <b>تقرير RSI اليومي - سوق العراق</b> ({today})")

        if new_low_alerts:
            lines.append("")
            lines.append("🔴 <b>قيعان RSI تاريخية جديدة:</b>")
            new_low_alerts.sort(key=lambda a: a['new_min'])
            for a in new_low_alerts:
                lines.append(
                    f"• <b>{a['symbol']}</b>: RSI {a['new_min']} "
                    f"(قاع سابق {a['old_min']}) — سعر {a['close']} — {a['date']}"
                )

        if approach_alerts:
            lines.append("")
            lines.append(f"🟡 <b>اقتراب من القاع (فرق ≤ {APPROACH_MARGIN}):</b>")
            approach_alerts.sort(key=lambda a: a['gap'])
            for a in approach_alerts:
                lines.append(
                    f"• <b>{a['symbol']}</b>: RSI {a['current_rsi']} "
                    f"(القاع {a['saved_min']}, فرق {a['gap']}) — سعر {a['close']} — {a['date']}"
                )

        message = '\n'.join(lines)
        send_telegram_message(message)
        print(message)
    else:
        print("لا توجد تنبيهات اليوم — لا سهم اقترب أو تجاوز قاعه المحفوظ.")

    if new_symbols_added:
        print(f"أسهم جديدة أُضيفت للقاعدة: {', '.join(new_symbols_added)}")

    print(f"تم فحص {len(history)} سهم. قيعان جديدة: {len(new_low_alerts)}. اقتراب: {len(approach_alerts)}.")


if __name__ == '__main__':
    history_path = sys.argv[1] if len(sys.argv) > 1 else 'isx_history_all.json'
    baseline_path = sys.argv[2] if len(sys.argv) > 2 else 'isx_rsi_lows.json'
    run_monitor(history_path, baseline_path)
