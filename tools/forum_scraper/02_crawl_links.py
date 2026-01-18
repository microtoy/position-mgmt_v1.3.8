import json
import os
import time
from playwright.sync_api import sync_playwright

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

BASE_URL = config["base_url"]
STATE_FILE = os.path.join(os.path.dirname(__file__), config["state_file"])
LINKS_FILE = os.path.join(os.path.dirname(__file__), config["links_file"])
TOPIC_PATTERN = config.get("topic_pattern", "/topic/")

def crawl_links():
    if not os.path.exists(STATE_FILE):
        print(f"[错误] 找不到登录状态文件: {STATE_FILE}")
        print("请先通过 01_login.py 完成登录。")
        return

    collected_links = set()

    with sync_playwright() as p:
        # Headless mode for speed, use state from previous step
        # Add User-Agent to avoid being detected as bot
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=STATE_FILE,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print(f"正在访问: {BASE_URL}")
        try:
            # Nuxt.js sites can be slow to hydrate. Wait for networkidle but allow time.
            page.goto(BASE_URL, timeout=60000, wait_until="networkidle")
        except:
            print("  [提示] 页面加载超时，尝试继续操作...")
        
        # Explicit wait to ensure hydration
        print("Waiting for page hydration...")
        page.wait_for_timeout(5000)

        # Try to switch to "Essence" (精华) tab if user requested it
        # Look for a link containing "精华"
        essence_btn = page.locator('a:has-text("精华"), div:has-text("精华")').first
        if essence_btn.is_visible():
            print("  -> 找到 '精华' 标签，正在切换...")
            try:
                essence_btn.click()
                page.wait_for_timeout(3000) # Wait for content update
            except Exception as e:
                print(f"  [警告] 切换精华失败: {e}")
        else:
            print("  -> 未找到 '精华' 标签，将抓取当前页面所有帖子。")

        # Basic logic: Find all links that look like topic links
        # Updated pattern based on user input: /thread/
        # This is a heuristic. You might need to adjust the selector or pattern based on the actual forum HTML.
        
        has_next_page = True
        page_num = 1
        max_pages = 5 # Safety limit for testing. Increase this later.

        while has_next_page and page_num <= max_pages:
            print(f"正在扫描第 {page_num} 页...")
            
            # Get all links
            links = page.locator("a").all()
            page_new_links = 0
            
            for link in links:
                try:
                    href = link.get_attribute("href")
                    if href and TOPIC_PATTERN in href:
                        full_url = href if href.startswith("http") else BASE_URL.rstrip("/") + href
                        if full_url not in collected_links:
                            collected_links.add(full_url)
                            page_new_links += 1
                except:
                    continue
            
            print(f"  -> 发现 {page_new_links} 个新主题 (总计: {len(collected_links)})")

            # Pagination Logic
            # Try to find a "Next" button. Common selectors:
            # - a:has-text("下一页")
            # - a:has-text("Next")
            # - .next
            # - .pagination a:last-child
            
            # Attempt to find 'Next' button
            next_btn = page.locator('a:has-text("下一页"), a:has-text("Next"), a[rel="next"]')
            
            if next_btn.count() > 0 and next_btn.first.is_visible():
                print("  -> 找到下一页按钮，正在跳转...")
                try:
                    next_btn.first.click()
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(2) # be polite
                    page_num += 1
                except Exception as e:
                    print(f"  [警告] 跳转失败: {e}")
                    has_next_page = False
            else:
                print("  -> 未找到下一页按钮，停止翻页。")
                has_next_page = False

        browser.close()

    # Save results
    if collected_links:
        with open(LINKS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(collected_links), f, indent=2)
        print(f"\n[完成] 已保存 {len(collected_links)} 个链接到 {LINKS_FILE}")
    else:
        print("\n[警告] 未找到任何符合条件的链接。请检查 topic_pattern 配置是否正确。")

if __name__ == "__main__":
    crawl_links()
