import asyncio
import os
from playwright.async_api import async_playwright

# ALIGNED CONFIG
PROFILE_DIR = os.path.abspath("browser_profile")
TARGET_URL = "https://bbs.quantclass.cn/thread/30995"
FIXED_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

async def main():
    async with async_playwright() as p:
        print(f"Loading Profile from: {PROFILE_DIR}")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=True,
            user_agent=FIXED_UA
        )
        page = context.pages[0] if context.pages else await context.new_page()
        
        print(f"Accessing: {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="domcontentloaded")
        await asyncio.sleep(5)
        
        status = await page.evaluate("""() => {
            const article = document.querySelector('.article-cont') || document.querySelector('.vditor-reset') || document.querySelector('.thread-cont');
            return {
                is_logged_in: document.body.innerText.includes('退出'),
                article_found: !!article,
                text_preview: article ? article.innerText.substring(0, 100) : "N/A",
                has_hidden_tag: document.body.innerText.includes("剩余内容已隐藏")
            };
        }""")
        
        print("="*60)
        print(f"Login Detected ('退出'): {status['is_logged_in']}")
        print(f"Article Found: {status['article_found']}")
        print(f"Hidden Tag in Body: {status['has_hidden_tag']}")
        print(f"Content Preview: {status['text_preview']}...")
        print("="*60)
        
        await page.screenshot(path='final_session_debug.png', full_page=True)
        print("Screenshot saved to final_session_debug.png")
        await context.close()

if __name__ == '__main__':
    asyncio.run(main())
