
import json
import os

progress_path = "/Users/microtoy/Documents/position-mgmt_v1.3.8/tools/forum_scraper/progress.json"

if os.path.exists(progress_path):
    with open(progress_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    processed = data.get("processed", [])
    queue = data.get("queue", [])
    
    print(f"当前已处理: {len(processed)} 条")
    print(f"当前队列: {len(queue)} 条")
    
    # 将 processed 转换为 [url, True] 并插入队列头部
    # 使用 True 作为 index_hint 比较稳妥
    new_items = [[url, True] for url in processed]
    
    updated_queue = new_items + queue
    updated_data = {
        "processed": [],
        "queue": updated_queue
    }
    
    with open(progress_path, 'w', encoding='utf-8') as f:
        json.dump(updated_data, f, indent=2, ensure_ascii=False)
    
    print(f"迁移完成！已处理置空，新队列总数: {len(updated_queue)} 条")
else:
    print("找不到 progress.json 文件")
