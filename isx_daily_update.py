import requests
from bs4 import BeautifulSoup

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
    print("🔄 جاري الاتصال بموقع السوق...")
    resp = requests.get(url, headers=headers, timeout=20)
    print(f"Status Code: {resp.status_code}")
    
    print("\n📝 أول 1000 حرف من كود الصفحة:")
    print(resp.text[:1000])
    
    soup = BeautifulSoup(resp.content, "html.parser")
    links = soup.find_all("a")
    print(f"\n🔗 إجمالي الروابط المكتشفة في الصفحة: {len(links)}")
    
    print("\n👇 عينة من أول 20 رابطاً تم العثور عليها:")
    count = 0
    for link in links:
        href = link.get("href", "")
        text = link.get_text().strip()
        if href:
            print(f"Text: {text[:40]} | Href: {href}")
            count += 1
            if count >= 20:
                break
                
except Exception as e:
    print(f"❌ حدث خطأ أثناء التشخيص: {e}")
