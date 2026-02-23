"""
Parser Failure Router - Routes failed/timed-out parser jobs to the large file processor
Triggered by Lambda failure destination from jrk-bill-parser
"""
import os
import json
import boto3
from datetime import datetime, timezone
from urllib.parse import unquote_plus

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")

BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
PARSED_INPUTS_PREFIX = os.getenv("PARSED_INPUTS_PREFIX", "Bill_Parser_2_Parsed_Inputs/")
PENDING_PREFIX = os.getenv("PENDING_PREFIX", "Bill_Parser_1_Pending_Parsing/")
LARGEFILE_PREFIX = os.getenv("LARGEFILE_PREFIX", "Bill_Parser_1_LargeFile/")
FAILED_PREFIX = os.getenv("FAILED_PREFIX", "Bill_Parser_Failed_Jobs/")
ERRORS_TABLE = os.getenv("ERRORS_TABLE", "jrk-bill-parser-errors")


def _write_error_file(bucket: str, failed_key: str, error_info: dict):
    """Write an .error.json file alongside the failed PDF with error details."""
    try:
        # Build error file key (same name but .error.json extension)
        if failed_key.lower().endswith(".pdf"):
            error_key = failed_key[:-4] + ".error.json"
        else:
            error_key = failed_key + ".error.json"

        s3.put_object(
            Bucket=bucket,
            Key=error_key,
            Body=json.dumps(error_info, indent=2),
            ContentType="application/json"
        )
        print(json.dumps({"message": "Wrote error file", "error_key": error_key}))
    except Exception as e:
        print(json.dumps({"error": "Failed to write error file", "message": str(e)}))


def _log_error_to_ddb(pdf_key: str, error_type: str, error_details: str, source_key: str):
    """Log error to DynamoDB for tracking."""
    try:
        now = datetime.now(timezone.utc)
        pk = f"error#{pdf_key}#{now.strftime('%Y%m%dT%H%M%SZ')}"
        ddb.put_item(
            TableName=ERRORS_TABLE,
            Item={
                "pk": {"S": pk},
                "pdf_key": {"S": pdf_key},
                "error_type": {"S": error_type},
                "error_details": {"S": error_details[:1000] if error_details else ""},
                "source_key": {"S": source_key},
                "timestamp": {"S": now.isoformat()},
                "date": {"S": now.strftime("%Y-%m-%d")},
            }
        )
        print(json.dumps({"message": "Logged error to DynamoDB", "pk": pk}))
    except Exception as e:
        print(json.dumps({"error": "Failed to log to DynamoDB", "message": str(e)}))


def _find_source_file(bucket: str, original_key: str, suffix: str) -> str:
    """
    Find the source file from multiple possible locations.
    When a parser times out, the file might be in:
    1. The original key from the S3 event (most likely for timeout before processing)
    2. Pending prefix (for normal and rework bills)
    3. Parsed_Inputs prefix (if parser started but timed out mid-way)

    Returns the key where the file was found, or None.
    """
    # List of possible source locations in priority order
    candidates = [
        original_key,  # Original location from S3 event
        f"{PENDING_PREFIX}{suffix}",  # Pending prefix
        f"{PARSED_INPUTS_PREFIX}{suffix}",  # Parsed inputs prefix
    ]

    for key in candidates:
        try:
            s3.head_object(Bucket=bucket, Key=key)
            print(json.dumps({"message": "Found source file", "key": key}))
            return key
        except Exception:
            continue

    return None


def lambda_handler(event, context):
    """
    Handle failed parser invocations:
    1. Extract the original S3 key from the failed event
    2. Capture error details and source information
    3. Copy the file from Parsed_Inputs to LargeFile prefix (or Failed if already tried)
    """
    print(json.dumps({"message": "Received failure event", "event": event}))

    # Lambda failure destinations wrap the original event in requestPayload
    request_payload = event.get("requestPayload", event)

    # Extract error information from the failure event
    error_type = event.get("requestContext", {}).get("condition", "UNKNOWN")
    error_message = ""

    # Try to get error message from various locations
    if "responsePayload" in event:
        resp = event.get("responsePayload", {})
        if isinstance(resp, dict):
            error_message = resp.get("errorMessage", resp.get("error", ""))
        elif isinstance(resp, str):
            error_message = resp
    if not error_message and "errorMessage" in event:
        error_message = event.get("errorMessage", "")

    for record in request_payload.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            continue

        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        # Extract the suffix (filename) from the original key
        # Original key could be from Standard or Pending prefix
        if "/" in key:
            suffix = key.rsplit("/", 1)[1]
        else:
            suffix = key

        # Check if this file has already been routed (has _LARGEFILE_ marker)
        if "_LARGEFILE_" in suffix or "_CHUNK_" in suffix.upper():
            print(json.dumps({
                "message": "File already processed by large file pipeline, moving to failed",
                "key": key
            }))
            # Move to failed prefix
            failed_key = f"{FAILED_PREFIX}{suffix}"
            try:
                parsed_key = f"{PARSED_INPUTS_PREFIX}{suffix}"
                s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": parsed_key}, Key=failed_key)
                print(json.dumps({"message": "Moved to failed", "failed_key": failed_key}))

                # Write error file with details
                error_info = {
                    "error_type": error_type or "TIMEOUT",
                    "error_message": error_message or "Parser timed out after large file retry",
                    "source_key": key,
                    "original_key": key,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "pipeline_stage": "large_file_processor",
                }
                _write_error_file(bucket, failed_key, error_info)
                _log_error_to_ddb(failed_key, error_info["error_type"], error_info["error_message"], key)

            except Exception as e:
                print(json.dumps({"error": "Failed to move to failed prefix", "message": str(e)}))
            continue

        # Find the source file from multiple possible locations
        source_key = _find_source_file(bucket, key, suffix)

        if not source_key:
            print(json.dumps({
                "error": "Source file not found in any location",
                "original_key": key,
                "suffix": suffix,
                "checked_locations": [key, f"{PENDING_PREFIX}{suffix}", f"{PARSED_INPUTS_PREFIX}{suffix}"]
            }))
            # Log to DynamoDB for tracking
            _log_error_to_ddb(suffix, "FILE_NOT_FOUND", "Could not locate source file for routing", key)
            continue

        # Add _LARGEFILE_ marker to prevent infinite loop
        base_name = suffix.rsplit(".", 1)[0] if "." in suffix else suffix
        ext = suffix.rsplit(".", 1)[1] if "." in suffix else "pdf"
        large_suffix = f"{base_name}_LARGEFILE_.{ext}"
        large_key = f"{LARGEFILE_PREFIX}{large_suffix}"

        try:
            # Copy from found source to LargeFile prefix (triggers large file processor)
            s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": source_key}, Key=large_key)

            print(json.dumps({
                "message": "Routed timed-out file to large file processor",
                "source": source_key,
                "destination": large_key
            }))
        except Exception as e:
            print(json.dumps({
                "error": "Failed to route to large file processor",
                "source_key": source_key,
                "message": str(e)
            }))

    return {"statusCode": 200, "body": json.dumps({"ok": True})}
