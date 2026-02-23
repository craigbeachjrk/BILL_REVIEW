#!/usr/bin/env python3
"""
bill_parser.py – Parse utility-bill PDFs with the Gemini API (Files API).
This script attempts to parse a whole PDF at once. If it fails, it falls back
to splitting the PDF into individual pages and processing them one by one.
"""

import os, sys, csv, logging, textwrap, time, shutil, argparse, ctypes
from pathlib import Path
from typing import List, Optional, Tuple
import re
from datetime import datetime

import fitz  # PyMuPDF
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from google.generativeai import types
from tqdm import tqdm
from colorama import Fore, Style, init as colorama_init
import tkinter as tk
from tkinter import filedialog, Tk
import json
import boto3
from botocore.exceptions import BotoCoreError, ClientError

# ───────── USER SETTINGS ─────────
# Prefer Secrets Manager or environment variable GEMINI_API_KEY.
# API_KEY is left blank to discourage hardcoding.
API_KEY     = ""
MODEL_NAME  = "gemini-2.5-pro"
DEBUG       = False  # ← toggle diagnostics
POLL_SEC    = 2      # How often to check file upload status
# ---------------------------------
COLUMNS = [
    "Bill To Name First Line", "Bill To Name Second Line", "Vendor Name", "Invoice Number", "Account Number", "Line Item Account Number",
    "Service Address", "Service City", "Service Zipcode", "Service State", "Bill Period Start", "Bill Period End", "Utility Type",
    "Consumption Amount", "Unit of Measure", "Previous Reading", "Previous Reading Date", "Current Reading", "Current Reading Date", "Rate", "Number of Days",
    "Line Item Description", "Line Item Charge",
    "Bill Date", "Due Date", "Special Instructions", "Inferred Fields"
]
PIPE_COUNT = len(COLUMNS) - 1
# ─────────────────────────────────

# S3 configuration for parsed outputs (NDJSON)
S3_BUCKET = "jrk-analytics-billing"
S3_PARSED_PREFIX = "Bill_Parser_3_Parsed_Outputs/"

