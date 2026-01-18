import json
import os
import re
import time
from playwright.sync_api import sync_playwright
import html2text

# Configuration
DIR_PATH = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(DIR_PATH, "config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

LINKS_FILE = os.path.join(DIR_PATH, config["links_file"])
STATE_FILE = os.path.join(DIR_PATH, config["state_file"])
OUTPUT_DIR = os.path.join(DIR_PATH, config["output_dir"])
PROGRESS_FILE = os.path.join(DIR_PATH, "progress.json")
BASE_URL = config["base_url"].rstrip("/")

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def sanitize_filename(name):
    name = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', name)
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return clean[:100] if clean else "thread_content"

def extract_links_from_page(page, topic_pattern):
    """Deep link extraction from content area"""
    new_links = set()
    try:
        # Try multiple containers that might hold our post links
        containers = page.locator(".vditor-reset, .article-cont, .post-content, .topic-content")
        
        # We search ALL matching containers
        for i in range(containers.count()):
            links = containers.nth(i).locator("a").all()
            for link in links:
                try:
                    href = link.get_attribute("href")
                    if href:
                        if topic_pattern in href or "/thread/" in href:
                            if not href.startswith("http"):
                                href = BASE_URL + href if href.startswith("/") else BASE_URL + "/" + href
                            # Clean up
                            href = href.split("?")[0].split("#")[0]
                            new_links.add(href)
                except:
                    continue
    except Exception as e:
        print(f"  [调试] 提取内部链接时出错: {e}")
    return new_links

def get_clean_title(page):
    """Aggressive title waiting and cleaning"""
    for _ in range(20):
        t = page.title().replace("- 量化小论坛", "").strip()
        t = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', t)
        if t and "主题详情页" not in t and t != "量化小论坛" and t != "":
            return t
        page.wait_for_timeout(1000)
    return page.title().replace("- 量化小论坛", "").strip()

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed": [], "queue": []}

def save_progress(processed, queue):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"processed": list(processed), "queue": queue}, f, indent=2, ensure_ascii=False)

def process_content():
    if not os.path.exists(LINKS_FILE):
        print(f"[错误] 找不到链接文件: {LINKS_FILE}")
        return

    progress = load_progress()
    processed_urls = set(progress["processed"])
    
    # If queue is empty, initialize from links.json
    if not progress["queue"]:
        with open(LINKS_FILE, "r", encoding="utf-8") as f:
            start_links = json.load(f)
        work_queue = [(url, True) for url in start_links]
    else:
        # Each item in queue is [url, is_index_hint] (list because JSON)
        work_queue = [(item[0], item[1]) for item in progress["queue"]]

    # h2t converter
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False 
    h.body_width = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=STATE_FILE,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            while work_queue:
                url, is_index_hint = work_queue.pop(0)
                if url in processed_urls:
                    continue
                
                print(f"[{len(processed_urls)+1} / 队列:{len(work_queue)}] 正在处理: {url}")
                
                try:
                    page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    
                    # Core wait for Vditor rendering
                    print("  -> 等待页面组件渲染...")
                    page.wait_for_timeout(5000) # Minimum base wait for Nuxt
                    
                    # Scroll to trigger lazy loading
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(2000)
                    
                    # Wait for CONTENT hydration (Proof of work)
                    print("  -> 等待正文内容加载...")
                    try:
                        page.wait_for_function("""
                            () => {
                                const el = document.querySelector('.vditor-reset, .article-cont');
                                return el && el.innerText.trim().length > 200;
                            }
                        """, timeout=15000)
                        print("  -> [成功] 检测到正文内容")
                    except:
                        print("  -> [警告] 等待正文超时，页面可能较短或加载缓慢")

                    title = get_clean_title(page)
                    if not title or "主题详情页" in title:
                        title = f"Topic_{url.split('/')[-1].split('?')[0]}"
                    
                    print(f"  -> 标题: {title}")

                    # 2. Extract content with priority
                    content_element = None
                    for sel in [".vditor-reset", ".article-cont", ".post-content"]:
                        el = page.locator(sel).first
                        if el.count() > 0 and len(el.inner_text().strip()) > 100:
                            content_element = el
                            break
                    
                    if content_element:
                        content_html = content_element.inner_html()
                        content_text = content_element.inner_text().strip()
                    else:
                        print("  -> [降级] 使用全页 Body 提取")
                        content_html = page.locator("body").inner_html()
                        content_text = page.locator("body").inner_text().strip()

                    # 3. Discovery logic (is this an index page?)
                    sub_links = extract_links_from_page(page, config["topic_pattern"])
                    is_index = any(kw in title for kw in ["汇总", "导航", "精华", "索引", "列表", "详情", "汇总"]) or len(sub_links) > 5
                    
                    if is_index:
                        new_links_batch = 0
                        for sl in sub_links:
                            if sl not in processed_urls:
                                if not any(item[0] == sl for item in work_queue):
                                    work_queue.append((sl, False))
                                    new_links_batch += 1
                        if new_links_batch > 0:
                            print(f"  -> [发现] {new_links_batch} 个新链接，当前队列: {len(work_queue)}")

                    # Save result
                    markdown = h.handle(content_html)
                    filename = f"{sanitize_filename(title)}.md"
                    filepath = os.path.join(OUTPUT_DIR, filename)
                    
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(f"# {title}\nURL: {url}\n\n{markdown}")
                    
                    print(f"  -> 已保存 ({len(markdown)} 字符)")
                    
                    # Persist state
                    processed_urls.add(url)
                    save_progress(processed_urls, work_queue)
                    
                    # Manners
                    time.sleep(1)

                except Exception as e:
                    print(f"  [失败] {url}: {e}")

        finally:
            browser.close()
            save_progress(processed_urls, work_queue)
    
    print(f"\n[任务完成] 总计处理: {len(processed_urls)} 个页面。")

if __name__ == "__main__":
    process_content()
