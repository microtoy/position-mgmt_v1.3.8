import json
import os
import re
from playwright.sync_api import sync_playwright

# Configuration
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

# Hardcoded index URL provided by user
INDEX_URL = "https://bbs.quantclass.cn/thread/69022"
STATE_FILE = os.path.join(os.path.dirname(__file__), config["state_file"])
LINKS_FILE = os.path.join(os.path.dirname(__file__), config["links_file"])
BASE_URL = config["base_url"].rstrip("/")

def extract_essence_links():
    if not os.path.exists(STATE_FILE):
        print(f"[错误] 找不到登录状态文件: {STATE_FILE}")
        return

    collected_links = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=STATE_FILE,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # Subscribe to console events to debug JS errors
        page.on("console", lambda msg: print(f"  [浏览器控制台] {msg.text}"))

        print(f"正在访问精华索引页: {INDEX_URL}")
        try:
            page.goto(INDEX_URL, timeout=60000, wait_until="domcontentloaded")
            print("页面骨架加载完毕，开始模拟人类滚动操作...")
            
            # Simulate scrolling to bottom to trigger lazy loading
            for i in range(5):
                page.mouse.wheel(0, 1000)
                page.wait_for_timeout(1000)
                print(f"  -> 滚动第 {i+1} 次...")
            
            # Final wait
            page.wait_for_timeout(3000)
            
            # Screenshot for debugging
            debug_screenshot = os.path.join(os.path.dirname(LINKS_FILE), "index_debug.png")
            page.screenshot(path=debug_screenshot)
            print(f"  [调试] 页面截图已保存: {debug_screenshot}")

        except Exception as e:
            print(f"[错误] 页面加载异常: {e}")
            browser.close()
            return

        print("正在提取链接...")
        links = page.locator("a").all()
        
        print(f"  [调试] 页面上共找到 {len(links)} 个链接标签")
        
        # Debug: check first 10 links
        for i, link in enumerate(links[:10]):
            try:
                print(f"    - debug link {i}: {link.get_attribute('href')} | text: {link.inner_text().strip()[:20]}")
            except: 
                pass

        for link in links:
            try:
                href = link.get_attribute("href")
                if not href:
                    continue

                # Normalization
                if not href.startswith("http"):
                    href = BASE_URL + href if href.startswith("/") else BASE_URL + "/" + href
                
                # Loose matching: if it looks like a thread OR a topic OR has an ID
                # User config says "/thread/", but maybe the index links are "/topic/"?
                # Let's trust the Config but also be permissive in debug.
                
                if config["topic_pattern"] in href:
                    # Exclude the index page itself
                    if "thread/69022" in href:
                        continue
                    collected_links.add(href)
            except:
                continue

        browser.close()

    if collected_links:
        # Sort for consistency
        sorted_links = sorted(list(collected_links))
        with open(LINKS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted_links, f, indent=2, ensure_ascii=False)
        
        print(f"\n[成功] 共提取到 {len(collected_links)} 个精华帖链接！")
        print(f"列表已保存至: {LINKS_FILE}")
        print("前5个链接示例:")
        for l in sorted_links[:5]:
            print(f"  - {l}")
    else:
        print("\n[警告] 未提取到任何链接。请检查页面加载是否完整，或 topic_pattern 是否匹配。")

if __name__ == "__main__":
    extract_essence_links()
