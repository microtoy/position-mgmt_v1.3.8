
import asyncio
from playwright.async_api import async_playwright

# Configuration
PROFILE_DIR = "browser_profile"
TARGET_URL = "https://bbs.quantclass.cn/thread/31814" # Use a known blocked URL

async def main():
    print("="*60)
    print("  人工辅助验证工具 (Persistent Context Mode)")
    print("="*60)
    print(f"1. 正在启动持久化浏览器 (Profile: {PROFILE_DIR})...")
    
    async with async_playwright() as p:
        # Launch persistent context
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False, # MUST be visible for login/captcha
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport=None,
            no_viewport=True
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        
        print(f"2. 正在访问目标页面: {TARGET_URL}")
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"   (页面加载提醒: {e})")

        print("\n" + "!"*60)
        print("  【请在弹出的浏览器中操作】")
        print("  1. 如果看到拼图，请手动完成。")
        print("  2. 如果右上角显示未登录，请【在此窗口】扫码登录。")
        print("  3. 确认能看到完整帖子内容。")
        print("!"*60 + "\n")
        
        input(">>> 完成登录/验证后，回这里按下【回车键】即可保存并退出... <<<")
        
        # In persistent mode, state is saved automatically. 
        # But we close gracefully to ensure write to disk.
        await context.close()
        print(f"✅ 持久化会话已就绪。下次运行爬虫将自动识别该状态。")

if __name__ == "__main__":
    asyncio.run(main())
