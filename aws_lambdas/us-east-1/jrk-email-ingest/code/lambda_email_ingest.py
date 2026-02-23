import os
import json
import re
import datetime as dt
import boto3
from email.parser import BytesParser
from email import policy

s3 = boto3.client("s3")


def _load_canon_map() -> list[tuple[re.Pattern, str]]:
    # Load from env if provided, else fallback to a reasonable default
    raw = os.getenv("ATTACHMENT_CANON_MAP", "")
    pairs: list[tuple[re.Pattern, str]] = []
    try:
        if raw:
            m = json.loads(raw)
            if isinstance(m, dict):
                for pattern, canon in m.items():
                    pairs.append((re.compile(pattern, re.IGNORECASE), str(canon)))
    except Exception:
        pass
    # Default patterns
    if not pairs:
        pairs = [
            (re.compile(r"\bNSF\b", re.IGNORECASE), "NSF"),
        ]
    return pairs

_CANON_MAP = _load_canon_map()

def _canonicalize(base_filename: str) -> str:
    for pat, canon in _CANON_MAP:
        if pat.search(base_filename):
            return canon
    # Fallback to sanitized base
    return _sanitize_filename(base_filename)

_CANON_MAP = _load_canon_map()

TARGET_BUCKET = os.getenv("TARGET_BUCKET", "jrk-email-partitioned-us-east-1")
PARTITIONED_PREFIX_ROOT = os.getenv("PARTITIONED_PREFIX_ROOT", "emails/")
STORE_ATTACHMENTS = os.getenv("STORE_ATTACHMENTS", "1") == "1"
ATTACHMENTS_PREFIX_ROOT = os.getenv("ATTACHMENTS_PREFIX_ROOT", "attachments/")
BUCKET_BILL = os.getenv("BUCKET", "jrk-analytics-billing")
INPUT_PREFIX_BILL = os.getenv("INPUT_PREFIX", "Bill_Parser_1_Pending_Parsing/")


def _today_parts(ts: dt.datetime | None = None):
    ts = ts or dt.datetime.utcnow()
    return ts.strftime("%Y"), ts.strftime("%m"), ts.strftime("%d"), ts.strftime("%Y%m%dT%H%M%SZ")


def _put_s3(bucket: str, key: str, body: bytes, content_type: str | None = None):
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type or "application/octet-stream")


def _sanitize_filename(name: str) -> str:
    name = name or "file"
    bad = "\\\r\n\t:/<>\"'|?*"
    for ch in bad:
        name = name.replace(ch, "_")
    return name[:200]


def _extract_recipients(msg) -> list[str]:
    rcpts = []
    for header in ("To", "Cc", "Bcc", "Delivered-To", "X-Original-To"):
        v = msg.get(header)
        if not v:
            continue
        from email.utils import getaddresses
        for _, addr in getaddresses([v]):
            if addr:
                rcpts.append(addr.lower())
    return list(dict.fromkeys(rcpts))


