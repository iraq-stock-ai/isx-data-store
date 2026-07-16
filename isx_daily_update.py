import requests
from bs4 import BeautifulSoup
import re

url = "http://www.isx-iq.net/isxportal/portal/storyList.html?activeTab=0"
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-IQ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
}

try:
    print("🔄 جاري الاتصال والتحليل الشامل لموقع السوق...")
    resp = requests.get(url, headers=headers, timeout=25)
    print(f"Status Code: {resp.status_code}")
    
    # تحويل المحتوى إلى نصوص
    html_content = resp.text
    soup = BeautifulSoup(resp.content, "html.parser")
    
    # 1. فحص وجود إطارات داخلية (iframes)
    iframes = soup.find_all("iframe")
    print(f"\n🖥️ [1] عدد الإطارات (iframes): {len(iframes)}")
    for idx, iframe in enumerate(iframes, 1):
        print(f"   - إطار {idx}: Src={iframe.get('src')} | ID={iframe.get('id')}")
        
    # 2. فحص الجداول (Tables)
    tables = soup.find_all("table")
    print(f"\n📊 [2] عدد الجداول في الصفحة: {len(tables)}")
    for idx, table in enumerate(tables[:3], 1):
        rows = table.find_all("tr")
        print(f"   - جدول {idx}: يحتوي على {len(rows)} صفوف (tr)")

    # 3. البحث عن روابط الأخبار النصية (حتى لو كانت داخل كود جافا سكربت أو أزرار)
    # سنبحث عن أي نص يحتوي على "storyDetails" أو "storyId" أو "story"
    matches = re.findall(r"storyDetails[^\s'\"<>]+", html_content, re.IGNORECASE)
    print(f"\n🔗 [3] الإشارات النصية لروابط الأخبار (storyDetails): {len(matches)}")
    if matches:
        print("   - عينة من الإشارات المكتشفة:")
        for m in list(set(matches))[:10]: # طباعة الفريدة منها فقط
            print(f"     * {m}")

    # 4. فحص أكواد الجافا سكربت الداخلية بحثاً عن طلبات جلب بيانات (AJAX / Fetch)
    print("\n📜 [4] فحص الجافا سكربت بحثاً عن استدعاءات:")
    scripts = soup.find_all("script")
    keywords = ["$.ajax", "$.get", "$.post", "fetch", "xmlhttprequest", "load", "activeTab", "tab"]
    found_any = False
    for s_idx, script in enumerate(scripts, 1):
        script_text = script.string or ""
        src = script.get("src")
        if src:
            if any(k in src.lower() for k in ["story", "news", "list", "tab"]):
                print(f"   - ملف جافا سكربت خارجي مشبوه: {src}")
                found_any = True
        else:
            lines = script_text.split("\n")
            for l_idx, line in enumerate(lines, 1):
                line_strip = line.strip()
                if any(k in line_strip.lower() for k in keywords):
                    # طباعة السطر الذي يحتوي الكلمة المفتاحية (بحد أقصى 120 حرف)
                    print(f"   - سكربت {s_idx} (سطر {l_idx}): {line_strip[:120]}")
                    found_any = True
    if not found_any:
        print("   - لم يتم العثور على كلمات مفتاحية للاستدعاءات في الجافا سكربت.")

except Exception as e:
    print(f"❌ حدث خطأ أثناء التحليل: {e}")
