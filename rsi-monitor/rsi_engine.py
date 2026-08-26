"""
محرك حساب RSI بطريقة Wilder (مطابق لإعدادات TradingView الافتراضية: period=14, source=Close).
يُستخدم من قبل build_baseline.py و rsi_monitor.py لضمان نفس المنطق بالضبط في كل مكان.
"""

import datetime


def wilder_rsi(closes, period=14):
    """
    Wilder's RSI classic implementation.
    closes: قائمة أسعار إغلاق مرتبة زمنيًا تصاعديًا (float)
    returns: قائمة RSI بنفس طول closes (None للمواضع قبل توفر أول قيمة RSI)
    """
    n = len(closes)
    rsi = [None] * n
    if n < period + 1:
        return rsi

    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    idx = period
    if avg_loss == 0:
        rsi[idx] = 100.0 if avg_gain > 0 else 50.0
    else:
        rs = avg_gain / avg_loss
        rsi[idx] = 100 - (100 / (1 + rs))

    for i in range(period, len(deltas)):
        gain = gains[i]
        loss = losses[i]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        idx = i + 1
        if avg_loss == 0:
            rsi[idx] = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi[idx] = 100 - (100 / (1 + rs))

    return rsi


def load_clean_series(recs):
    """
    تنظيف سجلات سهم واحد من isx_history_all.json:
    - استبعاد close<=0 أو فارغ (تعليق تداول)
    - استبعاد تكرار نفس التاريخ (نحتفظ بأول ظهور فقط)
    - ترتيب تصاعدي بالتاريخ
    يُرجع: (dates: list[datetime], closes: list[float])
    """
    seen_dates = set()
    parsed = []
    for r in recs:
        if not r.get('date') or not r.get('close'):
            continue
        try:
            c = float(r['close'])
        except (ValueError, TypeError):
            continue
        if c <= 0:
            continue
        if r['date'] in seen_dates:
            continue
        seen_dates.add(r['date'])
        try:
            d = datetime.datetime.strptime(r['date'], '%d/%m/%Y')
        except ValueError:
            continue
        parsed.append((d, c))

    parsed.sort(key=lambda x: x[0])
    dates = [p[0] for p in parsed]
    closes = [p[1] for p in parsed]
    return dates, closes


def compute_symbol_rsi(recs, period=14):
    """
    يحسب سلسلة RSI كاملة لسهم واحد من سجلاته الخام.
    يُرجع: dict فيه dates, closes, rsi_values أو None لو البيانات غير كافية.
    """
    dates, closes = load_clean_series(recs)
    if len(closes) < period + 1:
        return None
    rsi_values = wilder_rsi(closes, period=period)
    return {
        'dates': dates,
        'closes': closes,
        'rsi_values': rsi_values,
    }
