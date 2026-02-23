#!/usr/bin/env python3
"""
bill_parser.py – Parse utility-bill PDFs with the Gemini API (Files API).
This script attempts to parse a whole PDF at once. If it fails, it falls back
to splitting the PDF into individual pages and processing them one by one.
"""

import os, sys, csv, logging, textwrap, time, shutil, argparse, ctypes
from pathlib import Path
from typing import List, Optional
from datetime import datetime

import fitz  # PyMuPDF
import google.generativeai as genai
from google.generativeai import types
from tqdm import tqdm
from colorama import Fore, Style, init as colorama_init
import tkinter as tk
from tkinter import filedialog, Tk

# ───────── USER SETTINGS ─────────
API_KEY     = "AIzaSyAFvRWaM5ADsL51dR2XLNoZZIFo-vKC_to"  # or rely on GEMINI_API_KEY env
MODEL_NAME  = "gemini-2.5-pro"
DEBUG       = False  # ← toggle diagnostics
POLL_SEC    = 2      # How often to check file upload status
# ---------------------------------
COLUMNS = [
    "Bill To Name First Line", "Bill To Name Second Line", "Vendor Name", "Invoice Number", "Account Number", "Line Item Account Number",
    "Service Address", "Bill Period Start", "Bill Period End", "Utility Type",
    "Consumption Amount", "Unit of Measure", "Previous Reading", "Previous Reading Date", "Current Reading", "Current Reading Date", "Rate", "Number of Days",
    "Line Item Description", "Line Item Charge",
    "Bill Date", "Due Date", "Special Instructions", "Inferred Fields"
]
PIPE_COUNT = len(COLUMNS) - 1
# ─────────────────────────────────

PROMPT = textwrap.dedent(f"""
    You are an expert utility-bill parser. Your task is to extract line-item data from the provided PDF file.

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
        - The following fields are CRITICAL and CANNOT be blank: `Bill To Name First Line`, `Vendor Name`, `Invoice Number`, `Account Number`, `Service Address`, `Bill Period Start`, `Bill Period End`, `Utility Type`, `Bill Date`, `Due Date`.
        - If any of these CRITICAL fields are missing, you MUST infer them from the context of the document provided. They should NEVER be `MISSING`.
        - The **`Inferred Fields`** column: If you infer any of the CRITICAL fields, list their exact column names here, separated by a hyphen (e.g., `Bill Date-Due Date`). If no fields were inferred for a row, leave this column blank.
        - Non-critical fields should be left BLANK if the information is not present. Do not use `MISSING` for these.

    **CONTEXT NOTE:** The provided document may be a multi-page chunk. Use information from earlier pages in the chunk (like bill headers) to fill in data for line items on later pages.

    **FIELD ORDER ({len(COLUMNS)} total):**
    {' | '.join(COLUMNS)}
""").strip()

# ═════════ helper functions ═════════
def pick_folder_gui() -> Optional[str]:
    root: Tk = tk.Tk(); root.withdraw()
    folder = filedialog.askdirectory(title="Select folder with PDF bills")
    root.destroy()
    return folder or None

def upload_file_to_gemini(file_path: Path, mime_type: str = 'application/pdf') -> Optional[types.File]:
    """Uploads a file to the Gemini API and waits for it to become active."""
    try:
        uploaded_file = genai.upload_file(path=file_path, mime_type=mime_type)
        pbar = tqdm(desc=f"  Uploading {file_path.name}", leave=False, bar_format='{desc}')
        while uploaded_file.state.name == 'PROCESSING':
            pbar.update()
            time.sleep(POLL_SEC)
            uploaded_file = genai.get_file(uploaded_file.name)
        pbar.close()
        if uploaded_file.state.name == 'ACTIVE':
            return uploaded_file
        else:
            print(f"{Fore.RED}[ERROR] Failed to upload {file_path.name}. State: {uploaded_file.state.name}{Style.RESET_ALL}")
            return None
    except Exception as e:
        print(f"{Fore.RED}[ERROR] Exception during upload of {file_path.name}: {e}{Style.RESET_ALL}")
        return None

def call_gemini_with_retry(model, file_object: types.File, source_name: str, pbar) -> list:
    """Calls Gemini with retry logic for column count validation."""
    reply_text = ""
    attempts = 0
    MAX_ATTEMPTS = 3
    all_rows_valid = False
    final_rows = []

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        prompt_to_use = PROMPT
        if attempts > 1:
            correction_prompt = (
                "Your previous response had an incorrect number of columns for one or more rows. "
                f"Please strictly adhere to providing exactly {len(COLUMNS)} pipe-delimited columns for every single row. Do not add or omit any columns."
            )
            prompt_to_use = f"{PROMPT}\n\n---CORRECTION---\n{correction_prompt}"

        try:
            reply = model.generate_content([prompt_to_use, file_object], generation_config={"response_mime_type": "text/plain"})
            reply_text = reply.text.strip()
        except Exception as e:
            pbar.set_description(f"{Fore.RED}Gemini Error on {source_name}{Style.RESET_ALL}")
            print(f"{Fore.RED}[ERROR] Gemini call failed for {source_name}: {e}{Style.RESET_ALL}")
            time.sleep(2)
            continue

        lines = [line.strip() for line in reply_text.split('\n') if line.strip() and line.strip() != "EMPTY"]
        if not lines:
            all_rows_valid = True
            break

        all_rows_valid = True
        for i, line in enumerate(lines):
            num_columns = len(line.split('|'))
            if num_columns != len(COLUMNS):
                all_rows_valid = False
                pbar.set_description(f"{Fore.YELLOW}Col Error on {source_name}{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}[RETRY] {source_name}, Row {i+1}: Incorrect column count ({num_columns} vs {len(COLUMNS)}). Retrying ({attempts}/{MAX_ATTEMPTS})...{Style.RESET_ALL}")
                time.sleep(2)
                break
        
        if all_rows_valid:
            break

    if all_rows_valid:
        for line in lines:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) == len(COLUMNS):
                parts.append(source_name)
                final_rows.append(parts)
    else:
        print(f"{Fore.RED}[FAIL] {source_name}: Failed to get correct column count after {MAX_ATTEMPTS} attempts. Skipping.{Style.RESET_ALL}")

    return final_rows

