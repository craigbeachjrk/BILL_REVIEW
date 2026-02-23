"""
Chunk Processor Lambda - Processes individual PDF chunks with context from previous chunks
Triggered by S3 ObjectCreated events on Bill_Parser_1_LargeFile_Chunks/
Uses Gemini API to parse chunk and maintains context across chunks
"""
import os
import json
import base64
import time
import boto3
import requests
from urllib.parse import unquote_plus
from datetime import datetime, timezone
from decimal import Decimal

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")
secrets = boto3.client("secretsmanager")

# Configuration
BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
MAX_ATTEMPTS = 6  # Number of retry attempts with key rotation
BASE_BACKOFF_SECONDS = 2  # Base delay for exponential backoff
CHUNK_STAGGER_SECONDS = 1.5  # Delay per chunk number to stagger API calls
CHUNKS_PREFIX = os.getenv("CHUNKS_PREFIX", "Bill_Parser_1_LargeFile_Chunks/")
CHUNK_RESULTS_PREFIX = os.getenv("CHUNK_RESULTS_PREFIX", "Bill_Parser_1_LargeFile_Results/")
JOBS_TABLE = os.getenv("JOBS_TABLE", "jrk-bill-parser-jobs")
ERRORS_TABLE = os.getenv("ERRORS_TABLE", "jrk-bill-parser-errors")
PARSER_SECRET_NAME = os.getenv("PARSER_SECRET_NAME", "gemini/parser-keys")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3-pro-preview")
MAX_DROPPED_ROWS_BEFORE_RETRY = 5  # Retry parsing if more than this many rows dropped

# Columns (same as standard parser)
COLUMNS = [
    "Bill To Name First Line", "Bill To Name Second Line", "Vendor Name", "Invoice Number", "Account Number", "Line Item Account Number",
    "Service Address", "Service City", "Service Zipcode", "Service State", "Meter Number", "Meter Size", "House Or Vacant", "Bill Period Start", "Bill Period End", "Utility Type",
    "Consumption Amount", "Unit of Measure", "Previous Reading", "Previous Reading Date", "Current Reading", "Current Reading Date", "Rate", "Number of Days",
    "Line Item Description", "Line Item Charge",
    "Bill Date", "Due Date", "Special Instructions", "Inferred Fields"
]

PROMPT_TEMPLATE = """
You are an expert utility-bill parser. Output ONLY pipe-separated (|) rows with exactly {col_count} fields ({pipe_count} pipes) in this order:
{columns}

{context_note}

CRITICAL: For EVERY row you output, include ALL header-level fields (Bill To Name, Vendor Name, Invoice Number, Account Number, Service Address, Bill Date, Due Date, etc.) even if you extracted them from earlier pages. Repeat these fields on EVERY line item row.

EXTRACTION - Extract EVERY line that has a dollar amount as a separate row. This includes:
- All charges (base, service, delivery, usage, demand, generation, transmission, distribution)
- All taxes, fees, surcharges, and regulatory charges
- All credits, adjustments, and discounts
- Each line item showing any charge or credit amount
- Energy charges by tier (extract each tier as a separate row)
- Franchise fees, public purpose programs, nuclear decommissioning
- ANY line with a $ amount - extract it!

DO NOT output ONLY a "Total Due" or "Amount Due" or "Balance Due" line. These are summary totals. However, if the page ONLY shows a total with no breakdown, extract the total as a single line item rather than returning EMPTY.

IMPORTANT: PG&E and other utility bills often have detailed charge breakdowns. Look for:
- Electric/Gas generation charges
- Electric/Gas delivery charges
- Baseline vs over-baseline rates
- Time-of-use rate tiers
- Public purpose programs
- Each of these should be a SEPARATE row

Rules and standardizations:
- Utility Type must be standardized to one of EXACTLY these values: Electricity | Gas | Trash | Water | Sewer | Stormwater | HOA | Internet | Phone
- Account Number: remove any non-digit characters
- House Or Vacant: return "House" for now
- Consumption Amount: Round to two decimal places if > 10 units
- Special Instructions: cap at 50 characters
- For Inferred Fields: if you infer CRITICAL fields, list column names separated by hyphen; else leave blank.

CRITICAL FORMATTING:
- NEVER include pipe characters (|) within any field value - use dashes (-) or commas instead
- Each row must have EXACTLY {col_count} fields separated by EXACTLY {pipe_count} pipes
- Line Item Description should NOT contain pipes - use dashes to separate parts if needed

NEVER return EMPTY if you see ANY dollar amounts on the page. If there's at least one $ amount, extract it.
""".strip()


