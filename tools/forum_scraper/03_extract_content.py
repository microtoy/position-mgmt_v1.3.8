import datetime
import asyncio
import sys
import os
import json
import random
import time
import re
from playwright.async_api import async_playwright
from util_stealth import apply_stealth, random_sleep, human_scroll, get_random_ua

# --- Configuration ---
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

LINKS_FILE = "links.json"
PROGRESS_FILE = "progress.json"
OUTPUT_DIR = "downloaded_pdfs" 
PROFILE_DIR = "browser_profile"
FIXED_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# --- Safety Settings ---
MIN_SLEEP = 30
MAX_SLEEP = 90

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# --- Define Logging ---
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scraper_pdf_final.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Progress Management ---
def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return {"processed": [], "queue": []}
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        if "queue" in data and data["queue"] and len(data["queue"]) > 0:
            if isinstance(data["queue"][0], str):
                 data["queue"] = [(url, False) for url in data["queue"]]
        return data

def save_progress(processed, queue):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"processed": list(processed), "queue": queue}, f, indent=2, ensure_ascii=False)

async def get_clean_title(page):
    """Wait for a real title to appear, checking both document.title and DOM elements."""
    for i in range(20): # Increase wait to 20s
        # 1. Check document.title
        t = await page.title()
        t = t.replace("- 量化小论坛", "").strip()
        t = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', t)
        
        # 2. Check DOM h1 if title looks generic
        if not t or any(kw in t for kw in ["主题详情页", "量化小论坛", "Loading", "正在加载"]):
            try:
                # Try fetching h1 or specific title classes
                dom_title = await page.evaluate("""() => {
                    const h1 = document.querySelector('h1');
                    if (h1 && h1.textContent.length > 2) return h1.textContent;
                    
                    const titleEl = document.querySelector('.thread-title') || document.querySelector('.title');
                    if (titleEl && titleEl.textContent.length > 2) return titleEl.textContent;
                    return "";
                }""")
                if dom_title:
                    t = dom_title.strip()
            except:
                pass

        # 3. Validation
        if t and all(kw not in t for kw in ["主题详情页", "量化小论坛", "Loading", "正在加载"]):
            if len(t) > 2:
                logger.info(f"  -> Found clean title: {t}")
                return t
        
        if i % 2 == 0: logger.info(f"  -> Waiting for title... (Current: {t})")
        await asyncio.sleep(1)
    
    # Fallback to whatever we have, but try DOM one last time
    final_t = await page.title()
    final_t = final_t.replace("- 量化小论坛", "").strip()
    if "主题详情页" in final_t:
         try:
            dom_title = await page.evaluate("() => document.querySelector('h1') ? document.querySelector('h1').textContent : ''")
            if dom_title: return dom_title.strip()
         except:
             pass
    return final_t

