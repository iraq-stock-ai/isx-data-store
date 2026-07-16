import os
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
from datetime import datetime

# إعداد مفتاح API
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

# إعداد PDF
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

BASE_URL = "http://www.isx-iq.net/isxportal/portal/"
ANNOUNCEMENT_URL = f"{BASE_URL}storyList.html?methodName=getAnnouncementStoryList"
NEWS_URL = f"{BASE_URL}storyList.html?methodName=getNewsStoryList"

def fetch_today_stories(url):
    # نستخدم جزءاً من التاريخ (اليوم/الشهر) للبحث، لأن السنة قد تكون ثابتة
    today_check = datetime.now().strftime("%d/%m") 
    print(f"جاري البحث عن روابط بتاريخ (يوم/شهر): {today_check}")
    
    stories = []
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
        
        table = soup.find("table")
        if not table: return stories
            
        for row in table.find_all("tr"):
            # نقوم بالبحث عن التاريخ داخل النص المكتوب في السطر
            if today_check in row.text:
                link_tag = row.find("a", href=True)
                if link_tag:
                    title = link_tag.text.strip()
                    href = link_tag['href']
                    if not href.startswith("http"):
                        href = BASE_URL + href
                    stories.append({"title": title, "url": href})
                    print(f"تم العثور على: {title}")
    except Exception as e:
        print(f"خطأ: {e}")
    return stories

def extract_content_from_details(details_url):
    try:
        response = requests.get(details_url, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
        
        # البحث عن رابط الـ PDF بأي طريقة
        pdf_tag = soup.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
        if pdf_tag:
            pdf_url = pdf_tag['href']
            if not pdf_url.startswith("http"):
                pdf_url = "https://www.isx-iq.net" + pdf_url
            return extract_pdf_text(pdf_url)
        
        return soup.get_text(separator="\n", strip=True)
    except: return ""

def extract_pdf_text(pdf_url):
    if not HAS_PDFPLUMBER: return "pdfplumber غير مثبت"
    try:
        res = requests.get(pdf_url, timeout=15)
        temp_path = "temp.pdf"
        with open(temp_path, "wb") as f: f.write(res.content)
        text = ""
        with pdfplumber.open(temp_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() + "\n"
        os.remove(temp_path)
        return text
    except: return "فشل استخراج الـ PDF"

def analyze_with_gemini(title, content):
    if not GEMINI_KEY: return "لا يوجد API KEY"
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"حلل هذا الإفصاح المالي: {title}\nالمحتوى: {content}\nالمطلوب: ملخص بـ 3 نقاط + هل الخبر إيجابي/سلبي؟"
        return model.generate_content(prompt).text
    except Exception as e: return f"خطأ في Gemini: {e}"

def main():
    all_items = fetch_today_stories(ANNOUNCEMENT_URL) + fetch_today_stories(NEWS_URL)
    if not all_items:
        print("لا توجد بيانات جديدة.")
        return

    for item in all_items:
        print(f"معالجة: {item['title']}")
        content = extract_content_from_details(item['url'])
        analysis = analyze_with_gemini(item['title'], content)
        print(f"النتيجة:\n{analysis}")

if __name__ == "__main__":
    main()
