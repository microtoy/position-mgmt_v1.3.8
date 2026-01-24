
import asyncio
import os
from playwright.async_api import async_playwright

# Configuration
import json
STATE_FILE = "state.json"
PROFILE_DIR = "browser_profile"
TARGET_URL = "https://bbs.quantclass.cn/thread/31814" 
FIXED_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

async def main():
    print("="*60)
    print("  人工辅助验证工具 (Persistent Context Mode)")
    print("="*60)
    print(f"1. 正在启动持久化浏览器 (Profile: {PROFILE_DIR})...")
    
    async with async_playwright() as p:
        # Use absolute path
        abs_profile_path = os.path.abspath(PROFILE_DIR)
        
        # Launch persistent context
        context = await p.chromium.launch_persistent_context(
            user_data_dir=abs_profile_path,
            headless=False, # MUST be visible for login/captcha
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            user_agent=FIXED_UA,
            viewport=None,
            no_viewport=True
        )

        # [Restoration Logic] Try to inject cookies AND LocalStorage from state.json if exists
        state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), STATE_FILE)
        if os.path.exists(state_path):
            print(f"3. 检测到外部状态文件: {STATE_FILE}")
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state_storage = json.load(f)
                    
                    # 1. Inject Cookies
                    if "cookies" in state_storage:
                        await context.add_cookies(state_storage["cookies"])
                        print(f"   -> 已尝试注入 {len(state_storage['cookies'])} 个 Cookies")
                    
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
                        print(f"   -> 已注册 {len(state_storage['origins'])} 个 Origin 的 LocalStorage 注入")
            except Exception as e:
                print(f"   -> (警告: 状态文件加载失败: {e})")
        
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
        
        # [Check Logic] Check if logged in before allowing save
        async def check_login_status(p):
            try:
                # 1. 检查页面文本特征
                content = await p.content()
                # 排除 HTML 标签后的纯文本检查
                text_content = await p.evaluate("() => document.body.innerText")
                
                # 登录状态的正面特征：退出、个人中心、发布、投籽
                positive_features = ["退出", "个人中心", "我的帖子", "消息", "投籽"]
                has_positive = any(feature in text_content for feature in positive_features)
                
                # 2. 检查 DOM 元素特征 (Discuz! Q 常见类名)
                has_user_element = await p.evaluate("""() => {
                    const selectors = [
                        '.user-name', '.avatar', '.login-out', 
                        '.header-user', '.user-info', '.my-profile',
                        'a[href*="/user/"]', 'img[src*="avatar"]'
                    ];
                    for (const s of selectors) {
                        const el = document.querySelector(s);
                        if (el && el.offsetHeight > 0) return true;
                    }
                    return false;
                }""")
                
                # 3. 反面特征检查：如果页面明显包含“登录”且没有头像，则可能未登录
                is_on_login_page = "login" in p.url.lower()
                
                # 综合判断：只要满足正面文本特征或 DOM 元素特征，即认为已登录
                return (has_positive or has_user_element) and not is_on_login_page
            except Exception as e:
                print(f"   (检测异常: {e})")
                return False

        while True:
            is_logged_in = await check_login_status(page)
            status_str = "【✅ 已登录】" if is_logged_in else "【❌ 未登录】"
            print(f"\n当前状态: {status_str}")
            
            if not is_logged_in:
                print(">>> 提醒: 检测到未登录，请先在浏览器中完成扫码登录。")
            
            user_input = input(">>> 输入 'y' 保存并退出，或按回车重新检测状态: ").strip().lower()
            if user_input == 'y':
                if not is_logged_in:
                    confirm = input("⚠️ 系统仍检测为‘未登录’，确定要保存吗？(y/n): ").strip().lower()
                    if confirm != 'y':
                        continue
                break

        # [Backup Logic] Force save state to state.json implies double safety
        try:
            print(f"4. 正在保存状态备份到 {STATE_FILE}...")
            await asyncio.sleep(2) # Give it a moment to sync
            await context.storage_state(path=state_path)
            print("   -> 状态备份已保存。")
        except Exception as e:
            print(f"   -> (保存失败: {e})")

        # In persistent mode, state is saved automatically. 
        # But we close gracefully to ensure write to disk.
        await context.close()
        print(f"✅ 持久化会话已就绪。下次运行爬虫将自动识别该状态。")

if __name__ == "__main__":
    asyncio.run(main())
