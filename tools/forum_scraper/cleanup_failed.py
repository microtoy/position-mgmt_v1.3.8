import json
import os
import re

DIR_PATH = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(DIR_PATH, "config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

OUTPUT_DIR = os.path.join(DIR_PATH, config["output_dir"])
PROGRESS_FILE = os.path.join(DIR_PATH, "progress.json")
FAILED_SUMMARY = os.path.join(DIR_PATH, "cleanup_report.json")

def cleanup():
    failed_urls = []
    failed_files = []
    
    print(f"正在扫描输出目录: {OUTPUT_DIR} ...")
    
    files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".md")]
    total_files = len(files)
    
    for idx, filename in enumerate(files):
        filepath = os.path.join(OUTPUT_DIR, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                
                # Check 1: Explicit "Hidden" marker
                is_failed = "剩余内容已隐藏" in content
                
                # Check 2: Editor Skeleton detection
                # "还能输入40000个字" is a clear sign the Vditor editor didn't hydrate the post
                if not is_failed and "还能输入40000个字" in content:
                    is_failed = True
                
                # Check 3: Generic Placeholder detection
                if not is_failed and (filename.startswith("Topic_") or "# 量化小论坛" in content[:50]):
                    is_failed = True

                # Check 4: Substantive content check
                if not is_failed:
                    has_ui_noise = "取 消 确 定" in content
                    clean_text = content.replace("取 消 确 定", "").replace("最热讨论", "").strip()
                    # Essence posts are usually long. Most failures have < 200 chinese chars.
                    chinese_chars = len(re.findall(r'[\u4e00-\u9fa5]', clean_text))
                    # Note: Summary/Index pages might be slightly shorter, but usually have many links.
                    # We'll stick to a safer 150 char limit + UI noise presence.
                    if (chinese_chars < 150 and has_ui_noise):
                        is_failed = True
                        
                if is_failed:
                    # Extract URL from the second line
                    f.seek(0)
                    lines = f.readlines()
                    url = None
                    for line in lines[:5]:
                        if line.startswith("URL:"):
                            url = line.replace("URL:", "").strip()
                            break
                    
                    if url:
                        failed_urls.append(url)
                    failed_files.append(filepath)
        except Exception as e:
            print(f"读取文件 {filename} 时出错: {e}")
            
        if (idx + 1) % 500 == 0:
            print(f"已扫描 {idx + 1} / {total_files}")

    print(f"\n扫描完成。发现 {len(failed_urls)} 个受限文档。")
    
    if not os.path.exists(PROGRESS_FILE):
        print("找不到 progress.json，仅执行文件删除。")
        for f in failed_files:
            os.remove(f)
    else:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            progress = json.load(f)
        
        original_processed_count = len(progress["processed"])
        original_queue_count = len(progress["queue"])
        
        processed_set = set(progress["processed"])
        queue_urls = [item[0] for item in progress["queue"]]
        
        # Reset state
        removed_count = 0
        requeued_count = 0
        
        for url in failed_urls:
            if url in processed_set:
                processed_set.remove(url)
                removed_count += 1
            
            if url not in queue_urls:
                # Add back to queue. [url, is_index_hint] format
                progress["queue"].insert(0, [url, False])
                queue_urls.append(url)
                requeued_count += 1
        
        progress["processed"] = list(processed_set)
        
        # Save updated progress
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(progress, f, indent=2, ensure_ascii=False)
            
        # Delete files
        for f in failed_files:
            os.remove(f)
            
        report = {
            "total_scanned": total_files,
            "failed_detected": len(failed_urls),
            "removed_from_processed": removed_count,
            "added_to_queue": requeued_count,
            "new_processed_total": len(progress["processed"]),
            "new_queue_total": len(progress["queue"])
        }
        
        with open(FAILED_SUMMARY, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            
        print("\n[清理报告]:")
        for k, v in report.items():
            print(f"  {k}: {v}")
        print(f"\n记录已保存至: {FAILED_SUMMARY}")

if __name__ == "__main__":
    cleanup()
