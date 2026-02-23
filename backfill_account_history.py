"""
Backfill account history records from Stage 7 and Stage 8 data.

This script scans existing posted bills and creates history records in DynamoDB
for fast AI review lookups.
"""
import boto3
import json
import gzip
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib

# Configuration
BUCKET = "jrk-analytics-billing"
POST_ENTRATA_PREFIX = "Bill_Parser_7_PostEntrata/"
UBI_ASSIGNED_PREFIX = "Bill_Parser_8_UBI_Assigned/"
HIST_ARCHIVE_PREFIX = "Bill_Parser_99_Historical Archive/"
AI_SUGGESTIONS_TABLE = "jrk-bill-ai-suggestions"

session = boto3.Session(profile_name="jrk-analytics-admin", region_name="us-east-1")
s3 = session.client("s3")
ddb = session.client("dynamodb")

# Stats
stats = {
    "files_scanned": 0,
    "records_written": 0,
    "errors": 0,
    "skipped_no_ids": 0,
    "skipped_duplicate": 0,
}

seen_keys = set()  # Track already processed vendor#property#account#date combos


def pdf_id_from_key(key: str) -> str:
    """Generate pdf_id from S3 key (same as main.py)."""
    return hashlib.sha1(key.encode()).hexdigest()


def normalize_date(bill_date: str) -> str:
    """Normalize bill_date to YYYY-MM-DD for sorting."""
    if not bill_date:
        return ""
    if "/" in bill_date:
        parts = bill_date.split("/")
        if len(parts) == 3:
            try:
                m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                if y < 100:
                    y += 2000
                return f"{y:04d}-{m:02d}-{d:02d}"
            except Exception:
                pass
    return bill_date


def process_jsonl_file(key: str) -> dict:
    """Process a single JSONL file and return history record data."""
    try:
        # Download file
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        body = obj["Body"].read()

        # Decompress if gzipped
        if key.endswith(".gz"):
            body = gzip.decompress(body)

        text = body.decode("utf-8")
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]

        if not lines:
            return None

        # Parse all records
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if not records:
            return None

        first = records[0]

        # Extract required fields
        vendor_id = str(first.get("EnrichedVendorID") or "").strip()
        property_id = str(first.get("EnrichedPropertyID") or "").strip()
        account_number = str(first.get("Account Number") or "").strip()
        bill_date = str(first.get("Bill Date") or "").strip()
        utility_type = str(first.get("Utility Type") or "").strip()

        if not vendor_id or not property_id:
            return {"skip": "no_ids"}

        # Calculate total amount
        total_amount = 0.0
        for rec in records:
            charge_raw = rec.get("Line Item Charge", 0)
            try:
                if isinstance(charge_raw, str):
                    total_amount += float(charge_raw.replace("$", "").replace(",", "").strip() or 0)
                else:
                    total_amount += float(charge_raw or 0)
            except (ValueError, TypeError):
                pass

        # Generate pdf_id
        pdf_id = pdf_id_from_key(key)

        return {
            "pdf_id": pdf_id,
            "vendor_id": vendor_id,
            "property_id": property_id,
            "account_number": account_number,
            "bill_date": bill_date,
            "total_amount": round(total_amount, 2),
            "line_count": len(records),
            "utility_type": utility_type,
            "s3_key": key,
        }

    except Exception as e:
        print(f"  Error processing {key}: {e}")
        return {"error": str(e)}


