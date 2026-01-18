import os
from playwright.sync_api import sync_playwright

# Configuration
# You can hardcode your URL here to avoid typing it every time
DEFAULT_URL = "" 
STATE_FILE = "state.json"

def login_and_save_state():
    target_url = DEFAULT_URL
    if not target_url:
        target_url = input("请输入论坛网址 (例如 https://www.example.com): ").strip()
    
    if not target_url.startswith("http"):
        target_url = "https://" + target_url

    print(f"正在启动浏览器访问: {target_url}")
    print("注意: 浏览器启动后，请在弹出的窗口中找到登录按钮并扫码登录。")

    state_path = os.path.join(os.path.dirname(__file__), STATE_FILE)

    with sync_playwright() as p:
        # Launch browser with head (visible) so you can scan the code
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            page.goto(target_url)
            
            print("\n" + "="*50)
            print("【等待用户操作】")
            print("1. 请在打开的浏览器中完成扫码登录。")
            print("2. 确认登录成功并跳转到首页后，请回到这里按下回车键。")
            print("="*50 + "\n")
            
            input("登录成功后，请按回车键保存状态...")
            
            # Save storage state (cookies, local storage)
            context.storage_state(path=state_path)
            print(f"\n[成功] 登录状态已保存至: {state_path}")
            print("现在你可以运行下一步的爬虫脚本了，它将自动使用此登录状态。")
            
        except Exception as e:
            print(f"\n[错误] 发生异常: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    login_and_save_state()
