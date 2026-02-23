import os
import sys
import json
import re
import time
import base64
import boto3
import requests
from io import BytesIO
from urllib.parse import unquote_plus
from datetime import datetime, timezone
from error_tracker import log_parser_error, extract_gemini_error_code

# Optional PyPDF2 import for page counting (gracefully degrade if not available)
try:
    import PyPDF2
    _HAS_PYPDF2 = True
except ImportError:
    _HAS_PYPDF2 = False
    print("PyPDF2 not available - page metadata will be disabled")

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")
secrets = boto3.client("secretsmanager")

BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
PENDING_PREFIX = os.getenv("PENDING_PREFIX", "Bill_Parser_1_Pending_Parsing/")
STANDARD_PREFIX = os.getenv("STANDARD_PREFIX", "Bill_Parser_1_Standard/")
PARSED_INPUTS_PREFIX = os.getenv("PARSED_INPUTS_PREFIX", "Bill_Parser_2_Parsed_Inputs/")
PARSED_OUTPUTS_PREFIX = os.getenv("PARSED_OUTPUTS_PREFIX", "Bill_Parser_3_Parsed_Outputs/")
ERRORS_TABLE = os.getenv("ERRORS_TABLE", "jrk-bill-parser-errors")
FAILED_PREFIX = os.getenv("FAILED_PREFIX", "Bill_Parser_Failed_Jobs/")
LARGEFILE_PREFIX = os.getenv("LARGEFILE_PREFIX", "Bill_Parser_1_LargeFile/")
PARSER_SECRET_NAME = os.getenv("PARSER_SECRET_NAME", "gemini/parser-keys")
# Separate secret for enrichment (Gemini 1.5 Flash) keys
MATCHER_SECRET_NAME = os.getenv("MATCHER_SECRET_NAME", "gemini/matcher-keys")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-pro")
# Enrichment config
ENRICH_MODEL = os.getenv("ENRICH_MODEL", "gemini-1.5-flash")
ENRICH_PREFIX = os.getenv("ENRICH_PREFIX", "Bill_Parser_Enrichment/exports/")
DIM_VENDOR_PREFIX = ENRICH_PREFIX + "dim_vendor/"
DIM_PROPERTY_PREFIX = ENRICH_PREFIX + "dim_property/"

# Columns per 8_PDF_PARSER_REMOVING_FAILURES.py
COLUMNS = [
    "Bill To Name First Line", "Bill To Name Second Line", "Vendor Name", "Invoice Number", "Account Number", "Line Item Account Number",
    "Service Address", "Service City", "Service Zipcode", "Service State", "Meter Number", "Meter Size", "House Or Vacant", "Bill Period Start", "Bill Period End", "Utility Type",
    "Consumption Amount", "Unit of Measure", "Previous Reading", "Previous Reading Date", "Current Reading", "Current Reading Date", "Rate", "Number of Days",
    "Line Item Description", "Line Item Charge",
    "Bill Date", "Due Date", "Special Instructions", "Inferred Fields"
]
PIPE_COUNT = len(COLUMNS) - 1


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Count pages in a PDF using PyPDF2 (optional dependency)."""
    if not _HAS_PYPDF2:
        return 0  # PyPDF2 not available
    try:
        pdf_file = BytesIO(pdf_bytes)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        return len(pdf_reader.pages)
    except Exception as e:
        print(f"Error counting PDF pages: {e}")
        return 0  # Unknown page count


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

def _looks_like_date(value: str) -> bool:
    """Check if value looks like a date (MM/DD/YYYY, YYYY-MM-DD, etc.)."""
    if not value or not value.strip():
        return True  # Empty is OK (optional field)
    v = value.strip()
    # Common date patterns
    import re
    date_patterns = [
        r'^\d{1,2}/\d{1,2}/\d{2,4}$',      # MM/DD/YYYY or M/D/YY
        r'^\d{4}-\d{2}-\d{2}$',             # YYYY-MM-DD
        r'^\d{1,2}-\d{1,2}-\d{2,4}$',       # MM-DD-YYYY
        r'^\d{4}/\d{2}/\d{2}$',             # YYYY/MM/DD
    ]
    for pattern in date_patterns:
        if re.match(pattern, v):
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
    import re
    date_patterns = [
        r'^\d{1,2}/\d{1,2}/\d{2,4}$',
        r'^\d{4}-\d{2}-\d{2}$',
        r'^\d{1,2}-\d{1,2}-\d{2,4}$',
        r'^\d{4}/\d{2}/\d{2}$',
    ]
    for pattern in date_patterns:
        if re.match(pattern, v):
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


PROMPT = f"""
You are an expert utility-bill parser. Output ONLY pipe-separated (|) rows with exactly {len(COLUMNS)} fields ({PIPE_COUNT} pipes) in this order:
{' | '.join(COLUMNS)}
If no line items are found, output the single word: EMPTY.

