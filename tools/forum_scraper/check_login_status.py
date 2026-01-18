import os
from playwright.sync_api import sync_playwright

# The URL user asked to check
TARGET_URL = "https://bbs.quantclass.cn/thread/34149"
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def check_one_page():
    if not os.path.exists(STATE_FILE):
        print(f"[错误] 找不到登录状态文件: {STATE_FILE}")
        return

    print(f"正在使用保存的状态访问: {TARGET_URL}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Use a real User-Agent to avoid simple blocking
        context = browser.new_context(
            storage_state=STATE_FILE,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # Block images to speed up loading
        # page.route("**/*.{png,jpg,jpeg}", lambda route: route.abort())

        try:
            # Monitor requests to see if content API fails
            page.on("request", lambda request: print(f"  [请求] {request.method} {request.url[:100]}"))
            page.on("response", lambda response: print(f"  [响应] {response.status} {response.url[:100]}"))

            # Use domcontentloaded first
            page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
            
            # Explicit wait for hydration
            print("页面骨架已加载，开始多次滚动以触发加载...")
            for i in range(3):
                page.mouse.wheel(0, 1000)
                page.wait_for_timeout(2000)
            
            # Final long wait
            print("等待 10s 以确保内容完全渲染...")
            page.wait_for_timeout(10000)
            
            title = page.title()
            print(f"最终页面标题: {title}")
            
            # HTML dump
            html_path = os.path.join(OUTPUT_DIR, "detailed_check.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            print(f"HTML已保存: {html_path}")

            # Screenshot (Full Page)
            screenshot_path = os.path.join(OUTPUT_DIR, "detailed_check_full.png")
            page.screenshot(path=screenshot_path, full_page=True)
            print(f"全页截图已保存: {screenshot_path}")

            # Deep DOM inspection
            content_info = page.evaluate("""() => {
                const containers = ['.vditor-reset', '.article-cont', '.thread-content', '.topic-content', '.post-content'];
                let res = {};
                containers.forEach(sel => {
                    const el = document.querySelector(sel);
                    if (el) {
                        res[sel] = {
                            text_len: el.innerText.length,
                            html_len: el.innerHTML.length,
                            link_count: el.querySelectorAll('a').length,
                            top_text: el.innerText.substring(0, 500)
                        };
                    }
                });
                return res;
            }""")
            import pprint
            print("\n[内容容器深度分析]:")
            pprint.pprint(content_info)

        except Exception as e:
            print(f"[出错] 访问失败: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    check_one_page()
