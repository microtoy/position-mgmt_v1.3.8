
import os
import time
import glob
from pikepdf import Pdf
import logging

# --- Configuration ---
SOURCE_DIR = "downloaded_pdfs"
# Direct Sync to Google Drive
OUTPUT_DIR = "/Users/microtoy/Library/CloudStorage/GoogleDrive-microtoy@gmail.com/我的云端硬盘/Quant_Books"
BATCH_SIZE = 50  # Merge every 50 files into one book
POLL_INTERVAL = 60 # Check every minute

if not os.path.exists(OUTPUT_DIR):
    try:
        os.makedirs(OUTPUT_DIR)
    except OSError:
        # Fallback if drive not mounted or permission issue
        print(f"Warning: Could not create {OUTPUT_DIR}. Check Drive connection.")
        OUTPUT_DIR = "merged_books"
        if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("auto_merger.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def get_processed_files():
    """Load list of already merged files to avoid duplicates."""
    record_file = "merged_history.txt"
    if not os.path.exists(record_file):
        return set()
    with open(record_file, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f)

def record_processed_files(files):
    """Save processed files to history."""
    with open("merged_history.txt", "a", encoding="utf-8") as f:
        for file in files:
            f.write(file + "\n")

def merge_batch(file_list, batch_index):
    """Merge a list of PDFs using pikepdf with Outline (Bookmarks)."""
    output_filename = os.path.join(OUTPUT_DIR, f"Quant_Strategies_Vol_{batch_index:03d}.pdf")
    
    try:
        new_pdf = Pdf.new()
        
        # Add a root outline item for the volume
        # Note: Pikepdf outline manipulation is a bit complex in older versions, 
        # but simply appending pages is robust. 
        # For simplicity and robustness with standard pikepdf, we iterate and try to add outline items pointing to the first page of each doc.
        
        with new_pdf.open_outline() as outline:
            current_page_count = 0
            
            for file_path in file_list:
                try:
                    src = Pdf.open(file_path)
                    page_count = len(src.pages)
                    
                    # Extract title from filename (remove _id and extension)
                    basename = os.path.basename(file_path)
                    # Example: "Title_of_Post_1234.pdf" -> "Title of Post"
                    title = basename.rsplit('_', 1)[0]
                    
                    # Append pages
                    new_pdf.pages.extend(src.pages)
                    
                    # Add Outline Item pointing to the start page of this document
                    # OutlineItem(title, destination_page_index)
                    outline.root.append(f"{title}", current_page_count)
                    
                    current_page_count += page_count
                    
                except Exception as e:
                    logger.error(f"Error reading/appending {file_path}: {e}")
        
        new_pdf.save(output_filename)
        logger.info(f"✅ Generated Book: {output_filename} (Contains {len(file_list)} papers with Bookmarks)")
        return True
    except Exception as e:
        logger.error(f"Failed to save batch {batch_index}: {e}")
        return False

def main():
    logger.info("启动自动合并机器人 (Auto Merger Bot)...")
    logger.info(f"监控目录: {SOURCE_DIR}")
    logger.info(f"合并策略: 每 {BATCH_SIZE} 篇 -> 1 本书")

    while True:
        try:
            # 1. Scan all PDFs
            all_pdfs = sorted(glob.glob(os.path.join(SOURCE_DIR, "*.pdf")))
            
            # 2. Filter out already processed
            processed = get_processed_files()
            new_pdfs = [p for p in all_pdfs if p not in processed]
            
            current_count = len(new_pdfs)
            logger.info(f"当前待合并积压: {current_count} 篇")

            # 3. Check if enough for a batch
            if current_count >= BATCH_SIZE:
                # Determine how many batches we can make
                num_batches = current_count // BATCH_SIZE
                
                # Get next volume number
                existing_books = glob.glob(os.path.join(OUTPUT_DIR, "Quant_Strategies_Vol_*.pdf"))
                next_vol_idx = len(existing_books) + 1
                
                for i in range(num_batches):
                    batch_files = new_pdfs[i*BATCH_SIZE : (i+1)*BATCH_SIZE]
                    
                    logger.info(f"⏳ 正在合并第 {next_vol_idx + i} 卷...")
                    if merge_batch(batch_files, next_vol_idx + i):
                        record_processed_files(batch_files)
                    
                logger.info("批次处理完成，继续监控...")
            else:
                logger.info(f"数量未达标 ({current_count}/{BATCH_SIZE})，等待下一轮...")

        except Exception as e:
            logger.error(f"Loop error: {e}")
        
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
