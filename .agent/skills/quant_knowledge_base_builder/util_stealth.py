import random
import asyncio
from playwright.async_api import Page
from playwright_stealth import Stealth

# Common User Agents (Chrome on Windows/Mac)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

def get_random_ua():
    return random.choice(USER_AGENTS)

async def apply_stealth(page: Page):
    """Apply stealth scripts and randomize viewport/UA."""
    await Stealth().apply_stealth_async(page)
    # Randomize viewport slightly to avoid perfect fingerprinting
    width = random.randint(1280, 1920)
    height = random.randint(720, 1080)
    await page.set_viewport_size({"width": width, "height": height})

async def random_sleep(min_seconds=2, max_seconds=5):
    """Sleep for a random amount of time."""
    sleep_time = random.uniform(min_seconds, max_seconds)
    await asyncio.sleep(sleep_time)

async def human_scroll(page: Page):
    """Simulate human-like scrolling behavior."""
    # Scroll down in random steps
    for _ in range(random.randint(3, 7)):
        scroll_amount = random.randint(300, 800)
        await page.mouse.wheel(0, scroll_amount)
        await random_sleep(0.5, 1.5)
        
        # Occasionally move mouse
        x = random.randint(100, 1000)
        y = random.randint(100, 800)
        await page.mouse.move(x, y)
    
    # Scroll back up a bit sometimes (reading behavior)
    if random.random() > 0.7:
        await page.mouse.wheel(0, -random.randint(200, 500))
        await random_sleep(0.5, 1.0)
