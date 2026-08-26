"""
build_baseline.py
------------------
يبني ملف القاعدة المرجعية isx_rsi_lows.json من isx_history_all.json.
يُشغّل مرة واحدة يدويًا لإنشاء القاعدة، أو لإعادة بنائها بالكامل عند الحاجة.
بعد ذلك، rsi_monitor.py يحدّث هذا الملف تلقائيًا يوميًا (تحديث تراكمي، مو إعادة بناء).

الاستخدام:
    python build_baseline.py [isx_history_all.json] [isx_rsi_lows.json]
"""

import json
import sys
import datetime
from rsi_engine import compute_symbol_rsi

RSI_PERIOD = 14
# نتجاهل أي سهم كان له أكثر من هذا العدد من سجلات close=0 المستبعدة نسبة لإجمالي
# سجلاته (مؤشر بيانات متقطعة جدًا / تعليق تداول طويل) عند وضع علامة تحذير الجودة،
# لكن ما نستبعده من القائمة إطلاقًا -- فقط نعلّمه بحقل data_quality_warning.
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


def build_baseline(history_path, output_path):
    with open(history_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    baseline = {}
    skipped = []

    for sym, recs in data.items():
        result = compute_symbol_rsi(recs, period=RSI_PERIOD)
        if result is None:
            skipped.append(sym)
            continue

        dates = result['dates']
        closes = result['closes']
        rsi_values = result['rsi_values']

        # أدنى RSI عبر كامل السلسلة المتوفرة
        min_rsi, min_date, min_idx = None, None, None
        for i, v in enumerate(rsi_values):
            if v is not None and (min_rsi is None or v < min_rsi):
                min_rsi, min_date, min_idx = v, dates[i], i

        if min_rsi is None:
            skipped.append(sym)
            continue

        excluded_zero = count_excluded_zero(recs)

        baseline[sym] = {
            'min_rsi': round(min_rsi, 4),
            'min_rsi_date': min_date.strftime('%Y-%m-%d'),
            'close_at_min_rsi': round(closes[min_idx], 4),
            'last_rsi': round(rsi_values[-1], 4) if rsi_values[-1] is not None else None,
            'last_rsi_date': dates[-1].strftime('%Y-%m-%d'),
            'last_close': round(closes[-1], 4),
            'records_used': len(closes),
            'excluded_zero_close': excluded_zero,
            'data_quality_warning': excluded_zero > EXCLUDED_ZERO_WARN_THRESHOLD,
            'last_updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        }

    output = {
        'meta': {
            'rsi_period': RSI_PERIOD,
            'methodology': 'Wilder RSI, source=close, matches TradingView default',
            'built_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'symbols_count': len(baseline),
            'symbols_skipped': skipped,
        },
        'symbols': baseline,
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"تم بناء القاعدة: {len(baseline)} سهم، {len(skipped)} مستبعد (بيانات غير كافية)")
    if skipped:
        print("المستبعدون:", ', '.join(skipped))


if __name__ == '__main__':
    history_path = sys.argv[1] if len(sys.argv) > 1 else 'isx_history_all.json'
    output_path = sys.argv[2] if len(sys.argv) > 2 else 'isx_rsi_lows.json'
    build_baseline(history_path, output_path)
