import os
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
from datetime import datetime
import json
import argparse
import pdfplumber

# إعداد مفتاح API
GEMINI_KEY = os.getenv("GEMINI_DISCLOSURES_API_KEY") or os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

BASE_URL = "http://www.isx-iq.net/isxportal/portal/"
URL = f"{BASE_URL}storyList.html?methodName=getAnnouncementStoryList"

def fetch_today_stories():
    today = datetime.now().strftime("%d/%m")
    stories = []
    try:
        response = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        if table:
            for row in table.find_all("tr"):
                if today in row.text:
                    link_tag = row.find("a", href=True)
                    if link_tag:
                        href = link_tag['href']
                        if not href.startswith("http"): href = BASE_URL + href
                        stories.append({"title": link_tag.text.strip(), "url": href})
    except Exception as e:
        print(f"خطأ: {e}")
    return stories

def extract_content(url):
    try:
        response = requests.get(url, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
        pdf_tag = soup.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
        if pdf_tag:
            pdf_url = pdf_tag['href'] if pdf_url.startswith("http") else "https://www.isx-iq.net" + pdf_tag['href']
            res = requests.get(pdf_url, timeout=15)
            with open("temp.pdf", "wb") as f: f.write(res.content)
            with pdfplumber.open("temp.pdf") as pdf:
                text = "".join([p.extract_text() or "" for p in pdf.pages])
            os.remove("temp.pdf")
            return text
        return soup.get_text(separator="\n", strip=True)
    except: return "فشل استخراج المحتوى"

def analyze(title, content):
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        return model.generate_content(f"حلل الإفصاح المالي: {title}\nالمحتوى: {content}\nالمطلوب: ملخص 3 نقاط + هل الخبر إيجابي أم سلبي؟").text
    except Exception as e: return f"خطأ: {e}"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing")
    parser.add_argument("--output")
    args = parser.parse_args()

    # تحميل البيانات الموجودة
    data = []
    if os.path.exists(args.existing):
        with open(args.existing, 'r', encoding='utf-8') as f:
            data = json.load(f)

    stories = fetch_today_stories()
    for item in stories:
        if not any(d['title'] == item['title'] for d in data):
            print(f"معالجة: {item['title']}")
            content = extract_content(item['url'])
            analysis = analyze(item['title'], content)
            item['analysis'] = analysis
            item['date'] = datetime.now().strftime("%Y-%m-%d")
            data.append(item)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    main()
