"""
PDF Router Lambda - Routes PDFs to appropriate parser based on size/complexity
Triggered by S3 ObjectCreated events on Bill_Parser_1_Pending_Parsing/
"""
import os
import json
import boto3
from urllib.parse import unquote_plus
from datetime import datetime, timezone
import PyPDF2
from io import BytesIO

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")

# Configuration
BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
PENDING_PREFIX = os.getenv("PENDING_PREFIX", "Bill_Parser_1_Pending_Parsing/")
STANDARD_PREFIX = os.getenv("STANDARD_PREFIX", "Bill_Parser_1_Standard/")
LARGEFILE_PREFIX = os.getenv("LARGEFILE_PREFIX", "Bill_Parser_1_LargeFile/")
ROUTER_TABLE = os.getenv("ROUTER_TABLE", "jrk-bill-router-log")

# Thresholds
MAX_PAGES_STANDARD = int(os.getenv("MAX_PAGES_STANDARD", "10"))
MAX_SIZE_MB_STANDARD = int(os.getenv("MAX_SIZE_MB_STANDARD", "10"))


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Count pages in a PDF using PyPDF2."""
    try:
        pdf_file = BytesIO(pdf_bytes)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        return len(pdf_reader.pages)
    except Exception as e:
        print(f"Error counting PDF pages: {e}")
        return -1  # Unknown page count


def log_routing_decision(pdf_key: str, page_count: int, file_size_mb: float, route: str, reason: str):
    """Log routing decision to DynamoDB for tracking."""
    try:
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        filename = pdf_key.rsplit('/', 1)[-1] if '/' in pdf_key else pdf_key

        item = {
            'pk': {'S': f"ROUTE#{filename}"},
            'timestamp': {'S': timestamp},
            'pdf_key': {'S': pdf_key},
            'page_count': {'N': str(page_count)},
            'file_size_mb': {'N': str(round(file_size_mb, 2))},
            'route': {'S': route},
            'reason': {'S': reason},
            'date': {'S': now.strftime('%Y-%m-%d')},
        }

        ddb.put_item(TableName=ROUTER_TABLE, Item=item)
    except Exception as e:
        print(f"Failed to log routing decision: {e}")


def lambda_handler(event, context):
    """
    Router Lambda Handler:
    1. Receives S3 event for new PDF in Pending
    2. Downloads PDF and analyzes (page count, file size)
    3. Routes to Standard or LargeFile prefix
    4. Logs decision to DynamoDB
    """
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            continue

        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        if not key.startswith(PENDING_PREFIX):
            continue

        # Get PDF metadata
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
            file_size_bytes = head.get('ContentLength', 0)
            file_size_mb = file_size_bytes / (1024 * 1024)
        except Exception as e:
            print(json.dumps({"error": "failed_to_get_metadata", "key": key, "message": str(e)}))
            continue

        # Download PDF to count pages
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            pdf_bytes = obj['Body'].read()
            page_count = count_pdf_pages(pdf_bytes)
        except Exception as e:
            print(json.dumps({"error": "failed_to_download_pdf", "key": key, "message": str(e)}))
            continue

        # Routing logic
        suffix = key[len(PENDING_PREFIX):]
        route = "standard"
        reason = "within_thresholds"

        if page_count < 0:
            # Couldn't determine page count - default to standard with warning
            route = "standard"
            reason = "unknown_page_count_default_standard"
        elif page_count > MAX_PAGES_STANDARD:
            route = "largefile"
            reason = f"page_count_{page_count}_exceeds_{MAX_PAGES_STANDARD}"
        elif file_size_mb > MAX_SIZE_MB_STANDARD:
            route = "largefile"
            reason = f"file_size_{file_size_mb:.1f}MB_exceeds_{MAX_SIZE_MB_STANDARD}MB"

        # Determine destination
        if route == "largefile":
            dest_key = f"{LARGEFILE_PREFIX}{suffix}"
        else:
            dest_key = f"{STANDARD_PREFIX}{suffix}"

        # Copy to destination
        try:
            s3.copy_object(
                Bucket=bucket,
                CopySource={'Bucket': bucket, 'Key': key},
                Key=dest_key
            )

            # Also copy any sidecar files (.notes.json, .rework.json) for rework metadata
            base_key = key.rsplit('.', 1)[0]
            dest_base = dest_key.rsplit('.', 1)[0]
            for sidecar_ext in ['.notes.json', '.rework.json']:
                sidecar_src = base_key + sidecar_ext
                sidecar_dest = dest_base + sidecar_ext
                try:
                    # Check if sidecar exists and copy it
                    s3.head_object(Bucket=bucket, Key=sidecar_src)
                    s3.copy_object(
                        Bucket=bucket,
                        CopySource={'Bucket': bucket, 'Key': sidecar_src},
                        Key=sidecar_dest
                    )
                    print(json.dumps({"message": "Copied sidecar", "src": sidecar_src, "dest": sidecar_dest}))
                except Exception as e:
                    # Sidecar doesn't exist or copy failed - that's expected for non-rework files
                    if "404" not in str(e) and "NoSuchKey" not in str(e):
                        print(json.dumps({"message": "Sidecar copy failed", "sidecar": sidecar_src, "error": str(e)}))

            # Delete from pending
            s3.delete_object(Bucket=bucket, Key=key)

            # Log routing decision
            log_routing_decision(key, page_count, file_size_mb, route, reason)

            print(json.dumps({
                "message": "PDF routed successfully",
                "source_key": key,
                "dest_key": dest_key,
                "route": route,
                "page_count": page_count,
                "file_size_mb": round(file_size_mb, 2),
                "reason": reason
            }))
        except Exception as e:
            print(json.dumps({
                "error": "routing_failed",
                "source_key": key,
                "message": str(e)
            }))

    return {"statusCode": 200, "body": json.dumps({"ok": True})}
