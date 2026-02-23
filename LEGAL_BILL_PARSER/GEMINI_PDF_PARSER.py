#!/usr/bin/env python3
"""
bill_parser.py – Parse legal-bill PDFs with the Gemini API (Files API).
This script processes each PDF as-is (no chunking) with retry-until-success
column validation and writes results to an Excel tracker.
After processing, PDFs are moved into a "_PARSED" folder.
"""

import os, sys, csv, logging, textwrap, time, shutil, argparse, ctypes
from pathlib import Path
from typing import List, Optional
from datetime import datetime

import pandas as pd
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
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
# Required output columns for legal bill parser
COLUMNS = [
    "Firm Name",
    "JRK Entity (Property)",
    "Invoice Number",
    "Claim Number",
    "Period Start Date",
    "Period End Date",
    "Matter",
    "Amount of Invoice",
    "Hours Billed For",
    "Terms",
]
PIPE_COUNT = len(COLUMNS) - 1
# Final output columns (appends file name to the parsed fields)
OUTPUT_COLUMNS = COLUMNS + ["PDF File Name"]
# ─────────────────────────────────

PROMPT = textwrap.dedent(f"""
    You are an expert legal bill parser. Extract the following fields from the provided PDF bill.

    RULES:
    1. Output ONLY pipe-separated (|) rows. NO headers, commentary, bullets, or markdown.
    2. Each row MUST have exactly {len(COLUMNS)} fields ({PIPE_COUNT} pipes).
    3. If nothing can be extracted at all, output the single word: EMPTY.

    Field requirements:
    - Amount of Invoice: numeric only (no currency symbols, commas, or text). Example: 1234.56
    - Hours Billed For: numeric hours (decimal allowed). Example: 12.5
    - Dates: use ISO format YYYY-MM-DD when possible.
    - If a value is not present, leave it blank (do NOT write MISSING).

    Context notes:
    - Bills may span multiple pages. Use earlier pages for context when populating later fields.

    FIELD ORDER ({len(COLUMNS)} total):
    {' | '.join(COLUMNS)}
""").strip()

# ═════════ helper functions ═════════
def pick_folder_gui() -> Optional[str]:
    root: Tk = tk.Tk(); root.withdraw()
    folder = filedialog.askdirectory(title="Select folder with PDF bills")
    root.destroy()
    return folder or None

def _first_last_day_previous_month(reference_dt: datetime) -> tuple[str, str]:
    """Given a datetime, return (first_day_iso, last_day_iso) of the previous month."""
    year = reference_dt.year
    month = reference_dt.month
    # Move to previous month
    if month == 1:
        p_year, p_month = year - 1, 12
    else:
        p_year, p_month = year, month - 1
    # First day
    first_day = datetime(p_year, p_month, 1)
    # Compute last day by going to first of current month and subtracting a day
    if month == 1:
        curr_first = datetime(year, month, 1)
    else:
        curr_first = datetime(year, month, 1)
    last_day = curr_first - pd.Timedelta(days=1)
    return first_day.strftime("%Y-%m-%d"), last_day.strftime("%Y-%m-%d")

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
    """Calls Gemini with retry logic for column count validation. Retries until success, updating the prompt with the prior output."""
    attempts = 0
    final_rows: list[list[str]] = []
    last_reply_text: str = ""

    while True:
        attempts += 1
        prompt_to_use = PROMPT
        if attempts > 1:
            correction_prompt = (
                "Your previous response did not contain the exact required number of columns for one or more rows. "
                f"Provide rows with exactly {len(COLUMNS)} pipe-delimited columns in the order listed. "
                "Here is what you returned last time; fix it by correcting column counts and formats only, and re-output rows correctly.\n\n"
            )
            prompt_to_use = f"{PROMPT}\n\n--- PRIOR RESPONSE THAT NEEDS CORRECTION ---\n{last_reply_text}\n\n--- INSTRUCTIONS ---\n{correction_prompt}"

        try:
            reply = model.generate_content([prompt_to_use, file_object], generation_config={"response_mime_type": "text/plain"})
            last_reply_text = (reply.text or "").strip()
        except Exception as e:
            pbar.set_description(f"{Fore.RED}Gemini Error on {source_name}{Style.RESET_ALL}")
            print(f"{Fore.RED}[ERROR] Gemini call failed for {source_name}: {e}{Style.RESET_ALL}")
            time.sleep(3)
            continue

        lines = [line.strip() for line in last_reply_text.split('\n') if line.strip() and line.strip().upper() != "EMPTY"]

        # If model says EMPTY, treat as success (no rows for this chunk)
        if not lines:
            return []

        all_rows_valid = True
        parsed_rows: list[list[str]] = []
        for i, line in enumerate(lines):
            parts = [p.strip() for p in line.split('|')]
            if len(parts) != len(COLUMNS):
                all_rows_valid = False
                pbar.set_description(f"{Fore.YELLOW}Col Error on {source_name}{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}[RETRY] {source_name}, Row {i+1}: Incorrect column count ({len(parts)} vs {len(COLUMNS)}). Attempt {attempts}...{Style.RESET_ALL}")
                time.sleep(2)
                break
            parsed_rows.append(parts)

        if all_rows_valid:
            final_rows = parsed_rows
            break

        # Safety: avoid truly infinite loop in pathological cases
        if attempts % 15 == 0:
            print(f"{Fore.YELLOW}[INFO] {source_name}: Still retrying... {attempts} attempts so far.{Style.RESET_ALL}")

    return final_rows

