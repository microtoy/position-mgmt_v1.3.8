
import asyncio
from playwright.async_api import async_playwright

# Configuration
STATE_FILE = "tools/forum_scraper/state.json"
TARGET_URL = "https://bbs.quantclass.cn/thread/31814" # Use a known blocked URL

async def main():
    print("="*60)
    print("  人工辅助验证工具 (Verify & Refresh Session)")
    print("="*60)
    print("1. 正在启动浏览器...")
    
    async with async_playwright() as p:
        # Launch non-headless browser
        browser = await p.chromium.launch(
            headless=False, # MUST be visible
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
        )
        
        # Create context using existing state (so you don't have to login from scratch)
        context = await browser.new_context(
            storage_state=STATE_FILE if "state.json" in str(STATE_FILE) else None,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport=None
        )
        
        page = await context.new_page()
        
        print(f"2. 正在访问被拦截页面: {TARGET_URL}")
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        except:
            print("   (页面加载超时，可能正在显示验证码，请在浏览器中查看)")

        print("\n" + "!"*60)
        print("  【请操作浏览器】")
        print("  1. 如果看到拼图/验证码，请手动完成它。")
        print("  2. 如果提示'访问频繁'，请等待几分钟后再试。")
        print("  3. 确保页面已正常显示内容（能看到帖子标题和正文）。")
        print("!"*60 + "\n")
        
        input(">>> 完成验证后，请回到这里按下【回车键】保存状态... <<<")
        
        # Save the refreshed state (cookies/headers)
        await context.storage_state(path=STATE_FILE)
        print(f"✅ 新的会话状态已保存至: {STATE_FILE}")
        
        await browser.close()
        print("浏览器已关闭。现在可以尝试重新运行爬虫了（建议先休息一会）。")

if __name__ == "__main__":
    asyncio.run(main())
