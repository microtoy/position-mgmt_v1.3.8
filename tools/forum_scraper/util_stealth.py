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

async def human_mouse_jitter(page: Page):
    """Simulate small human mouse jitters."""
    for _ in range(random.randint(2, 5)):
        offset_x = random.randint(-2, 2)
        offset_y = random.randint(-2, 2)
        await page.mouse.move(random.randint(0, 1000) + offset_x, random.randint(0, 800) + offset_y)
        await asyncio.sleep(random.uniform(0.1, 0.3))

async def human_random_click(page: Page):
    """Simulate a random click on a safe area of the page."""
    if random.random() > 0.7:
        # Avoid clicking too close to edges or known UI elements if possible
        x = random.randint(200, 800)
        y = random.randint(200, 600)
        await page.mouse.click(x, y)
        await random_sleep(0.5, 1.5)

async def human_scroll(page: Page):
    """Simulate human-like variable scrolling behavior."""
    # Scroll down in random steps with variable speed
    total_scrolls = random.randint(5, 10)
    for i in range(total_scrolls):
        # Variable scroll amount
        scroll_amount = random.randint(200, 1000)
        
        # Simulate acceleration/deceleration by breaking the scroll into smaller chunks
        steps = random.randint(3, 6)
        for s in range(steps):
             await page.mouse.wheel(0, scroll_amount / steps)
             await asyncio.sleep(random.uniform(0.05, 0.15))
             
        # Reading pause
        await random_sleep(1.0, 3.0)
        
        # Occasionally move/jitter mouse while reading
        if random.random() > 0.5:
            await human_mouse_jitter(page)
            
        # Occasional random click
        await human_random_click(page)
    
    # Scroll back up a bit sometimes (browsing behavior)
    if random.random() > 0.8:
        await page.mouse.wheel(0, -random.randint(300, 600))
        await random_sleep(1.0, 2.0)
