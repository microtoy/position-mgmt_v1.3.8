
import json
import os
import glob

DOWNLOAD_DIR = "tools/forum_scraper/downloaded_pdfs"
PROGRESS_FILE = "tools/forum_scraper/progress.json"
BASE_URL = "https://bbs.quantclass.cn/thread/"

def main():
    # 1. Find bad files
    bad_files = glob.glob(os.path.join(DOWNLOAD_DIR, "主题详情页_*.pdf"))
    if not bad_files:
        print("No bad files found.")
        return

    print(f"Found {len(bad_files)} bad files.")
    
    ids_to_requeue = []
    for f in bad_files:
        # Extract ID from filename: "主题详情页_12345.pdf"
        try:
            basename = os.path.basename(f)
            thread_id = basename.split("_")[-1].replace(".pdf", "")
            ids_to_requeue.append(thread_id)
            
            # Delete file
            os.remove(f)
            print(f"Deleted: {basename}")
        except Exception as e:
            print(f"Error processing {f}: {e}")

    # 2. Update progress.json
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        processed_set = set(data.get("processed", []))
        queue = data.get("queue", [])
        
        requeued_count = 0
        for tid in ids_to_requeue:
            url = f"{BASE_URL}{tid}"
            
            # Remove from processed
            if url in processed_set:
                processed_set.remove(url)
                
            # Add to queue (if not already there)
            # We insert at 0 to prioritize
            is_in_queue = any(item[0] == url for item in queue)
            if not is_in_queue:
                queue.insert(0, [url, False])
                requeued_count += 1
                print(f"Requeued: {url}")
            else:
                print(f"Already in queue: {url}")

        data["processed"] = list(processed_set)
        data["queue"] = queue
        
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        print(f"Progress updated. Requeued {requeued_count} items.")

if __name__ == "__main__":
    main()
