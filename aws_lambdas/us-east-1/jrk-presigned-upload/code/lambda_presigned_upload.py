import os
import json
import boto3
from datetime import datetime, timezone
import re

s3 = boto3.client("s3")

BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
PREFIX = os.getenv("PENDING_PREFIX", "Bill_Parser_1_Pending_Parsing/")
MAX_SIZE_BYTES = int(os.getenv("MAX_SIZE_BYTES", "26214400"))  # 25 MB default
EXPIRES_IN = int(os.getenv("EXPIRES_IN", "600"))  # 10 minutes

# Optional simple API key check; set API_KEY in Lambda env to enforce
API_KEY = os.getenv("API_KEY")
API_KEY_HEADER = os.getenv("API_KEY_HEADER", "x-api-key")
# Optional: comma-separated allowlist of public IPs, e.g. "64.60.29.50,203.0.113.10"
ALLOWLIST_IPS = [ip.strip() for ip in os.getenv("ALLOWLIST_IPS", "").split(",") if ip.strip()]


def _unauthorized():
    return {
        "statusCode": 401,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"message": "Unauthorized"}),
    }


def lambda_handler(event, context):
    # Optional: enforce a simple API key header
    if API_KEY:
        headers = event.get("headers") or {}
        # Normalize header keys to lowercase
        headers_lower = {k.lower(): v for k, v in headers.items()}
        if headers_lower.get(API_KEY_HEADER.lower()) != API_KEY:
            return _unauthorized()

    # Optional: enforce source IP allowlist (works behind API Gateway via X-Forwarded-For)
    if ALLOWLIST_IPS:
        headers = event.get("headers") or {}
        xff = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for") or ""
        # XFF may contain multiple IPs, take the first (client's public IP)
        client_ip = xff.split(",")[0].strip() if xff else ""
        if client_ip not in ALLOWLIST_IPS:
            return _unauthorized()

    # Use a flat holding zone (no date partitioning) for quick in/out
    key_prefix = f"{PREFIX}"
    # Optional client-provided filename; sanitize to avoid path traversal and non-pdf
    qs = (event.get("queryStringParameters") or {})
    raw_name = (qs.get("filename") or "").strip()
    filename = raw_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if not filename.lower().endswith(".pdf") or not re.fullmatch(r"[\w.,@()+\- ]+\.pdf", filename):
        filename = f"upload-{context.aws_request_id}.pdf"
    object_key = key_prefix + filename

    conditions = [
        {"bucket": BUCKET},
        ["starts-with", "$key", key_prefix],
        {"Content-Type": "application/pdf"},
        ["content-length-range", 1, MAX_SIZE_BYTES],
    ]
    fields = {"Content-Type": "application/pdf"}

    presigned = s3.generate_presigned_post(
        Bucket=BUCKET,
        Key=object_key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=EXPIRES_IN,
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "url": presigned["url"],
            "fields": presigned["fields"],
            "object_key": object_key,
        }),
    }