def get_keys_from_secret() -> list:
    """Get API keys from Secrets Manager."""
    try:
        resp = secrets.get_secret_value(SecretId=PARSER_SECRET_NAME)
        raw = resp.get("SecretString", "")
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "keys" in parsed:
                return [str(k).strip() for k in parsed["keys"]][:3]
        except:
            pass
        if "," in raw:
            return [k.strip() for k in raw.split(",") if k.strip()][:3]
        return [k.strip() for k in raw.splitlines() if k.strip()][:3]
    except Exception as e:
        print(f"Error getting keys: {e}")
        return []


def get_job_info(job_id: str) -> dict:
    """Get job information from DynamoDB."""
    try:
        resp = ddb.get_item(TableName=JOBS_TABLE, Key={'job_id': {'S': job_id}})
        if 'Item' not in resp:
            return None

        item = resp['Item']
        return {
            'job_id': item['job_id']['S'],
            'total_chunks': int(item['total_chunks']['N']),
            'chunks_completed': int(item['chunks_completed']['N']),
            'status': item['status']['S'],
            'previous_context': item.get('previous_context', {}).get('S', ''),
            'chunk_results': [r['S'] for r in item.get('chunk_results', {}).get('L', [])],
            'expected_lines': int(item.get('expected_lines', {}).get('N', '0')),
            'bill_from': item.get('bill_from', {}).get('S', '')
        }
    except Exception as e:
        print(f"Error getting job info: {e}")
        return None


class RateLimitError(Exception):
    """Raised when Gemini API returns 429 (quota exhausted)."""
    pass


def cleanse_field(value: str) -> str:
    """Remove pipe characters and clean up field values."""
    if not value:
        return ""
    # Replace pipes with dashes to preserve readability
    cleaned = value.replace("|", "-").replace("\n", " ").replace("\r", " ")
    # Collapse multiple spaces
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned.strip()


def normalize_row(parts: list, expected_columns: int) -> list:
    """
    Normalize a row to the expected column count.

    - If too many columns: Join extra columns into Line Item Description (index 24)
    - If too few columns: Pad with empty strings
    - Cleanse all fields of pipe characters
    """
    # First cleanse all parts
    parts = [cleanse_field(p) for p in parts]

    if len(parts) == expected_columns:
        return parts

    if len(parts) > expected_columns:
        # Too many columns - likely pipe in description field
        # Line Item Description is at index 24, Line Item Charge at 25
        # Join extra columns into description
        extra_count = len(parts) - expected_columns

        # Take first 24 columns as-is (everything before description)
        normalized = parts[:24]

        # Join description and extra columns
        description_parts = parts[24:24 + extra_count + 1]
        normalized.append(" - ".join(description_parts))

        # Add remaining columns (charge onwards)
        normalized.extend(parts[24 + extra_count + 1:])

        # Ensure we have exactly expected_columns
        if len(normalized) < expected_columns:
            normalized.extend([""] * (expected_columns - len(normalized)))
        elif len(normalized) > expected_columns:
            normalized = normalized[:expected_columns]

        return normalized

    # Too few columns - pad with empty strings
    return parts + [""] * (expected_columns - len(parts))


# ============================================================================
# CONTENT VALIDATION SCHEMA
# ============================================================================
# Define expected data types for each column (by index)
# Types: "text", "date", "numeric", "text_not_numeric" (must be text, not a number/date)
COLUMN_TYPES = {
    13: "date",      # Bill Period Start
    14: "date",      # Bill Period End
    16: "numeric",   # Consumption Amount (optional but if present must be numeric)
    18: "numeric",   # Previous Reading
    19: "date",      # Previous Reading Date
    20: "numeric",   # Current Reading
    21: "date",      # Current Reading Date
    22: "numeric",   # Rate
    23: "numeric",   # Number of Days
    24: "text_not_numeric",  # Line Item Description - MUST be text, not a number/date
    25: "numeric",   # Line Item Charge - MUST be a dollar amount
    26: "date",      # Bill Date
    # 27: Due Date - skip validation (can have text like "If paying after...")
}

