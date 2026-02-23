#!/usr/bin/env python3
"""
bill_parser.py – OCR utility-bill PDFs (PyMuPDF + Tesseract) then parse rows with Gemini.
Flip DEBUG = True / False at the top; each Gemini call is hard-timeout-capped.
"""

import os, sys, csv, logging, textwrap, threading, time
from pathlib import Path
from typing import List, Optional

import fitz
from PIL import Image
import pytesseract
import google.generativeai as genai
from tqdm import tqdm
from colorama import Fore, Style, init as colorama_init
import tkinter as tk
from tkinter import filedialog, Tk

# ───────── USER SETTINGS ─────────
API_KEY       = "AIzaSyAFvRWaM5ADsL51dR2XLNoZZIFo-vKC_to"   # or rely on GEMINI_API_KEY env
TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
MODEL_NAME    = "gemini-2.5-flash"
MAX_CHARS     = 65_000
DEBUG         = True          # ← toggle diagnostics
TIMEOUT_SEC   = 600            # per-request Gemini timeout
# ---------------------------------
COLUMNS = [
    "Bill To Name First Line","Bill To Name Second Line","Account Number",
    "Service Address","Bill Period Start","Bill Period End","Utility Type",
    "Consumption Amount","Line Item Description","Line Item Charge",
    "Bill Date","Due Date"
]
PIPE_COUNT = len(COLUMNS) - 1  # = 12
# ─────────────────────────────────

pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE

PROMPT = textwrap.dedent(f"""
    You are a strict utility-bill parser.

    • Output ONLY the line-item rows, no header or commentary.
    • Each row must contain exactly {len(COLUMNS)} fields separated by “|”
      → {PIPE_COUNT} pipes per row.
    • If there are no rows, output the single word EMPTY.

    Field order:
    {' | '.join(COLUMNS[:-1])}

    ```
    {{bill_text}}
    ```
""").strip()

# ═════════ helper functions ═════════
def pick_folder_gui() -> Optional[str]:
    root: Tk = tk.Tk(); root.withdraw()
    folder = filedialog.askdirectory(title="Select folder with PDF bills")
    root.destroy()
    return folder or None

def ocr_pdf(pdf: Path, dpi=300) -> List[str]:
    doc = fitz.open(pdf)
    text_chunks: List[str] = []
    for page in range(doc.page_count):
        pix = doc.load_page(page).get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        text_chunks.append(pytesseract.image_to_string(img, lang="eng"))
    doc.close()
    return text_chunks

def call_gemini(model, prompt, timeout=TIMEOUT_SEC):
    """Run generate_content with a hard timeout."""
    result, err = {}, {}
    def _run():
        try:
            result["val"] = model.generate_content(prompt)
        except Exception as e:
            err["val"] = e
    t = threading.Thread(target=_run, daemon=True)
    t.start(); t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"Gemini call exceeded {timeout}s")
    if "val" in err:
        raise err["val"]
    return result["val"]