Rules and standardizations:
- Utility Type must be standardized to one of EXACTLY these values: Electricity | Gas | Trash | Water | Sewer | Stormwater | HOA | Internet | Phone. Do NOT output any other value (e.g., Pass-through, Tax, Fees). If the charge is a component of a Water bill (e.g., taxes/fees/surcharges), still set Utility Type to Water.
- Meter Number: extract the service meter identifier if present, else leave blank.
- Meter Size: extract the meter size (e.g., 5/8", 1", etc.) if present, else leave blank.
- House Or Vacant: ONLY return "House" only for now
- Account Number: remove any non-digit characters from this (spaces & punctuation etc.)
- Consumption Amount: Round the consumption to two decimal places if greater than 10 units consumption
- Special Instructions: cap this at 50 characters

For the Inferred Fields column: if you infer any CRITICAL fields, list their column names separated by a hyphen (e.g., Bill Date-Due Date); else leave blank.
""".strip()

MAX_ATTEMPTS = 10

# In-memory caches (persist for warm invocations)
_VENDOR_CANDIDATES = None  # list[dict]
_PROPERTY_CANDIDATES = None  # list[dict]


def _sanitize_key(raw: str) -> str:
    """Extract an AIza* token from raw strings, trimming quotes/wrappers."""
    if not raw:
        return ""
    m = re.search(r"(AIza[0-9A-Za-z_\-]{20,})", raw)
    return m.group(1) if m else raw.strip().strip('"').strip("'")


def get_keys_from_secret() -> list:
    """Return up to 3 API keys from Secrets Manager, tolerating multiple formats:
    - {"keys": ["k1","k2","k3"]}
    - ["k1","k2","k3"]
    - Plaintext: newline or comma separated
    - {"key1":"k1","key2":"k2","key3":"k3"}
    """
    resp = secrets.get_secret_value(SecretId=PARSER_SECRET_NAME)
    raw = resp.get("SecretString")
    if not raw:
        return []
    raw = raw.strip()
    # Try JSON first
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            if "keys" in parsed and isinstance(parsed["keys"], list):
                return [str(x).strip() for x in parsed["keys"] if str(x).strip()][:10]
            # Look for key1/key2/key3
            collected = []
            for i in (1, 2, 3):
                v = parsed.get(f"key{i}")
                if v:
                    collected.append(str(v).strip())
            if collected:
                return collected[:10]
        elif isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()][:10]
    except Exception:
        pass
    # Fallback: plaintext separated by newline or comma
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
    else:
        parts = [p.strip() for p in raw.splitlines()]
    cleaned = [_sanitize_key(p) for p in parts]
    return [k for k in cleaned if k][:10]


def get_matcher_keys_from_secret() -> list:
    """Return up to 3 enrichment (matcher) API keys from Secrets Manager, tolerant to multiple formats."""
    resp = secrets.get_secret_value(SecretId=MATCHER_SECRET_NAME)
    raw = resp.get("SecretString")
    if not raw:
        return []
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            if "keys" in parsed and isinstance(parsed["keys"], list):
                cleaned = [_sanitize_key(str(x)) for x in parsed["keys"]]
                return [k for k in cleaned if k][:10]
            collected = []
            for i in (1, 2, 3):
                v = parsed.get(f"key{i}")
                if v:
                    collected.append(_sanitize_key(str(v)))
            if collected:
                return collected[:10]
        elif isinstance(parsed, list):
            cleaned = [_sanitize_key(str(x)) for x in parsed]
            return [k for k in cleaned if k][:10]
    except Exception:
        pass
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
    else:
        parts = [p.strip() for p in raw.splitlines()]
    return [p for p in parts if p][:10]


def call_gemini_rest(api_key: str, pdf_bytes: bytes, prompt: str) -> str:
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
    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini REST error {r.status_code}: {r.text[:300]}")
    data = r.json()
    # Extract text
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
    text = "".join([p.get("text", "") for p in parts if isinstance(p, dict)])
    return text.strip()


def _list_latest_object(bucket: str, prefix: str):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    contents = resp.get("Contents") or []
    if not contents:
        return None
    latest = max(contents, key=lambda x: x.get("LastModified"))
    return latest.get("Key")


def _wait_for_object(bucket: str, key: str, attempts: int = 8, sleep_ms: int = 300) -> bool:
    for _ in range(attempts):
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return True
        except Exception:
            time.sleep(max(0.05, sleep_ms / 1000.0))
    return False


def _copy_with_retry(bucket: str, src_key: str, dest_key: str, attempts: int = 3) -> bool:
    for i in range(attempts):
        try:
            s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": src_key}, Key=dest_key)
            return True
        except Exception as e:
            # if destination already exists, treat as success
            try:
                s3.head_object(Bucket=bucket, Key=dest_key)
                return True
            except Exception:
                if i == attempts - 1:
                    return False
                time.sleep(0.25)


def _load_jsonl_from_s3(bucket: str, key: str) -> list:
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read().decode("utf-8", errors="ignore")
    items = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return items


def _ensure_enrichment_loaded():
    global _VENDOR_CANDIDATES, _PROPERTY_CANDIDATES
    if _VENDOR_CANDIDATES is None:
        vk = _list_latest_object(BUCKET, DIM_VENDOR_PREFIX)
        _VENDOR_CANDIDATES = []
        if vk:
            records = _load_jsonl_from_s3(BUCKET, vk)
            for r in records:
                # per user: use vendor name for both id and name
                name = (r.get("vendor_name") or r.get("Vendor Name") or r.get("name") or "").strip()
                if name:
                    _VENDOR_CANDIDATES.append({"id": name, "name": name})
    if _PROPERTY_CANDIDATES is None:
        pk = _list_latest_object(BUCKET, DIM_PROPERTY_PREFIX)
        _PROPERTY_CANDIDATES = []
        if pk:
            records = _load_jsonl_from_s3(BUCKET, pk)
            for r in records:
                # per user: use dim property property name as candidate
                name = (r.get("property_name") or r.get("Property Name") or r.get("name") or "").strip()
                if name:
                    _PROPERTY_CANDIDATES.append({"id": name, "name": name})


def gemini_match_rest(api_key: str, target: str, candidates: list, threshold: float = 0.85, max_alternates: int = 2) -> dict:
    """Call Gemini to perform fuzzy match. Returns dict with keys: best, alternates.
    If below threshold or error, returns {}.
    """
    if not target or not candidates:
        return {}
    # Keep payload small: only name/id and cap candidates
    capped = candidates[:500]
    payload_obj = {
        "task": "fuzzy_match",
        "threshold": threshold,
        "max_alternates": max_alternates,
        "target": target,
        "candidates": capped,
        "instructions": "Compare target to candidates by semantics and normalization. Respond ONLY JSON: {\"best\":{\"id\":str,\"name\":str,\"score\":float}, \"alternates\":[... up to max_alternates]}. If no match >= threshold, return {}."
    }
    prompt = json.dumps(payload_obj, ensure_ascii=False)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{ENRICH_MODEL}:generateContent?key={api_key}"
    req = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    r = requests.post(url, headers={"Content-Type": "application/json"}, data=json.dumps(req), timeout=60)
    if r.status_code != 200:
        return {}
    data = r.json()
    candidates_arr = (data.get("candidates") or [])
    if not candidates_arr:
        return {}
    parts = (((candidates_arr[0] or {}).get("content") or {}).get("parts") or [])
    text = "".join([p.get("text", "") for p in parts if isinstance(p, dict)]).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _normalize_reply(text: str) -> str:
    # Normalize common alternate pipe characters and stray unicode separators
    replacements = {
        "Â¦": "|", "ï½œ": "|", "â”‚": "|", "â”ƒ": "|", "Â¦": "|",
        "\u00a0": " ",  # non-breaking space
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    # Trim whitespace on each line
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(lines)


def call_gemini_with_retry_rest(api_key: str, pdf_bytes: bytes, source_name: str):
    global __EXPECTED_LINES
    attempts = 0
    prev_reply = ""
    prev_content_errors = []  # Track content validation errors for retry feedback
    rows: list[list[str]] = []
    failed_due_to_columns = False
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        prompt = PROMPT
        # Add expected_lines hint if provided from rework metadata
        if __EXPECTED_LINES and __EXPECTED_LINES > 0:
            prompt += (f"\n\n**CRITICAL REQUIREMENT**: A human reviewer has verified this bill contains EXACTLY {__EXPECTED_LINES} line items. "
                       f"You MUST output EXACTLY {__EXPECTED_LINES} rows of data. This is NOT optional. "
                       f"The human can see the bill and has counted {__EXPECTED_LINES} distinct charges/line items. "
                       "Look carefully at EVERY charge on the bill including: base charges, usage charges, fees, taxes, surcharges, credits, adjustments, and any other itemized amounts. "
                       "Each distinct charge MUST be its own row. Do NOT combine or aggregate charges. Do NOT skip any charges. "
                       f"If you cannot find {__EXPECTED_LINES} line items, look harder - they are there. Check for charges that may appear in different sections or formats.\n\n"
                       "**COLUMN ORDER - ALL 30 COLUMNS IN EXACT ORDER**:\n"
                       "1. Bill To Name First Line (customer name)\n"
                       "2. Bill To Name Second Line (customer name line 2)\n"
                       "3. Vendor Name (utility company name)\n"
                       "4. Invoice Number\n"
                       "5. Account Number (digits only)\n"
                       "6. Line Item Account Number\n"
                       "7. Service Address (street address)\n"
                       "8. Service City\n"
                       "9. Service Zipcode\n"
                       "10. Service State (2-letter code)\n"
                       "11. Meter Number (meter ID)\n"
                       "12. Meter Size (physical size: 5/8\", 1\", 2\" etc - NOT 'House')\n"
                       "13. House Or Vacant (always 'House')\n"
                       "14. Bill Period Start (date MM/DD/YYYY)\n"
                       "15. Bill Period End (date MM/DD/YYYY)\n"
                       "16. Utility Type (Electricity|Gas|Water|Sewer|Trash|Stormwater|HOA|Internet|Phone)\n"
                       "17. Consumption Amount (numeric usage)\n"
                       "18. Unit of Measure (kWh, CCF, gallons, therms)\n"
                       "19. Previous Reading (numeric)\n"
                       "20. Previous Reading Date (date)\n"
                       "21. Current Reading (numeric)\n"
                       "22. Current Reading Date (date)\n"
                       "23. Rate (price per unit)\n"
                       "24. Number of Days\n"
                       "25. Line Item Description (TEXT like 'Water Usage', 'Electric Charge' - NOT a number)\n"
                       "26. Line Item Charge (DOLLAR AMOUNT like 32.41 - MUST be a number)\n"
                       "27. Bill Date (date MM/DD/YYYY)\n"
                       "28. Due Date (date MM/DD/YYYY)\n"
                       "29. Special Instructions (max 50 chars)\n"
                       "30. Inferred Fields\n"
                       "CRITICAL: Column 25 must be TEXT description, Column 26 must be DOLLAR AMOUNT. Do NOT swap them.")
        if attempts > 1 and prev_reply:
            excerpt = prev_reply[:1500]
            prompt += ("\n\nYou previously returned data with formatting errors. "
                       f"Each row must have exactly {len(COLUMNS)} fields. "
                       "Here is your last output (reference only):\n" + excerpt +
                       "\nNow output only corrected rows with the exact number of columns.")
        # Add specific content validation error feedback
        if attempts > 1 and prev_content_errors:
            prompt += ("\n\n**CONTENT VALIDATION ERRORS FROM YOUR PREVIOUS ATTEMPT**:\n"
                       + "\n".join(prev_content_errors[:10]) +
                       "\n\nFix these column mapping issues. Ensure:\n"
                       "- Date fields contain dates (MM/DD/YYYY format)\n"
                       "- Numeric fields contain numbers (dollar amounts, readings, etc.)\n"
                       "- Line Item Description is TEXT (the name of the charge), NOT a number\n"
                       "- Line Item Charge is a DOLLAR AMOUNT (a number like 32.41), NOT a date")
        try:
            reply_text = call_gemini_rest(api_key, pdf_bytes, prompt)
        except Exception as e:
            prev_reply = str(e)
            time.sleep(3)
            continue

        prev_reply = reply_text
        if reply_text.upper() == "EMPTY":
            return [], False, prev_reply

        # parse lines, validate and normalize pipe count
        candidate_rows = []
        normalized_count = 0
        norm = _normalize_reply(reply_text)
        for line in norm.splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split('|')]

            # Skip header rows or lines with too few parts (likely not data)
            if len(parts) < 10:
                continue

            # Normalize the row (handles extra/missing columns and cleanses pipes)
            if len(parts) != len(COLUMNS):
                normalized_count += 1
                if normalized_count <= 3:  # Log first 3 normalizations
                    print(json.dumps({"message": "row_normalized", "original_cols": len(parts), "expected": len(COLUMNS), "preview": line[:200]}))

            normalized = normalize_row(parts, len(COLUMNS))
            candidate_rows.append(normalized)

        if candidate_rows:
            # Validate content of all rows
            all_content_errors = []
            for row_idx, row in enumerate(candidate_rows):
                is_valid, errors = validate_row_content(row)
                if not is_valid:
                    for err in errors:
                        all_content_errors.append(f"Row {row_idx + 1}: {err}")

            if all_content_errors:
                # Content validation failed - log and retry
                print(json.dumps({
                    "message": "content_validation_failed",
                    "attempt": attempts,
                    "error_count": len(all_content_errors),
                    "errors": all_content_errors[:5]  # Log first 5 errors
                }))
                prev_content_errors = all_content_errors
                failed_due_to_columns = True
                time.sleep(2)
                continue  # Retry with error feedback

            # All validation passed - return success
            rows = [[*r, f"{source_name}"] for r in candidate_rows]
            return rows, False, prev_reply
        else:
            failed_due_to_columns = True
            time.sleep(2)

    return rows, failed_due_to_columns, prev_reply


# Injected: Bill From hint threading
__BILL_FROM_RAW = ""
__BILL_FROM_HINT = ""
# Injected: Expected line count hint from rework
__EXPECTED_LINES = 0

def _norm_bf(s: str) -> str:
    try:
        import re
        if not s: return ""
        s = s.upper().replace('&', ' AND ')
        s = re.sub(r'[-_/]', ' ', s)
        s = re.sub(r"[\.,'()]", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        for token in [" CUSTOMER SERVICES", " UTILITIES", " UTILITY SERVICES"]:
            s = s.replace(token, "")
        return re.sub(r"\s+", " ", s).strip()
    except Exception:
        return str(s or "")
def write_ndjson(bucket: str, key_stem: str, rows: list[list[str]], source_input_key: str, bill_from: str = "", pdf_id: str = "", total_pages: int = 0):
    """Write parsed rows to NDJSON file in S3.

    Args:
        bucket: S3 bucket name
        key_stem: Base filename for output
        rows: Parsed line item rows
        source_input_key: Full S3 key of source PDF
        bill_from: Optional vendor hint
        pdf_id: Optional PDF identifier
        total_pages: Total pages in source PDF (for page metadata)
    """
    ndjson_lines = []
    now = datetime.now(timezone.utc)
    parsed_at_utc = now.isoformat()
    # helper to coerce various date strings into MM/DD/YYYY
    def fmt_us_date(s: str) -> str:
        if not s:
            return ""
        s = str(s).strip()
        import re as _re

        # First try flexible regex for M-D-YY, M/D/YY, M-D-YYYY, M/D/YYYY formats
        # This handles single-digit months/days like "11-2-25" or "1-2-25"
        flex_match = _re.match(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})$', s)
        if flex_match:
            m, d, y = flex_match.groups()
            m, d = int(m), int(d)
            y = int(y)
            # Convert 2-digit year to 4-digit (assume 2000s for 00-99)
            if y < 100:
                y = 2000 + y if y < 50 else 1900 + y
            if 1 <= m <= 12 and 1 <= d <= 31:
                return f"{m:02d}/{d:02d}/{y:04d}"

        # common patterns with zero-padded values
        fmts = [
            "%m/%d/%Y", "%m/%d/%y",
            "%Y-%m-%d", "%Y/%m/%d",
            "%m-%d-%Y", "%m-%d-%y",
            "%b %d, %Y", "%B %d, %Y",
        ]
        for f in fmts:
            try:
                d = datetime.strptime(s, f)
                return d.strftime("%m/%d/%Y")
            except Exception:
                pass
        # try to pull 8 digits like 20250813 or 08132025
        digits = _re.sub(r"\D", "", s)
        try:
            if len(digits) == 8:
                # try YYYYMMDD then MMDDYYYY
                try:
                    d = datetime.strptime(digits, "%Y%m%d")
                except Exception:
                    d = datetime.strptime(digits, "%m%d%Y")
                return d.strftime("%m/%d/%Y")
        except Exception:
            pass
        return s  # give up: keep original
    for r in rows:
        data = {k: v for k, v in zip(COLUMNS, r[:len(COLUMNS)])}
        data["source_file_page"] = r[len(COLUMNS)] if len(r) > len(COLUMNS) else key_stem
        # include full S3 key to the parsed input PDF so downstream can pre-sign accurately
        data["source_input_key"] = source_input_key
        data["PDF_LINK"] = source_input_key
        # Inject Bill From and normalized hint if present
        if bill_from:
            try:
                import re
                def __norm_name(s: str) -> str:
                    if not s: return ""
                    s = s.upper().replace('&',' AND ')
                    s = re.sub(r'[-_/]',' ', s)
                    s = re.sub(r"[\.,'()]","", s)
                    s = re.sub(r"\s+"," ", s).strip()
                    for token in [" CUSTOMER SERVICES"," UTILITIES"," UTILITY SERVICES"]:
                        s = s.replace(token, "")
                    return re.sub(r"\s+"," ", s).strip()
                data["Bill From"] = bill_from
                data["bill_from_hint"] = __norm_name(bill_from)
            except Exception:
                pass
        try:
            if __BILL_FROM_RAW:
                data["Bill From"] = __BILL_FROM_RAW
                data["bill_from_hint"] = __BILL_FROM_HINT
        except Exception:
            pass
        inferred = data.get("Inferred Fields", "")
        if isinstance(inferred, str) and inferred.strip():
            data["Inferred Fields"] = [s.strip() for s in inferred.split("-") if s.strip()]
        else:
            data["Inferred Fields"] = []
        # normalize all date fields to MM/DD/YYYY
        for dk in [
            "Bill Period Start", "Bill Period End", "Bill Date", "Due Date",
            "Previous Reading Date", "Current Reading Date"
        ]:
            if dk in data and isinstance(data[dk], str):
                data[dk] = fmt_us_date(data[dk])
        # Enrichment: Vendors and Properties via Gemini 1.5 Flash
        try:
            _ensure_enrichment_loaded()
            # use dedicated matcher keys (rotate deterministically per file)
            mkeys = get_matcher_keys_from_secret()
            api_key = mkeys[hash(key_stem) % len(mkeys)] if mkeys else None
            if api_key:
                vendor_target = (data.get("Vendor Name") or "").strip()
                prop_target = (data.get("Bill To Name First Line") or "").strip()
                if vendor_target and _VENDOR_CANDIDATES:
                    vm = gemini_match_rest(api_key, vendor_target, _VENDOR_CANDIDATES)
                    if isinstance(vm, dict) and vm.get("best"):
                        data["EnrichedVendor"] = vm.get("best")
                if prop_target and _PROPERTY_CANDIDATES:
                    pm = gemini_match_rest(api_key, prop_target, _PROPERTY_CANDIDATES)
                    if isinstance(pm, dict) and pm.get("best"):
                        data["EnrichedProperty"] = pm.get("best")
        except Exception as _:
            # do not block parsing on enrichment errors
            pass
        data["parsed_at_utc"] = parsed_at_utc
        data["pdf_id"] = pdf_id if pdf_id else key_stem
        # Add page metadata for UI page-to-line mapping
        # Standard parser processes entire PDF as one unit, so all lines span pages 1 to total_pages
        data["source_chunk"] = 0  # Not chunked (standard parser)
        data["source_page_start"] = 1 if total_pages > 0 else 0
        data["source_page_end"] = total_pages if total_pages > 0 else 0
        ndjson_lines.append(json.dumps(data, ensure_ascii=False))

    out_prefix = f"{PARSED_OUTPUTS_PREFIX}yyyy={now.year:04d}/mm={now.month:02d}/dd={now.day:02d}/"
    out_key = f"{out_prefix}source=s3/{key_stem}.jsonl"
    body = "\n".join(ndjson_lines) + "\n"
    s3.put_object(Bucket=BUCKET, Key=out_key, Body=body.encode('utf-8'), ContentType='application/x-ndjson')
    return out_key


def lambda_handler(event, context):
    # Process each record; move object out of Pending ASAP, then parse
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            continue
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])  # may be URL-encoded

        # Accept files from both Pending (direct uploads) and Standard (routed by router Lambda)
        is_pending = key.startswith(PENDING_PREFIX)
        is_standard = key.startswith(STANDARD_PREFIX)

        if not (is_pending or is_standard):
            continue

        # Compute suffix and copy into Parsed_Inputs
        if is_pending:
            suffix = key[len(PENDING_PREFIX):]
        else:  # is_standard
            suffix = key[len(STANDARD_PREFIX):]
        dest_key_inputs = f"{PARSED_INPUTS_PREFIX}{suffix}"
        # Wait for object to be available, then copy with retry
        if not _wait_for_object(bucket, key):
            print(json.dumps({"message": "Pending object not yet visible", "key": key}))
            continue
        if not _copy_with_retry(bucket, key, dest_key_inputs):
            print(json.dumps({"message": "Failed to move from Pending to Parsed_Inputs", "key": key}))
            continue
                # Capture Bill From from sidecar and metadata before deletion
        bill_from = ""
        expected_lines = 0
        try:
            pending_side = key.rsplit('.',1)[0] + '.notes.json'
            print(json.dumps({"message": "Looking for notes.json", "pending_side": pending_side, "bucket": bucket}))
            side_obj = s3.get_object(Bucket=bucket, Key=pending_side)
            side_body = side_obj['Body'].read().decode('utf-8','ignore')
            side = json.loads(side_body)
            bill_from = str(side.get('Bill From') or side.get('bill_from') or '').strip()
            # Also check for expected_lines in notes.json
            expected_lines = int(side.get('expected_line_count') or side.get('expected_lines') or side.get('min_lines') or 0)
            print(json.dumps({"message": "Read notes.json successfully", "expected_lines": expected_lines, "bill_from": bill_from}))
        except Exception as e:
            print(json.dumps({"message": "Failed to read notes.json", "error": str(e), "pending_side": pending_side if 'pending_side' in dir() else "unknown"}))
        # Also try to read .rework.json for expected_line_count hint
        try:
            rework_side = key.rsplit('.',1)[0] + '.rework.json'
            rework_obj = s3.get_object(Bucket=bucket, Key=rework_side)
            rework_body = rework_obj['Body'].read().decode('utf-8','ignore')
            rework = json.loads(rework_body)
            if not bill_from:
                bill_from = str(rework.get('Bill From') or rework.get('bill_from') or '').strip()
            if not expected_lines:
                expected_lines = int(rework.get('expected_line_count') or rework.get('expected_lines') or rework.get('min_lines') or 0)
                print(json.dumps({"message": "Read expected_lines from rework.json", "expected_lines": expected_lines}))
        except Exception as e:
            # rework.json won't exist in Pending, this is expected
            pass
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
            md = {k.lower(): v for k,v in (head.get('Metadata') or {}).items()}
            if not bill_from:
                bill_from = str(md.get('bill-from') or '').strip()
        except Exception:
            pass
        try:
            global __BILL_FROM_RAW, __BILL_FROM_HINT, __EXPECTED_LINES
            __BILL_FROM_RAW = bill_from
            __BILL_FROM_HINT = _norm_bf(bill_from)
            __EXPECTED_LINES = expected_lines
            if expected_lines:
                print(json.dumps({"message": "Expected lines hint found", "expected_lines": expected_lines, "key": key}))
        except Exception:
            pass
        # Track source key for deletion AFTER processing completes successfully
        # This prevents data loss if Lambda crashes during parsing
        source_to_delete = key if (is_pending or is_standard) else None
        queue_name = "Pending" if is_pending else "Standard" if is_standard else None

        # Download the PDF
        obj = s3.get_object(Bucket=bucket, Key=dest_key_inputs)
        pdf_bytes = obj['Body'].read()

        # Count pages for page metadata (used in UI for page-to-line mapping)
        total_pages = count_pdf_pages(pdf_bytes)
        if total_pages > 0:
            print(json.dumps({"message": "PDF page count", "total_pages": total_pages, "key": key}))

        # Fetch keys after moving the file, so Pending stays clean even if secret is malformed
        keys = get_keys_from_secret()
        if not keys:
            # Move to failed for visibility
            failed_key = f"{FAILED_PREFIX}{suffix}"
            s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": dest_key_inputs}, Key=failed_key)
            print(json.dumps({"message": "No valid Gemini keys found in secret; moved to failed", "failed_key": failed_key}))
            continue

        # Rotate over up to 10 attempts, cycling API keys if needed
        attempt = 0
        last_error = None
        rows = []
        last_reply = ""  # Initialize to avoid UnboundLocalError
        failed_due_to_columns = False
        while attempt < MAX_ATTEMPTS:
            attempt += 1
            api_key = keys[(attempt - 1) % len(keys)]  # simple rotation among 3 keys
            try:
                rows, failed_due_to_columns, last_reply = call_gemini_with_retry_rest(api_key, pdf_bytes, source_name=suffix)
                if rows or not failed_due_to_columns:
                    break
            except Exception as e:
                last_error = str(e)
                time.sleep(3)

        if rows:
            key_stem = f"{dest_key_inputs.split('/',1)[-1].rsplit('.',1)[0]}"
            out_key = write_ndjson(BUCKET, key_stem, rows, dest_key_inputs, bill_from=bill_from, pdf_id=key_stem, total_pages=total_pages)
            print(json.dumps({"message": "Parsed and wrote NDJSON", "out_key": out_key, "rows": len(rows), "total_pages": total_pages}))
            # NOW safe to delete source - output has been written successfully
            if source_to_delete:
                try:
                    s3.delete_object(Bucket=bucket, Key=source_to_delete)
                    print(json.dumps({"message": f"Deleted from {queue_name} after successful parse", "key": source_to_delete}))
                except Exception:
                    pass
        else:
            # Check if this file has already been through the large file processor
            # Files from large file processor have "_LARGEFILE_" in their name
            already_tried_large = "_LARGEFILE_" in suffix or "_CHUNK_" in suffix.upper()

            if not already_tried_large:
                # Route to large file processor for chunked parsing
                # Mark the file so we know it's been routed (avoid infinite loop)
                base_name = suffix.rsplit('.', 1)[0]
                ext = suffix.rsplit('.', 1)[1] if '.' in suffix else 'pdf'
                large_suffix = f"{base_name}_LARGEFILE_.{ext}"
                large_key = f"{LARGEFILE_PREFIX}{large_suffix}"

                try:
                    s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": dest_key_inputs}, Key=large_key)
                    print(json.dumps({
                        "message": "Standard parsing failed; routing to large file processor",
                        "source": dest_key_inputs,
                        "large_file_key": large_key,
                        "failed_due_to_columns": failed_due_to_columns,
                        "error": last_error
                    }))
                    # Delete source after successful routing to large file processor
                    if source_to_delete:
                        try:
                            s3.delete_object(Bucket=bucket, Key=source_to_delete)
                            print(json.dumps({"message": f"Deleted from {queue_name} after large file routing", "key": source_to_delete}))
                        except Exception:
                            pass
                except Exception as route_err:
                    print(json.dumps({
                        "message": "Failed to route to large file processor",
                        "error": str(route_err)
                    }))
                    # Fall through to move to failed prefix
                    already_tried_large = True

            if already_tried_large:
                # Move the input to failed prefix for manual review
                failed_key = f"{FAILED_PREFIX}{suffix}"
                s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": dest_key_inputs}, Key=failed_key)
                # Emit diagnostics of last reply to help debugging
                diag_prefix = f"{FAILED_PREFIX}diagnostics/"
                diag_key = f"{diag_prefix}{suffix.rsplit('/',1)[-1]}.txt"
                diag = {
                    "message": "Parsing failed diagnostics",
                    "failed_due_to_columns": failed_due_to_columns,
                    "last_error": last_error,
                    "attempts": attempt,
                }
                body = (json.dumps(diag, ensure_ascii=False) + "\n\n=== last_model_reply ===\n" + (last_reply if isinstance(last_reply, str) else ""))
                s3.put_object(Bucket=bucket, Key=diag_key, Body=body.encode('utf-8'), ContentType='text/plain')
                print(json.dumps({
                    "message": "Parsing failed after attempts; moved to failed prefix",
                    "failed_due_to_columns": failed_due_to_columns,
                    "error": last_error,
                    "failed_key": failed_key
                }))
            # Delete source after routing to large file or failed - processing is complete
            if source_to_delete:
                try:
                    s3.delete_object(Bucket=bucket, Key=source_to_delete)
                    print(json.dumps({"message": f"Deleted from {queue_name} after routing/failing", "key": source_to_delete}))
                except Exception:
                    pass

    return {"statusCode": 200, "body": json.dumps({"ok": True})}


