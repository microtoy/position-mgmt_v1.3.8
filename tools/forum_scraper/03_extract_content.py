import json
import os
import re
import asyncio
import random
import logging
from playwright.async_api import async_playwright
import html2text
from bs4 import BeautifulSoup

# Import v2.0 Skills
from util_stealth import apply_stealth, human_scroll, random_sleep, get_random_ua
from util_images import download_image, get_local_path, is_valid_image_url

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scraper_v2.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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

# Resource Blocking List
BLOCKED_RESOURCE_TYPES = ["font", "media", "stylesheet", "other"]
BLOCKED_DOMAINS = ["google-analytics.com", "hm.baidu.com", "googletagmanager.com", "doubleclick.net"]

# === SAFE MODE CONFIGURATION (Single Account Protection) ===
SAFE_MODE = True  # Set to False for faster (but riskier) scraping
BATCH_LIMIT = 15  # Max pages per batch before forced cooldown
DAILY_LIMIT = 50  # Max pages per day (manual enforcement)
MIN_DELAY_SECONDS = 60  # Minimum wait between pages
MAX_DELAY_SECONDS = 120  # Maximum wait between pages
BATCH_COOLDOWN_MINUTES = 120  # Cooldown between batches (2 hours)


async def block_aggressively(route):
    """Phase 8: Optimization - Block unnecessary resources."""
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
        return
    
    url = route.request.url
    if any(domain in url for domain in BLOCKED_DOMAINS):
        await route.abort()
        return
        
    await route.continue_()

def sanitize_filename(name):
    name = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', name)
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return clean[:100] if clean else "thread_content"

# --- Content Validation Logic (Code over Text Rule) ---
def is_valid_content(text, title, is_index=False):
    if not text or len(text) < 50:
        return False
    if "还能输入40000个字" in text:
        return False
    if title.startswith("Topic_"):
        return False
    chinese_chars = len(re.findall(r'[\u4e00-\u9fa5]', text))
    if is_index:
        return chinese_chars > 50
    else:
        return chinese_chars > 150

async def extract_links_from_page(page, topic_pattern):
    new_links = set()
    try:
        containers = page.locator(".vditor-reset, .article-cont, .post-content, .topic-content")
        count = await containers.count()
        for i in range(count):
            links = await containers.nth(i).locator("a").all()
            for link in links:
                href = await link.get_attribute("href")
                if href and (topic_pattern in href or "/thread/" in href):
                    if not href.startswith("http"):
                        href = BASE_URL + href if href.startswith("/") else BASE_URL + "/" + href
                    href = href.split("?")[0].split("#")[0]
                    new_links.add(href)
    except Exception as e:
        logger.error(f"Link Discovery Error: {e}")
    return new_links

async def get_clean_title(page):
    for _ in range(10):
        t = await page.title()
        t = t.replace("- 量化小论坛", "").strip()
        t = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', t)
        if t and "主题详情页" not in t and t != "量化小论坛" and t != "":
            return t
        await asyncio.sleep(1)
    t = await page.title()
    return t.replace("- 量化小论坛", "").strip()

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed": [], "queue": []}

def save_progress(processed, queue):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"processed": list(processed), "queue": queue}, f, indent=2, ensure_ascii=False)

