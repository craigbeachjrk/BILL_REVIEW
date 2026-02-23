# ╭────────────── API KEY (testing only) ─────────────╮
API_KEY = "AIzaSyAFvRWaM5ADsL51dR2XLNoZZIFo-vKC_to"      # ← put your key here for local runs
# ╰────────────────────────────────────────────────────╯


#!/usr/bin/env python3
"""
bill_parser_files.py – Parse utility-bill PDFs with Gemini Files API
--------------------------------------------------------------------
  pip install --upgrade google-generativeai tqdm pandas
  # uses Tkinter (built-in) for folder picker if no path is supplied
"""
import os, sys, csv, time, argparse, logging
from pathlib import Path
from typing import List

import google.generativeai as genai
from google.generativeai import types
from tqdm import tqdm

import logging, http.client
http.client.HTTPConnection.debuglevel = 1           # low-level HTTP
logging.basicConfig(level=logging.INFO)
logging.getLogger("google").setLevel(logging.DEBUG) # Gemini SDK


# ─── GUI folder picker ────────────────────────────────────────────
import tkinter as tk
from tkinter import filedialog
def pick_folder_gui() -> str | None:
    root = tk.Tk()
    root.withdraw()                  # hide empty window
    path = filedialog.askdirectory(title="Select folder with PDF bills")
    root.destroy()
    return path if path else None
# ──────────────────────────────────────────────────────────────────

PROMPT = """
You are a strict utility-bill parser. I will attach one PDF file.
Return ONLY the line-item rows as pipe-delimited text (no header) in
this exact column order:

Bill To Name First Line | Bill To Name Second Line | Account Number |
Service Address | Bill Period Start | Bill Period End | Utility Type |
Consumption Amount | Line Item Description | Line Item Charge |
Bill Date | Due Date | Input PDF File Name
"""

MODEL_NAME = "gemini-2.5-flash"
POLL_SEC   = 1.5

COLUMNS = [
    "Bill To Name First Line", "Bill To Name Second Line", "Account Number",
    "Service Address", "Bill Period Start", "Bill Period End", "Utility Type",
    "Consumption Amount", "Line Item Description", "Line Item Charge",
    "Bill Date", "Due Date", "Input PDF File Name"
]

# ────────────────── helper fns ───────────────────────────────────
def upload_pdf(pdf_path: Path) -> types.File:
    f = genai.upload_file(path=pdf_path)
    while f.state != "ACTIVE":
        time.sleep(POLL_SEC)
        f = genai.get_file(f.name)
    return f

def parse_one_bill(pdf: Path, model: genai.GenerativeModel) -> List[List[str]]:
    fobj = upload_pdf(pdf)
    resp = model.generate_content(
        [PROMPT, fobj], request_options={"response_mime_type": "text/plain"}
    )
    lines = [ln for ln in resp.text.splitlines() if ln.strip()]
    return [[pdf.name] + [c.strip() for c in ln.split("|")] for ln in lines]
# ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Parse utility-bill PDFs with Gemini Files API")
    ap.add_argument("folder", nargs="?", help="Folder containing PDF bills")
    ap.add_argument("-o", "--output", default="parsed_bills.csv",
                    help="Output CSV file")
    args = ap.parse_args()

    folder = args.folder or pick_folder_gui()
    if not folder:
        print("No folder selected – exiting.")
        sys.exit(1)

    genai.configure(api_key=API_KEY or os.getenv("GEMINI_API_KEY", ""))

    model = genai.GenerativeModel(MODEL_NAME)
    rows: list[list[str]] = []

    pdfs = list(Path(folder).rglob("*.pdf"))
    if not pdfs:
        print("No PDFs found in", folder)
        sys.exit(1)

    for pdf in tqdm(pdfs, desc="Parsing bills"):
        try:
            rows.extend(parse_one_bill(pdf, model))
        except Exception:
            logging.exception("Failed on %s", pdf.name)

    if not rows:
        print("Nothing parsed – exiting.")
        return

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([["SourceFile"] + COLUMNS, *rows])

    print(f"Wrote {len(rows)} rows → {args.output}")

if __name__ == "__main__":
    main()