import re as _re

def _looks_like_date(value: str) -> bool:
    """Check if value looks like a date (MM/DD/YYYY, YYYY-MM-DD, etc.)."""
    if not value or not value.strip():
        return True  # Empty is OK (optional field)
    v = value.strip()
    # Common date patterns
    date_patterns = [
        r'^\d{1,2}/\d{1,2}/\d{2,4}$',      # MM/DD/YYYY or M/D/YY
        r'^\d{4}-\d{2}-\d{2}$',             # YYYY-MM-DD
        r'^\d{1,2}-\d{1,2}-\d{2,4}$',       # MM-DD-YYYY
        r'^\d{4}/\d{2}/\d{2}$',             # YYYY/MM/DD
    ]
    for pattern in date_patterns:
        if _re.match(pattern, v):
            return True
    return False


def _looks_like_numeric(value: str) -> bool:
    """Check if value looks like a number (can be negative, with decimals, commas, or $ sign)."""
    if not value or not value.strip():
        return True  # Empty is OK (optional field)
    v = value.strip()
    # Remove currency symbols, commas, parentheses (for negative)
    v = v.replace('$', '').replace(',', '').replace('(', '-').replace(')', '').strip()
    if not v:
        return True
    try:
        float(v)
        return True
    except ValueError:
        return False


def _is_date_value(value: str) -> bool:
    """Check if value IS a date (not just looks like one - for detecting misplaced dates)."""
    if not value or not value.strip():
        return False
    v = value.strip()
    date_patterns = [
        r'^\d{1,2}/\d{1,2}/\d{2,4}$',
        r'^\d{4}-\d{2}-\d{2}$',
        r'^\d{1,2}-\d{1,2}-\d{2,4}$',
        r'^\d{4}/\d{2}/\d{2}$',
    ]
    for pattern in date_patterns:
        if _re.match(pattern, v):
            return True
    return False


def _is_pure_numeric(value: str) -> bool:
    """Check if value is purely numeric (for detecting numbers where text should be)."""
    if not value or not value.strip():
        return False
    v = value.strip()
    v = v.replace('$', '').replace(',', '').replace('(', '-').replace(')', '').replace('-', '').strip()
    if not v:
        return False
    try:
        float(v)
        return True
    except ValueError:
        return False


def validate_row_content(row: list) -> tuple[bool, list[str]]:
    """
    Validate that row content matches expected data types.
    Returns (is_valid, list_of_errors).
    """
    errors = []

    for col_idx, expected_type in COLUMN_TYPES.items():
        if col_idx >= len(row):
            continue
        value = row[col_idx]
        col_name = COLUMNS[col_idx] if col_idx < len(COLUMNS) else f"Column {col_idx}"

        if expected_type == "date":
            if value and value.strip() and not _looks_like_date(value):
                errors.append(f"{col_name} (col {col_idx}): expected date, got '{value[:50]}'")

        elif expected_type == "numeric":
            if value and value.strip() and not _looks_like_numeric(value):
                errors.append(f"{col_name} (col {col_idx}): expected numeric, got '{value[:50]}'")

        elif expected_type == "text_not_numeric":
            # This field should be TEXT, not a number or date (indicates column shift)
            if _is_pure_numeric(value):
                errors.append(f"{col_name} (col {col_idx}): expected text description, got numeric '{value[:50]}'")
            elif _is_date_value(value):
                errors.append(f"{col_name} (col {col_idx}): expected text description, got date '{value[:50]}'")

    return (len(errors) == 0, errors)


