import json
import os
from playwright.sync_api import sync_playwright

# Load config to get paths
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

BASE_URL = config["base_url"]
# Try to guess the "Essence" filter URL. Common patterns:
# - filter=digest
# - type=digest
# - /digest
ESSENCE_URL_GUESS = BASE_URL + "?filter=digest" 

STATE_FILE = os.path.join(os.path.dirname(__file__), config["state_file"])
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "debug_source.html")

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def debug_page():
    if not os.path.exists(STATE_FILE):
        print(f"[错误] 找不到登录状态文件: {STATE_FILE}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        print(f"1. 正在访问首页: {BASE_URL}")
        page.goto(BASE_URL, timeout=60000, wait_until="domcontentloaded")
        print(f"   页面标题: {page.title()}")
        
        # Check for "Essence" / "精华" link
        essence_link = page.locator('a:has-text("精华")').first
        if essence_link.is_visible():
            print(f"   [发现] '精华' 按钮/链接，链接地址: {essence_link.get_attribute('href')}")
        else:
            print("   [未发现] '精华' 按钮 (首页可能没有直接入口)")

        # Dump all links to see what we are dealing with
        print("2. 正在分析页面上的链接特征...")
        links = page.locator("a").all()
        sample_links = []
        for i, link in enumerate(links):
            href = link.get_attribute("href")
            text = link.inner_text().strip()
            if href and len(text) > 5: # Filter out short nav links
                sample_links.append(f"{text} -> {href}")
            if len(sample_links) >= 10: # Just get top 10 relevant looking ones
                break
        
        print("   链接采样 (前10个):")
        for l in sample_links:
            print(f"   - {l}")

        # Save HTML for manual inspection
        with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"3. 页面源码已保存至: {OUTPUT_HTML}")
        
        # Take a screenshot
        SCREENSHOT_PATH = os.path.join(OUTPUT_DIR, "debug_view.png")
        page.screenshot(path=SCREENSHOT_PATH)
        print(f"4. 页面截图已保存至: {SCREENSHOT_PATH}")
        print(f"   当前URL: {page.url}")

        browser.close()

if __name__ == "__main__":
    debug_page()
