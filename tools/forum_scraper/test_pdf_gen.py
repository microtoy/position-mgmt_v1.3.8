
import asyncio
import os
import random
from playwright.async_api import async_playwright
from util_stealth import apply_stealth

# Target URL for testing (The one we successfully scraped earlier)
TEST_URL = "https://bbs.quantclass.cn/thread/2370"
STATE_FILE = "/Users/microtoy/Documents/position-mgmt_v1.3.8/tools/forum_scraper/state.json"
OUTPUT_PDF = "/Users/microtoy/Documents/position-mgmt_v1.3.8/tools/forum_scraper/downloaded_content/test_report.pdf"

async def generate_pdf():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True) # PDF printing supported in headless
        context = await browser.new_context(
            storage_state=STATE_FILE if os.path.exists(STATE_FILE) else None,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await apply_stealth(page)
        
        print(f"Loading {TEST_URL}...")
        await page.goto(TEST_URL, wait_until="domcontentloaded", timeout=60000)
        
        # Inject custom CSS to clean up for PDF
        await page.add_style_tag(content="""
            .header, .footer, .sidebar, .thread-catelog, .el-dialog__wrapper, .v-note-op, .article-footer-operate { display: none !important; }
            .article-cont { width: 100% !important; margin: 0 !important; padding: 20px !important; }
            body { background: white !important; }
        """)
        
        print("Waiting for content hydration...")
        await asyncio.sleep(10)
        
        # Expand any lazy loaded elements if possible
        await page.mouse.wheel(0, 10000)
        await asyncio.sleep(5)

        print(f"Printing PDF to {OUTPUT_PDF}...")
        await page.pdf(
            path=OUTPUT_PDF,
            format="A4",
            print_background=True,
            margin={"top": "20px", "bottom": "20px", "left": "20px", "right": "20px"}
        )
        print("Done!")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(generate_pdf())
