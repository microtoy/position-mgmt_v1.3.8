
import json
import os

PROGRESS_FILE = "progress.json"
TARGET_URL = "https://bbs.quantclass.cn/thread/30580"

if os.path.exists(PROGRESS_FILE):
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Insert at the beginning of the queue
    # Ensure format matches [url, is_index_hint]
    processed_set = set(data.get("processed", []))
    
    # Remove if already in queue to avoid dupes
    new_queue = [item for item in data.get("queue", []) if item[0] != TARGET_URL]
    
    # Remove from processed if present (force retry)
    if TARGET_URL in processed_set:
        print(f"Removing {TARGET_URL} from processed list to force re-scrape.")
        data["processed"] = list(processed_set - {TARGET_URL})
    
    # Insert at priority 0
    new_queue.insert(0, [TARGET_URL, False])
    data["queue"] = new_queue
    
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"Successfully injected {TARGET_URL} to the top of the queue.")
else:
    print("progress.json not found!")
