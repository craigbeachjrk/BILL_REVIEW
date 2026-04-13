"""
Lambda: jrk-vendor-property-mapper
Trigger: EventBridge daily schedule
Purpose: Build a vendor-property mapping from historical invoice filenames.

Scans S7 (Posted) and Archive stage S3 keys to determine which vendors
have historically billed which properties. The enricher Lambda uses this
to pre-filter vendor candidates before calling Gemini, reducing the
candidate list from ~7,000 to ~7 per property (1000x reduction).

Output: S3 JSON file at Bill_Parser_Enrichment/exports/vendor_property_map/latest.json.gz
Format: {
    "built_at": "2026-04-13T...",
    "property_count": 93,
    "pair_count": 679,
    "by_property": {
        "PROPERTY_ID": ["VENDOR_ID_1", "VENDOR_ID_2", ...],
        ...
    },
    "by_vendor": {
        "VENDOR_ID": ["PROPERTY_ID_1", "PROPERTY_ID_2", ...],
        ...
    }
}
"""
import os
import json
import gzip
import time
import boto3
from datetime import datetime, date, timezone, timedelta
from collections import defaultdict

BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
OUTPUT_KEY = os.getenv("OUTPUT_KEY", "Bill_Parser_Enrichment/exports/vendor_property_map/latest.json.gz")
MONTHS_BACK = int(os.getenv("MONTHS_BACK", "12"))
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

s3 = boto3.client("s3", region_name=AWS_REGION)

STAGE_PREFIXES = [
    "Bill_Parser_7_PostEntrata_Submission/",
    "Bill_Parser_8_UBI_Assigned/",
    "Bill_Parser_99_Historical Archive/",
]


def _parse_pair_from_key(key: str):
    """Extract (property_id, vendor_id) from S3 key filename.
    Format: Property-Vendor-Account-MM-DD-YYYY-MM-DD-YYYY-MM-DD-YYYY_timestamp.jsonl
    """
    fname = key.rsplit("/", 1)[-1]
    if not fname.endswith(".jsonl"):
        return None
    base = fname.replace(".jsonl", "")
    # Strip timestamp suffix
    if "_" in base:
        base = base.rsplit("_", 1)[0]
    parts = base.split("-")
    if len(parts) < 3:
        return None
    return parts[0], parts[1]


def build_mapping() -> dict:
    """Scan S3 filenames and build property-vendor mapping."""
    start = time.time()
    today = date.today()

    by_property = defaultdict(set)
    by_vendor = defaultdict(set)
    total_keys = 0

    for prefix in STAGE_PREFIXES:
        for month_offset in range(MONTHS_BACK):
            total_m = today.year * 12 + today.month - 1 - month_offset
            y = total_m // 12
            m = total_m % 12 + 1
            p = f"{prefix}yyyy={y}/mm={m:02d}/"
            try:
                paginator = s3.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=BUCKET, Prefix=p):
                    for obj in page.get("Contents", []):
                        total_keys += 1
                        pair = _parse_pair_from_key(obj["Key"])
                        if pair:
                            prop_id, vendor_id = pair
                            by_property[prop_id].add(vendor_id)
                            by_vendor[vendor_id].add(prop_id)
            except Exception as e:
                print(f"[VENDOR-PROP MAP] Error scanning {p}: {e}")

    # Convert sets to sorted lists for JSON serialization
    result = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "months_scanned": MONTHS_BACK,
        "total_keys_scanned": total_keys,
        "property_count": len(by_property),
        "vendor_count": len(by_vendor),
        "pair_count": sum(len(v) for v in by_property.values()),
        "by_property": {k: sorted(v) for k, v in sorted(by_property.items())},
        "by_vendor": {k: sorted(v) for k, v in sorted(by_vendor.items())},
    }

    elapsed = time.time() - start
    print(json.dumps({
        "message": "Vendor-property mapping built",
        "properties": len(by_property),
        "vendors": len(by_vendor),
        "pairs": result["pair_count"],
        "keys_scanned": total_keys,
        "elapsed_s": round(elapsed, 1),
    }))

    return result


def write_to_s3(data: dict):
    """Write mapping to S3 as gzipped JSON."""
    body = gzip.compress(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    s3.put_object(
        Bucket=BUCKET,
        Key=OUTPUT_KEY,
        Body=body,
        ContentType="application/gzip",
    )
    print(f"[VENDOR-PROP MAP] Written to s3://{BUCKET}/{OUTPUT_KEY} ({len(body)} bytes)")


def lambda_handler(event, context):
    try:
        mapping = build_mapping()
        write_to_s3(mapping)
        return {
            "statusCode": 200,
            "body": json.dumps({
                "ok": True,
                "properties": mapping["property_count"],
                "vendors": mapping["vendor_count"],
                "pairs": mapping["pair_count"],
            })
        }
    except Exception as e:
        print(f"[VENDOR-PROP MAP] FAILED: {e}")
        import traceback
        traceback.print_exc()
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


if __name__ == "__main__":
    result = build_mapping()
    write_to_s3(result)
    print(json.dumps(result, indent=2)[:500])