# ═════════ main processing (no chunking) ═════════
def process_pdf(pdf_path: Path, model, pbar) -> list:
    """Processes a single PDF file as-is (no splitting)."""
    pbar.set_description(f"Processing {pdf_path.name}")
    source_name = pdf_path.name
    rows = []

    uploaded_file = upload_file_to_gemini(pdf_path)
    if uploaded_file:
        try:
            rows = call_gemini_with_retry(model, uploaded_file, source_name, pbar)
        finally:
            # Retry deleting the uploaded file reference in Gemini
            delete_attempts = 3
            for attempt in range(delete_attempts):
                try:
                    genai.delete_file(uploaded_file.name)
                    break  # Exit loop on success
                except (google_exceptions.ServiceUnavailable, google_exceptions.InternalServerError) as e:
                    if attempt < delete_attempts - 1:
                        print(f"{Fore.YELLOW}[WARN] Failed to delete {uploaded_file.name} (attempt {attempt + 1}/{delete_attempts}). Retrying...{Style.RESET_ALL}")
                        time.sleep(5)  # Wait before retrying
                    else:
                        print(f"{Fore.RED}[FAIL] Failed to delete {uploaded_file.name} after {delete_attempts} attempts. Continuing... Error: {e}{Style.RESET_ALL}")

    return rows

def pick_excel_tracker_or_create(default_dir: Path) -> Path:
    """Lets the user choose an existing Excel tracker; if none selected, creates a new timestamped file path."""
    root: Tk = tk.Tk(); root.withdraw()
    selected = filedialog.askopenfilename(
        title="Select existing LEGAL_BILL_TRACKER.xlsx (Cancel to create a new one)",
        filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        initialdir=str(default_dir)
    )
    root.destroy()
    if selected:
        return Path(selected)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return default_dir / f"LEGAL_PDF_PARSE_{ts}.xlsx"