def handler(event, context):
    # Expect SES -> S3 action event: extract the S3 location of the raw mail
    # and write it to the TARGET_BUCKET partitioned as yyyy=/mm=/dd=
    # Optionally extract attachments to a separate prefix.
    record = (event.get("Records") or [])[0]
    s3_bucket = None
    s3_key = None
    # Case 1: Triggered by S3 Put event
    if record.get("s3"):
        s3_bucket = record["s3"]["bucket"]["name"]
        s3_key = record["s3"]["object"]["key"]
    # Case 2: Triggered by SES receipt rule (S3 action then Lambda action)
    elif record.get("ses"):
        ses = record["ses"]
        mail = ses.get("mail", {})
        receipt = ses.get("receipt", {})
        action = receipt.get("action", {})
        bucket_name = action.get("bucketName") or action.get("bucket")
        prefix = action.get("objectKeyPrefix", "") or ""
        msg_id = mail.get("messageId") or mail.get("messageID") or ""
        if bucket_name and msg_id:
            s3_bucket = bucket_name
            # SES S3 object key is typically prefix + messageId
            s3_key = f"{prefix}{msg_id}"
    if not s3_bucket or not s3_key:
        # Unable to resolve source object
        raise RuntimeError("Could not resolve S3 object from event (expected S3 Put or SES receipt event)")

    raw_obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    raw_bytes = raw_obj["Body"].read()

    # Parse MIME
    msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    now = dt.datetime.utcnow()
    yyyy, mm, dd, ts = _today_parts(now)
    date_iso = now.date().isoformat()

    # Flat output prefixes (no date partitioning)
    part_prefix = f"{PARTITIONED_PREFIX_ROOT}"

    # Save raw RFC822
    message_id = (msg.get("Message-Id") or msg.get("Message-ID") or "").strip("<>") or os.path.basename(s3_key)
    raw_key = f"{part_prefix}{ts}_{_sanitize_filename(message_id or 'message')}.eml"
    _put_s3(TARGET_BUCKET, raw_key, raw_bytes, content_type="message/rfc822")

    # Extract attachments: store only PDFs for bills@ and only CSVs for reports@
    if STORE_ATTACHMENTS:
        recipients = _extract_recipients(msg) or ["unknown@unknown"]
        # Determine routing
        route = "other"
        if any(r.startswith("bills@") for r in recipients):
            route = "bills"
        elif any(r.startswith("reports@") for r in recipients):
            route = "reports"
        for part in msg.walk():
            filename = part.get_filename()
            if not filename:
                continue
            try:
                content = part.get_payload(decode=True) or b""
            except Exception:
                continue
            if not content:
                continue
            # Build attachment key
            safe_name = _sanitize_filename(filename)
            base_no_ext = os.path.splitext(safe_name)[0] or "file"
            canonical = _canonicalize(base_no_ext)
            # Order requested: attachments/recipient=<local-part>/<canonical>/<YYYY-MM-DD>/
            rcpt = recipients[0].replace("@", "_")
            att_prefix = f"{ATTACHMENTS_PREFIX_ROOT}recipient={rcpt}/{canonical}/{date_iso}/"
            att_key = f"{att_prefix}{ts}_{safe_name}"
            ctype = part.get_content_type() or "application/octet-stream"
            # Filters: bills=PDF only, reports=CSV only, other=skip
            lower_name = safe_name.lower()
            is_pdf = lower_name.endswith(".pdf") or ctype == "application/pdf"
            # Be lenient for CSV: rely on extension OR common csv-ish content types
            is_csv = (
                lower_name.endswith(".csv")
                or "csv" in (ctype or "").lower()
                or ctype in ("text/plain", "application/vnd.ms-excel", "application/octet-stream") and lower_name.endswith(".csv")
            )
            should_store = (route == "bills" and is_pdf) or (route == "reports" and is_csv)
            if should_store:
                _put_s3(TARGET_BUCKET, att_key, content, content_type=ctype)

            # Mirror PDF attachments for bills@ into bill parser input bucket/prefix
            try:
                to_bills = (route == "bills")
                if is_pdf and to_bills:
                    bill_key = f"{INPUT_PREFIX_BILL}{ts}_{safe_name}"
                    print(f"[MIRROR] Attempting to write PDF to {BUCKET_BILL}/{bill_key} ({len(content)} bytes)")
                    _put_s3(BUCKET_BILL, bill_key, content, content_type="application/pdf")
                    print(f"[MIRROR] SUCCESS: {bill_key}")
            except Exception as e:
                # Log the error but don't fail the whole invocation
                print(f"[MIRROR] FAILED to write {safe_name} to {BUCKET_BILL}: {type(e).__name__}: {e}")

    return {
        "ok": True,
        "raw_key": raw_key,
        "bucket": TARGET_BUCKET,
        "attachments": STORE_ATTACHMENTS,
    }
