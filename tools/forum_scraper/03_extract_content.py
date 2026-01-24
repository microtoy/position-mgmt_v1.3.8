import datetime
import asyncio
import sys
import os
import json
import random
import time
import re
import requests
from playwright.async_api import async_playwright
from util_stealth import apply_stealth, random_sleep, human_scroll, get_random_ua

# --- Configuration ---
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

LINKS_FILE = "links.json"
PROGRESS_FILE = "progress.json"
STATE_FILE = "state.json"
OUTPUT_DIR = "downloaded_pdfs" 
PROFILE_DIR = "browser_profile"
FIXED_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# --- Safety Settings ---
DAILY_LIMIT_MIN = 50  # 每日最少 50 篇
DAILY_LIMIT_MAX = 80  # 每日最多 80 篇
HOURLY_LIMIT = 10     # 每小时最多 10 篇
NIGHT_START = 23    # 夜间休息开始时间（23点）
NIGHT_END = 7       # 夜间休息结束时间
DAILY_COUNT_FILE = "daily_count.json"  # 记录每日访问量
HOURLY_COUNT_FILE = "hourly_count.json" # 记录每小时访问量

# 时段配置：模拟人类学习节奏
# 早间(7-12): 活跃学习，间隔短
# 午间(12-14): 午休，暂停或极慢
# 下午(14-18): 活跃学习
# 晚间(18-23): 轻度学习，间隔长
TIME_SLOTS = {
    "morning": {"hours": range(7, 12), "min_sleep": 45, "max_sleep": 90},      # 早间：增加间隔
    "lunch": {"hours": range(12, 14), "min_sleep": 300, "max_sleep": 600},     # 午休：基本暂停
    "afternoon": {"hours": range(14, 18), "min_sleep": 50, "max_sleep": 100},  # 下午：增加间隔
    "evening": {"hours": range(18, 23), "min_sleep": 70, "max_sleep": 150},    # 晚间：更慢
}

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

# --- WeCom Notification ---
WECOM_WEBHOOK = config.get("wecom_webhook", "")
WECOM_WEBHOOK_ERROR = config.get("wecom_webhook_error", "")