def _safe_move_to_parsed(src: Path, parsed_dir: Path) -> None:
    """Move the processed PDF into parsed_dir; avoid overwriting by appending a timestamp if needed."""
    try:
        parsed_dir.mkdir(exist_ok=True)
    except Exception as e:
        print(f"{Fore.YELLOW}[WARN] Could not ensure _PARSED directory exists: {e}{Style.RESET_ALL}")
    target = parsed_dir / src.name
    if target.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = parsed_dir / f"{src.stem}_{ts}{src.suffix}"
    try:
        shutil.move(str(src), str(target))
    except Exception as e:
        print(f"{Fore.YELLOW}[WARN] Failed to move {src.name} to {parsed_dir.name}: {e}{Style.RESET_ALL}")

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

    # Get original PDFs in the selected folder
    pdfs = sorted([p for p in folder.glob("*.pdf")])
    if not pdfs:
        sys.exit("No PDFs found in the selected folder to process.")

    print(f"{Fore.CYAN}--- PROCESSING {len(pdfs)} PDF(S) ---{Style.RESET_ALL}")
    genai.configure(api_key=API_KEY or os.getenv("GEMINI_API_KEY", ""))
    model = genai.GenerativeModel(MODEL_NAME)

    # Parse all PDFs and aggregate rows
    all_rows: list[list[str]] = []
    total_rows_written = 0
    parsed_dir = folder / "_PARSED"
    pbar_pdfs = tqdm(pdfs, desc="Processing PDFs")
    for pdf_path in pbar_pdfs:
        rows_from_pdf = process_pdf(pdf_path, model, pbar_pdfs)
        if rows_from_pdf:
            # Infer missing period dates if needed based on file modified time
            try:
                file_mtime = datetime.fromtimestamp(pdf_path.stat().st_mtime)
            except Exception:
                file_mtime = datetime.now()
            default_start, default_end = _first_last_day_previous_month(file_mtime)

            start_idx = COLUMNS.index("Period Start Date")
            end_idx = COLUMNS.index("Period End Date")

            for row in rows_from_pdf:
                # Ensure row has correct length (it should due to validation)
                if len(row) == len(COLUMNS):
                    # Fill missing start/end dates
                    if not row[start_idx].strip():
                        row[start_idx] = default_start
                    if not row[end_idx].strip():
                        row[end_idx] = default_end
                    # Append PDF File Name
                    row_with_file = row + [pdf_path.name]
                    all_rows.append(row_with_file)
            total_rows_written += len(rows_from_pdf)
            print(f"{Fore.GREEN}[OK] {pdf_path.name}: Parsed {len(rows_from_pdf)} row(s){Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}[WARN] {pdf_path.name}: No rows parsed{Style.RESET_ALL}")
        # Move processed PDF into _PARSED folder
        _safe_move_to_parsed(pdf_path, parsed_dir)

    # Write final output to Excel tracker (no TSVs)
    tracker_path = pick_excel_tracker_or_create(folder)
    df_new = pd.DataFrame(all_rows, columns=OUTPUT_COLUMNS)
    if tracker_path.exists():
        try:
            df_existing = pd.read_excel(tracker_path)
            # Ensure correct columns; if mismatch, create a new file next to it
            if list(df_existing.columns) != OUTPUT_COLUMNS:
                print(f"{Fore.YELLOW}[WARN] Existing tracker columns differ. Creating a new tracker to avoid corruption.{Style.RESET_ALL}")
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                tracker_path = tracker_path.with_name(f"LEGAL_PDF_PARSE_{ts}.xlsx")
                df_existing = pd.DataFrame(columns=OUTPUT_COLUMNS)
        except Exception as e:
            print(f"{Fore.YELLOW}[WARN] Could not read existing tracker ({e}). Creating a new one.{Style.RESET_ALL}")
            df_existing = pd.DataFrame(columns=OUTPUT_COLUMNS)
    else:
        df_existing = pd.DataFrame(columns=OUTPUT_COLUMNS)

    df_final = pd.concat([df_existing, df_new], ignore_index=True)
    try:
        df_final.to_excel(tracker_path, index=False)
        print(f"{Fore.CYAN}Finished! Wrote a total of {total_rows_written} rows to Excel: {tracker_path.name}{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}[ERROR] Failed to write Excel output: {e}{Style.RESET_ALL}")

    # Restore normal sleep behavior
    if sys.platform == 'win32':
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        print(f"{Fore.YELLOW}[INFO] System sleep prevention deactivated.{Style.RESET_ALL}")

if __name__ == "__main__":
    main()
