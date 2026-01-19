
import os
import shutil
import json
import re
import sys
import logging

# Suppress pdfminer logs
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# Try to import pdfplumber
try:
    import pdfplumber
except ImportError:
    print("❌ Error: 'pdfplumber' library is not installed.")
    print("Please run: pip install pdfplumber")
    sys.exit(1)

PDF_DIR = "downloaded_pdfs"
ARCHIVE_DIR = os.path.join(PDF_DIR, "archive_bad_layout")
PROGRESS_FILE = "progress.json"

# Keywords that suggest the post contains code which might be rendered incorrectly in old PDFs
CODE_KEYWORDS = [
    "def ", "class ", "import ", "from ", 
    "pd.", "np.", "plt.", "print(", 
    "return ", "var ", "const ", "function ", 
    "console.log", "=>", "dataframe", 
    "read_csv", "backtrader"
]

def main():
    if not os.path.exists(PDF_DIR):
        print(f"❌ PDF directory '{PDF_DIR}' not found.")
        return

    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    # 1. Identify PDFs with code
    files_to_reprocess = []
    
    print("🔍 Scanning PDFs for code content...")
    
    pdf_files = [f for f in os.listdir(PDF_DIR) if f.endswith(".pdf") and f != "archive_bad_layout"]
    
    count = 0
    problem_count = 0
    
    for filename in pdf_files:
        filepath = os.path.join(PDF_DIR, filename)
        has_code = False
        
        try:
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if not text:
                        continue
                    # Check for keywords
                    if any(kw in text for kw in CODE_KEYWORDS):
                        has_code = True
                        break
        except Exception as e:
            print(f"⚠️ Error reading {filename}: {e}")
            continue

        if has_code:
            print(f"  -> Found Code: {filename}")
            
            # Extract ID from filename (Format: Title_ID.pdf)
            match = re.search(r'_(\d+)\.pdf$', filename)
            if match:
                thread_id = match.group(1)
                url = f"https://bbs.quantclass.cn/thread/{thread_id}"
                
                # Move to archive
                dest = os.path.join(ARCHIVE_DIR, filename)
                shutil.move(filepath, dest)
                
                files_to_reprocess.append(url)
                problem_count += 1
        
        count += 1
        if count % 10 == 0:
            print(f"   ... scanned {count}/{len(pdf_files)}")

    print(f"\n✅ Audit Complete. Found {problem_count} files with code.")
    
    if not files_to_reprocess:
        return

    # 2. Update Progress JSON
    print("\n🔄 Updating progress.json...")
    
    try:
        with open(PROGRESS_FILE, 'r') as f:
            data = json.load(f)
            
        processed_set = set(data.get('processed', []))
        queue = data.get('queue', [])
        
        requeued_count = 0
        
        for url in files_to_reprocess:
            # Only requeue if it was marked as processed (avoid duplicates if already in queue)
            # Actually, even if not in processed, we want to ensure it is in queue if we archived the PDF
            
            if url in processed_set:
                processed_set.remove(url)
                # Insert at FRONT of queue for immediate retry
                queue.insert(0, [url, False]) 
                requeued_count += 1
            else:
                 # Check if already in queue
                in_queue = any(item[0] == url for item in queue)
                if not in_queue:
                    queue.insert(0, [url, False])
                    requeued_count += 1

        data['processed'] = list(processed_set)
        data['queue'] = queue
        
        with open(PROGRESS_FILE, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        print(f"✅ Re-queued {requeued_count} URLs.")
        
    except Exception as e:
        print(f"❌ Error updating progress.json: {e}")

if __name__ == "__main__":
    main()