async def process_content():
    if not os.path.exists(LINKS_FILE):
        logger.critical(f"Links file not found: {LINKS_FILE}")
        return

    progress = load_progress()
    processed_urls = set(progress["processed"])
    
    if not progress["queue"]:
        with open(LINKS_FILE, "r", encoding="utf-8") as f:
            start_links = json.load(f)
        work_queue = [(url, True) for url in start_links]
    else:
        work_queue = [(item[0], item[1]) for item in progress["queue"]]

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False 
    h.body_width = 0

    # Safe Mode: Batch counter
    batch_counter = 0
    session_counter = 0  # Total pages this session
    consecutive_failures = 0 # Circuit breaker
    MAX_CONSECUTIVE_FAILURES = 5

    async with async_playwright() as p:
        # Optimization: Headless=True for performance
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=STATE_FILE,
            user_agent=get_random_ua(),
            viewport={"width": 1440, "height": 900}
        )
        
        page = await context.new_page()
        # Optimization: Route Interception
        await page.route("**/*", block_aggressively)
        
        await apply_stealth(page)

        try:
            while work_queue:
                url, is_index_hint = work_queue.pop(0)
                if url in processed_urls:
                    continue
                
                # Check Circuit Breaker
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.critical(f"🛑 CRITICAL: {consecutive_failures} consecutive failures detected. Stopping script to protect account.")
                    logger.critical("   Possible causes: IP ban, Session expired, or Site layout change.")
                    break

                logger.info(f"[{len(processed_urls)+1} / Q:{len(work_queue)}] Processing: {url}")
                
                # Robust Retry Loop (Exponential Backoff)
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        await page.goto(url, timeout=90000, wait_until="domcontentloaded")
                        break # Success
                    except Exception as e:
                        if attempt == max_retries - 1:
                            logger.error(f"Failed to load {url} after {max_retries} attempts. Error: {e}")
                            # Skip this URL, maybe re-queue later manually
                            pass 
                        else:
                            wait_time = 2 ** (attempt + 1)
                            logger.warning(f"Load failed, retrying in {wait_time}s... ({e})")
                            await asyncio.sleep(wait_time)
                else:
                    # If loop finished without break (all attempts failed)
                    work_queue.append((url, is_index_hint)) # Re-queue for later
                    consecutive_failures += 1 # Increment failure
                    await random_sleep(10, 20)
                    continue

                try:
                    logger.info("  -> Simulating human reading...")
                    await human_scroll(page) 
                    
                    logger.info("  -> Waiting for content hydration...")
                    try:
                        await page.wait_for_selector(".vditor-reset, .article-cont", timeout=20000)
                        valid_hydration = await page.evaluate("""() => {
                            const el = document.querySelector('.vditor-reset, .article-cont');
                            return el && el.innerText.trim().length > 100;
                        }""")
                        if not valid_hydration:
                             logger.warning("  -> Content too short, might be skeleton.")
                    except:
                         logger.warning("  -> Content selector timeout.")

                    title = await get_clean_title(page)
                    if not title or "主题详情页" in title:
                        title = f"Topic_{url.split('/')[-1].split('?')[0]}"
                    
                    logger.info(f"  -> Title: {title}")

                    content_element = page.locator(".vditor-reset, .article-cont, .post-content").first
                    if await content_element.count() > 0:
                        content_html = await content_element.inner_html()
                        content_text = await content_element.inner_text()
                    else:
                        logger.warning("  -> [Fallback] Body extraction")
                        content_element = page.locator("body")
                        content_html = await content_element.inner_html()
                        content_text = await content_element.inner_text()

                    # Phase 5: Image Localization
                    soup = BeautifulSoup(content_html, "html.parser")
                    images = soup.find_all("img")
                    if images:
                        logger.info(f"  -> Found {len(images)} images, processing...")
                        for img in images:
                            img_url = img.get("src")
                            if is_valid_image_url(img_url):
                                local_rel_path = get_local_path(img_url, OUTPUT_DIR)
                                full_local_path = os.path.join(OUTPUT_DIR, local_rel_path)
                                success = await download_image(page, img_url, full_local_path)
                                if success:
                                    img["src"] = local_rel_path
                        content_html = str(soup)

                    # Validation Check
                    is_index = any(kw in title for kw in ["汇总", "导航", "精华", "索引"])
                    if not is_valid_content(content_text, title, is_index):
                        logger.warning("  -> [REJECTED] Content validation failed. Re-queueing.")
                        work_queue.append((url, is_index_hint))
                        consecutive_failures += 1  # Increment failure
                        await random_sleep(5, 10)
                        continue

                    # Discovery
                    sub_links = await extract_links_from_page(page, config["topic_pattern"])
                    if is_index or len(sub_links) > 5:
                        new_cnt = 0
                        for sl in sub_links:
                             if sl not in processed_urls and not any(q[0] == sl for q in work_queue):
                                 work_queue.append((sl, False))
                                 new_cnt += 1
                        if new_cnt:
                            logger.info(f"  -> Discovered {new_cnt} new links.")

                    # Save
                    markdown = h.handle(content_html)
                    filename = f"{sanitize_filename(title)}.md"
                    filepath = os.path.join(OUTPUT_DIR, filename)
                    
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(f"# {title}\nURL: {url}\n\n{markdown}")
                    logger.info(f"  -> Saved ({len(markdown)} chars).")

                    # Success! Reset circuit breaker
                    consecutive_failures = 0

                    processed_urls.add(url)
                    save_progress(processed_urls, work_queue)
                    
                    # === SAFE MODE: Anti-Ban Sleep ===
                    batch_counter += 1
                    session_counter += 1
                    
                    if SAFE_MODE:
                        # Check batch limit
                        if batch_counter >= BATCH_LIMIT:
                            logger.warning(f"🛑 Batch limit reached ({BATCH_LIMIT}). Starting {BATCH_COOLDOWN_MINUTES} min cooldown...")
                            logger.info(f"   Session total: {session_counter} pages. Queue remaining: {len(work_queue)}")
                            save_progress(processed_urls, work_queue)
                            await asyncio.sleep(BATCH_COOLDOWN_MINUTES * 60)
                            batch_counter = 0
                            logger.info("✅ Cooldown complete. Resuming...")
                        else:
                            # Normal Safe Mode delay (60-120s)
                            delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
                            logger.info(f"  -> Safe Mode: Resting {delay:.0f}s... (Batch: {batch_counter}/{BATCH_LIMIT})")
                            await asyncio.sleep(delay)
                    else:
                        # Fast mode (original behavior)
                        await random_sleep(30, 90)

                except Exception as e:
                    logger.error(f"  [Failure] {url}: {e}")
                    await random_sleep(10, 20)

        finally:
            await browser.close()
            save_progress(processed_urls, work_queue)
    
    logger.info(f"Total processed: {len(processed_urls)}")

if __name__ == "__main__":
    asyncio.run(process_content())
