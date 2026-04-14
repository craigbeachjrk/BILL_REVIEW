"""
Lambda: jrk-enrichment-retry
Trigger: EventBridge daily schedule (5:00 AM UTC)
Purpose: Find Stage 3 files that never made it to Stage 4 and re-trigger enrichment.

When the enricher Lambda times out or crashes, the S3 trigger doesn't retry.
Files sit in Stage 3 forever. This Lambda scans for orphaned files and
re-triggers them by copying in-place (which fires the S3 ObjectCreated event).
"""
import os
import json
import time
import boto3
from datetime import datetime, date, timezone, timedelta

BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
STAGE3_PREFIX = os.getenv("STAGE3_PREFIX", "Bill_Parser_3_Parsed_Outputs/")
STAGE4_PREFIX = os.getenv("STAGE4_PREFIX", "Bill_Parser_4_Enriched_Outputs/")
DAYS_BACK = int(os.getenv("DAYS_BACK", "3"))
MIN_AGE_MINUTES = int(os.getenv("MIN_AGE_MINUTES", "30"))  # Don't retry files less than 30 min old
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

s3 = boto3.client("s3", region_name=AWS_REGION)


def find_and_retry():
    start = time.time()
    now = datetime.now(timezone.utc)
    min_age_cutoff = now - timedelta(minutes=MIN_AGE_MINUTES)
    paginator = s3.get_paginator("list_objects_v2")

    stage3 = {}  # stem -> {key, last_modified}
    stage4 = set()  # stem set

    for day_offset in range(DAYS_BACK):
        d = now - timedelta(days=day_offset)
        y, m, dd = d.strftime("%Y"), d.strftime("%m"), d.strftime("%d")

        p3 = f"{STAGE3_PREFIX}yyyy={y}/mm={m}/dd={dd}/"
        p4 = f"{STAGE4_PREFIX}yyyy={y}/mm={m}/dd={dd}/"

        for page in paginator.paginate(Bucket=BUCKET, Prefix=p3):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".jsonl"):
                    continue
                stem = key.replace(STAGE3_PREFIX, "")
                last_mod = obj.get("LastModified")
                # Skip files that are too new (might still be processing)
                if last_mod and last_mod.replace(tzinfo=timezone.utc) > min_age_cutoff:
                    continue
                stage3[stem] = {"key": key, "last_modified": str(last_mod)}

        for page in paginator.paginate(Bucket=BUCKET, Prefix=p4):
            for obj in page.get("Contents", []):
                stem = obj["Key"].replace(STAGE4_PREFIX, "")
                stage4.add(stem)

    # Find orphaned files
    missing = sorted(k for k in stage3 if k not in stage4)
    print(json.dumps({
        "message": "Scan complete",
        "stage3_files": len(stage3),
        "stage4_files": len(stage4),
        "orphaned": len(missing),
        "days_scanned": DAYS_BACK,
    }))

    if not missing:
        return {"retriggered": 0, "stage3": len(stage3), "stage4": len(stage4)}

    # Re-trigger by copying in-place
    retriggered = 0
    errors = 0
    for stem in missing:
        key = stage3[stem]["key"]
        try:
            s3.copy_object(
                Bucket=BUCKET,
                Key=key,
                CopySource={"Bucket": BUCKET, "Key": key},
                MetadataDirective="REPLACE",
                ContentType="application/x-ndjson",
            )
            retriggered += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(json.dumps({"error": "retry_failed", "key": key, "message": str(e)[:200]}))

    elapsed = time.time() - start
    result = {
        "retriggered": retriggered,
        "errors": errors,
        "stage3": len(stage3),
        "stage4": len(stage4),
        "elapsed_s": round(elapsed, 1),
    }
    print(json.dumps({"message": "Retry complete", **result}))
    return result


def lambda_handler(event, context):
    try:
        result = find_and_retry()
        return {"statusCode": 200, "body": json.dumps(result)}
    except Exception as e:
        print(f"[ENRICHMENT RETRY] FAILED: {e}")
        import traceback
        traceback.print_exc()
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