def send_wecom_alert(title: str, content: str, is_error: bool = False):
    """发送企业微信群通知，is_error=True 时使用错误专用 Webhook"""
    webhook = WECOM_WEBHOOK_ERROR if is_error else WECOM_WEBHOOK
    if not webhook:
        logger.warning("未配置企业微信 Webhook，跳过通知")
        return
    
    try:
        # 成功用绿色勾，失败用红色警告
        icon = "⚠️" if is_error else "✅"
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"### {icon} {title}\n{content}"
            }
        }
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("✅ 企业微信通知发送成功")
        else:
            logger.warning(f"企业微信通知发送失败: {resp.text}")
    except Exception as e:
        logger.error(f"企业微信通知异常: {e}")

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
        
        # 2. Check DOM for title if document.title looks generic
        if not t or any(kw in t for kw in ["主题详情页", "量化小论坛", "Loading", "正在加载", "最新回复"]):
            try:
                # 尝试多种选择器获取真实标题
                dom_title = await page.evaluate("""() => {
                    // 按优先级尝试多个选择器
                    const selectors = [
                        '.thread-title',
                        '.article-title', 
                        '.post-title',
                        '.topic-title',
                        'h1.title',
                        'h1',
                        '.content-header h1',
                        '.main-content h1'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.textContent.trim().length > 2) {
                            const text = el.textContent.trim();
                            // 排除通用标题
                            if (!text.includes('主题详情页') && !text.includes('最新回复')) {
                                return text;
                            }
                        }
                    }
                    return "";
                }""")
                if dom_title:
                    t = dom_title.strip()
            except:
                pass

        # 3. Validation - 排除更多通用标题
        generic_titles = ["主题详情页", "量化小论坛", "Loading", "正在加载", "最新回复", "首页"]
        if t and all(kw not in t for kw in generic_titles):
            if len(t) > 2:
                logger.info(f"  -> Found clean title: {t}")
                return t
        
        if i % 2 == 0: logger.info(f"  -> Waiting for title... (Current: {t})")
        await asyncio.sleep(1)
    
    # Fallback: 尝试从页面内容提取标题
    final_t = await page.title()
    final_t = final_t.replace("- 量化小论坛", "").strip()
    
    # 最后尝试：从文章内容的第一行提取
    try:
        first_line = await page.evaluate("""() => {
            const article = document.querySelector('.article-cont') || document.querySelector('.vditor-reset');
            if (article) {
                const firstP = article.querySelector('p, h1, h2, h3');
                if (firstP) return firstP.textContent.trim().substring(0, 50);
            }
            return "";
        }""")
        if first_line and len(first_line) > 5:
            return first_line
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

        # [Addition] Inject cookies AND LocalStorage from state.json
        state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), STATE_FILE)
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state_storage = json.load(f)
                    
                    # 1. Inject Cookies
                    if "cookies" in state_storage:
                        await context.add_cookies(state_storage["cookies"])
                        logger.info(f"  -> Injected {len(state_storage['cookies'])} cookies")
                    
                    # 2. Inject LocalStorage via Init Script (Deep Restoration)
                    if "origins" in state_storage:
                        for origin_data in state_storage["origins"]:
                            origin = origin_data.get("origin")
                            ls_items = origin_data.get("localStorage", [])
                            if origin and ls_items:
                                # Construct JS to set localStorage
                                ls_script = "\n".join([
                                    f"localStorage.setItem({json.dumps(item['name'])}, {json.dumps(item['value'])});"
                                    for item in ls_items
                                ])
                                # Use add_init_script to ensure it runs before any page scripts
                                await context.add_init_script(f"(function() {{ if (window.location.origin === '{origin}') {{ {ls_script} }} }})()")
                        logger.info(f"  -> Registered LocalStorage injection for {len(state_storage['origins'])} origins")
                            
            except Exception as e:
                logger.warning(f"  -> Failed to load state from {STATE_FILE}: {e}")

        logger.info(f"Starting crawl with {len(work_queue)} items in queue...")

        while work_queue:
            if consecutive_failures >= 5:
                logger.error("Too many consecutive failures. Stopping.")
                break

            url, is_index_hint = work_queue.pop(0)
            
            if url in processed_urls:
                continue
            
            # --- SCHEDULING LOGIC (Human Learning Pattern) ---
            now = datetime.datetime.now()
            current_hour = now.hour
            
            # 1. 每日/每小时限额检查
            today_str = now.strftime("%Y-%m-%d")
            this_hour_str = now.strftime("%Y-%m-%d %H")
            
            # 使用 random 种子（基于日期）生成今天的固定限额，增加拟人感
            random.seed(today_str)
            daily_limit_today = random.randint(DAILY_LIMIT_MIN, DAILY_LIMIT_MAX)
            random.seed() # 重置种子
            
            daily_data = {}
            if os.path.exists(DAILY_COUNT_FILE):
                try:
                    with open(DAILY_COUNT_FILE, "r") as f: daily_data = json.load(f)
                except: pass
            today_count = daily_data.get(today_str, 0)
            
            hourly_data = {}
            if os.path.exists(HOURLY_COUNT_FILE):
                try:
                    with open(HOURLY_COUNT_FILE, "r") as f: hourly_data = json.load(f)
                except: pass
            hour_count = hourly_data.get(this_hour_str, 0)
            
            # 检查每日上限
            if today_count >= daily_limit_today:
                logger.warning(f"📊 [Limit] 今日已访问 {today_count} 篇，达到今日动态上限 {daily_limit_today}。")
                logger.warning(f"📊 等待至明天 07:00 重置...")
                tomorrow_7am = (now + datetime.timedelta(days=1)).replace(hour=7, minute=0, second=0)
                wait_seconds = (tomorrow_7am - now).total_seconds()
                await asyncio.sleep(wait_seconds)
                continue

            # 检查每小时上限
            if hour_count >= HOURLY_LIMIT:
                logger.warning(f"⏳ [Hourly Limit] 本小时已访问 {hour_count} 篇，达到上限 {HOURLY_LIMIT}。")
                next_hour = (now + datetime.timedelta(hours=1)).replace(minute=1, second=0)
                wait_seconds = (next_hour - now).total_seconds()
                logger.warning(f"⏳ 将在下个整点 ({next_hour.strftime('%H:%M')}) 恢复，等待 {int(wait_seconds/60)} 分钟...")
                await asyncio.sleep(wait_seconds)
                continue
            
            # 2. 夜间休息 (23:00 - 07:00)
            if current_hour >= NIGHT_START or current_hour < NIGHT_END:
                wake_time = now.replace(hour=NIGHT_END, minute=0, second=0)
                if current_hour >= NIGHT_START:
                    wake_time += datetime.timedelta(days=1)
                wait_seconds = (wake_time - now).total_seconds()
                logger.warning(f"🌙 [睡眠] 现在是 {now.strftime('%H:%M')}，进入夜间休息模式。")
                logger.warning(f"🌙 将于明早 {NIGHT_END}:00 自动恢复，等待 {wait_seconds/3600:.1f} 小时...")
                save_progress(list(processed_urls), work_queue)
                await asyncio.sleep(wait_seconds)
                logger.info("☀️ 早安！开始新的一天。")
                continue
            
            # 3. 时段感知动态间隔
            min_sleep, max_sleep = 30, 90  # 默认值
            current_slot = "default"
            for slot_name, slot_config in TIME_SLOTS.items():
                if current_hour in slot_config["hours"]:
                    min_sleep = slot_config["min_sleep"]
                    max_sleep = slot_config["max_sleep"]
                    current_slot = slot_name
                    break
            
            # 4. 午休时段特殊处理（12:00-14:00 基本暂停）
            if current_slot == "lunch":
                logger.info(f"🍽️ [午休] 现在是午餐时间 ({now.strftime('%H:%M')})，放慢节奏...")
            
            # 5. 每30分钟休息5分钟
            if time.time() - last_short_break > 1800:
                logger.info(f"☕ [休息] 连续工作30分钟，休息5分钟...")
                await asyncio.sleep(300)
                last_short_break = time.time()
                logger.info("☕ 休息结束，继续学习。")
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
                    /* 1. 彻底暴力清理所有干扰元素（只隐藏按钮和遮罩，不隐藏内容） */
                    .header, .header-container, .top-nav, .nav-bar, .breadcrumb,
                    .footer, .sidebar, .thread-catelog, .el-dialog__wrapper, 
                    .v-note-op, .article-footer-operate, .thread-status, 
                    .myprofile-bomb-box, .el-backtop, .v-modal, .mask,
                    [class*="skeleton"], [class*="loading"], [class*="mask"],
                    [class*="overlay"], [class*="placeholder"], [class*="lazy"],
                    [class*="toolbar"], [class*="action-bar"], .copy-code-btn,
                    /* 隐藏折叠/展开按钮本身，但不隐藏其包裹的内容 */
                    [class*="expand"] i, [class*="collapse"] i, [class*="fold"] i,
                    .show-more, .read-more-btn { 
                        display: none !important; 
                        opacity: 0 !important;
                        visibility: hidden !important;
                    }

                    /* 强制显示可能被折叠的内容 */
                    [class*="content-hidden"], [class*="collapsed"], .is-collapsed {
                        display: block !important;
                        max-height: none !important;
                        visibility: visible !important;
                        opacity: 1 !important;
                    }

                    /* 2. 深度重置布局流：强制所有元素回归标准文档流 */
                    * {
                        position: static !important;
                        float: none !important;
                        clear: none !important; /* 后面会针对性设置 */
                        box-sizing: border-box !important;
                    }

                    html, body, #__nuxt, #__layout, .global, .w-100 {
                        display: block !important;
                        height: auto !important;
                        width: 100% !important;
                        overflow: visible !important;
                        margin: 0 !important;
                        padding: 0 !important;
                        background: white !important;
                    }

                    /* 3. 文章容器：确保它是布局的稳固基座 */
                    .article-cont { 
                        display: block !important;
                        width: 100% !important; 
                        padding: 20px !important; 
                        background: white !important;
                    }

                    /* 强制文章内部的直接子元素（如 h1, h2, p, pre, div）垂直线性排列 */
                    .article-cont > *, .vditor-reset > * {
                        display: block !important;
                        clear: both !important; /* 强制换行，防止重叠 */
                        margin-bottom: 1.2em !important;
                        position: static !important;
                        visibility: visible !important;
                        opacity: 1 !important;
                    }

                    /* 4. 代码块：针对性修复高度计算问题 */
                    pre, code, .hljs, .vditor-reset pre, .vditor-reset code {
                        display: block !important;
                        width: 100% !important;
                        height: auto !important;
                        min-height: 1.5em !important;
                        max-height: none !important;
                        overflow: visible !important; /* 确保内容撑开容器高度 */
                        white-space: pre-wrap !important; 
                        word-wrap: break-word !important;
                        word-break: break-all !important;
                        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace !important;
                        font-size: 11px !important;
                        line-height: 1.4 !important;
                        background: #f8f8f8 !important;
                        border: 1px solid #ddd !important;
                        padding: 12px !important;
                        margin: 20px 0 !important;
                        tab-size: 4 !important;
                    }

                    /* 5. 评论区：极限压缩且保持整齐 */
                    .comment-list, .reply-list {
                        display: block !important;
                        margin-top: 30px !important;
                        border-top: 1px solid #eee !important;
                    }
                    .comment-item, .reply-item {
                        display: block !important;
                        padding: 8px 0 !important;
                        border-bottom: 1px solid #f0f0f0 !important;
                        clear: both !important;
                    }
                    .comment-item-header, .reply-item-header {
                        display: flex !important;
                        align-items: center !important;
                        margin-bottom: 4px !important;
                    }
                    .avatar {
                        width: 18px !important;
                        height: 18px !important;
                        margin-right: 8px !important;
                        border-radius: 50% !important;
                    }
                    .nickname { font-size: 11px !important; font-weight: bold !important; color: #555 !important; }
                    .time { font-size: 10px !important; color: #999 !important; margin-left: 10px !important; }
                    .comment-item-content, .reply-item-content {
                        font-size: 12px !important;
                        color: #333 !important;
                        padding-left: 26px !important;
                        line-height: 1.5 !important;
                    }

                    /* 6. 特殊：彻底移除所有伪元素装饰，防止莫名其妙的灰色线条/块 */
                    *::before, *::after {
                        display: none !important;
                        content: none !important;
                    }

                    /* 7. 打印优化 */
                    @media print {
                        * { -webkit-print-color-adjust: exact !important; }
                    }
                """)

                
                # 3. Enhanced Wait Strategy (Hydration)
                logger.info("  -> Waiting for hydration...")
                await asyncio.sleep(10) # Base wait
                
                # 4. 循环滚动触底，确保触发所有懒加载 (Crucial for Long PDF)
                logger.info("  -> Scrolling to trigger lazy loads...")
                await page.evaluate("""async () => {
                    let lastHeight = document.documentElement.scrollHeight;
                    while (true) {
                        window.scrollBy(0, 1500);
                        await new Promise(r => setTimeout(r, 800));
                        let newHeight = document.documentElement.scrollHeight;
                        if (newHeight === lastHeight) {
                            // 再次尝试滚动一段距离，确认是否真的到底
                            window.scrollBy(0, 1000);
                            await new Promise(r => setTimeout(r, 1200));
                            if (document.documentElement.scrollHeight === newHeight) break;
                        }
                        lastHeight = newHeight;
                        if (lastHeight > 50000) break; // 安全阈值，防止无限滚动
                    }
                }""")
                await asyncio.sleep(2)
                
                # 5. 回到顶部提取标题
                await page.evaluate("window.scrollTo(0, 0)")
                title = await get_clean_title(page)
                
                # --- AUTO-STOP PROTECTION (改进版) ---
                # 只有当标题和内容都不正常时才判定为 antibot
                is_title_generic = "主题详情页" in title or title == ""
                
                if is_title_generic:
                    # 先检查页面内容是否已经加载（通过文章区域是否有实质内容）
                    content_check = await page.evaluate("""() => {
                        const article = document.querySelector('.article-cont') || 
                                        document.querySelector('.vditor-reset') || 
                                        document.querySelector('.thread-cont');
                        if (article && article.innerText.trim().length > 100) {
                            return { hasContent: true, length: article.innerText.length };
                        }
                        return { hasContent: false, length: 0 };
                    }""")
                    
                    if content_check.get("hasContent"):
                        # 内容已加载，只是标题获取失败，使用 thread_id 作为标题
                        logger.info(f"  -> 标题获取失败，但内容已加载 ({content_check.get('length')} chars)，继续处理...")
                        title = f"Thread_{url.split('/')[-1].split('?')[0]}"
                    else:
                        # 标题和内容都没有，真正的 antibot
                        debug_screenshot_path = f"error_antibot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        await page.screenshot(path=debug_screenshot_path, full_page=True)
                        logger.warning(f"⚠️ [Anti-bot] Detected (Title='{title}'). Screenshot saved: {debug_screenshot_path}")
                        logger.warning(f"⚠️ [Anti-bot] Waiting 10 minutes before retrying...")
                        
                        # 发送企业微信告警（使用错误专用 Webhook）
                        send_wecom_alert(
                            "论坛爬虫 Anti-bot 告警",
                            f"> **检测时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"> **问题URL**: {url}\n"
                            f"> **当前标题**: {title}\n"
                            f"> **队列剩余**: {len(work_queue)} 篇\n\n"
                            f"程序将等待 10 分钟后自动重试...",
                            is_error=True
                        )
                        
                        # Re-queue current item to ensure it gets retry later
                        work_queue.insert(0, (url, is_index_hint))
                        save_progress(list(processed_urls), work_queue)
                        await asyncio.sleep(600)  # Wait 10 minutes
                        continue  # Then continue with queue
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
                    
                    // 改进后的登录检测：检查“退出”文字
                    const bodyText = document.body.innerText;
                    const is_logged_in = bodyText.includes('退出') || 
                                         bodyText.includes('个人中心') || 
                                         !!document.querySelector('.avatar');
                    
                    if (text.includes("剩余内容已隐藏") || text.includes("报名课程即可查看完整内容")) {
                        return { status: "HIDDEN", is_logged_in: is_logged_in };
                    }
                    if (!article || article.innerText.trim().length < 50) {
                        return { status: "EMPTY", is_logged_in: is_logged_in };
                    }
                    return { status: "OK", is_logged_in: is_logged_in };
                }""")
                
                content_status = check_result.get("status")
                is_logged_in = check_result.get("is_logged_in", False)
                
                if content_status == "HIDDEN":
                    if not is_logged_in:
                        # 核心改动：如果未登录看到隐藏，认为是会话失效，而不是真的VIP
                        logger.error(f"  -> [CRITICAL] 会话失效！检测到未登录且内容被隐藏。")
                        
                        # 保存故障快照
                        debug_dir = "temp_screenshots"
                        if not os.path.exists(debug_dir): os.makedirs(debug_dir)
                        fail_screenshot = os.path.join(debug_dir, f"session_fail_{datetime.datetime.now().strftime('%H%M%S')}.png")
                        await page.screenshot(path=fail_screenshot, full_page=True)
                        logger.info(f"  -> 已保存会话失效截图: {fail_screenshot}")

                        send_wecom_alert(
                            "🚨 爬虫会话失效",
                            f"> **检测时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"> **URL**: {url}\n"
                            f"> **显示文本**: {check_result.get('status')}\n"
                            f"> **状态**: 检测到未登录，请重新运行 verify_and_refresh.py\n"
                            f"> **截图**: {fail_screenshot}",
                            is_error=True
                        )
                        # 将当前任务放回队列并停止
                        work_queue.insert(0, (url, is_index_hint))
                        save_progress(list(processed_urls), work_queue)
                        break # 中断循环，等待用户干预
                    
                    logger.warning(f"  -> [SKIP] VIP/权限帖，跳过此帖。")
                    processed_urls.add(url)
                    save_progress(list(processed_urls), work_queue)
                    # 发送 skip 通知
                    send_wecom_alert(
                        "⚠️ 跳过 VIP/权限帖",
                        f"> **时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"> **URL**: {url}\n"
                        f"> 进度: {len(processed_urls)}/{len(processed_urls)+len(work_queue)}",
                        is_error=True
                    )
                    await random_sleep(5, 10)
                    continue
                elif content_status != "OK":
                    logger.warning("  -> Content seems empty. Skipping.")
                    # 发送内容为空通知
                    send_wecom_alert(
                        "⚠️ 内容为空",
                        f"> **时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"> **URL**: {url}\n"
                        f"> 已重新加入队列等待重试",
                        is_error=True
                    )
                    consecutive_failures += 1
                    work_queue.append((url, is_index_hint))
                    await asyncio.sleep(30)
                    continue

                # 7. 模拟阅读时间（根据内容长度计算，更加隐蔽）
                content_length = await page.evaluate("""() => {
                    const article = document.querySelector('.article-cont') || 
                                    document.querySelector('.vditor-reset') || 
                                    document.querySelector('.thread-cont');
                    return article ? article.innerText.length : 500;
                }""")
                # 假设阅读速度 300-500 字/分钟，计算阅读时间（秒）
                # 最少15秒，最多120秒
                base_read_time = max(15, min(120, content_length / 400 * 60))
                read_time = int(base_read_time * random.uniform(0.8, 1.3))  # 加入随机波动
                logger.info(f"  -> 模拟阅读 {read_time} 秒 (内容 {content_length} 字)...")
                
                # 阅读时模拟缓慢滚动
                scroll_steps = random.randint(3, 6)
                for _ in range(scroll_steps):
                    await asyncio.sleep(read_time / scroll_steps)
                    scroll_amount = random.randint(200, 600)
                    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                
                # 8. 阅读完毕，回到顶部准备打印 PDF
                await page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(1)
                
                # 9. 获取页面总高度以实现“无分页”长图 PDF
                height = await page.evaluate("() => document.documentElement.scrollHeight")
                # 增加一点缓冲高度
                pdf_height = height + 50
                
                # 10. Print to PDF (Long Page, No Pagination)
                logger.info(f"  -> Printing Long PDF ({height}px): {pdf_filename}")
                await page.pdf(
                    path=filepath,
                    width="1200px",  # 固定宽度，模拟网页
                    height=f"{pdf_height}px",
                    print_background=True,
                    margin={"top": "0px", "bottom": "0px", "left": "0px", "right": "0px"}
                )
                
                logger.info(f"  -> Success!")
                processed_urls.add(url)
                consecutive_failures = 0
                
                # 发送成功通知到企业微信（带时间戳）
                send_wecom_alert(
                    "✅ 爬取成功",
                    f"> **{safe_title}**\n"
                    f"> {url}\n"
                    f"> 时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"> 进度: {len(processed_urls)}/{len(processed_urls)+len(work_queue)} | 剩余: {len(work_queue)}"
                )

                # 10. Save Progress (EVERY TIME for safety)
                save_progress(list(processed_urls), work_queue)
                
                # 11. 更新计数器
                daily_data[today_str] = daily_data.get(today_str, 0) + 1
                with open(DAILY_COUNT_FILE, "w") as f:
                    json.dump(daily_data, f)
                
                hourly_data[this_hour_str] = hourly_data.get(this_hour_str, 0) + 1
                with open(HOURLY_COUNT_FILE, "w") as f:
                    json.dump(hourly_data, f)
                
                # 10. 时段感知动态间隔
                sleep_time = random.randint(min_sleep, max_sleep)
                logger.info(f"  -> [Safety] 时段:{current_slot} 休息 {sleep_time} 秒...")
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error processing {url}: {e}")
                consecutive_failures += 1
                work_queue.append((url, is_index_hint))
                await asyncio.sleep(30)

        await context.close()
    
    save_progress(list(processed_urls), work_queue)
    logger.info("🎉 队列已清空或达到上限，爬虫任务完成。")

if __name__ == "__main__":
    asyncio.run(process_content())