def write_history_record(data: dict) -> bool:
    """Write a history record to DynamoDB."""
    try:
        sort_date = normalize_date(data["bill_date"])
        account_key = f"{data['vendor_id']}#{data['property_id']}#{data['account_number']}".lower().replace(" ", "")

        # Check for duplicate
        dedup_key = f"{account_key}#{sort_date}"
        if dedup_key in seen_keys:
            return False
        seen_keys.add(dedup_key)

        item = {
            "pk": {"S": f"HISTORY#{account_key}"},
            "sk": {"S": f"BILL#{sort_date}#{data['pdf_id'][:12]}"},
            "pdf_id": {"S": data["pdf_id"]},
            "vendor_id": {"S": data["vendor_id"]},
            "property_id": {"S": data["property_id"]},
            "account_number": {"S": data["account_number"]},
            "bill_date": {"S": data["bill_date"]},
            "total_amount": {"N": str(data["total_amount"])},
            "line_count": {"N": str(data["line_count"])},
            "utility_type": {"S": data["utility_type"] or ""},
            "created_at": {"S": datetime.now(timezone.utc).isoformat()},
            "backfilled": {"BOOL": True},
        }

        ddb.put_item(TableName=AI_SUGGESTIONS_TABLE, Item=item)
        return True

    except Exception as e:
        print(f"  Error writing record: {e}")
        return False


def list_jsonl_files(prefix: str, months_back: int = 3) -> list:
    """List all JSONL files in the given prefix for the last N months."""
    files = []
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=months_back * 31)

    # Build month-level prefixes
    prefixes_to_scan = []
    current = start_date.replace(day=1)
    while current <= end_date:
        prefixes_to_scan.append(f"{prefix}yyyy={current.year}/mm={current.month:02d}/")
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    for pfx in prefixes_to_scan:
        try:
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=BUCKET, Prefix=pfx):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.endswith(".jsonl") or key.endswith(".jsonl.gz"):
                        files.append(key)
        except Exception as e:
            print(f"  Error listing {pfx}: {e}")

    return files


def main():
    print("=" * 60)
    print("Account History Backfill")
    print("=" * 60)
    print(f"Started: {datetime.now()}")
    print()

    # Collect all files from Stage 7, Stage 8, and Stage 99
    print("Scanning Stage 7 (PostEntrata)...")
    stage7_files = list_jsonl_files(POST_ENTRATA_PREFIX, months_back=3)
    print(f"  Found {len(stage7_files)} files")

    print("Scanning Stage 8 (UBI Assigned)...")
    stage8_files = list_jsonl_files(UBI_ASSIGNED_PREFIX, months_back=3)
    print(f"  Found {len(stage8_files)} files")

    print("Scanning Stage 99 (Historical Archive)...")
    stage99_files = list_jsonl_files(HIST_ARCHIVE_PREFIX, months_back=3)
    print(f"  Found {len(stage99_files)} files")

    all_files = stage7_files + stage8_files + stage99_files
    print(f"\nTotal files to process: {len(all_files)}")
    print()

    # Process files with thread pool
    print("Processing files...")
    batch_size = 100

    for batch_start in range(0, len(all_files), batch_size):
        batch = all_files[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(all_files) + batch_size - 1) // batch_size

        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} files)...")

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(process_jsonl_file, key): key for key in batch}

            for future in as_completed(futures):
                stats["files_scanned"] += 1
                result = future.result()

                if result is None:
                    continue
                elif result.get("skip") == "no_ids":
                    stats["skipped_no_ids"] += 1
                elif result.get("error"):
                    stats["errors"] += 1
                else:
                    # Write to DynamoDB
                    if write_history_record(result):
                        stats["records_written"] += 1
                    else:
                        stats["skipped_duplicate"] += 1

        # Progress update
        print(f"    Written: {stats['records_written']}, Skipped: {stats['skipped_no_ids'] + stats['skipped_duplicate']}, Errors: {stats['errors']}")

    print()
    print("=" * 60)
    print("Backfill Complete!")
    print("=" * 60)
    print(f"Files scanned:     {stats['files_scanned']}")
    print(f"Records written:   {stats['records_written']}")
    print(f"Skipped (no IDs):  {stats['skipped_no_ids']}")
    print(f"Skipped (dupes):   {stats['skipped_duplicate']}")
    print(f"Errors:            {stats['errors']}")
    print(f"Finished: {datetime.now()}")


if __name__ == "__main__":
    main()
