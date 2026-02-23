#!/usr/bin/env python3
"""
bill_parser.py – OCR utility-bill PDFs (PyMuPDF + Tesseract) then parse rows with Gemini.
Flip DEBUG = True / False at the top; each Gemini call is hard-timeout-capped.
"""

import os, sys, csv, logging, textwrap, threading, time
from pathlib import Path
from typing import List, Optional
from datetime import datetime

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
DEBUG         = False          # ← toggle diagnostics
TIMEOUT_SEC   = 600            # per-request Gemini timeout
# ---------------------------------
COLUMNS = [
    "Bill To Name First Line","Bill To Name Second Line","Account Number","Line Item Account Number",
    "Service Address","Bill Period Start","Bill Period End","Utility Type",
    "Consumption Amount","Unit of Measure","Line Item Description","Line Item Charge",
    "Bill Date","Due Date", "Inferred Fields"
]
PIPE_COUNT = len(COLUMNS) - 1
# ─────────────────────────────────

pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE

PROMPT = textwrap.dedent(f"""
    You are an expert utility-bill parser. Your task is to extract line-item data from OCR text.

    **RULES:**
    1.  **Output Format:**
        - Output ONLY pipe-separated (|) rows. NO headers, commentary, or markdown.
        - Each row MUST have exactly {len(COLUMNS)} fields ({PIPE_COUNT} pipes).
        - If no line items are found, output the single word: EMPTY.

    2.  **Field Requirements:**
        - **`Consumption Amount` & `Line Item Charge`**: Must be numbers only (float). NO currency symbols, text, or commas.
        - **`Utility Type`**: Standardize to one of: `Electricity`, `Gas`, `Trash`, `Water`, `Sewer`, `Stormwater`, `HOA`.
        - **`Unit of Measure`**: Extract the unit for consumption (e.g., `kWh`, `CCF`, `Gallons`).
        - **`Line Item Account Number`**: If a line item has a specific account number different from the main one, put it here.

    3.  **Inference Logic:**
        - The following fields are CRITICAL and CANNOT be blank: `Bill To Name First Line`, `Account Number`, `Service Address`, `Bill Period Start`, `Bill Period End`, `Utility Type`, `Bill Date`, `Due Date`.
        - If any of these CRITICAL fields are missing on the current page, you MUST infer them from the context of the document provided. They should NEVER be `MISSING`.
        - The **`Inferred Fields`** column: If you infer any of the CRITICAL fields, list their exact column names here, separated by a hyphen (e.g., `Bill Date-Due Date`). If no fields were inferred for a row, leave this column blank.
        - Non-critical fields (`Consumption Amount`, `Unit of Measure`, `Line Item Account Number`, `Line Item Charge`, `Bill To Name Second Line`) should be left BLANK if the information is not present. Do not use `MISSING` for these.

    **FIELD ORDER ({len(COLUMNS)} total):**
    {' | '.join(COLUMNS)}

    **OCR TEXT TO PARSE:**
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
        prior_page_text = ""
        page_iterator = tqdm(enumerate(page_texts), total=len(page_texts), desc=f"  Pages in {pdf.name}", leave=False)
        for page_num, ocr_text in page_iterator:

            # --- Call Gemini with Retry Logic for Column Count ---
            reply_text = ""
            attempts = 0
            MAX_ATTEMPTS = 3
            all_rows_valid = False

            while attempts < MAX_ATTEMPTS:
                attempts += 1
                # Use prior page text for context if available
                contextual_ocr_text = f"{prior_page_text}\n\n---\n\n{ocr_text}" if prior_page_text and page_num > 0 else ocr_text
                prompt_to_use = PROMPT.format(bill_text=contextual_ocr_text)
                
                if attempts > 1:
                    correction_prompt = (
                        "Your previous response had an incorrect number of columns for one or more rows. "
                        f"Please strictly adhere to providing exactly {len(COLUMNS)} pipe-delimited columns for every single row. Do not add or omit any columns."
                    )
                    prompt_to_use = f"{prompt_to_use}\n\n---CORRECTION---\n{correction_prompt}"

                if DEBUG:
                    pdf.with_suffix("").with_name(f"{pdf.stem}_page_{page_num+1}_attempt_{attempts}.prompt.txt").write_text(prompt_to_use, encoding="utf-8")

                try:
                    reply = call_gemini(model, prompt_to_use)
                    reply_text = reply.text.strip()
                    if DEBUG:
                        pdf.with_suffix("").with_name(f"{pdf.stem}_page_{page_num+1}_attempt_{attempts}.gemini.txt").write_text(reply_text, encoding="utf-8")
                except Exception as e:
                    page_iterator.set_description(f"{Fore.RED}Page {page_num+1}/{len(page_texts)} Gemini Error{Style.RESET_ALL}")
                    print(f"{Fore.RED}[ERROR] Gemini call failed for {pdf.name} page {page_num + 1}: {e}{Style.RESET_ALL}")
                    time.sleep(2)
                    continue # This continue applies to the while loop, will retry

                # --- Validate the reply ---
                lines = [line.strip() for line in reply_text.split('\n') if line.strip() and line.strip() != "EMPTY"]
                if not lines:
                    all_rows_valid = True # Empty reply is valid
                    break

                all_rows_valid = True # Assume valid until a bad row is found
                for i, line in enumerate(lines):
                    num_columns = len(line.split('|'))
                    if num_columns != len(COLUMNS):
                        all_rows_valid = False
                        page_iterator.set_description(f"Page {page_num+1} Col Error")
                        print(f"{Fore.YELLOW}[RETRY] Page {page_num + 1}, Row {i+1}: Incorrect column count ({num_columns} vs {len(COLUMNS)}). Retrying ({attempts}/{MAX_ATTEMPTS})...{Style.RESET_ALL}")
                        time.sleep(2) # Small delay before retry
                        break # Breaks inner for-loop to trigger a retry
                
                if all_rows_valid:
                    break # Breaks while-loop, the data is good

            # --- Process Final, Validated Data ---
            if not all_rows_valid:
                print(f"{Fore.RED}[FAIL] Page {page_num + 1}: Failed to get correct column count after {MAX_ATTEMPTS} attempts. Skipping page.{Style.RESET_ALL}")
                prior_page_text = ocr_text # Still update context even on failure
                continue # Skip to next page in the PDF

            lines = [line.strip() for line in reply_text.split('\n') if line.strip() and line.strip() != "EMPTY"]
            if lines:
                for line in lines:
                    parts = [p.strip() for p in line.split('|')]
                    # Ensure row has correct number of columns before appending source, just in case
                    if len(parts) == len(COLUMNS):
                        parts.append(f"{pdf.name}_page{page_num+1}")
                        pdf_rows.append(parts)
                    else:
                         print(f"{Fore.RED}[ERROR] Internal validation failed for {pdf.name} page {page_num + 1} after retries. Row skipped.{Style.RESET_ALL}")

                page_iterator.set_description(f"Page {page_num+1}/{len(page_texts)} ({len(lines)} rows)")
            else:
                page_iterator.set_description(f"Page {page_num+1}/{len(page_texts)} (0 rows)")

            # Update prior page data for the next iteration
            prior_page_text = ocr_text
            time.sleep(1) # Rate limit

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
    output_dir = Path(folder) / "_PARSED_OUTPUTS"
    input_dir_processed = Path(folder) / "_PARSED_INPUTS"
    output_dir.mkdir(exist_ok=True)
    input_dir_processed.mkdir(exist_ok=True)
    print(f"{Fore.CYAN}Found {len(pdfs)} PDFs. Outputs will be in '{output_dir.name}', processed PDFs will be moved to '{input_dir_processed.name}'.{Style.RESET_ALL}")

    pdf_iterator = tqdm(pdfs, desc="Processing PDFs")
    total_rows_written = 0
    for pdf in pdf_iterator:
        pdf_iterator.set_description(f"Processing {pdf.name}")
        rows_from_pdf = process_pdf(pdf, model)

        if rows_from_pdf:
            # --- Determine Filename from Parsed Data ---
            try:
                bill_date_idx = COLUMNS.index('Bill Date')
                name_idx = COLUMNS.index('Bill To Name First Line')

                # Find the most common property name
                names = [row[name_idx] for row in rows_from_pdf if row[name_idx] and row[name_idx] != 'MISSING']
                property_name = max(set(names), key=names.count).replace(" ", "_") if names else "UNKNOWN_PROPERTY"

                # Find min and max bill dates
                bill_dates = []
                for row in rows_from_pdf:
                    try:
                        # Attempt to parse date, supporting multiple common formats
                        bill_dates.append(datetime.strptime(row[bill_date_idx], '%m/%d/%Y'))
                    except (ValueError, IndexError):
                        try:
                            bill_dates.append(datetime.strptime(row[bill_date_idx], '%Y-%m-%d'))
                        except (ValueError, IndexError):
                            continue # Skip if date is malformed or missing
                
                if bill_dates:
                    min_date = min(bill_dates).strftime('%Y-%m-%d')
                    max_date = max(bill_dates).strftime('%Y-%m-%d')
                    output_filename = f"{property_name}_{min_date}_{max_date}.tsv"
                else:
                    output_filename = pdf.stem + "_NODATES.tsv"

            except Exception as e:
                print(f"{Fore.YELLOW}[WARN] Could not generate custom filename for {pdf.name}: {e}. Using default.{Style.RESET_ALL}")
                output_filename = pdf.stem + ".tsv"
            
            output_path = output_dir / output_filename
            # --- Write Output File ---
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter='\t')
                writer.writerow(COLUMNS + ["SourceFile_Page"])
                writer.writerows(rows_from_pdf)
            
            total_rows_written += len(rows_from_pdf)
            print(f"{Fore.GREEN}[OK] {pdf.name}: Parsed and wrote {len(rows_from_pdf)} rows to {output_filename}{Style.RESET_ALL}")

            # Move the processed PDF
            try:
                destination = input_dir_processed / pdf.name
                pdf.rename(destination)
            except Exception as e:
                print(f"{Fore.RED}[ERROR] Could not move {pdf.name} to {input_dir_processed}: {e}{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}[WARN] {pdf.name}: No rows parsed{Style.RESET_ALL}")

    print(f"{Fore.CYAN}Finished! Wrote a total of {total_rows_written} rows across {len(pdfs)} files.{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
