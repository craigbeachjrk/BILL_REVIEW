#!/usr/bin/env python3
"""
JRK Bill Uploader (Company-Wide Executable)
-------------------------------------------
Uploads all PDFs from a user-selected folder to the fixed S3 location:
  s3://jrk-analytics-billing/Bill_Parser_1_Pending_Parsing/
Then moves local files into a sibling folder named "_Moved_to_Parser".

Capabilities (strict):
- Uploads ONLY .pdf files via PutObject to the above S3 bucket/prefix
- Does NOT delete, overwrite, or list existing S3 objects
- Moves local files only within the selected folder into "_Moved_to_Parser"
- No other network calls besides S3 PutObject

Requirements:
- AWS CLI/credentials via SSO profile (default: jrk-analytics-admin) OR env/IAM
- Python packages: boto3, tqdm, colorama

Usage:
- Double-click the EXE (or run this script): a folder picker will open.
- Select the folder containing PDFs.
- The script uploads each PDF and moves it locally on success.

Command-line options (optional):
- --folder <path>    Process this folder without the GUI.
- --profile <name>   AWS profile to use (default: jrk-analytics-admin)
- --region <name>    AWS region (default: us-east-1)
- --dry-run          Log actions but do not upload or move files
"""

import os
import sys
import argparse
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, Tk
from typing import Optional
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from tqdm import tqdm
from colorama import Fore, Style, init as colorama_init
import requests

BUCKET = "jrk-analytics-billing"
PENDING_PREFIX = "Bill_Parser_1_Pending_Parsing/"

DEFAULT_PROFILE = "jrk-analytics-admin"
DEFAULT_REGION = "us-east-1"
DEFAULT_API_URL = "https://p9fk3slot9.execute-api.us-east-1.amazonaws.com/prod/upload-token"


def pick_folder_gui() -> Optional[str]:
    root: Tk = tk.Tk(); root.withdraw()
    folder = filedialog.askdirectory(title="Select folder with PDF bills to upload")
    root.destroy()
    return folder or None


def get_s3_client(profile: Optional[str], region: Optional[str]):
    import boto3.session
    if profile:
        session = boto3.session.Session(profile_name=profile, region_name=region)
    else:
        session = boto3.session.Session(region_name=region)
    return session.client("s3")


def upload_pdf(client, file_path: Path, bucket: str, key_prefix: str) -> str:
    key = f"{key_prefix}{file_path.name}"
    extra_args = {"ContentType": "application/pdf"}
    client.upload_file(str(file_path), bucket, key, ExtraArgs=extra_args)
    return key


def main():
    colorama_init()
    parser = argparse.ArgumentParser(description="Upload PDFs to S3 Pending_Parsing and move locally to _Moved_to_Parser")
    parser.add_argument("--folder", help="Folder with PDFs (skip GUI)")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="AWS CLI profile (fallback mode only)")
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region (fallback mode only)")
    parser.add_argument("--dry-run", action="store_true", help="Log actions only; do not upload or move")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Pre-signed token API endpoint (default: corporate API). Uses presigned POST (no AWS profile needed).")
    parser.add_argument("--api-key", help="API key header value for token endpoint (if required)")
    args = parser.parse_args()

    folder = Path(args.folder or pick_folder_gui() or "")
    if not folder or not folder.exists():
        print(f"{Fore.RED}No folder selected or folder does not exist.{Style.RESET_ALL}")
        sys.exit(1)

    moved_dir = folder / "_Moved_to_Parser"
    moved_dir.mkdir(exist_ok=True)

    pdfs = sorted([p for p in folder.glob("*.pdf")])
    if not pdfs:
        print(f"{Fore.YELLOW}No PDF files found in selected folder.{Style.RESET_ALL}")
        sys.exit(0)

    # Default to presigned mode (corporate API). Only fall back to profile if --api-url is empty or overridden.
    s3 = None
    if not args.api_url:
        try:
            s3 = get_s3_client(args.profile, args.region)
        except Exception as e:
            print(f"{Fore.RED}Failed to create S3 client: {e}{Style.RESET_ALL}")
            sys.exit(1)

    pbar = tqdm(pdfs, desc="Uploading PDFs")
    for pdf in pbar:
        pbar.set_description(f"Uploading {pdf.name}")
        try:
            if args.dry_run:
                key = f"{PENDING_PREFIX}{pdf.name}"
                print(f"{Fore.CYAN}[DRY-RUN]{Style.RESET_ALL} Would upload to s3://{BUCKET}/{key}")
                print(f"{Fore.CYAN}[DRY-RUN]{Style.RESET_ALL} Would move to {moved_dir / pdf.name}")
            else:
                if args.api_url:
                    # Presigned POST flow
                    headers = {}
                    if args.api_key:
                        headers["x-api-key"] = args.api_key
                    try:
                        # Pass the original filename so the server preserves it
                        resp = requests.get(args.api_url, headers=headers, params={"filename": pdf.name}, timeout=30)
                        resp.raise_for_status()
                        payload = resp.json()
                        url = payload["url"]
                        fields = payload["fields"]
                        object_key = payload.get("object_key", "")
                    except Exception as e:
                        print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} Failed to obtain presigned token: {e}")
                        continue

                    # POST multipart form (ensure file handle is CLOSED before renaming on Windows)
                    try:
                        with open(pdf, "rb") as fh:
                            files = {"file": (pdf.name, fh, "application/pdf")}
                            post_resp = requests.post(url, data=fields, files=files, timeout=120)
                        if post_resp.status_code not in (200, 204, 201):
                            print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} Upload failed {pdf.name}: HTTP {post_resp.status_code} {post_resp.text[:200]}")
                            continue
                        # Now that fh is closed, it's safe to move the file on Windows
                        target = moved_dir / pdf.name
                        pdf.rename(target)
                        dest_key = object_key or f"{PENDING_PREFIX}{pdf.name}"
                        print(f"{Fore.GREEN}[OK]{Style.RESET_ALL} Uploaded via presigned -> s3://{BUCKET}/{dest_key} and moved to {target}")
                    except Exception as e:
                        print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} Presigned upload error for {pdf.name}: {e}")
                else:
                    # Direct S3 (requires profile/credentials)
                    key = upload_pdf(s3, pdf, BUCKET, PENDING_PREFIX)
                    # Move local file only after successful upload
                    target = moved_dir / pdf.name
                    pdf.rename(target)
                    print(f"{Fore.GREEN}[OK]{Style.RESET_ALL} Uploaded to s3://{BUCKET}/{key} and moved to {target}")
        except (BotoCoreError, ClientError) as e:
            print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} Failed to upload {pdf.name}: {e}")
        except OSError as e:
            print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} Failed to move {pdf.name}: {e}")

    print(f"{Fore.CYAN}Done uploading. Verify files in s3://{BUCKET}/{PENDING_PREFIX}{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
