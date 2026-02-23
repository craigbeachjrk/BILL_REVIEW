#!/usr/bin/env python3
"""
bill_parser_ocr_first.py – OCR utility-bill PDFs locally, then parse with Gemini
-------------------------------------------------------------------------------
Install deps   :  pip install --upgrade google-generativeai pdf2image pytesseract pillow tqdm colorama
Poppler (PDF→image): https://github.com/oschwartz10612/poppler-windows/releases/  (add bin/ to PATH)
Tesseract OCR :  https://github.com/tesseract-ocr/tesseract (add tesseract.exe to PATH)
"""

import os, sys, csv, time, logging, argparse
from pathlib import Path
from typing import List

import pytesseract
from pdf2image import convert_from_path
from PIL import Image
import google.generativeai as genai
from tqdm import tqdm
from colorama import Fore, Style, init as colorama_init
import tkinter as tk
from tkinter import filedialog

# ╭──────────────── API KEY (testing) ───────────────╮
API_KEY = "AIzaSyAFvRWaM5ADsL51dR2XLNoZZIFo-vKC_to"      # ← paste here *or* set GEMINI_API_KEY env
# ╰───────────────────────────────────────────────────╯

# ─── add near the top (after imports) ───────────────────────────
POPPLER_PATH = r"C:\Program Files\poppler-24.02.0\Library\bin"  # ← adjust
# ----------------------------------------------------------------

# Gemini prompt – keep exactly one set of {} for bill text
PROMPT = """
You are a strict utility-bill parser. I will give you the raw text of one bill
between triple back-ticks. Extract ONLY the line-item rows, pipe-delimited,
(no header), in this column order:

Bill To Name First Line | Bill To Name Second Line | Account Number |
Service Address | Bill Period Start | Bill Period End | Utility Type |
Consumption Amount | Line Item Description | Line Item Charge |
Bill Date | Due Date | Input PDF File Name

"""

MODEL_NAME = "gemini-2.5-flash"
MAX_CHARS  = 65_000          # keep prompt <128k tokens
COLUMNS = [
    "Bill To Name First Line", "Bill To Name Second Line", "Account Number",
    "Service Address", "Bill Period Start", "Bill Period End", "Utility Type",
    "Consumption Amount", "Line Item Description", "Line Item Charge",
    "Bill Date", "Due Date", "Input PDF File Name"
]

# ─────────── helpers ─────────────────────────────────────────────
def pick_folder_gui() -> str | None:
    root = tk.Tk(); root.withdraw()
    folder = filedialog.askdirectory(title="Select folder with PDF bills")
    root.destroy()
    return folder or None


def ocr_pdf(pdf_path: Path) -> str:
    """Convert PDF pages to images and OCR. Falls back to pdfminer on failure."""
    try:
        pages = convert_from_path(
            pdf_path,
            dpi=300,
            poppler_path=POPPLER_PATH,
        )
    except Exception as e:
        raise RuntimeError(
            f"PDF→image conversion failed on {pdf_path.name}: {e}\n"
            f"Poppler path: {POPPLER_PATH}"
        )

    text_parts = []
    for idx, page in enumerate(pages, 1):
        txt = pytesseract.image_to_string(page, lang="eng")
        text_parts.append(txt or "")
    whole_text = "\n".join(text_parts).strip()
    if not whole_text:
        raise RuntimeError(f"OCR produced no text for {pdf_path.name}")
    return whole_text[:MAX_CHARS]


def parse_with_gemini(text: str, model: genai.GenerativeModel) -> List[str]:
    prompt = PROMPT.format(bill_text=text)
    resp = model.generate_content(prompt, request_options={"response_mime_type": "text/plain"})
    return [ln for ln in resp.text.splitlines() if ln.strip()]


# ─────────── main ────────────────────────────────────────────────
def main():
    colorama_init()                             # enable colour on Windows
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    ap = argparse.ArgumentParser(description="OCR PDFs locally, then parse with Gemini")
    ap.add_argument("folder", nargs="?", help="Folder with PDF bills (omit for GUI)")
    ap.add_argument("-o", "--output", default="parsed_bills.csv", help="Output CSV file")
    args = ap.parse_args()

    folder = args.folder or pick_folder_gui()
    if not folder:
        print("No folder supplied – exiting.")
        sys.exit(1)

    genai.configure(api_key=API_KEY or os.getenv("GEMINI_API_KEY", ""))
    model = genai.GenerativeModel(MODEL_NAME)

    pdfs = sorted(Path(folder).rglob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {folder}")
        sys.exit(1)

    rows_all: list[list[str]] = []
    print(f"{Fore.CYAN}Found {len(pdfs)} PDFs. Starting OCR…{Style.RESET_ALL}")

    for pdf in tqdm(pdfs, desc="Processing bills"):
        try:
            ocr_text = ocr_pdf(pdf)
            lines = parse_with_gemini(ocr_text, model)
            parsed_rows = [[pdf.name] + [c.strip() for c in ln.split("|")] for ln in lines]

            if not parsed_rows:
                print(f"{Fore.YELLOW}⚠  No rows parsed in {pdf.name}{Style.RESET_ALL}")
            else:
                rows_all.extend(parsed_rows)
                print(f"{Fore.GREEN}✓ parsed {len(parsed_rows)} rows from {pdf.name}{Style.RESET_ALL}")

        except Exception as e:
            print(f"{Fore.RED}✗ {pdf.name} failed: {e}{Style.RESET_ALL}")
            logging.exception("stack trace")

    if not rows_all:
        print("Nothing parsed – nothing written.")
        return

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([["SourceFile"] + COLUMNS, *rows_all])

    print(f"{Fore.CYAN}Finished! Wrote {len(rows_all)} rows → {args.output}{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
