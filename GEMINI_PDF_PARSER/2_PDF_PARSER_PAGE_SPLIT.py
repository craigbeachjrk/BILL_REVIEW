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
    • Each row MUST contain exactly {len(COLUMNS)} fields separated by “|” ({PIPE_COUNT} pipes).
    • If a value for a field is not found in the text, you MUST use the exact word "MISSING" for that field.
    • DO NOT omit any fields. Every row must be complete.
    • If there are no line-item rows in the text, output the single word EMPTY.

    Field order:
    {' | '.join(COLUMNS)}

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

# ═════════ main processing ═════════
def process_pdf(pdf: Path, model) -> list:
    """OCR and parse a single PDF, page by page."""
    pdf_rows = []
    try:
        # 1. OCR all pages first
        page_texts = ocr_pdf(pdf)
        if DEBUG:
            combined_text = "\n".join(page_texts).strip()[:MAX_CHARS]
            pdf.with_suffix(".ocr.txt").write_text(combined_text, encoding="utf-8")

        # 2. Process each page with Gemini
        prior_page_data = ""
        page_iterator = tqdm(enumerate(page_texts), total=len(page_texts), desc=f"  Pages in {pdf.name}", leave=False)
        for page_num, ocr_text in page_iterator:
            prompt = PROMPT.format(bill_text=ocr_text)
            if DEBUG: pdf.with_suffix("").with_name(f"{pdf.stem}_page_{page_num+1}.prompt.txt").write_text(prompt, encoding="utf-8")

            # Initial Gemini call for the current page
            reply = call_gemini(model, prompt).text.strip()

            # Check if we should retry with the prior page's context
            rows_extracted = [line for line in reply.splitlines() if line.strip() and line.strip() != "EMPTY" and line.count("|") == PIPE_COUNT]
            if not rows_extracted and prior_page_data and page_num > 0:
                page_iterator.set_description(f"  Pages in {pdf.name} (retrying page {page_num+1})")
                combined_text = prior_page_data + "\n" + ocr_text
                prompt = PROMPT.format(bill_text=combined_text)
                if DEBUG: pdf.with_suffix("").with_name(f"{pdf.stem}_page_{page_num+1}_combined.prompt.txt").write_text(prompt, encoding="utf-8")
                reply = call_gemini(model, prompt).text.strip()

            if DEBUG: pdf.with_suffix("").with_name(f"{pdf.stem}_page_{page_num+1}.gemini.txt").write_text(reply, encoding="utf-8")

            # 3. Collect valid rows from the final reply
            good_rows_on_page = 0
            for line in reply.splitlines():
                if not line.strip() or line.strip() == "EMPTY": continue
                if line.count("|") != PIPE_COUNT:
                    if DEBUG: print(f"{Fore.YELLOW}    → BAD row ({line.count('|')} pipes): {line}{Style.RESET_ALL}")
                    continue
                pdf_rows.append([s.strip() for s in line.split("|")] + [f"{pdf.name}_page_{page_num+1}"])
                good_rows_on_page += 1

            # Update prior page data for the next iteration
            prior_page_data = ocr_text

    except Exception as e:
        print(f"{Fore.RED}[ERROR] {pdf.name} failed: {e}{Style.RESET_ALL}")
        logging.exception("traceback")
    
    return pdf_rows


def main():
    colorama_init()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # --- Argument Parsing ---
    import argparse
    ap = argparse.ArgumentParser(description="OCR PDFs → Gemini parser with timeout")
    ap.add_argument("folder", nargs="?", help="Folder with PDFs (GUI if omitted)")
    ap.add_argument("-o","--output", default="parsed_bills.csv", help="Output CSV file")
    args = ap.parse_args()

    # --- Get PDF folder ---
    folder = args.folder or pick_folder_gui()
    if not folder: sys.exit("No folder selected – exiting.")
    pdfs = sorted(Path(folder).rglob("*.pdf"))
    if not pdfs: sys.exit(f"No PDFs found in {folder}")

    # --- Setup Gemini ---
    genai.configure(api_key=API_KEY or os.getenv("GEMINI_API_KEY", ""))
    model = genai.GenerativeModel(MODEL_NAME)

    # --- Main Loop ---
    all_rows = []
    print(f"{Fore.CYAN}Found {len(pdfs)} PDFs. Starting…{Style.RESET_ALL}")
    pdf_iterator = tqdm(pdfs, desc="Processing PDFs")
    for pdf in pdf_iterator:
        pdf_iterator.set_description(f"Processing {pdf.name}")
        rows_from_pdf = process_pdf(pdf, model)
        if rows_from_pdf:
            all_rows.extend(rows_from_pdf)
            print(f"{Fore.GREEN}[OK] {pdf.name}: Parsed {len(rows_from_pdf)} rows{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}[WARN] {pdf.name}: No rows parsed{Style.RESET_ALL}")

    # --- Write Output ---
    if not all_rows:
        print("Nothing parsed – nothing written."); return
    
    # Add source filename to header
    output_columns = COLUMNS + ["SourceFile_Page"]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(output_columns)
        writer.writerows(all_rows)
    
    print(f"{Fore.CYAN}Finished! Wrote {len(all_rows)} rows → {args.output}{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