# Secrets Manager configuration (parser uses 2.5 Pro keys)
PARSER_SECRET_NAME = "gemini/parser-keys"

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
"""
).strip()

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

def _sanitize_key(raw: str) -> str:
    """Extract a valid-looking Gemini API key substring from raw input.
    Keys typically start with 'AIza' and contain letters, digits, '-' or '_'.
    """
    if not raw:
        return ""
    m = re.search(r"(AIza[0-9A-Za-z_\-]{20,})", raw)
    return m.group(1) if m else raw.strip().strip('"').strip("'")


def get_parser_api_keys_from_secrets() -> List[str]:
    """Fetch up to 3 Gemini API keys from AWS Secrets Manager for rotation.
    Accepts formats:
      - {"keys": ["k1","k2","k3"]}
      - ["k1","k2","k3"]
      - {"key1":"k1","key2":"k2","key3":"k3"}
      - Plaintext newline- or comma-separated
    Returns list of keys (possibly empty).
    """
    try:
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        profile = os.getenv("AWS_PROFILE") or "jrk-analytics-admin"
        session = boto3.Session(profile_name=profile, region_name=region)
        sm = session.client("secretsmanager")
        resp = sm.get_secret_value(SecretId=PARSER_SECRET_NAME)
        secret_str = (resp.get("SecretString") or "").strip()
        if not secret_str:
            return []
        # Try JSON first
        try:
            parsed = json.loads(secret_str)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("keys"), list) and parsed["keys"]:
                    cleaned = [_sanitize_key(str(x)) for x in parsed["keys"]]
                    return [k for k in cleaned if k][:3]
                collected = []
                for i in (1, 2, 3):
                    v = parsed.get(f"key{i}")
                    if v:
                        collected.append(_sanitize_key(str(v)))
                if collected:
                    return collected[:3]
            elif isinstance(parsed, list) and parsed:
                cleaned = [_sanitize_key(str(x)) for x in parsed]
                return [k for k in cleaned if k][:3]
        except json.JSONDecodeError:
            # Fallback: plaintext newline/comma-separated
            parts = [p.strip() for p in (secret_str.split(',') if ',' in secret_str else secret_str.splitlines())]
            cleaned = [_sanitize_key(p) for p in parts]
            return [k for k in cleaned if k][:3]
    except (BotoCoreError, ClientError, json.JSONDecodeError) as e:
        print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} Could not retrieve Gemini parser keys from Secrets Manager: {e}")
        return []

def call_gemini_with_retry(model, file_object: types.File, source_name: str, pbar) -> Tuple[List[List[str]], bool]:
    """Calls Gemini with retry logic for column count validation.

    Returns:
        (final_rows, failed_due_to_columns):
            - final_rows: list of parsed rows (each row is a list of strings). If EMPTY was returned, this may be an empty list.
            - failed_due_to_columns: True if the model repeatedly returned the wrong number of columns and we exhausted retries.
    """
    reply_text = ""
    attempts = 0
    MAX_ATTEMPTS = 5
    all_rows_valid = False
    final_rows = []

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        prompt_to_use = PROMPT
        if attempts > 1:
            # Include the previous reply to help the model correct itself, but clip to avoid huge prompts
            prev_excerpt = reply_text.strip()
            MAX_EXCERPT_CHARS = 2000
            if len(prev_excerpt) > MAX_EXCERPT_CHARS:
                prev_excerpt = prev_excerpt[:MAX_EXCERPT_CHARS] + "\n...[truncated]"

            correction_prompt = (
                "You did not give the correct number of columns. Each row must have exactly "
                f"{len(COLUMNS)} fields (pipe-delimited). Do not add or omit any columns.\n\n"
                "Here's what you gave me last time (for reference only):\n"
                f"{prev_excerpt}\n\n"
                "Now, output only the corrected rows with exactly the required number of columns."
            )
            prompt_to_use = f"{PROMPT}\n\n--- CORRECTION ATTEMPT {attempts} OF {MAX_ATTEMPTS} ---\n{correction_prompt}"

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
        return final_rows, False
    else:
        print(f"{Fore.RED}[FAIL] {source_name}: Failed to get correct column count after {MAX_ATTEMPTS} attempts.{Style.RESET_ALL}")
        return [], True

# ═════════ main processing ═════════
def process_chunk(pdf_chunk: Path, keys: List[str], pbar) -> Tuple[List[List[str]], bool]:
    """Processes a single, pre-chunked PDF file."""
    pbar.set_description(f"Processing {pdf_chunk.name}")
    source_name = pdf_chunk.name
    chunk_rows = []
    failed_due_to_columns = False
    
    # Authenticate with Gemini before upload; rotate keys if needed for upload itself
    uploaded_file = None
    for idx in range(max(1, len(keys))):
        current_key = _sanitize_key(keys[idx % len(keys)]) if keys else ""
        try:
            if current_key:
                genai.configure(api_key=current_key)
                fp = f"{current_key[:6]}...{current_key[-4:]}"
                pbar.set_description(f"Uploading {pdf_chunk.name} with key {idx+1}/{len(keys)} [{fp}]")
            uploaded_file = upload_file_to_gemini(pdf_chunk)
            if uploaded_file:
                break
        except Exception as e:
            print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} {source_name}: upload auth with key {idx+1}/{len(keys)} failed: {e}")
            time.sleep(1)

    if uploaded_file:
        try:
            # Rotate across provided keys; reconfigure model per key
            for idx in range(max(1, len(keys))):
                current_key = _sanitize_key(keys[idx % len(keys)]) if keys else ""
                try:
                    if current_key:
                        genai.configure(api_key=current_key)
                        fp = f"{current_key[:6]}...{current_key[-4:]}"
                        pbar.set_description(f"Parsing {pdf_chunk.name} with key {idx+1}/{len(keys)} [{fp}]")
                    model = genai.GenerativeModel(MODEL_NAME)
                    chunk_rows, failed_due_to_columns = call_gemini_with_retry(model, uploaded_file, source_name, pbar)
                    if chunk_rows or not failed_due_to_columns:
                        break
                except Exception as e:
                    print(f"{Fore.YELLOW}[WARN]{Style.RESET_ALL} {source_name}: key rotation {idx+1}/{len(keys)} failed: {e}")
                    time.sleep(1)
        finally:
            # Retry deleting the file to handle transient network errors
            delete_attempts = 3
            for attempt in range(delete_attempts):
                try:
                    genai.delete_file(uploaded_file.name)
                    break # Exit loop on success
                except (google_exceptions.ServiceUnavailable, google_exceptions.InternalServerError) as e:
                    if attempt < delete_attempts - 1:
                        print(f"{Fore.YELLOW}[WARN] Failed to delete {uploaded_file.name} (attempt {attempt + 1}/{delete_attempts}). Retrying...{Style.RESET_ALL}")
                        time.sleep(5) # Wait before retrying
                    else:
                        print(f"{Fore.RED}[FAIL] Failed to delete {uploaded_file.name} after {delete_attempts} attempts. Continuing... Error: {e}{Style.RESET_ALL}")

    return chunk_rows, failed_due_to_columns

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
    # No local outputs; parsed NDJSON will be written to S3

    # --- PROCESSING --- 
    pdfs_to_process = sorted([p for p in folder.glob("*.pdf")])
    if not pdfs_to_process:
        sys.exit(f"No PDFs found in '{folder}' to process.")

    print(f"{Fore.CYAN}--- PROCESSING {len(pdfs_to_process)} FILE(S) ---{Style.RESET_ALL}")
    # Secrets Manager ONLY (no .env fallback). Use SSO profile jrk-analytics-admin.
    # Ensure you've run: aws sso login --profile jrk-analytics-admin
    keys = get_parser_api_keys_from_secrets()
    if not keys:
        sys.exit(f"{Fore.RED}[ERROR]{Style.RESET_ALL} No Gemini API keys found in Secrets Manager. Make sure you're logged in (aws sso login --profile jrk-analytics-admin) and the secret '{PARSER_SECRET_NAME}' has keys.")
    # Debug: show sanitized key fingerprints
    fps = [f"{k[:6]}...{k[-4:]}" for k in keys]
    print(f"{Fore.CYAN}[DEBUG]{Style.RESET_ALL} Retrieved {len(keys)} key(s) from Secrets Manager: {', '.join(fps)}")

    total_rows_written = 0
    failed_dir = folder / "_FAILED_PARSING"
    failed_dir.mkdir(exist_ok=True)
    moved_inputs_dir = folder / "_Inputs_Moved_to_Parser"
    moved_inputs_dir.mkdir(exist_ok=True)
    # Use SSO profile + region for S3 as well (avoid 'Unable to locate credentials')
    _region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    _profile = os.getenv("AWS_PROFILE") or "jrk-analytics-admin"
    _session = boto3.Session(profile_name=_profile, region_name=_region)
    s3_client = _session.client("s3")
    pbar_chunks = tqdm(pdfs_to_process, desc="Processing Files")
    for chunk_pdf in pbar_chunks:
        rows_from_chunk, failed_due_to_columns = process_chunk(chunk_pdf, keys, pbar_chunks)

        if rows_from_chunk:
            # Build NDJSON lines
            ndjson_lines = []
            parsed_at_utc = datetime.utcnow().isoformat(timespec='seconds') + "Z"
            for row in rows_from_chunk:
                # row consists of columns + ["SourceFile_Page"] value
                data = {k: v for k, v in zip(COLUMNS, row[:len(COLUMNS)])}
                data["source_file_page"] = row[len(COLUMNS)] if len(row) > len(COLUMNS) else chunk_pdf.name
                # Convert inferred fields to array if present
                inferred = data.get("Inferred Fields", "")
                if isinstance(inferred, str) and inferred.strip():
                    # split by hyphen per prompt guidance
                    data["Inferred Fields"] = [s.strip() for s in inferred.split("-") if s.strip()]
                else:
                    data["Inferred Fields"] = []
                data["parsed_at_utc"] = parsed_at_utc
                ndjson_lines.append(json.dumps(data, ensure_ascii=False))

            # S3 key structure with date partitioning and source filename
            today = datetime.utcnow()
            key = (
                f"{S3_PARSED_PREFIX}yyyy={today.year:04d}/mm={today.month:02d}/dd={today.day:02d}/"
                f"source={folder.name}/file={chunk_pdf.stem}.jsonl"
            )
            body = "\n".join(ndjson_lines) + "\n"
            try:
                s3_client.put_object(
                    Bucket=S3_BUCKET,
                    Key=key,
                    Body=body.encode("utf-8"),
                    ContentType="application/x-ndjson",
                )
                total_rows_written += len(rows_from_chunk)
                print(f"{Fore.GREEN}[OK] {chunk_pdf.name}: Parsed {len(rows_from_chunk)} rows -> s3://{S3_BUCKET}/{key}{Style.RESET_ALL}")
                # Move the input PDF to _Inputs_Moved_to_Parser after successful upload
                try:
                    destination = moved_inputs_dir / chunk_pdf.name
                    chunk_pdf.rename(destination)
                    print(f"{Fore.CYAN}[MOVED]{Style.RESET_ALL} {chunk_pdf.name} -> {moved_inputs_dir.name}")
                except OSError as e:
                    print(f"{Fore.RED}[ERROR] Could not move processed file {chunk_pdf.name} to {moved_inputs_dir.name}: {e}{Style.RESET_ALL}")
            except Exception as e:
                print(f"{Fore.RED}[ERROR] Failed to upload NDJSON for {chunk_pdf.name} to S3: {e}{Style.RESET_ALL}")
        else:
            if failed_due_to_columns:
                # Move failed chunk for manual review
                try:
                    destination = failed_dir / chunk_pdf.name
                    chunk_pdf.rename(destination)
                    print(f"{Fore.RED}[MOVED] {chunk_pdf.name}: Moved to {failed_dir.name} after 5 failed attempts.{Style.RESET_ALL}")
                except OSError as e:
                    print(f"{Fore.RED}[ERROR] Could not move failed chunk {chunk_pdf.name} to {failed_dir.name}: {e}{Style.RESET_ALL}")
            else:
                print(f"{Fore.YELLOW}[WARN] {chunk_pdf.name}: No rows parsed{Style.RESET_ALL}")

    print(f"{Fore.CYAN}Finished! Wrote a total of {total_rows_written} rows across {len(pdfs_to_process)} files.{Style.RESET_ALL}")

    # Restore normal sleep behavior
    if sys.platform == 'win32':
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        print(f"{Fore.YELLOW}[INFO] System sleep prevention deactivated.{Style.RESET_ALL}")

if __name__ == "__main__":
    main()
