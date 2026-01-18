
import asyncio
from playwright.async_api import async_playwright
import os
import json

STATE_FILE = "tools/forum_scraper/state.json"
URL = "https://bbs.quantclass.cn/thread/31814"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
             storage_state=STATE_FILE if os.path.exists(STATE_FILE) else None,
             user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        print(f"Navigating to {URL}...")
        await page.goto(URL, wait_until="domcontentloaded")
        await asyncio.sleep(5)
        
        print(f"Title: {await page.title()}")
        
        # Dump H1s
        h1s = await page.evaluate("() => Array.from(document.querySelectorAll('h1')).map(e => e.textContent)")
        print(f"H1s: {h1s}")
        
        # Dump H2s
        h2s = await page.evaluate("() => Array.from(document.querySelectorAll('h2')).map(e => e.textContent)")
        print(f"H2s: {h2s}")
        
        # Inject CSS exactly as in 03_extract_content.py
        await page.add_style_tag(content="""
            /* 1. Hide excessive UI (Conservative) */
            .footer, .sidebar, .thread-catelog, .el-dialog__wrapper, 
            .v-note-op, .article-footer-operate, .thread-status, 
            .myprofile-bomb-box, .el-backtop { 
                display: none !important; 
                opacity: 0 !important;
                pointer-events: none !important;
            }
            #__nuxt, #__layout, .global, .w-100 {
                position: static !important;
                overflow: visible !important;
                height: auto !important;
                margin: 0 !important;
                padding: 0 !important;
                min-width: 100% !important;
            }
            .article-cont { 
                width: 100% !important; 
                margin: 0 !important; 
                padding: 20px !important; 
                max-width: none !important; 
                position: static !important;
                background: white !important;
                z-index: 100 !important;
            }
            pre, code, .vditor-reset pre, .vditor-reset code {
                white-space: pre-wrap !important; 
                word-wrap: break-word !important;
                overflow-x: hidden !important; 
                max-width: 100% !important;
            }
        """)

        # Wait for hydration
        await asyncio.sleep(5)
        
        # Find element with most text
        longest_el = await page.evaluate("""() => {
            let maxLen = 0;
            let maxEl = null;
            document.querySelectorAll('div').forEach(el => {
                // Ignore script/style
                if (['SCRIPT', 'STYLE'].includes(el.tagName)) return;
                // Only consider leaves or close to leaves to avoid selecting 'body'
                if (el.innerText.length > maxLen) {
                     maxLen = el.innerText.length;
                     maxEl = el;
                }
            });
            return maxEl ? {
                tag: maxEl.tagName,
                class: maxEl.className,
                id: maxEl.id,
                len: maxLen,
                text: maxEl.innerText.substring(0, 200)
            } : null;
        }""")
        
        print(f"Longest Element: {json.dumps(longest_el, indent=2, ensure_ascii=False)}")
        
        # Test Saving PDF
        output_path = "tools/forum_scraper/downloaded_pdfs/Fama-French三因子模型于沪深300上的复现，效果出乎意料的好！_31814.pdf"
        print(f"Attempting to save PDF to: {output_path}")
        try:
             await page.pdf(path=output_path, format="A4")
             print("PDF save successful!")
             if os.path.exists(output_path):
                 print(f"File verified on disk. Size: {os.path.getsize(output_path)}")
             else:
                 print("File NOT found on disk after save!")
        except Exception as e:
            print(f"PDF save failed: {e}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