def log_parsing_error(job_id: str, chunk_num: int, error_type: str, details: str, source_file: str = ""):
    """Log parsing errors to DynamoDB for tracking."""
    try:
        now = datetime.now(timezone.utc)
        pk = f"chunk_error#{job_id}#chunk{chunk_num}#{now.strftime('%Y%m%dT%H%M%SZ')}"
        ddb.put_item(
            TableName=ERRORS_TABLE,
            Item={
                "pk": {"S": pk},
                "job_id": {"S": job_id},
                "chunk_num": {"N": str(chunk_num)},
                "error_type": {"S": error_type},
                "error_details": {"S": details[:1000] if details else ""},
                "source_file": {"S": source_file},
                "timestamp": {"S": now.isoformat()},
                "date": {"S": now.strftime("%Y-%m-%d")},
            }
        )
        print(json.dumps({"message": "Logged parsing error to DynamoDB", "pk": pk, "error_type": error_type}))
    except Exception as e:
        print(json.dumps({"error": "Failed to log parsing error to DynamoDB", "message": str(e)}))


def call_gemini_api(api_key: str, pdf_bytes: bytes, prompt: str, timeout: int = 90) -> str:
    """Call Gemini API to parse PDF chunk. Raises RateLimitError for 429 errors."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": "application/pdf", "data": base64.b64encode(pdf_bytes).decode("ascii")}},
                    {"text": prompt},
                ],
            }
        ]
    }
    headers = {"Content-Type": "application/json"}

    try:
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=timeout)
        if r.status_code == 429:
            raise RateLimitError(f"Gemini quota exhausted (429): {r.text[:300]}")
        if r.status_code != 200:
            raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:500]}")

        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
        text = "".join([p.get("text", "") for p in parts if isinstance(p, dict)])
        return text.strip()
    except RateLimitError:
        raise
    except Exception as e:
        raise


def parse_chunk_with_retry(api_keys: list, pdf_bytes: bytes, chunk_num: int, total_chunks: int, previous_context: str, expected_lines: int = 0) -> tuple[list[list[str]], str]:
    """
    Parse a PDF chunk with key rotation and exponential backoff.

    Implements:
    1. Key rotation - cycles through all available API keys
    2. Exponential backoff - increasing delays on 429 errors
    3. Staggered processing - initial delay based on chunk number

    Returns rows + new context summary. Returns empty on complete failure.
    """

    # Stagger chunk processing to avoid simultaneous API calls
    # Chunk 1 starts immediately, chunk 2 waits 1.5s, chunk 3 waits 3s, etc.
    stagger_delay = (chunk_num - 1) * CHUNK_STAGGER_SECONDS
    if stagger_delay > 0:
        print(json.dumps({"message": "Staggering chunk start", "chunk": chunk_num, "delay_seconds": stagger_delay}))
        time.sleep(stagger_delay)

    # Build context note for prompt
    if previous_context and chunk_num > 1:
        context_note = f"IMPORTANT: This is chunk {chunk_num} of {total_chunks}. Previous chunks contained:\n{previous_context}\n\nUse the vendor, account number, bill dates, and service address from the previous context and include them on EVERY row you output from this chunk. Continue extracting line items from this chunk, maintaining consistency with previous data."
    else:
        context_note = f"This is chunk {chunk_num} of {total_chunks} from a multi-page invoice. Extract header information (vendor, account, dates, addresses) and include them on EVERY row."

    prompt = PROMPT_TEMPLATE.format(
        col_count=len(COLUMNS),
        pipe_count=len(COLUMNS)-1,
        columns=' | '.join(COLUMNS),
        context_note=context_note
    )

    # Add expected_lines hint if provided from rework metadata
    if expected_lines and expected_lines > 0:
        lines_per_chunk = max(1, expected_lines // total_chunks)
        prompt += (f"\n\nIMPORTANT: The user has indicated the FULL bill should have approximately {expected_lines} line items total across all {total_chunks} chunks. "
                   f"Each chunk should extract approximately {lines_per_chunk} or more line items on average. "
                   f"Please carefully extract ALL line items from this chunk, including any itemized charges, fees, taxes, or adjustments. "
                   "Do not aggregate or summarize multiple charges into a single line item - extract each one separately.")

    # Retry loop with key rotation and exponential backoff
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        # Rotate through API keys
        api_key = api_keys[attempt % len(api_keys)]
        key_index = attempt % len(api_keys)

        try:
            print(json.dumps({
                "message": "Attempting API call",
                "chunk": chunk_num,
                "attempt": attempt + 1,
                "max_attempts": MAX_ATTEMPTS,
                "key_index": key_index
            }))

            reply_text = call_gemini_api(api_key, pdf_bytes, prompt, timeout=90)

            # Success! Parse the response
            if reply_text.upper() == "EMPTY":
                print(json.dumps({"message": "chunk_reported_empty", "chunk": chunk_num}))
                return [], f"Chunk {chunk_num} empty (no line items)"

            # Parse pipe-delimited response
            rows = []
            normalized_count = 0  # Rows that needed normalization
            for line in reply_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split('|')]

                # Skip header rows or lines with too few parts (likely not data)
                if len(parts) < 10:
                    continue

                # Normalize the row (handles extra/missing columns and cleanses pipes)
                if len(parts) != len(COLUMNS):
                    normalized_count += 1
                    if normalized_count <= 3:  # Log first 3 normalizations
                        print(json.dumps({"message": "row_normalized", "chunk": chunk_num, "original_cols": len(parts), "expected": len(COLUMNS), "preview": line[:200]}))

                normalized = normalize_row(parts, len(COLUMNS))

                # Validate content types
                is_valid, content_errors = validate_row_content(normalized)
                if not is_valid:
                    print(json.dumps({
                        "warning": "content_validation_failed",
                        "chunk": chunk_num,
                        "row": len(rows) + 1,
                        "errors": content_errors[:3]  # Log first 3 errors
                    }))

                rows.append(normalized)

            # Generate context summary for next chunk
            if rows:
                bill_to = rows[0][0] if len(rows[0]) > 0 else ""
                vendor = rows[0][2] if len(rows[0]) > 2 else ""
                invoice_num = rows[0][3] if len(rows[0]) > 3 else ""
                account = rows[0][4] if len(rows[0]) > 4 else ""
                service_addr = rows[0][6] if len(rows[0]) > 6 else ""
                service_city = rows[0][7] if len(rows[0]) > 7 else ""
                service_zip = rows[0][8] if len(rows[0]) > 8 else ""
                service_state = rows[0][9] if len(rows[0]) > 9 else ""
                bill_date = rows[0][26] if len(rows[0]) > 26 else ""
                due_date = rows[0][27] if len(rows[0]) > 27 else ""

                context_summary = (
                    f"Bill To: {bill_to} | Vendor: {vendor} | Invoice: {invoice_num} | "
                    f"Account: {account} | Service Address: {service_addr}, {service_city}, {service_state} {service_zip} | "
                    f"Bill Date: {bill_date} | Due Date: {due_date} | "
                    f"Extracted {len(rows)} line items from chunk {chunk_num}"
                )
            else:
                context_summary = f"No items extracted from chunk {chunk_num}"

            if normalized_count > 0:
                print(json.dumps({"message": "rows_normalized_total", "chunk": chunk_num, "normalized": normalized_count, "total_rows": len(rows)}))
            print(json.dumps({"message": "Chunk parsed successfully", "chunk": chunk_num, "rows": len(rows), "attempt": attempt + 1}))
            return rows, context_summary

        except RateLimitError as e:
            # Exponential backoff for rate limit errors
            backoff_delay = BASE_BACKOFF_SECONDS * (2 ** attempt)  # 2, 4, 8, 16, 32, 64 seconds
            last_error = str(e)
            print(json.dumps({
                "warning": "rate_limit_hit",
                "chunk": chunk_num,
                "attempt": attempt + 1,
                "key_index": key_index,
                "backoff_seconds": backoff_delay,
                "error": str(e)[:200]
            }))
            time.sleep(backoff_delay)

        except Exception as e:
            # Other errors - shorter delay, still rotate keys
            last_error = str(e)
            print(json.dumps({
                "warning": "api_call_failed",
                "chunk": chunk_num,
                "attempt": attempt + 1,
                "key_index": key_index,
                "error": str(e)[:200]
            }))
            time.sleep(BASE_BACKOFF_SECONDS)

    # All attempts exhausted
    print(json.dumps({
        "error": "chunk_failed_all_attempts",
        "chunk": chunk_num,
        "attempts": MAX_ATTEMPTS,
        "last_error": last_error[:300] if last_error else "unknown"
    }))
    return [], f"Chunk {chunk_num} failed after {MAX_ATTEMPTS} attempts: {last_error[:100] if last_error else 'unknown'}"


def update_job_progress(job_id: str, chunk_num: int, result_key: str, context_summary: str):
    """Update job record with chunk completion."""
    try:
        # Atomically increment chunks_completed and append result
        ddb.update_item(
            TableName=JOBS_TABLE,
            Key={'job_id': {'S': job_id}},
            UpdateExpression="SET chunks_completed = chunks_completed + :inc, previous_context = :context, chunk_results = list_append(if_not_exists(chunk_results, :empty_list), :result)",
            ExpressionAttributeValues={
                ':inc': {'N': '1'},
                ':context': {'S': context_summary},
                ':result': {'L': [{'S': result_key}]},
                ':empty_list': {'L': []}
            }
        )
        print(json.dumps({"message": "Job progress updated", "job_id": job_id, "chunk": chunk_num}))
    except Exception as e:
        print(f"Error updating job progress: {e}")


def lambda_handler(event, context):
    """
    Chunk Processor Handler:
    1. Receives chunk PDF from S3
    2. Gets job info and previous context from DynamoDB
    3. Calls Gemini API to parse chunk with context
    4. Saves result to S3
    5. Updates job progress in DynamoDB
    """
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            continue

        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        if not key.startswith(CHUNKS_PREFIX):
            continue

        # Extract job_id and chunk_num from key
        # Format: Bill_Parser_1_LargeFile_Chunks/{job_id}/chunk_001.pdf
        path_parts = key[len(CHUNKS_PREFIX):].split('/')
        if len(path_parts) < 2:
            print(json.dumps({"error": "invalid_chunk_key", "key": key}))
            continue

        job_id = path_parts[0]
        chunk_filename = path_parts[1]
        chunk_num = int(chunk_filename.split('_')[1].split('.')[0])  # chunk_001.pdf -> 1

        print(json.dumps({"message": "Processing chunk", "job_id": job_id, "chunk_num": chunk_num}))

        # Get job info
        job_info = get_job_info(job_id)
        if not job_info:
            print(json.dumps({"error": "job_not_found", "job_id": job_id}))
            continue

        # Download chunk PDF
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            pdf_bytes = obj['Body'].read()
        except Exception as e:
            print(json.dumps({"error": "failed_to_download_chunk", "key": key, "message": str(e)}))
            continue

        # Get API keys
        keys = get_keys_from_secret()
        if not keys:
            print(json.dumps({"error": "no_api_keys"}))
            continue

        # Parse chunk with key rotation and exponential backoff
        rows, context_summary = parse_chunk_with_retry(
            keys,  # Pass all keys for rotation
            pdf_bytes,
            chunk_num,
            job_info['total_chunks'],
            job_info['previous_context'],
            job_info.get('expected_lines', 0)
        )

        # Save chunk result to S3
        result_key = f"{CHUNK_RESULTS_PREFIX}{job_id}/chunk_{str(chunk_num).zfill(3)}.json"
        try:
            result_data = {
                "job_id": job_id,
                "chunk_num": chunk_num,
                "rows": rows,
                "context_summary": context_summary,
                "parsed_at": datetime.now(timezone.utc).isoformat()
            }
            s3.put_object(
                Bucket=bucket,
                Key=result_key,
                Body=json.dumps(result_data, ensure_ascii=False).encode('utf-8'),
                ContentType='application/json'
            )
            print(json.dumps({"message": "Chunk result saved", "result_key": result_key}))
        except Exception as e:
            print(json.dumps({"error": "failed_to_save_result", "message": str(e)}))
            continue

        # Update job progress
        update_job_progress(job_id, chunk_num, result_key, context_summary)

        # Check if all chunks completed
        if job_info['chunks_completed'] + 1 >= job_info['total_chunks']:
            print(json.dumps({"message": "All chunks completed", "job_id": job_id, "status": "ready_for_aggregation"}))
            # TODO: Trigger aggregator Lambda here (via SNS or direct invocation)

    return {"statusCode": 200, "body": json.dumps({"ok": True})}