async def process_content():
    progress = load_progress()
    processed_urls = set(progress["processed"])
    work_queue = progress["queue"]
    
    if not work_queue and os.path.exists(LINKS_FILE):
        with open(LINKS_FILE, "r", encoding="utf-8") as f:
            all_links = json.load(f)
            count = 0
            for link in all_links:
                if link not in processed_urls:
                    work_queue.append((link, False))
                    count += 1
        logger.info(f"Reloaded {count} items from links.json")


    consecutive_failures = 0
    last_short_break = time.time() # Timer for 30m breaks
    
    async with async_playwright() as p:
        # STRATEGY: Persistent Browser Profile for maximum durability and realistic fingerprint.
        logger.info(f"Initializing PDF Scraper (Mode: Persistent Context, Profile: {PROFILE_DIR})...")
        
        # Use absolute path for PROFILE_DIR to ensure consistency across CWDs
        abs_profile_path = os.path.abspath(PROFILE_DIR)
        
        context = await p.chromium.launch_persistent_context(
            user_data_dir=abs_profile_path,
            headless=True,
            user_agent=FIXED_UA,
            # Increase viewport width to ensure tables fit!
            viewport={"width": 1600, "height": 1200}
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        await apply_stealth(page)

        logger.info(f"Starting crawl with {len(work_queue)} items in queue...")

        while work_queue:
            if consecutive_failures >= 5:
                logger.error("Too many consecutive failures. Stopping.")
                break

            url, is_index_hint = work_queue.pop(0)
            
            if url in processed_urls:
                continue
            
            # --- SCHEDULING LOGIC (User Request) ---
            # 1. Night Curfew (02:00 - 06:00)
            now = datetime.datetime.now()
            if 2 <= now.hour < 6:
                logger.warning(f"🌙 [Schedule] It is {now.strftime('%H:%M')}. Entering Night Mode (02:00-06:00).")
                logger.warning("🌙 Sleeping until 06:00...")
                while 2 <= datetime.datetime.now().hour < 6:
                    await asyncio.sleep(300) # Check every 5 mins
                logger.info("☀️ Good morning! Resuming work.")

            # 2. Periodic Break (Every 30m rest 5m)
            if time.time() - last_short_break > 1800: # 30 mins
                logger.info(f"☕ [Schedule] Script ran for 30 mins. Taking a 5-minute break...")
                await asyncio.sleep(300)
                last_short_break = time.time()
                logger.info("☕ Break over. Back to work.")
            # ---------------------------------------

            logger.info(f"[{len(processed_urls)+1} / Q:{len(work_queue)}] Processing: {url}")
            
            try:
                # 1. Navigation
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    logger.warning(f"  -> Nav warning: {e}")

                # 2. CSS Cleanup & Table Fixes
                await page.add_style_tag(content="""
                    /* 1. Hide excessive UI (Conservative) */
                    .footer, .sidebar, .thread-catelog, .el-dialog__wrapper, 
                    .v-note-op, .article-footer-operate, .thread-status, 
                    .myprofile-bomb-box, .el-backtop { 
                        display: none !important; 
                        opacity: 0 !important;
                        pointer-events: none !important;
                    }

                    /* 2. Reset Layout context */
                    #__nuxt, #__layout, .global, .w-100 {
                        position: static !important;
                        overflow: visible !important;
                        height: auto !important;
                        margin: 0 !important;
                        padding: 0 !important;
                        min-width: 100% !important; /* Ensure full width */
                    }

                    /* 3. Article Container */
                    .article-cont { 
                        width: 100% !important; 
                        margin: 0 !important; 
                        padding: 20px !important; 
                        max-width: none !important; 
                        position: static !important;
                        background: white !important;
                        z-index: 100 !important;
                    }

                    body { 
                        background: white !important; 
                        padding-top: 0 !important;
                        min-width: 100% !important;
                    }
                    
                    div[class*="header"], div[style*="fixed"] {
                        position: static !important;
                    }

                    /* 4. TABLE FIXES (Crucial) */
                    table {
                        width: 100% !important; 
                        max-width: 100% !important;
                        table-layout: auto !important; /* Allow cells to expand/contract based on content */
                    }
                    /* Force text wrapping inside cells */
                    td, th {
                        white-space: normal !important;
                        word-wrap: break-word !important;
                        overflow-wrap: break-word !important;
                    }
                    /* If table is still somehow too wide, scale it down slightly to fit */
                    @media print {
                        table {
                            page-break-inside: auto;
                        }
                        tr {
                            page-break-inside: avoid;
                            page-break-after: auto;
                        }
                    }
                    /* 5. CODE BLOCK FIXES (Crucial) */
                    pre, code, .vditor-reset pre, .vditor-reset code {
                        white-space: pre-wrap !important; 
                        word-wrap: break-word !important;
                        overflow-x: hidden !important; /* Prevent scrollbars showing up */
                        max-width: 100% !important;
                    }
                """)
                
                # 3. Enhanced Wait Strategy (Hydration)
                logger.info("  -> Waiting for hydration...")
                await asyncio.sleep(10) # Base wait
                
                # 4. Scroll to trigger lazy loading (Crucial)
                await page.mouse.wheel(0, 15000) 
                await asyncio.sleep(5)
                
                # 5. Extract Title
                title = await get_clean_title(page)
                
                # --- AUTO-STOP PROTECTION (User Request) ---
                if "主题详情页" in title:
                    logger.critical(f"🛑 [CRITICAL] Anti-bot detected (Title='{title}'). Stopping IMMEDIATELY to protect account.")
                    logger.critical(f"🛑 Please run verify_and_refresh.py to solve CAPTCHA.")
                    # Re-queue current item to ensure it gets retry later
                    work_queue.insert(0, (url, is_index_hint))
                    save_progress(list(processed_urls), work_queue)
                    sys.exit(1) # Force exit
                # -------------------------------------------

                safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
                thread_id = url.split("/")[-1].split("?")[0]
                if not safe_title:
                    safe_title = f"Topic_{thread_id}"
                
                pdf_filename = f"{safe_title}_{thread_id}.pdf"
                filepath = os.path.join(OUTPUT_DIR, pdf_filename)
                
                # 6. Content Check (to avoid blank PDFs & skip VIP posts)
                check_result = await page.evaluate("""() => {
                    const article = document.querySelector('.article-cont') || document.querySelector('.vditor-reset') || document.querySelector('.thread-cont');
                    const text = article ? article.innerText : "";
                    
                    if (text.includes("剩余内容已隐藏") || text.includes("报名课程即可查看完整内容")) {
                        return { status: "HIDDEN" };
                    }
                    if (!article || article.innerText.trim().length < 50) {
                        return { status: "EMPTY" };
                    }
                    return { status: "OK" };
                }""")
                
                content_status = check_result.get("status")
                if content_status == "HIDDEN":
                    logger.warning(f"  -> [SKIP] VIP/权限帖，跳过此帖。")
                    processed_urls.add(url)
                    save_progress(list(processed_urls), work_queue)
                    await random_sleep(5, 10)
                    continue
                elif content_status != "OK":
                    logger.warning("  -> Content seems empty. Skipping.")
                    consecutive_failures += 1
                    work_queue.append((url, is_index_hint))
                    await asyncio.sleep(30)
                    continue

                # 7. Print to PDF (The method USER liked)
                logger.info(f"  -> Printing PDF: {pdf_filename}")
                await page.pdf(
                    path=filepath,
                    format="A4",
                    print_background=True,
                    margin={"top": "20px", "bottom": "20px", "left": "20px", "right": "20px"}
                )
                
                logger.info(f"  -> Success!")
                processed_urls.add(url)
                consecutive_failures = 0

                # 8. Save Progress (EVERY TIME for safety)
                save_progress(list(processed_urls), work_queue)
                
                # 9. SAFETY SLEEP
                sleep_time = random.randint(MIN_SLEEP, MAX_SLEEP)
                logger.info(f"  -> [Safety] Sleeping for {sleep_time} s...")
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error processing {url}: {e}")
                consecutive_failures += 1
                work_queue.append((url, is_index_hint))
                await asyncio.sleep(30)

        await browser.close()
    
    save_progress(list(processed_urls), work_queue)

if __name__ == "__main__":
    asyncio.run(process_content())
