import os
import sys
import json
import re
import time
import base64
import boto3
import requests
from urllib.parse import unquote_plus
from datetime import datetime, timezone

s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")

BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
PENDING_PREFIX = os.getenv("PENDING_PREFIX", "Bill_Parser_1_Pending_Parsing/")
PARSED_INPUTS_PREFIX = os.getenv("PARSED_INPUTS_PREFIX", "Bill_Parser_2_Parsed_Inputs/")
PARSED_OUTPUTS_PREFIX = os.getenv("PARSED_OUTPUTS_PREFIX", "Bill_Parser_3_Parsed_Outputs/")
FAILED_PREFIX = os.getenv("FAILED_PREFIX", "Bill_Parser_Failed_Jobs/")
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

PROMPT = f"""
You are an expert utility-bill parser. Output ONLY pipe-separated (|) rows with exactly {len(COLUMNS)} fields ({PIPE_COUNT} pipes) in this order:
{' | '.join(COLUMNS)}
If no line items are found, output the single word: EMPTY.

Rules and standardizations:
- Utility Type must be standardized to one of EXACTLY these values: Electricity | Gas | Trash | Water | Sewer | Stormwater | HOA. Do NOT output any other value (e.g., Pass-through, Tax, Fees). If the charge is a component of a Water bill (e.g., taxes/fees/surcharges), still set Utility Type to Water.
- Meter Number: extract the service meter identifier if present, else leave blank.
- Meter Size: extract the meter size (e.g., 5/8", 1", etc.) if present, else leave blank.
- House Or Vacant: Output "Vacant" if the Service Address clearly contains an apartment/unit indicator (e.g., Apt, Apartment, Unit, Ste, Suite, #, Bldg/Building with unit). Otherwise output "House".

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
                return [str(x).strip() for x in parsed["keys"] if str(x).strip()][:3]
            # Look for key1/key2/key3
            collected = []
            for i in (1, 2, 3):
                v = parsed.get(f"key{i}")
                if v:
                    collected.append(str(v).strip())
            if collected:
                return collected[:3]
        elif isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()][:3]
    except Exception:
        pass
    # Fallback: plaintext separated by newline or comma
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
    else:
        parts = [p.strip() for p in raw.splitlines()]
    cleaned = [_sanitize_key(p) for p in parts]
    return [k for k in cleaned if k][:3]


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
                return [k for k in cleaned if k][:3]
            collected = []
            for i in (1, 2, 3):
                v = parsed.get(f"key{i}")
                if v:
                    collected.append(_sanitize_key(str(v)))
            if collected:
                return collected[:3]
        elif isinstance(parsed, list):
            cleaned = [_sanitize_key(str(x)) for x in parsed]
            return [k for k in cleaned if k][:3]
    except Exception:
        pass
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
    else:
        parts = [p.strip() for p in raw.splitlines()]
    return [p for p in parts if p][:3]


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
        "¦": "|", "｜": "|", "│": "|", "┃": "|", "¦": "|",
        "\u00a0": " ",  # non-breaking space
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    # Trim whitespace on each line
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(lines)


def call_gemini_with_retry_rest(api_key: str, pdf_bytes: bytes, source_name: str):
    attempts = 0
    prev_reply = ""
    rows: list[list[str]] = []
    failed_due_to_columns = False
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        prompt = PROMPT
        if attempts > 1 and prev_reply:
            excerpt = prev_reply[:1500]
            prompt += ("\n\nYou previously returned an incorrect number of columns. "
                       f"Each row must have exactly {len(COLUMNS)} fields. "
                       "Here is your last output (reference only):\n" + excerpt +
                       "\nNow output only corrected rows with the exact number of columns.")
        try:
            reply_text = call_gemini_rest(api_key, pdf_bytes, prompt)
        except Exception as e:
            prev_reply = str(e)
            time.sleep(3)
            continue

        prev_reply = reply_text
        if reply_text.upper() == "EMPTY":
            return [], False, prev_reply

        # parse lines, validate pipe count
        candidate_rows = []
        bad = False
        norm = _normalize_reply(reply_text)
        for line in norm.splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split('|')]
            if len(parts) != len(COLUMNS):
                bad = True
                # do not break; try to salvage other lines
                continue
            candidate_rows.append(parts)

        if candidate_rows:
            rows = [[*r, f"{source_name}"] for r in candidate_rows]
            return rows, False, prev_reply
        else:
            failed_due_to_columns = True
            time.sleep(2)

    return rows, failed_due_to_columns, prev_reply


def write_ndjson(bucket: str, key_stem: str, rows: list[list[str]], source_input_key: str):
    ndjson_lines = []
    now = datetime.now(timezone.utc)
    parsed_at_utc = now.isoformat()
    # helper to coerce various date strings into MM/DD/YYYY
    def fmt_us_date(s: str) -> str:
        if not s:
            return ""
        s = str(s).strip()
        # common patterns
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
        digits = re.sub(r"\D", "", s)
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
        if not key.startswith(PENDING_PREFIX):
            continue

        # Compute suffix and copy into Parsed_Inputs
        suffix = key[len(PENDING_PREFIX):]
        dest_key_inputs = f"{PARSED_INPUTS_PREFIX}{suffix}"
        # Wait for object to be available, then copy with retry
        if not _wait_for_object(bucket, key):
            print(json.dumps({"message": "Pending object not yet visible", "key": key}))
            continue
        if not _copy_with_retry(bucket, key, dest_key_inputs):
            print(json.dumps({"message": "Failed to move from Pending to Parsed_Inputs", "key": key}))
            continue
        # Delete original from Pending regardless of parse outcome (holding zone policy)
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass

        # Download the PDF
        obj = s3.get_object(Bucket=bucket, Key=dest_key_inputs)
        pdf_bytes = obj['Body'].read()

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
            out_key = write_ndjson(BUCKET, key_stem, rows, dest_key_inputs)
            print(json.dumps({"message": "Parsed and wrote NDJSON", "out_key": out_key, "rows": len(rows)}))
        else:
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

    return {"statusCode": 200, "body": json.dumps({"ok": True})}
