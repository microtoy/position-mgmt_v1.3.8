import os
import hashlib
from urllib.parse import urlparse
from playwright.async_api import Page

# Allowed paths for precision filtering
ALLOWED_PATH_SEGMENT = "/storage/flarum/"
# Blocked segments (redundant if we strict check allowed, but good for safety)
BLOCKED_SEGMENTS = ["/emoji/", "/avatars/", ".ico", ".svg"]

def is_valid_image_url(url: str) -> bool:
    """
    Check if the image URL is a substantive post image.
    Rule: Must contain '/storage/flarum/' and NOT contain blocked keywords.
    """
    if not url:
        return False
        
    lower_url = url.lower()
    
    # Precision Filter: Must be in the user-generated content storage
    if ALLOWED_PATH_SEGMENT not in lower_url:
        return False
        
    # Additional safety against noise
    for block in BLOCKED_SEGMENTS:
        if block in lower_url:
            return False
            
    return True

def get_local_path(url: str, output_base_dir: str) -> str:
    """Generate a local filename based on URL hash to avoid duplicates."""
    parsed = urlparse(url)
    ext = os.path.splitext(parsed.path)[1]
    if not ext or len(ext) > 5:
        ext = ".jpg" # Default fallback
        
    # Create valid filename from hash
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
    filename = f"{url_hash}{ext}"
    
    return os.path.join("images", filename) # Relative to md file location

async def download_image(page: Page, url: str, full_local_path: str):
    """
    Download image using page context (cookies) to avoid 403.
    """
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(full_local_path), exist_ok=True)
        
        # Don't re-download if exists
        if os.path.exists(full_local_path):
            return True

        # Use the page's request context to fetch (carrying session cookies)
        response = await page.request.get(url, timeout=10000)
        if response.status == 200:
            data = await response.body()
            with open(full_local_path, "wb") as f:
                f.write(data)
            return True
        else:
            print(f"Failed to download {url}: Status {response.status}")
            return False
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        return False
