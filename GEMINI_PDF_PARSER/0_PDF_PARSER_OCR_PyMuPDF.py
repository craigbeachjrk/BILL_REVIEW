#!/usr/bin/env python3
"""
bill_parser.py – OCR PDFs (PyMuPDF + Tesseract) → parse line-items with Gemini
-----------------------------------------------------------------------------
• Flip DEBUG = True/False below to turn diagnostics on or off.
"""

import os, sys, csv, logging, textwrap
from pathlib import Path
from typing import List, Optional

import fitz                                  # PyMuPDF
from PIL import Image
import pytesseract
import google.generativeai as genai
from tqdm import tqdm
from colorama import Fore, Style, init as colorama_init
import tkinter as tk
from tkinter import filedialog, Tk

# ─────────── USER SETTINGS ─────────────────────────────────────
API_KEY        = "AIzaSyAFvRWaM5ADsL51dR2XLNoZZIFo-vKC_to"   # leave blank to pick up GEMINI_API_KEY env
TESSERACT_EXE  = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
MODEL_NAME     = "gemini-2.5-flash"
MAX_CHARS      = 65_000
DEBUG          = True       # ← flip to False to silence diagnostics
# ---------------------------------------------------------------
COLUMNS = [
    "Bill To Name First Line", "Bill To Name Second Line", "Account Number",
    "Service Address", "Bill Period Start", "Bill Period End", "Utility Type",
    "Consumption Amount", "Line Item Description", "Line Item Charge",
    "Bill Date", "Due Date", "Input PDF File Name"
]
PIPE_COUNT = len(COLUMNS) - 1  # required pipes per row
# ───────────────────────────────────────────────────────────────

pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE

PROMPT_TMPL = textwrap.dedent(f"""
    You are a strict utility-bill parser.

    • Output ONLY the line-item rows. No commentary or header.
    • Each row must have exactly {len(COLUMNS)} fields separated by the “|”
      character — therefore **{PIPE_COUNT} pipes per row**.
    • If no rows exist, output the single word EMPTY.

    Field order:
    {' | '.join(COLUMNS[:-1])}

    ```
    {{bill_text}}
    ```
""").strip()

# ───────── helper functions ────────────────────────────────────
def pick_folder_gui() -> Optional[str]:
    root: Tk = tk.Tk(); root.withdraw()
    folder = filedialog.askdirectory(title="Select folder with PDF bills")
    root.destroy()
    return folder or None


def ocr_pdf(pdf: Path, dpi: int = 300) -> str:
    doc = fitz.open(pdf)
    parts: List[str] = []
    for p in range(doc.page_count):
        pix = doc.load_page(p).get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        parts.append(pytesseract.image_to_string(img, lang="eng"))
    doc.close()
    return "\n".join(parts).strip()[:MAX_CHARS]


# ─────────── main ──────────────────────────────────────────────
def main() -> None:
    colorama_init()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    import argparse
    ap = argparse.ArgumentParser(description="OCR PDFs → Gemini bill parser")
    ap.add_argument("folder", nargs="?", help="Folder with PDFs (GUI if omitted)")
    ap.add_argument("-o", "--output", default="parsed_bills.csv", help="Output CSV")
    args = ap.parse_args()

    folder = args.folder or pick_folder_gui()
    if not folder:
        sys.exit("No folder selected – exiting.")

    genai.configure(api_key=API_KEY or os.getenv("GEMINI_API_KEY", ""))
    model = genai.GenerativeModel(MODEL_NAME)

    pdfs = sorted(Path(folder).rglob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found in {folder}")

    rows: list[list[str]] = []
    print(f"{Fore.CYAN}Found {len(pdfs)} PDFs. Starting…{Style.RESET_ALL}")

    for pdf in tqdm(pdfs, desc="Processing"):
        try:
            ocr_text = ocr_pdf(pdf)
            if DEBUG:
                pdf.with_suffix(".ocr.txt").write_text(ocr_text, encoding="utf-8")
                print(f"{Fore.BLUE}OCR {pdf.name}: {len(ocr_text)} chars{Style.RESET_ALL}")

            prompt = PROMPT_TMPL.format(bill_text=ocr_text)
            if DEBUG:
                pdf.with_suffix(".prompt.txt").write_text(prompt, encoding="utf-8")

            reply = model.generate_content(prompt).text.strip()
            if DEBUG:
                pdf.with_suffix(".gemini.txt").write_text(reply, encoding="utf-8")

            for line in reply.splitlines():
                if not line.strip() or line.strip() == "EMPTY":
                    continue
                if line.count("|") != PIPE_COUNT:
                    if DEBUG:
                        print(f"{Fore.YELLOW}BAD row ({line.count('|')} pipes): {line}{Style.RESET_ALL}")
                    continue
                fields = [f.strip() for f in line.split("|")]
                rows.append(fields + [pdf.name])

            good_rows = len([r for r in rows if r[-1] == pdf.name])
            status = (Fore.GREEN + "✓" if good_rows else Fore.YELLOW + "⚠")
            print(f"{status} {pdf.name}: {good_rows} rows{Style.RESET_ALL}")

        except Exception as e:
            print(f"{Fore.RED}✗ {pdf.name} failed: {e}{Style.RESET_ALL}")
            logging.exception("traceback")

    if not rows:
        print("Nothing parsed – nothing written.")
        return

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([COLUMNS, *rows])

    print(f"{Fore.CYAN}Finished! Wrote {len(rows)} rows → {args.output}{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
