import os
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai

# إعداد مفتاح API لـ Gemini
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
else:
    print("تحذير: لم يتم العثور على مفتاح GEMINI_API_KEY في البيئة!")

# محاولة استيراد مكتبة قراءة الـ PDF
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    print("تحذير: مكتبة pdfplumber غير مثبتة. سيتم تخطي استخراج نصوص الـ PDF.")
    HAS_PDFPLUMBER = False

# روابط سوق العراق للأوراق المالية المباشرة (AJAX Endpoints)
BASE_URL = "http://www.isx-iq.net/isxportal/portal/"
ANNOUNCEMENT_URL = f"{BASE_URL}storyList.html?methodName=getAnnouncementStoryList"
NEWS_URL = f"{BASE_URL}storyList.html?methodName=getNewsStoryList"

def fetch_stories(url):
    """جلب الأخبار والإفصاحات من روابط الـ AJAX"""
    stories = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.find_all("a")
        
        for link in links:
            href = link.get("href", "")
            title = link.text.strip()
            if not title:
                continue
            
            # تحويل الروابط النسبية إلى روابط كاملة
            if href and not href.startswith("http"):
                href = BASE_URL + href
            
            if "storyDetails" in href or href.endswith(".pdf"):
                stories.append({
                    "title": title,
                    "url": href
                })
    except Exception as e:
        print(f"خطأ أثناء جلب البيانات من {url}: {e}")
    return stories

def extract_pdf_text(pdf_url):
    """تحميل ملف الـ PDF وقراءته سطر بسطر"""
    if not HAS_PDFPLUMBER:
        return ""
    try:
        response = requests.get(pdf_url, timeout=15)
        response.raise_for_status()
        
        temp_filename = "temp_disclosure.pdf"
        with open(temp_filename, "wb") as f:
            f.write(response.content)
        
        text = ""
        with pdfplumber.open(temp_filename) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        
        if os.path.exists(temp_filename):
            os.remove(temp_filename)
        return text
    except Exception as e:
        print(f"خطأ أثناء قراءة ملف الـ PDF من الرابط {pdf_url}: {e}")
        return ""

def analyze_with_gemini(title, content):
    """إرسال الخبر لـ Gemini لتحليله وتلخيصه"""
    if not GEMINI_KEY:
        return "مفتاح الـ API غير متوفر لإجراء التحليل."
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        أنت محلل مالي خبير في سوق العراق للأوراق المالية (ISX).
        قم بتحليل وتلخيص هذا الإفصاح أو الخبر المرفق بلغة عربية مبسطة وواضحة للمستثمرين العراقيين:
        
        العنوان: {title}
        المحتوى التفصيلي:
        {content}
        
        المطلوب منك:
        1. ملخص سريع ومفهوم للخبر بـ 3 نقاط أساسية كحد أقصى.
        2. هل الخبر (إيجابي / سلبي / محايد) على حركة السهم؟ مع تعليل مبسط جداً.
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"خطأ أثناء التحليل باستخدام Gemini: {e}"

def main():
    print("جاري جلب آخر تحديثات سوق العراق للأوراق المالية...")
    
    announcements = fetch_stories(ANNOUNCEMENT_URL)
    news = fetch_stories(NEWS_URL)
    
    all_items = announcements + news
    print(f"تم العثور على {len(all_items)} تحديث محتمل.")
    
    if not all_items:
        print("لم يتم العثور على أي بيانات جديدة. تأكد من عمل الموقع الإلكتروني.")
        return

    # سنقوم بتحليل أول 5 أخبار جديدة فقط لتوفير وقت التشغيل وحدود الـ API
    for i, item in enumerate(all_items[:5]):
        print(f"\n[{i+1}/{len(all_items[:5])}] جاري معالجة: {item['title']}")
        
        content = ""
        if item['url'].endswith(".pdf"):
            print(f"جاري تنزيل وقراءة ملف الـ PDF: {item['url']}")
            content = extract_pdf_text(item['url'])
        else:
            try:
                res = requests.get(item['url'], timeout=15)
                detail_soup = BeautifulSoup(res.text, "html.parser")
                content = detail_soup.get_text(separator="\n", strip=True)
            except Exception as e:
                print(f"تعذر جلب تفاصيل الرابط: {e}")
        
        if not content or content.strip() == "":
            content = "تعذر استخراج النص التفصيلي تلقائياً. يرجى مراجعة الرابط المباشر."
            
        analysis = analyze_with_gemini(item['title'], content)
        print("=" * 50)
        print(f"تحليل الخبر: {item['title']}")
        print(analysis)
        print("=" * 50)

if __name__ == "__main__":
    main()