# ═════════ main ═════════
def main():
    colorama_init()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    import argparse
    ap = argparse.ArgumentParser(description="OCR PDFs → Gemini parser with timeout")
    ap.add_argument("folder", nargs="?", help="Folder with PDFs (GUI if omitted)")
    ap.add_argument("-o","--output", default="parsed_bills.csv", help="Output CSV file")
    args = ap.parse_args()

    folder = args.folder or pick_folder_gui()
    if not folder: sys.exit("No folder selected – exiting.")

    genai.configure(api_key=API_KEY or os.getenv("GEMINI_API_KEY", ""))
    model = genai.GenerativeModel(MODEL_NAME)

    pdfs = sorted(Path(folder).rglob("*.pdf"))
    if not pdfs: sys.exit(f"No PDFs found in {folder}")

    rows: list[list[str]] = []
    print(f"{Fore.CYAN}Found {len(pdfs)} PDFs. Starting…{Style.RESET_ALL}")

    for pdf in tqdm(pdfs, desc="Processing"):
        try:
            # OCR - now returns a list of page texts
            page_texts = ocr_pdf(pdf)
            if DEBUG:
                # Save all page texts combined for debugging
                combined_text = "\n".join(page_texts).strip()[:MAX_CHARS]
                pdf.with_suffix(".ocr.txt").write_text(combined_text, encoding="utf-8")
                print(f"{Fore.BLUE}OCR {pdf.name}: {len(combined_text)} chars{Style.RESET_ALL}")

            # Process each page, keeping track of prior page data
            prior_page_data = ""
            for page_num, ocr_text in enumerate(page_texts):
                # Check if we need to reference prior page data
                # For now, we'll use a simple approach: if no rows were extracted from current page,
                # try again with prior page data prepended
                prompt = PROMPT.format(bill_text=ocr_text)
                
                if DEBUG:
                    # Save prompt for each page
                    page_prompt_path = pdf.with_suffix("").with_name(f"{pdf.stem}_page_{page_num+1}.prompt.txt")
                    page_prompt_path.write_text(prompt, encoding="utf-8")
                
                # Gemini with timeout - process each page
                start = time.time()
                reply = call_gemini(model, prompt).text.strip()
                dur   = time.time() - start
                
                # Check if we got any valid rows
                rows_extracted = []
                for line in reply.splitlines():
                    if not line.strip() or line.strip() == "EMPTY": continue
                    if line.count("|") == PIPE_COUNT:
                        rows_extracted.append([s.strip() for s in line.split("|")])
                
                # If no rows extracted and we have prior page data, try again with combined context
                if len(rows_extracted) == 0 and prior_page_data and page_num > 0:
                    combined_text = prior_page_data + "\n" + ocr_text
                    prompt = PROMPT.format(bill_text=combined_text)
                    
                    if DEBUG:
                        # Save combined prompt for debugging
                        combined_prompt_path = pdf.with_suffix("").with_name(f"{pdf.stem}_page_{page_num+1}_combined.prompt.txt")
                        combined_prompt_path.write_text(prompt, encoding="utf-8")
                    
                    # Gemini with timeout - process with combined context
                    start = time.time()
                    reply = call_gemini(model, prompt).text.strip()
                    dur   = time.time() - start
                
                if DEBUG:
                    # Save reply for each page
                    page_reply_path = pdf.with_suffix("").with_name(f"{pdf.stem}_page_{page_num+1}.gemini.txt")
                    page_reply_path.write_text(reply, encoding="utf-8")
                    print(f"{Fore.MAGENTA}Gemini {pdf.name} page {page_num+1} latency: {dur:.1f}s{Style.RESET_ALL}")

                good = 0
                for line in reply.splitlines():
                    if not line.strip() or line.strip() == "EMPTY": continue
                    if line.count("|") != PIPE_COUNT:
                        if DEBUG:
                            print(f"{Fore.YELLOW}BAD row ({line.count('|')} pipes): {line}{Style.RESET_ALL}")
                        continue
                    rows.append([s.strip() for s in line.split("|")] + [f"{pdf.name}_page_{page_num+1}"])
                    good += 1

                if good > 0:
                    msg = Fore.GREEN+"[OK]"
                    print(f"{msg} {pdf.name} page {page_num+1}: {good} rows{Style.RESET_ALL}")
                else:
                    msg = Fore.YELLOW+"[WARN]"
                    print(f"{msg} {pdf.name} page {page_num+1}: {good} rows{Style.RESET_ALL}")
                
                # Save current page data for potential use on next page
                prior_page_data = ocr_text

        except Exception as e:
            print(f"{Fore.RED}[ERROR] {pdf.name} failed: {e}{Style.RESET_ALL}")
            logging.exception("traceback")

    if not rows:
        print("Nothing parsed – nothing written."); return
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([COLUMNS,*rows])
    print(f"{Fore.CYAN}Finished! Wrote {len(rows)} rows → {args.output}{Style.RESET_ALL}")

if __name__ == "__main__":
    main()