# ═════════ main processing ═════════
def process_chunk(pdf_chunk: Path, model, pbar) -> list:
    """Processes a single, pre-chunked PDF file."""
    pbar.set_description(f"Processing {pdf_chunk.name}")
    source_name = pdf_chunk.name
    chunk_rows = []
    
    uploaded_file = upload_file_to_gemini(pdf_chunk)
    if uploaded_file:
        try:
            chunk_rows = call_gemini_with_retry(model, uploaded_file, source_name, pbar)
        finally:
            genai.delete_file(uploaded_file.name) # Ensure cleanup

    return chunk_rows

def main():
    colorama_init()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Prevent computer from sleeping during script execution (Windows only)
    if sys.platform == 'win32':
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        print(f"{Fore.YELLOW}[INFO] System sleep prevention activated.{Style.RESET_ALL}")

    parser = argparse.ArgumentParser(description="Pre-processes and parses utility-bill PDFs with the Gemini Files API.")
    parser.add_argument("folder", nargs="?", help="Folder with PDFs (GUI if omitted)")
    args = parser.parse_args()

    folder = Path(args.folder or pick_folder_gui())
    if not folder: sys.exit("No folder selected – exiting.")

    # --- Define Directories ---
    output_dir = folder / "_PARSED_OUTPUTS"
    input_dir_processed = folder / "_PARSED_INPUTS"
    preprocessed_dir = folder / "_PREPROCESSED"
    output_dir.mkdir(exist_ok=True)
    input_dir_processed.mkdir(exist_ok=True)
    preprocessed_dir.mkdir(exist_ok=True)

    # --- STAGE 1: PREPROCESSING --- 
    # Get original PDFs, ignoring any already processed
    original_pdfs = sorted([p for p in folder.glob("*.pdf")])
    if not original_pdfs:
        print(f"{Fore.YELLOW}No new PDFs found in root folder to preprocess.{Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN}--- STAGE 1: PREPROCESSING {len(original_pdfs)} PDF(s) ---{Style.RESET_ALL}")
        CHUNK_SIZE = 5
        for pdf in tqdm(original_pdfs, desc="Preprocessing PDFs"):
            try:
                doc = fitz.open(pdf)
                if doc.page_count > 0:
                    for start_page in range(0, doc.page_count, CHUNK_SIZE):
                        end_page = min(start_page + CHUNK_SIZE - 1, doc.page_count - 1)
                        chunk_pdf_path = preprocessed_dir / f"{pdf.stem}_p{start_page+1}-{end_page+1}.pdf"
                        with fitz.open() as new_doc:
                            new_doc.insert_pdf(doc, from_page=start_page, to_page=end_page)
                            new_doc.save(chunk_pdf_path)
                doc.close()
                # Move original PDF after successful chunking
                pdf.rename(input_dir_processed / pdf.name)
            except Exception as e:
                print(f"{Fore.RED}[ERROR] Failed to preprocess {pdf.name}: {e}{Style.RESET_ALL}")

    # --- STAGE 2: PROCESSING --- 
    chunked_pdfs = sorted([p for p in preprocessed_dir.glob("*.pdf")])
    if not chunked_pdfs:
        sys.exit(f"No preprocessed PDFs found in '{preprocessed_dir.name}' to process.")

    print(f"{Fore.CYAN}--- STAGE 2: PROCESSING {len(chunked_pdfs)} CHUNK(S) ---{Style.RESET_ALL}")
    genai.configure(api_key=API_KEY or os.getenv("GEMINI_API_KEY", ""))
    model = genai.GenerativeModel(MODEL_NAME)

    total_rows_written = 0
    for chunk_pdf in tqdm(chunked_pdfs, desc="Processing Chunks"):
        rows_from_chunk = process_chunk(chunk_pdf, model, tqdm)

        if rows_from_chunk:
            output_filename = chunk_pdf.stem + ".tsv"
            output_path = output_dir / output_filename
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter='\t')
                writer.writerow(COLUMNS + ["SourceFile_Page"]) 
                writer.writerows(rows_from_chunk)
            
            total_rows_written += len(rows_from_chunk)
            print(f"{Fore.GREEN}[OK] {chunk_pdf.name}: Parsed and wrote {len(rows_from_chunk)} rows to {output_filename}{Style.RESET_ALL}")
            # Delete the chunk after it's been processed and outputted
            try:
                chunk_pdf.unlink()
            except OSError as e:
                print(f"{Fore.RED}[ERROR] Could not delete chunk {chunk_pdf.name}: {e}{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}[WARN] {chunk_pdf.name}: No rows parsed{Style.RESET_ALL}")

    print(f"{Fore.CYAN}Finished! Wrote a total of {total_rows_written} rows across {len(chunked_pdfs)} chunks.{Style.RESET_ALL}")

    # Restore normal sleep behavior
    if sys.platform == 'win32':
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        print(f"{Fore.YELLOW}[INFO] System sleep prevention deactivated.{Style.RESET_ALL}")

if __name__ == "__main__":
    main()
