
import os
import json
import re
import glob

# --- Configuration ---
PDF_DIR = "tools/forum_scraper/downloaded_pdfs"
PROGRESS_FILE = "tools/forum_scraper/progress.json"
LINKS_FILE = "tools/forum_scraper/links.json"

# Quality Thresholds
MIN_SIZE_BYTES = 450 * 1024  # 450 KB
BAD_KEYWORDS = ["主题详情页", "Topic_", "正在加载", "Loading"]

def get_url_from_id(thread_id, all_links):
    # Try to find full URL from links.json based on thread ID
    # This is a bit expensive but accurate.
    # Pattern: .../thread/{id} or .../thread/{id}?
    for link in all_links:
        if f"/{thread_id}" in link:
            return link
    # Fallback construction
    return f"https://bbs.quantclass.cn/thread/{thread_id}"

def main():
    print("="*60)
    print("PDF Quality Check & Auto-Requeue Tool")
    print("="*60)
    print(f"Directory: {PDF_DIR}")
    print(f"Size Threshold: {MIN_SIZE_BYTES/1024:.2f} KB")
    print(f"Bad Keywords: {BAD_KEYWORDS}")
    print("-" * 60)

    # 1. Load Data
    all_pdfs = glob.glob(os.path.join(PDF_DIR, "*.pdf"))
    
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        progress = json.load(f)
    
    processed_set = set(progress.get("processed", []))
    queue = progress.get("queue", [])
    
    # Load all links for ID lookup
    all_links = []
    if os.path.exists(LINKS_FILE):
        with open(LINKS_FILE, "r", encoding="utf-8") as f:
             all_links = json.load(f)

    bad_files = []

    # 2. Scan Files
    for pdf_path in all_pdfs:
        filename = os.path.basename(pdf_path)
        size = os.path.getsize(pdf_path)
        
        reason = None
        
        # Check 1: Keywords
        for kw in BAD_KEYWORDS:
            if kw in filename:
                reason = f"Keyword '{kw}' detected"
                break
        
        # Check 2: Size
        if not reason and size < MIN_SIZE_BYTES:
            reason = f"Size too small ({size/1024:.2f} KB < {MIN_SIZE_BYTES/1024:.2f} KB)"
            
        if reason:
            bad_files.append((pdf_path, filename, reason))

    if not bad_files:
        print("✅ No bad files found! All PDFs look healthy.")
        return

    print(f"⚠️ Found {len(bad_files)} problematic files:")
    
    requeued_count = 0
    
    # 3. Process Bad Files
    for path, filename, reason in bad_files:
        print(f"  [BAD] {filename}")
        print(f"        Reason: {reason}")
        
        # Extract Thread ID
        # Format: Title_ID.pdf
        try:
            thread_id = filename.rsplit('_', 1)[1].replace(".pdf", "")
        except IndexError:
            print(f"        ❌ Could not extract ID from filename. Skipping auto-requeue.")
            continue
            
        target_url = get_url_from_id(thread_id, all_links)
        
        # Action 1: Delete File
        try:
            os.remove(path)
            print(f"        🗑️ File deleted.")
        except Exception as e:
            print(f"        ❌ Delete failed: {e}")
            
        # Action 2: Update Progress
        # Remove from processed
        if target_url in processed_set:
            processed_set.remove(target_url)
            print(f"        🔄 Removed from 'processed'.")
            
        # Add to queue (if not already there)
        # Check if already in queue
        in_queue = False
        for item in queue:
            if item[0] == target_url:
                in_queue = True
                break
        
        if not in_queue:
            queue.insert(0, [target_url, False]) # High priority
            requeued_count += 1
            print(f"        Enqueue: {target_url} (Priority High)")
        else:
            print(f"        (Already in queue)")
            
    # 4. Save Progress
    if requeued_count > 0 or len(bad_files) > 0:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "processed": list(processed_set),
                "queue": queue
            }, f, indent=2, ensure_ascii=False)
        print("-" * 60)
        print(f"💾 Progress saved. Requeued {requeued_count} items.")
    else:
        print("-" * 60)
        print("No changes needed in progress.json.")

if __name__ == "__main__":
    main()
