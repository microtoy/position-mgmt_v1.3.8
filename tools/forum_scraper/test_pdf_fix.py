import asyncio
import os
import re
import datetime
from playwright.async_api import async_playwright

PROFILE_DIR = "browser_profile"
OUTPUT_DIR = "downloaded_pdfs"
FIXED_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

TEST_URLS = [
    "https://bbs.quantclass.cn/thread/20933",
    "https://bbs.quantclass.cn/thread/4218",
    "https://bbs.quantclass.cn/thread/16469",
]

async def main():
    async with async_playwright() as p:
        abs_profile_path = os.path.abspath(PROFILE_DIR)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=abs_profile_path,
            headless=True,
            user_agent=FIXED_UA,
            viewport={"width": 1600, "height": 1200}
        )
        page = context.pages[0] if context.pages else await context.new_page()
        
        for url in TEST_URLS:
            print(f"Processing: {url}")
            await page.goto(url, wait_until="domcontentloaded")
            
            # CSS注入
            await page.add_style_tag(content="""
                .footer, .sidebar, .thread-catelog, .el-dialog__wrapper, 
                .top-bar, .article-action, .thread-reply, .donate-btn,
                .vditor-toolbar, .comment-list, [class*="dialog"],
                [class*="toast"], [class*="popup"], [class*="modal"] {
                    display: none !important;
                }
                pre, code, .vditor-reset pre, .vditor-reset code,
                .hljs, pre code, div[class*="code"], 
                .language-python, .language-javascript, .language-text,
                [class*="highlight"] pre, [class*="highlight"] code {
                    white-space: pre-wrap !important; 
                    word-wrap: break-word !important;
                    word-break: break-all !important;
                    overflow-x: visible !important;
                    overflow-wrap: break-word !important;
                    max-width: 100% !important;
                    display: block !important;
                }
                .vditor-reset, .article-cont, [class*="scroll"], 
                pre, code, .hljs, [class*="code-block"], [class*="highlight"] {
                    overflow-x: visible !important;
                    overflow-y: visible !important;
                    overflow: visible !important;
                    max-height: none !important;
                    height: auto !important;
                }
            """)
            
            await asyncio.sleep(5)
            await page.mouse.wheel(0, 15000)
            await asyncio.sleep(3)
            
            # 滚动回顶部
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(1)
            
            # 获取标题
            title = await page.title()
            title = title.replace("- 量化小论坛", "").strip()
            safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
            thread_id = url.split("/")[-1]
            
            pdf_filename = f"{safe_title}_{thread_id}_TEST.pdf"
            filepath = os.path.join(OUTPUT_DIR, pdf_filename)
            
            await page.pdf(
                path=filepath,
                format="A4",
                print_background=True,
                margin={"top": "20px", "bottom": "20px", "left": "20px", "right": "20px"}
            )
            print(f"  -> Saved: {pdf_filename}")
            await asyncio.sleep(2)
        
        await context.close()
        print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
