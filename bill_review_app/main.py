def _entrata_post_succeeded(resp_text: str) -> tuple[bool, str]:
    """Best-effort parse of Entrata response body to determine true success.
    Returns (success, reason). Treats HTTP 200 with embedded errors (e.g., duplicates) as failure.
    """
    try:
        t = (resp_text or "").strip()
        if not t:
            return False, "empty_response"
        # Try JSON first
        try:
            j = json.loads(t)
        except Exception:
            j = None
        if isinstance(j, dict):
            # Common patterns: { response: { result: { status: 'ok'|'error', message: '...' } } }
            resp = j.get("response") if isinstance(j.get("response"), dict) else j
            res = resp.get("result") if isinstance(resp.get("result"), dict) else resp
            status = str(res.get("status") or resp.get("status") or "").lower()
            msg = str(res.get("message") or resp.get("message") or "").lower()
            # Treat any explicit error status as failure
            if status in ("error", "fail", "failed"):
                return False, status or (msg or "error")
            # Scan for duplicate indicators
            blob = json.dumps(j, ensure_ascii=False).lower()
            if any(k in blob for k in ["duplicate", "already exists", "already posted", "invoice exists"]):
                return False, "duplicate"
            # Heuristic success
            if status in ("ok", "success"):
                return True, status or "ok"
            # If no obvious signals, fall back to keyword scan
        # Keyword scan on text
        low = t.lower()
        if any(k in low for k in ["duplicate", "already exists", "already posted", "error", "failed", "failure"]):
            # Prefer specific duplicate reason if present
            if "duplicate" in low or "already" in low:
                return False, "duplicate"
            return False, "error"
        # Default to success if none of the error markers found
        return True, "ok"
    except Exception:
        return False, "parse_error"

import os
import datetime as dt
from datetime import datetime
from typing import List, Dict, Any, Tuple
import json

import boto3
import snowflake.connector
from fastapi import FastAPI, Request, Form, Body, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.middleware.gzip import GZipMiddleware
from urllib.parse import urlparse, unquote, parse_qs
import requests
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Response, Depends
from itsdangerous import TimestampSigner, BadSignature
from passlib.hash import bcrypt as passlib_bcrypt  # legacy; avoid calling due to backend issues
import bcrypt as pybcrypt
import hashlib
import auth  # New role-based authentication module
from itertools import islice
import re
import gzip
from io import BytesIO
from zoneinfo import ZoneInfo
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# -------- Config --------
BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
ENRICH_PREFIX = os.getenv("ENRICH_PREFIX", "Bill_Parser_4_Enriched_Outputs/")
OVERRIDE_PREFIX = os.getenv("OVERRIDE_PREFIX", "Bill_Parser_5_Overrides/")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
REVIEW_TABLE = os.getenv("REVIEW_TABLE", "jrk-bill-review")
REVIEW_QUEUE_URL = os.getenv("REVIEW_QUEUE_URL", "")
APP_SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")
SESSION_COOKIE = "br_sess"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 3600
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "1") == "1"
DRAFTS_TABLE = os.getenv("DRAFTS_TABLE", "jrk-bill-drafts")
PRE_ENTRATA_PREFIX = os.getenv("PRE_ENTRATA_PREFIX", "Bill_Parser_6_PreEntrata_Submission/")
EXPORTS_ROOT = os.getenv("EXPORTS_ROOT", "Bill_Parser_Enrichment/exports/")
# Catalog sources from exports
DIM_VENDOR_PREFIX = os.getenv("DIM_VENDOR_PREFIX", EXPORTS_ROOT + "dim_vendor/")
DIM_PROPERTY_PREFIX = os.getenv("DIM_PROPERTY_PREFIX", EXPORTS_ROOT + "dim_property/")
DIM_GL_PREFIX = os.getenv("DIM_GL_PREFIX", EXPORTS_ROOT + "dim_gl_account/")
DIM_UOM_PREFIX = os.getenv("DIM_UOM_PREFIX", EXPORTS_ROOT + "dim_uom_mapping/")
# Config storage (accounts to track)
CONFIG_BUCKET = os.getenv("CONFIG_BUCKET", BUCKET)
CONFIG_PREFIX = os.getenv("CONFIG_PREFIX", "Bill_Parser_Config/")
ACCOUNTS_TRACK_KEY = os.getenv("ACCOUNTS_TRACK_KEY", CONFIG_PREFIX + "accounts_to_track.json")
UBI_MAPPING_KEY = os.getenv("UBI_MAPPING_KEY", CONFIG_PREFIX + "ubi_mapping.json")
REWORK_PREFIX = os.getenv("REWORK_PREFIX", "Bill_Parser_Rework_Input/")
SHORT_TABLE = os.getenv("SHORT_TABLE", "jrk-url-short")
CONFIG_TABLE = os.getenv("CONFIG_TABLE", "jrk-bill-config")
DEBUG_TABLE = os.getenv("DEBUG_TABLE", "jrk-bill-review-debug")
STAGE4_PREFIX = os.getenv("STAGE4_PREFIX", "Bill_Parser_4_Enriched_Outputs/")
STAGE6_PREFIX = os.getenv("STAGE6_PREFIX", "Bill_Parser_6_PreEntrata_Submission/")
POST_ENTRATA_PREFIX = os.getenv("POST_ENTRATA_PREFIX", "Bill_Parser_7_PostEntrata_Submission/")
HIST_ARCHIVE_PREFIX = os.getenv("HIST_ARCHIVE_PREFIX", "Bill_Parser_99_Historical Archive/")
FAILED_JOBS_PREFIX = os.getenv("FAILED_JOBS_PREFIX", "Bill_Parser_Failed_Jobs/")
ERRORS_TABLE = os.getenv("ERRORS_TABLE", "jrk-bill-parser-errors")
MANUAL_ENTRIES_TABLE = os.getenv("MANUAL_ENTRIES_TABLE", "jrk-bill-manual-entries")
PARSED_INPUTS_PREFIX = os.getenv("PARSED_INPUTS_PREFIX", "Bill_Parser_2_Parsed_Inputs/")
REWORK_PREFIX = os.getenv("REWORK_PREFIX", "Bill_Parser_Rework_Input/")

# Use default credentials chain on AWS (App Runner)
s3 = boto3.client("s3", region_name=AWS_REGION)
ddb = boto3.client("dynamodb", region_name=AWS_REGION)
sqs = boto3.client("sqs", region_name=AWS_REGION)

# -------- App --------
app = FastAPI(title="Bill Review", version="1.0")

# Add GZip compression middleware to reduce response sizes (especially for JSON APIs)
app.add_middleware(GZipMiddleware, minimum_size=1000)

base_dir = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))

# simple in-memory cache
_CACHE: dict = {}
CACHE_TTL_SECONDS = 300  # 5 minutes for today's data
CACHE_TTL_PAST_DAYS = 3600  # 1 hour for past days (they change less frequently)

def _get_cache_ttl(y: str, m: str, d: str) -> int:
    """Return appropriate cache TTL - shorter for today, longer for past days."""
    try:
        today = dt.date.today()
        check_date = dt.date(int(y), int(m), int(d))
        if check_date >= today:
            return CACHE_TTL_SECONDS  # Today or future: short TTL
        return CACHE_TTL_PAST_DAYS  # Past days: longer TTL
    except Exception:
        return CACHE_TTL_SECONDS

def invalidate_day_cache(y: str, m: str, d: str):
    try:
        _CACHE.pop(("load_day", y, m, d), None)
    except Exception:
        pass

# -------- VACANT GL DESC Helpers --------
# GL codes that trigger the special VACANT description format
VACANT_GL_CODES = {
    "5705-0000": "VE",  # VACANT ELEC
    "5715-0000": "VG",  # VACANT GAS
    "5720-1000": "VW",  # VACANT WATER
    "5721-1000": "VS",  # VACANT SEWER
}
VACANT_NAME_CODES = {
    "VACANT ELEC": "VE",
    "VACANT ELECTRIC": "VE",
    "VACANT GAS": "VG",
    "VACANT WATER": "VW",
    "VACANT SEWER": "VS",
}

def _parse_service_address(addr: str) -> tuple[str, str, str]:
    """Parse service address into (street_num, street_letter, unit).

    Examples:
        "9436 North St APT 159" -> ("9436", "N", "159")
        "728 Franklin Ave Unit F316" -> ("728", "F", "F316")
        "123 Main Street #4A" -> ("123", "M", "4A")
    """
    import re
    addr = (addr or "").strip().upper()

    # Extract street number (first numeric sequence)
    street_num = ""
    num_match = re.match(r'^(\d+)', addr)
    if num_match:
        street_num = num_match.group(1)

    # Extract first letter of street name (word after street number)
    street_letter = ""
    # Skip the number and find the next word
    after_num = re.sub(r'^\d+\s*', '', addr)
    word_match = re.match(r'([A-Z])', after_num)
    if word_match:
        street_letter = word_match.group(1)

    # Extract unit number (after APT, UNIT, #, STE, SUITE, APARTMENT, BLDG)
    unit = ""
    unit_match = re.search(r'\b(?:APT|UNIT|#|STE|SUITE|APARTMENT|BLDG)\.?\s*([A-Z0-9-]+)', addr, re.I)
    if unit_match:
        unit = unit_match.group(1)

    return (street_num, street_letter, unit)

def _format_date_compact(date_str: str) -> str:
    """Convert date to compact M/D/YY format.

    Examples:
        "07/24/2025" -> "7/24/25"
        "2025-08-21" -> "8/21/25"
        "7/24/25" -> "7/24/25" (already compact)
    """
    if not date_str:
        return ""
    date_str = str(date_str).strip()

    # Try various date formats
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%Y/%m/%d"):
        try:
            dt_obj = datetime.strptime(date_str, fmt)
            return f"{dt_obj.month}/{dt_obj.day}/{dt_obj.strftime('%y')}"
        except ValueError:
            continue

    # If we can't parse, return as-is
    return date_str

def _get_vacant_code(gl_num: str, gl_name: str) -> str | None:
    """Return the VACANT utility code (VE, VG, VW, VS) if this is a VACANT GL, else None."""
    gl_num = (gl_num or "").strip()
    gl_name = (gl_name or "").strip().upper()

    # Check by GL number first
    if gl_num in VACANT_GL_CODES:
        return VACANT_GL_CODES[gl_num]

    # Check by GL name
    for name, code in VACANT_NAME_CODES.items():
        if name in gl_name:
            return code

    return None

def _build_vacant_gl_desc(rec: dict) -> str:
    """Build the special VACANT GL DESC format.

    Format: (M/D/YY-M/D/YY V[E/G/W/S] Street#Letter@Unit[!])
    Example: (7/24/25-8/21/25 VE 9436N@159)

    Lines with credits/transfers get ! at the end:
    - Balance Transfer, Credit Balance, CA Climate Credit, Credit Adjustment/Transfer
    """
    gl_num = str(rec.get("EnrichedGLAccountNumber") or "").strip()
    gl_name = str(rec.get("EnrichedGLAccountName") or "").strip()

    vacant_code = _get_vacant_code(gl_num, gl_name)
    if not vacant_code:
        return None  # Not a VACANT GL

    # Parse dates
    bps = _format_date_compact(rec.get("Bill Period Start") or "")
    bpe = _format_date_compact(rec.get("Bill Period End") or "")
    date_range = f"{bps}-{bpe}" if (bps or bpe) else ""

    # Parse service address
    addr = str(rec.get("Service Address") or "").strip()
    street_num, street_letter, unit = _parse_service_address(addr)
    addr_part = f"{street_num}{street_letter}@{unit}"

    # Check for credit/transfer line items that get ! suffix
    line_desc = str(rec.get("Line Item Description") or "").strip().upper()
    credit_keywords = [
        "BALANCE TRANSFER",
        "CREDIT BALANCE",
        "CA CLIMATE CREDIT",
        "CLIMATE CREDIT",
        "CREDIT ADJUSTMENT",
        "CREDIT TRANSFER",
    ]
    suffix = "!" if any(kw in line_desc for kw in credit_keywords) else ""

    return f"({date_range} {vacant_code} {addr_part}{suffix})"

# Entrata prototype import
try:
    from .entrata_send_invoices_prototype import (
        build_send_invoices_payload,
        load_vendor_cache,
        do_post,
    )
except Exception:
    # Support running as script
    from entrata_send_invoices_prototype import (
        build_send_invoices_payload,
        load_vendor_cache,
        do_post,
    )

# -------- Auth helpers --------
signer = TimestampSigner(APP_SECRET)

def set_session(resp: Response, username: str):
    token = signer.sign(username.encode("utf-8")).decode("utf-8")
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE_SECONDS, httponly=True, secure=SECURE_COOKIES, samesite="lax")

def get_current_user(request: Request) -> str | None:
    # Bypass auth entirely when DISABLE_AUTH=1 (for emergency/debug deploys)
    if os.getenv("DISABLE_AUTH", "0") == "1":
        return os.getenv("ADMIN_USER", "admin")
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        raw = signer.unsign(token, max_age=SESSION_MAX_AGE_SECONDS)
        return raw.decode("utf-8")
    except BadSignature:
        return None

def require_user(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        # Raise 307 to login
        from fastapi import HTTPException
        raise HTTPException(status_code=307, detail="redirect", headers={"Location": "/login"})
    return user

# -------- Helpers --------

def list_dates() -> List[Dict[str, Any]]:
    # Cache list_dates to avoid expensive S3 pagination on every parse page load
    cache_key = ("list_dates",)
    now = time.time()
    ent = _CACHE.get(cache_key)
    if ent and (now - ent.get("ts", 0) < CACHE_TTL_SECONDS):
        return ent.get("data", [])

    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET, Prefix=ENRICH_PREFIX)
    seen = set()
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            parts = key.split("/")
            try:
                y = next(p for p in parts if p.startswith("yyyy="))[5:]
                m = next(p for p in parts if p.startswith("mm="))[3:]
                d = next(p for p in parts if p.startswith("dd="))[3:]
                seen.add((y, m, d))
            except StopIteration:
                continue
    dates = sorted([{ "label": f"{y}-{m}-{d}", "tuple": (y,m,d) } for (y,m,d) in seen], key=lambda x: x["label"], reverse=True)

    _CACHE[cache_key] = {"ts": now, "data": dates}
    return dates


def _fetch_s3_file(key: str) -> List[Dict[str, Any]]:
    """Fetch a single S3 JSONL file and parse it. Used for parallel loading."""
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8", errors="ignore")
        rows = []
        for idx, line in enumerate(body.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rec["__s3_key__"] = key
                rec["__row_idx__"] = idx
                rec["__id__"] = f"{key}#{idx}"
                rows.append(rec)
            except Exception:
                continue
        return rows
    except Exception:
        return []


def load_day(y: str, m: str, d: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
    # cached by date - use longer TTL for past days
    _k = ("load_day", y, m, d)
    now = time.time()
    ent = _CACHE.get(_k)
    ttl = _get_cache_ttl(y, m, d)
    if not force_refresh and ent and (now - ent.get("ts", 0) < ttl):
        return ent.get("data", [])
    prefix = f"{ENRICH_PREFIX}yyyy={y}/mm={m}/dd={d}/"
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET, Prefix=prefix)

    # Collect all keys first
    keys = []
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith(".jsonl"):
                keys.append(key)

    # Fetch files in parallel (up to 50 concurrent requests)
    rows: List[Dict[str, Any]] = []
    if keys:
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = {executor.submit(_fetch_s3_file, key): key for key in keys}
            for future in as_completed(futures):
                try:
                    file_rows = future.result()
                    rows.extend(file_rows)
                except Exception:
                    pass

    _CACHE[_k] = {"ts": now, "data": rows}
    return rows


def pdf_id_from_key(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def line_id_from(key: str, idx: int) -> str:
    return f"{pdf_id_from_key(key)}#{idx}"


def write_overrides(y: str, m: str, d: str, overrides: List[Dict[str, Any]]) -> str | None:
    if not overrides:
        return None
    ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_prefix = f"{OVERRIDE_PREFIX}yyyy={y}/mm={m}/dd={d}/"
    out_key = f"{out_prefix}overrides_{ts}.jsonl"
    body = "\n".join(json.dumps(o, ensure_ascii=False) for o in overrides) + "\n"
    s3.put_object(Bucket=BUCKET, Key=out_key, Body=body.encode("utf-8"), ContentType="application/x-ndjson")
    return out_key


def _write_jsonl(prefix: str, y: str, m: str, d: str, basename: str, rows: List[Dict[str, Any]]) -> str:
    ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_prefix = f"{prefix}yyyy={y}/mm={m}/dd={d}/"
    out_key = f"{out_prefix}{basename}_{ts}.jsonl"
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    s3.put_object(Bucket=BUCKET, Key=out_key, Body=body.encode('utf-8'), ContentType='application/x-ndjson')
    return out_key


def put_status(id_: str, status: str, user: str):
    now_iso = dt.datetime.utcnow().isoformat()
    item = {
        "pk": {"S": id_},
        "status": {"S": status},
        "updated_by": {"S": user},
        "updated_utc": {"S": now_iso}
    }
    # Add submitted_at timestamp when marking as Submitted
    if status == "Submitted":
        item["submitted_at"] = {"S": now_iso}
    ddb.put_item(TableName=REVIEW_TABLE, Item=item)


def get_draft(pdf_id: str, line_id: str, user: str) -> Dict[str, Any] | None:
    pk = f"draft#{pdf_id}#{line_id}#{user}"
    resp = ddb.get_item(TableName=DRAFTS_TABLE, Key={"pk": {"S": pk}})
    item = resp.get("Item")
    if not item:
        return None
    out = {k: list(v.values())[0] for k, v in item.items()}
    if "fields" in out:
        try:
            out["fields"] = json.loads(out["fields"]) if isinstance(out["fields"], str) else out["fields"]
        except Exception:
            out["fields"] = {}
    return out


def _extract_ymd_from_key(key: str) -> tuple[str, str, str]:
    # Try yyyy=YYYY/mm=MM/dd=DD pattern first
    parts = key.split('/')
    try:
        y = next(p for p in parts if p.startswith('yyyy='))[5:]
        m = next(p for p in parts if p.startswith('mm='))[3:]
        d = next(p for p in parts if p.startswith('dd='))[3:]
        if y and m and d:
            return y, m, d
    except StopIteration:
        pass
    # Try YYYY/MM/DD pattern under stage root
    for i in range(len(parts) - 3):
        if parts[i].isdigit() and len(parts[i]) == 4 and parts[i+1].isdigit() and parts[i+2].isdigit():
            return parts[i], parts[i+1], parts[i+2]
    today = dt.datetime.utcnow()
    return today.strftime('%Y'), today.strftime('%m'), today.strftime('%d')


def _rewrite_status(rows: list[dict], status: str) -> list[dict]:
    out = []
    for r in rows:
        if isinstance(r, dict):
            r2 = dict(r)
            r2['Status'] = status
            out.append(r2)
    return out


def _basename_from_key(key: str) -> str:
    return os.path.basename(key).rsplit('/', 1)[-1].replace('/', '_')


# Fields added by update-line-item that should NOT affect the hash
# These fields are volatile and added after initial parsing
_VOLATILE_LINE_FIELDS = {
    "Charge Code",
    "Charge Code Source",
    "Charge Code Overridden",
    "Charge Code Override Reason",
    "Mapped Utility Name",
    "Current Amount",
    "Amount Overridden",
    "Amount Override Reason",
    "Is Excluded From UBI",
    "Exclusion Reason",
    "is_excluded_from_ubi",
    "exclusion_reason",
}


def _compute_stable_line_hash(rec: dict) -> str:
    """Compute a stable hash for a line item, excluding volatile fields.

    This ensures that adding/removing charge codes via refreshGLMappings
    doesn't change the hash and cause bills to disappear from unassigned view.
    """
    import hashlib
    # Create a copy without volatile fields
    stable_rec = {k: v for k, v in rec.items() if k not in _VOLATILE_LINE_FIELDS}
    line_data = json.dumps(stable_rec, sort_keys=True)
    return hashlib.sha256(line_data.encode()).hexdigest()


def _try_load_pdf_b64(row: dict) -> tuple[str, str, str] | None:
    """Attempt to locate a PDF in S3 using row fields and return (b64, filename, url?)."""
    # Prefer explicit keys
    cand = row.get("__pdf_s3_key__") or row.get("source_input_key") or row.get("PDF_LINK") or row.get("pdfKey") or ""
    if isinstance(cand, str) and cand:
        key = cand
        # Normalize possible absolute URLs back to key
        if key.startswith("http://") or key.startswith("https://"):
            # We could resolve and parse but most of our links are direct S3 key paths; fall back to path part
            try:
                parsed = urlparse(key)
                key = parsed.path.lstrip('/')
            except Exception:
                key = cand
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
            import base64
            raw = obj["Body"].read()
            b64 = base64.b64encode(raw).decode('ascii')
            fname = os.path.basename(key) or "invoice.pdf"
            return (b64, fname, "")
        except Exception:
            return None
    return None


@app.post("/api/post_to_entrata")
def api_post_to_entrata(request: Request, keys: str = Form(...), vendor_overrides: str | None = Form(None), post_month: str | None = Form(None), post_month_date: str | None = Form(None), user: str = Depends(require_user)):
    try:
        # Use ||| as delimiter to support filenames with commas (e.g., "San Francisco Water, Power and Sewer")
        sel = [k.strip() for k in (keys or '').split('|||') if k.strip()]
        if not sel:
            return JSONResponse({"error": "no_keys"}, status_code=400)
        cache = load_vendor_cache()
        # Parse optional vendor overrides { vendorId: locationId }
        overrides: dict[str, str] = {}
        try:
            if vendor_overrides:
                ov = json.loads(vendor_overrides)
                if isinstance(ov, dict):
                    overrides = {str(k): str(v) for k, v in ov.items() if v}
        except Exception:
            pass
        updated = 0
        errors: list[dict] = []
        unresolved: list[dict] = []
        results: list[dict] = []
        # Preload GL override helpers once
        gl_num_to_id = _load_gl_number_to_id_map()
        gl_name_to_id = _load_gl_name_to_id_map()
        att_index = _index_accounts_to_track_by_key()
        for key in sel:
            # Read rows from S3
            rows = _read_json_records_from_s3([key])
            if not rows:
                errors.append({"key": key, "error": "empty_rows"}); continue
            # Attach PDF (best-effort) to the first row for inclusion in payload
            try:
                pdf_trip = _try_load_pdf_b64(rows[0]) if isinstance(rows[0], dict) else None
                if pdf_trip:
                    b64, fname, url = pdf_trip
                    rows[0]["__pdf_b64__"] = b64
                    rows[0]["__pdf_filename__"] = fname
                    if url:
                        rows[0]["__pdf_url__"] = url
            except Exception:
                pass
            # Apply GL override for this (propertyId,vendorId,accountNumber) if present in Accounts-To-Track
            try:
                if isinstance(rows[0], dict):
                    pid = str(rows[0].get("EnrichedPropertyID") or rows[0].get("Property Id") or rows[0].get("PropertyID") or rows[0].get("PropertyId") or "").strip()
                    vid = str(rows[0].get("EnrichedVendorID") or rows[0].get("Vendor ID") or rows[0].get("VendorID") or "").strip()
                    acct = str(rows[0].get("Account Number") or rows[0].get("AccountNumber") or "").strip()
                    cfg = att_index.get((pid, vid, acct))
                    if cfg and cfg.get("glAccountNumber"):
                        wanted_num = str(cfg.get("glAccountNumber") or "").strip()
                        gid = gl_num_to_id.get(wanted_num)
                        if gid:
                            for r in rows:
                                if isinstance(r, dict):
                                    r["EnrichedGLAccountID"] = gid
            except Exception:
                pass

            # Recompute GL DESC_NEW with the latest format to avoid stale descriptions from older Stage 6 files
            try:
                def _norm(v: Any) -> str:
                    return (str(v or "").strip())
                def _rebuild_desc(rec: dict) -> str:
                    # Check for VACANT GL - use special format
                    vacant_desc = _build_vacant_gl_desc(rec)
                    if vacant_desc:
                        return vacant_desc

                    # HOUSE format (standard)
                    addr = _norm(rec.get("Service Address")).upper()
                    acct = _norm(rec.get("Account Number"))
                    li_acct = _norm(rec.get("Line Item Account Number"))
                    meter = _norm(rec.get("Meter Number"))
                    desc = _norm(rec.get("Line Item Description")).upper()
                    cons = _norm(rec.get("ENRICHED CONSUMPTION") or rec.get("Consumption Amount"))
                    uom = _norm(rec.get("ENRICHED UOM") or rec.get("Unit of Measure")).upper()
                    bps = _norm(rec.get("Bill Period Start"))
                    bpe = _norm(rec.get("Bill Period End"))
                    rng = f"{bps}-{bpe}" if (bps or bpe) else ""
                    parts = [desc, rng, addr, acct, li_acct, meter, cons, uom]
                    return " | ".join(parts)
                for r in rows:
                    if isinstance(r, dict):
                        r["GL DESC_NEW"] = _rebuild_desc(r)
            except Exception:
                pass

            # Honor user edits from Parse: prefer GL Account Name over Number, then fallback to Number
            # This ensures explicit Name changes win even if the Number was not updated.
            try:
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    glname = str(r.get("EnrichedGLAccountName") or r.get("GL Account Name") or "").strip()
                    glnum = str(r.get("EnrichedGLAccountNumber") or r.get("GL Account Number") or "").strip()
                    # 1) Try by Name first (user-facing control in Parse)
                    if glname:
                        gid2 = gl_name_to_id.get(glname.upper())
                        if gid2:
                            r["EnrichedGLAccountID"] = gid2
                    # 2) If still no ID, try by Number
                    if not r.get("EnrichedGLAccountID") and glnum:
                        gid = gl_num_to_id.get(glnum)
                        if gid:
                            r["EnrichedGLAccountID"] = gid
            except Exception:
                pass

            # Resolver: must have exactly one location for vendor
            def resolver(vendor_id: str) -> str:
                vid = str(vendor_id)
                # 0) Check if EnrichedVendorLocationID is already set (user selected from dropdown)
                if rows and isinstance(rows[0], dict):
                    saved_loc = str(rows[0].get("EnrichedVendorLocationID") or "").strip()
                    if saved_loc:
                        return saved_loc
                # 1) explicit override wins
                if vid in overrides and overrides[vid]:
                    return overrides[vid]
                locs = cache.get(vid, []) if isinstance(cache, dict) else []
                # 0 locations: vendor ID not found in cache - user needs to check their vendor selection
                if len(locs) == 0:
                    errors.append({"key": key, "error": f"Vendor ID '{vid}' not found in vendor cache. Please verify the correct vendor is selected in the Review/Parse page."})
                    raise ValueError(f"vendor_not_found:{vid}")
                # 1 location: auto-assign it
                if len(locs) == 1:
                    # Handle both old format (string) and new format (dict with id/name)
                    loc = locs[0]
                    return loc["id"] if isinstance(loc, dict) else str(loc)
                # 2+ locations: prompt user to select
                # locs is now [{id, name}, ...] for frontend to display
                unresolved.append({
                    "key": key,
                    "vendorId": vid,
                    "choices": locs
                })
                # raise to break out of build
                raise ValueError(f"vendor_location_unresolved:{vid}:{len(locs)}")

            try:
                # Prefer MM/YYYY if provided; otherwise accept a date (YYYY-MM-DD) and extract month
                pm_arg = None
                if post_month and isinstance(post_month, str) and '/' in post_month:
                    # Convert MM/YYYY -> pseudo date as first of month for parser
                    try:
                        mm, yyyy = post_month.split('/')
                        pm_arg = f"{yyyy}-{mm}-01"
                    except Exception:
                        pm_arg = None
                if not pm_arg and post_month_date:
                    pm_arg = post_month_date
                payload = build_send_invoices_payload(rows, resolver, post_month_date=pm_arg)
            except Exception as e:
                # If unresolved locations exist, we will return a promptable response instead of erroring out
                if any(isinstance(u, dict) and u.get("key") == key for u in unresolved):
                    continue
                errors.append({"key": key, "error": f"payload_build_failed: {e}"}); continue

            ok, text = do_post(payload, dry_run=False)
            # Parse body to detect silent failures (e.g., duplicates) even on HTTP 200
            succ, reason = _entrata_post_succeeded(text if isinstance(text, str) else str(text)) if ok else (False, "http_error")
            if not succ:
                errors.append({"key": key, "error": f"entrata_post_failed:{reason}", "response": (text[:2000] if isinstance(text, str) else str(text))})
                results.append({"key": key, "posted": False, "moved": False})
                continue
            # Count successful Entrata post immediately
            updated += 1

            # Move the JSONL to POST_ENTRATA_PREFIX with Status=Posted
            try:
                y, m, d = _extract_ymd_from_key(key)
                base = _basename_from_key(key)
                new_key = _write_jsonl(POST_ENTRATA_PREFIX, y, m, d, base.replace('.jsonl',''), _rewrite_status(rows, 'Posted'))
                # Delete original
                s3.delete_object(Bucket=BUCKET, Key=key)
                results.append({"key": key, "posted": True, "moved": True, "newKey": new_key})
            except Exception as e:
                errors.append({"key": key, "error": f"move_failed: {e}"})
                results.append({"key": key, "posted": True, "moved": False})
                continue
        # If any unresolved vendor locations were discovered and not satisfied by overrides, prompt client
        if unresolved and not overrides:
            return JSONResponse({
                "ok": False,
                "message": "vendor_locations_needed",
                "unresolved": unresolved,
                "errors": errors
            }, status_code=400)
        # Invalidate TRACK cache so POSTED appears immediately after posting
        try:
            _TRACK_CACHE.clear(); _TRACK_CACHE_TS.clear()
        except Exception:
            pass
        return {"ok": True, "updated": updated, "errors": errors, "unresolved": unresolved, "results": results}
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.post("/api/advance_to_post_stage")
def api_advance_to_post_stage(keys: str = Form(...), user: str = Depends(require_user)):
    """Move selected pre-Entrata merged JSONL files to Post-Entrata stage WITHOUT posting.
    Does not modify Status; simply re-writes the JSONL under POST_ENTRATA_PREFIX and deletes the source.
    Records PostedBy and PostedAt for metrics tracking.
    """
    try:
        # Use ||| as delimiter to support filenames with commas (e.g., "San Francisco Water, Power and Sewer")
        sel = [k.strip() for k in (keys or '').split('|||') if k.strip()]
        if not sel:
            return JSONResponse({"error": "no_keys"}, status_code=400)
        results: list[dict] = []
        errors: list[dict] = []
        posted_at = dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
        for key in sel:
            try:
                rows = _read_json_records_from_s3([key])
                if not rows:
                    errors.append({"key": key, "error": "empty_rows"}); continue
                y, m, d = _extract_ymd_from_key(key)
                base = _basename_from_key(key)
                # Add PostedBy and PostedAt to each row for metrics tracking
                for row in rows:
                    row["PostedBy"] = user
                    row["PostedAt"] = posted_at
                new_key = _write_jsonl(POST_ENTRATA_PREFIX, y, m, d, base.replace('.jsonl',''), rows)
                s3.delete_object(Bucket=BUCKET, Key=key)
                results.append({"key": key, "moved": True, "newKey": new_key})
            except Exception as e:
                errors.append({"key": key, "error": str(e)})
        return {"ok": True, "results": results, "errors": errors}
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.post("/api/archive_parsed")
def api_archive_parsed(keys: str = Form(...), user: str = Depends(require_user)):
    """Move selected merged JSONL files to Historical Archive, partitioned by yyyy/mm/dd.
    Works for any source stage. Content is preserved. Source is deleted after archive write.
    """
    try:
        # Use ||| as delimiter to support filenames with commas (e.g., "San Francisco Water, Power and Sewer")
        sel = [k.strip() for k in (keys or '').split('|||') if k.strip()]
        if not sel:
            return JSONResponse({"error": "no_keys"}, status_code=400)
        results: list[dict] = []
        errors: list[dict] = []
        for key in sel:
            try:
                # Read entire object text, split into JSON lines
                body = _read_s3_text(BUCKET, key)
                rows = []
                for ln in body.splitlines():
                    ln = (ln or '').strip()
                    if not ln:
                        continue
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        pass
                if not rows:
                    errors.append({"key": key, "error": "empty_rows"}); continue
                y, m, d = _extract_ymd_from_key(key)
                base = _basename_from_key(key)
                new_key = _write_jsonl(HIST_ARCHIVE_PREFIX, y, m, d, base.replace('.jsonl',''), rows)
                s3.delete_object(Bucket=BUCKET, Key=key)
                results.append({"key": key, "archived": True, "newKey": new_key})
            except Exception as e:
                errors.append({"key": key, "error": str(e)})
        return {"ok": True, "results": results, "errors": errors}
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)

# Month-level listing: list once per month prefix rather than per day
def _iter_stage_objects_by_month(prefix_root: str, months: List[dt.date]):
    seen: set[str] = set()
    for md in months:
        y = md.year; m = md.month
        prefixes = [
            f"{prefix_root}yyyy={y}/mm={m:02d}/",
            f"{prefix_root}{y}/{m:02d}/",
        ]
        for p in prefixes:
            try:
                resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=p, MaxKeys=2000)
                for obj in resp.get("Contents", []) or []:
                    k = obj.get("Key", "")
                    if k and k not in seen:
                        seen.add(k); yield k
            except Exception:
                pass


def put_draft(pdf_id: str, line_id: str, user: str, fields: Dict[str, Any], date: str, invoice: str):
    pk = f"draft#{pdf_id}#{line_id}#{user}"
    ddb.put_item(
        TableName=DRAFTS_TABLE,
        Item={
            "pk": {"S": pk},
            "pdf_id": {"S": pdf_id},
            "line_id": {"S": line_id},
            "user": {"S": user},
            "date": {"S": date},
            "invoice": {"S": str(invoice)},
            "fields": {"S": json.dumps(fields, ensure_ascii=False)},
            "updated_utc": {"S": dt.datetime.utcnow().isoformat()}
        }
    )


def _parse_s3_from_url(url: str) -> tuple[str, str] | None:
    """Try to extract (bucket, key) from a presigned S3 URL.
    Supports both bucket.s3.amazonaws.com/key and s3.amazonaws.com/bucket/key formats.
    """
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        path = p.path.lstrip('/')
        if host.endswith('.s3.amazonaws.com'):
            # bucket.s3.amazonaws.com/key
            bucket = host.split('.s3.amazonaws.com')[0]
            key = unquote(path)
            return bucket, key
        if host == 's3.amazonaws.com':
            # s3.amazonaws.com/bucket/key
            parts = path.split('/', 1)
            if len(parts) == 2:
                return parts[0], unquote(parts[1])
        # S3 virtual-hosted-style in regional endpoints e.g. bucket.s3.us-east-1.amazonaws.com
        if '.s3.' in host and host.endswith('.amazonaws.com'):
            bucket = host.split('.s3.')[0]
            return bucket, unquote(path)
    except Exception:
        pass
    return None


def _resolve_final_url(url: str) -> str:
    """Follow redirects (e.g., short links) to the final URL. Use GET for better compatibility."""
    try:
        r = requests.get(url, allow_redirects=True, timeout=8, stream=True)
        return r.url
    except Exception:
        return url


# -------- Routes (HTML) --------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "prefill_user": ""})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Login with new role-based authentication system."""
    try:
        user = auth.authenticate(username, password)
        if not user:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Invalid email or password",
                "prefill_user": username
            }, status_code=401)

        # Check if user must change password
        if user.get("must_change_password"):
            # Redirect to change password page
            resp = RedirectResponse(url="/change-password", status_code=302)
            set_session(resp, username)
            return resp

        resp = RedirectResponse(url="/", status_code=302)
        set_session(resp, username)
        return resp
    except Exception as e:
        print(f"[LOGIN] Error: {e}")
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid email or password",
            "prefill_user": username
        }, status_code=401)

@app.get("/change-password", response_class=HTMLResponse)
def change_password_form(request: Request, user: str = Depends(require_user)):
    """Show change password form."""
    return templates.TemplateResponse("change_password.html", {"request": request, "user": user})


@app.post("/change-password")
async def change_password(request: Request, current_password: str = Form(...), new_password: str = Form(...), confirm_password: str = Form(...)):
    """Change user password."""
    user_id = get_current_user(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)

    if new_password != confirm_password:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user_id,
            "error": "Passwords do not match"
        }, status_code=400)

    if len(new_password) < 8:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user_id,
            "error": "Password must be at least 8 characters"
        }, status_code=400)

    # Verify current password
    user = auth.authenticate(user_id, current_password)
    if not user:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user_id,
            "error": "Current password is incorrect"
        }, status_code=401)

    # Update password
    if auth.update_password(user_id, new_password):
        return RedirectResponse("/", status_code=302)
    else:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user_id,
            "error": "Failed to update password"
        }, status_code=500)


@app.get("/config/users", response_class=HTMLResponse)
def users_page(request: Request, user: str = Depends(require_user)):
    """User management page (System Admins only)."""
    user_data = auth.get_user(user)
    if not user_data or user_data.get("role") != "System_Admins":
        return templates.TemplateResponse("error.html", {
            "request": request,
            "user": user,
            "error": "Access denied. System Admins only."
        }, status_code=403)

    return templates.TemplateResponse("users.html", {
        "request": request,
        "user": user,
        "user_role": user_data.get("role"),
        "roles": auth.ROLES
    })


@app.get("/api/users")
def api_list_users(user: str = Depends(require_user)):
    """API endpoint to list all users."""
    user_data = auth.get_user(user)
    if not user_data or user_data.get("role") != "System_Admins":
        return JSONResponse({"error": "Access denied"}, status_code=403)

    users = auth.list_users()
    return {"users": users}


@app.post("/api/users")
async def api_create_user(request: Request, admin_user: str = Depends(require_user)):
    """API endpoint to create a new user."""
    user_data = auth.get_user(admin_user)
    if not user_data or user_data.get("role") != "System_Admins":
        return JSONResponse({"error": "Access denied"}, status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    user_id = payload.get("user_id", "").strip()
    password = payload.get("password", "").strip()
    role = payload.get("role", "").strip()
    full_name = payload.get("full_name", "").strip()

    if not user_id or not password or not role or not full_name:
        return JSONResponse({"error": "All fields required"}, status_code=400)

    if role not in auth.ROLES:
        return JSONResponse({"error": "Invalid role"}, status_code=400)

    if len(password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters"}, status_code=400)

    if auth.create_user(user_id, password, role, full_name, created_by=admin_user):
        return {"ok": True}
    else:
        return JSONResponse({"error": "User already exists"}, status_code=400)


@app.post("/api/users/{user_id}/disable")
def api_disable_user(user_id: str, admin_user: str = Depends(require_user)):
    """API endpoint to disable a user."""
    user_data = auth.get_user(admin_user)
    if not user_data or user_data.get("role") != "System_Admins":
        return JSONResponse({"error": "Access denied"}, status_code=403)

    if auth.disable_user(user_id):
        return {"ok": True}
    else:
        return JSONResponse({"error": "Failed to disable user"}, status_code=500)


@app.post("/api/users/{user_id}/enable")
def api_enable_user(user_id: str, admin_user: str = Depends(require_user)):
    """API endpoint to enable a user."""
    user_data = auth.get_user(admin_user)
    if not user_data or user_data.get("role") != "System_Admins":
        return JSONResponse({"error": "Access denied"}, status_code=403)

    if auth.enable_user(user_id):
        return {"ok": True}
    else:
        return JSONResponse({"error": "Failed to enable user"}, status_code=500)


@app.post("/api/users/{user_id}/reset-password")
async def api_reset_password(user_id: str, request: Request, admin_user: str = Depends(require_user)):
    """API endpoint to reset a user's password."""
    user_data = auth.get_user(admin_user)
    if not user_data or user_data.get("role") != "System_Admins":
        return JSONResponse({"error": "Access denied"}, status_code=403)

    try:
        payload = await request.json()
        new_password = payload.get("new_password", "").strip()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    if not new_password or len(new_password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters"}, status_code=400)

    if auth.update_password(user_id, new_password):
        # Set must_change_password flag
        try:
            ddb.update_item(
                TableName=auth.USERS_TABLE,
                Key={"user_id": {"S": user_id}},
                UpdateExpression="SET must_change_password = :must_change",
                ExpressionAttributeValues={":must_change": {"BOOL": True}}
            )
        except Exception as e:
            print(f"[API] Error setting must_change_password for {user_id}: {e}")
        return {"ok": True}
    else:
        return JSONResponse({"error": "Failed to reset password"}, status_code=500)


@app.post("/api/users/{user_id}/role")
async def api_change_role(user_id: str, request: Request, admin_user: str = Depends(require_user)):
    """API endpoint to change a user's role."""
    user_data = auth.get_user(admin_user)
    if not user_data or user_data.get("role") != "System_Admins":
        return JSONResponse({"error": "Access denied"}, status_code=403)

    try:
        payload = await request.json()
        new_role = payload.get("role", "").strip()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Validate role
    valid_roles = {"System_Admins", "UBI_Admins", "Utility_APs"}
    if new_role not in valid_roles:
        return JSONResponse({"error": f"Invalid role. Must be one of: {', '.join(valid_roles)}"}, status_code=400)

    try:
        ddb.update_item(
            TableName=auth.USERS_TABLE,
            Key={"user_id": {"S": user_id}},
            UpdateExpression="SET #r = :role",
            ExpressionAttributeNames={"#r": "role"},
            ExpressionAttributeValues={":role": {"S": new_role}}
        )
        return {"ok": True}
    except Exception as e:
        print(f"[API] Error changing role for {user_id}: {e}")
        return JSONResponse({"error": "Failed to change role"}, status_code=500)


@app.get("/health")
def health():
    """Simple healthcheck endpoint for App Runner HTTP checks."""
    return {"ok": True}


@app.post("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    # clear cookie
    resp.delete_cookie(SESSION_COOKIE)
    return resp
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("landing.html", {"request": request, "user": user})


@app.get("/parse", response_class=HTMLResponse)
def parse_dashboard(request: Request, user: str = Depends(require_user)):
    """Parse dashboard with pagination. Shows last 8 days by default with 'Load More' option."""
    # Cache the entire dashboard to avoid expensive S3 operations on every page load
    cache_key = ("parse_dashboard",)
    now = time.time()
    ent = _CACHE.get(cache_key)
    refresh = request.query_params.get("refresh") == "1"

    # Pagination params - default to first 8 days
    limit = int(request.query_params.get("limit", 8))
    offset = int(request.query_params.get("offset", 0))
    # Date range filter (optional)
    start_date = request.query_params.get("start", "")
    end_date = request.query_params.get("end", "")

    if not refresh and ent and (now - ent.get("ts", 0) < 600):  # 10 min cache
        all_day_cards = ent.get("data", [])
    else:
        dates = list_dates()
        all_day_cards = []
        for d in dates:
            y, m, dd = d["tuple"]
            counts = day_status_counts(y, m, dd)
            all_day_cards.append({
                "date": f"{y}-{m}-{dd}",
                "label": d["label"],
                "review": counts["REVIEW"],
                "partial": counts["PARTIAL"],
                "complete": counts["COMPLETE"],
            })
        _CACHE[cache_key] = {"ts": now, "data": all_day_cards}

    # Apply date range filter if provided
    filtered_cards = all_day_cards
    if start_date:
        filtered_cards = [c for c in filtered_cards if c["date"] >= start_date]
    if end_date:
        filtered_cards = [c for c in filtered_cards if c["date"] <= end_date]

    # Apply pagination
    total_count = len(filtered_cards)
    day_cards = filtered_cards[offset:offset + limit]
    has_more = (offset + limit) < total_count

    return templates.TemplateResponse("index.html", {
        "request": request,
        "days": day_cards,
        "user": user,
        "total_count": total_count,
        "has_more": has_more,
        "current_limit": limit,
        "current_offset": offset,
        "start_date": start_date,
        "end_date": end_date,
    })


@app.get("/input", response_class=HTMLResponse)
def input_view(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse("input.html", {"request": request, "user": user})


@app.get("/search", response_class=HTMLResponse)
def search_view(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse("search.html", {"request": request, "user": user})


@app.get("/api/search")
def api_search(
    request: Request,
    account: str = "",
    vendor: str = "",
    property: str = "",
    start_date: str = "",
    end_date: str = "",
    user: str = Depends(require_user)
):
    """Search across all parse dates for invoices matching criteria."""
    account = (account or "").strip().lower()
    vendor = (vendor or "").strip().lower()
    prop = (property or "").strip().lower()

    if not account and not vendor and not prop:
        return JSONResponse({"error": "At least one search term required"}, status_code=400)

    # Get all available dates
    dates = list_dates()

    # Filter by date range if provided
    if start_date:
        dates = [d for d in dates if f"{d['tuple'][0]}-{d['tuple'][1]}-{d['tuple'][2]}" >= start_date]
    if end_date:
        dates = [d for d in dates if f"{d['tuple'][0]}-{d['tuple'][1]}-{d['tuple'][2]}" <= end_date]

    results = []
    max_results = 500

    for d in dates:
        if len(results) >= max_results:
            break

        y, m, dd = d["tuple"]
        date_str = f"{y}-{m}-{dd}"

        try:
            rows = load_day(y, m, dd)
        except Exception:
            continue

        # Group rows by pdf_id to get unique invoices
        by_pdf: dict = {}
        for r in rows:
            key = r.get("__s3_key__", "")
            if not key:
                continue
            pid = pdf_id_from_key(key)
            if pid not in by_pdf:
                by_pdf[pid] = {"rows": [], "key": key}
            by_pdf[pid]["rows"].append(r)

        for pid, data in by_pdf.items():
            if len(results) >= max_results:
                break

            first_row = data["rows"][0]

            # Extract searchable fields - check multiple possible field names
            row_account = str(
                first_row.get("Account Number") or first_row.get("ACCOUNT_ID") or first_row.get("account_id") or
                first_row.get("Account ID") or first_row.get("ACCOUNT_NUMBER") or ""
            ).lower()
            row_vendor = str(
                first_row.get("EnrichedVendor") or first_row.get("Vendor Name") or first_row.get("VENDOR") or
                first_row.get("vendor") or first_row.get("Vendor") or first_row.get("VENDOR_NAME") or ""
            ).lower()
            row_property = str(
                first_row.get("EnrichedPropertyName") or first_row.get("EnrichedProperty") or first_row.get("PROPERTY") or
                first_row.get("property") or first_row.get("Property") or first_row.get("PROPERTY_NAME") or ""
            ).lower()

            # Check if matches search criteria
            matches = True
            if account and account not in row_account:
                matches = False
            if vendor and vendor not in row_vendor:
                matches = False
            if prop and prop not in row_property:
                matches = False

            if not matches:
                continue

            # Calculate total amount
            total = 0.0
            for r in data["rows"]:
                try:
                    amt = r.get("Line Item Charge") or r.get("AMOUNT") or r.get("amount") or r.get("Amount") or r.get("LINE_AMOUNT") or 0
                    total += float(amt)
                except (ValueError, TypeError):
                    pass

            # Get display values using same field lookups
            display_account = (
                first_row.get("Account Number") or first_row.get("ACCOUNT_ID") or first_row.get("account_id") or
                first_row.get("Account ID") or first_row.get("ACCOUNT_NUMBER") or ""
            )
            display_vendor = (
                first_row.get("EnrichedVendor") or first_row.get("Vendor Name") or first_row.get("VENDOR") or
                first_row.get("vendor") or first_row.get("Vendor") or first_row.get("VENDOR_NAME") or ""
            )
            display_property = (
                first_row.get("EnrichedPropertyName") or first_row.get("EnrichedProperty") or first_row.get("PROPERTY") or
                first_row.get("property") or first_row.get("Property") or first_row.get("PROPERTY_NAME") or ""
            )
            results.append({
                "date": date_str,
                "pdf_id": pid,
                "account_id": display_account,
                "vendor": display_vendor,
                "property": display_property,
                "amount": total
            })

    return {"results": results, "truncated": len(results) >= max_results}


# Default to the Pending_Parsing prefix to match the S3 event rule
INPUT_PREFIX = os.getenv("INPUT_PREFIX", "Bill_Parser_1_Pending_Parsing/")


@app.post("/api/upload_input")
def api_upload_input(file: UploadFile = File(...), user: str = Depends(require_user)):
    """Upload a PDF to Bill Parser 1 input location."""
    try:
        # Validate
        fname = file.filename or "uploaded.pdf"
        if not fname.lower().endswith(".pdf"):
            return JSONResponse({"error": "file must be a .pdf"}, status_code=400)
        # time + sanitize filename (no path separators)
        now = dt.datetime.utcnow()
        y = now.strftime('%Y'); m = now.strftime('%m'); d = now.strftime('%d')
        ts = now.strftime('%Y%m%dT%H%M%SZ')
        base = os.path.basename(fname).replace("\\", "_").replace("/", "_")
        # Always write flat (no yyyy/mm/dd) to trigger Bill Parser 1 S3 event rules
        key = f"{INPUT_PREFIX}{ts}_{base}"
        body = file.file.read()
        if not body:
            return JSONResponse({"error": "empty file"}, status_code=400)
        s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType=file.content_type or 'application/pdf')
        return {"ok": True, "key": key}
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.get("/post", response_class=HTMLResponse)
def post_view(request: Request, user: str = Depends(require_user)):
    """List merged pre-Entrata files (Bill_Parser_6_PreEntrata_Submission) with Title and Status.

    Totals loaded lazily via AJAX to speed up initial render.
    """
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET, Prefix=PRE_ENTRATA_PREFIX)
    items = []
    for page in pages:
        for obj in page.get("Contents", []) or []:
            k = obj.get("Key", "")
            if not k.lower().endswith('.jsonl'):
                continue
            size = int(obj.get('Size', 0) or 0)
            lm = obj.get('LastModified')
            last_mod = lm.isoformat() if lm else ''
            last_mod_ts = lm.timestamp() if lm else 0
            # Friendly PT time
            last_mod_human = ''
            try:
                if lm:
                    pac = lm.astimezone(ZoneInfo('America/Los_Angeles'))
                    last_mod_human = pac.strftime('%b %d, %Y %I:%M %p %Z')
            except Exception:
                last_mod_human = last_mod
            # PERF: Only read first line for title/status - total loaded lazily via AJAX
            title = ""
            status = ""
            total_amount = None  # None = not loaded yet, will be loaded via AJAX
            try:
                # Use S3 Select or range read to get just first line (much faster)
                # Read enough bytes to capture large first lines with enriched data
                obj_data = s3.get_object(Bucket=BUCKET, Key=k, Range='bytes=0-16384')
                txt = obj_data['Body'].read().decode('utf-8', errors='ignore')
                first_line = txt.split('\n')[0].strip() if txt else ''

                submitter = ""
                submitted_at = ""
                if first_line:
                    rec = json.loads(first_line)
                    acct = (rec.get('Account Number') or rec.get('AccountNumber') or '').strip()
                    prop = (rec.get('EnrichedPropertyName') or rec.get('Property Name') or '').strip()
                    vend = (rec.get('EnrichedVendorName') or rec.get('Vendor Name') or rec.get('Vendor') or '').strip()
                    parts = k[len(PRE_ENTRATA_PREFIX):].split('/')
                    y = parts[0].split('=')[1] if len(parts)>0 and '=' in parts[0] else ''
                    m = parts[1].split('=')[1] if len(parts)>1 and '=' in parts[1] else ''
                    d = parts[2].split('=')[1] if len(parts)>2 and '=' in parts[2] else ''
                    date_str = f"{y}-{m}-{d}" if y and m and d else ''
                    # Use pre-computed Title if available, otherwise build from fields
                    # Template expects 4 parts: Account | Property | Vendor | Date
                    existing_title = str(rec.get("Title", "")).strip()
                    if existing_title and " | " in existing_title:
                        title = existing_title
                    else:
                        title = f"{acct} | {prop} | {vend} | {date_str}"
                    status = str(rec.get("Status", "")).strip() or "Not Posted"
                    if not status:
                        status = "Not Posted"
                    submitter = str(rec.get("Submitter", "")).strip()
                    submitted_at = str(rec.get("SubmittedAt", "")).strip()
            except Exception:
                submitter = ""
                submitted_at = ""
                # Fallback: parse from filename (format: Property-Vendor-Account-dates_timestamp.jsonl)
                try:
                    basename = k.rsplit('/', 1)[-1].replace('.jsonl', '')
                    parts = k[len(PRE_ENTRATA_PREFIX):].split('/')
                    y = parts[0].split('=')[1] if len(parts) > 0 and '=' in parts[0] else ''
                    m = parts[1].split('=')[1] if len(parts) > 1 and '=' in parts[1] else ''
                    d = parts[2].split('=')[1] if len(parts) > 2 and '=' in parts[2] else ''
                    date_str = f"{y}-{m}-{d}" if y and m and d else ''
                    # Remove timestamp suffix and use filename as property fallback
                    name_part = basename.rsplit('_', 1)[0] if '_' in basename and len(basename.rsplit('_', 1)) > 1 else basename
                    # Always 4 parts: Account | Property | Vendor | Date
                    title = f" | {name_part} |  | {date_str}"
                except Exception:
                    title = f" | {k.rsplit('/', 1)[-1]} |  | "
            items.append({
                "key": k,
                "size": size,
                "last_modified": last_mod,
                "last_modified_ts": last_mod_ts,
                "last_modified_human": last_mod_human,
                "title": title,
                "status": status or "Not Posted",
                "total_amount": total_amount,  # Loaded lazily
                "submitter": submitter,
                "submitted_at": submitted_at
            })
    items.sort(key=lambda x: x.get("last_modified_ts") or 0, reverse=True)
    return templates.TemplateResponse("post.html", {"request": request, "items": items, "user": user})


@app.get("/api/post/total")
def api_post_total(key: str, user: str = Depends(require_user)):
    """PERF: Lazy-load total for a single POST file. Called via AJAX after page renders."""
    if not key:
        return {"total": 0}

    # Check per-file cache
    cache_key = f"post_total_{key}"
    now = time.time()
    ent = _CACHE.get(cache_key)
    if ent and (now - ent.get("ts", 0) < CACHE_TTL_SECONDS):
        return {"total": ent.get("data", 0)}

    total_amount = 0.0
    try:
        obj_data = s3.get_object(Bucket=BUCKET, Key=key)
        txt = obj_data['Body'].read().decode('utf-8', errors='ignore')
        for line in txt.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                charge_str = str(rec.get("Line Item Charge", "0") or "0").replace("$", "").replace(",", "").strip()
                total_amount += float(charge_str) if charge_str else 0.0
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
    except Exception as e:
        print(f"[POST TOTAL] Error reading {key}: {e}")

    _CACHE[cache_key] = {"ts": time.time(), "data": total_amount}
    return {"total": total_amount}


@app.get("/ubi", response_class=HTMLResponse)
def ubi_view(request: Request, user: str = Depends(require_user)):
    """Stub page to classify posted records for UBI with a selectable post date (1st of month)."""
    # Next few first-of-month options
    today = dt.date.today().replace(day=1)
    options = []
    for i in range(0, 6):
        mo = (today.month - 1 + i) % 12 + 1
        yr = today.year + ((today.month - 1 + i) // 12)
        options.append(f"{yr:04d}-{mo:02d}-01")
    return templates.TemplateResponse("ubi.html", {"request": request, "options": options, "user": user})


@app.get("/billback", response_class=HTMLResponse)
def billback_view(request: Request, user: str = Depends(require_user)):
    """BILLBACK page - manage invoices for tenant billback after posting to Entrata."""
    return templates.TemplateResponse("billback.html", {"request": request, "user": user})


@app.get("/billback/summary", response_class=HTMLResponse)
def billback_summary_view(request: Request, user: str = Depends(require_user)):
    """BILLBACK Summary page - view aggregated billback data by property, vendor, charge code, and month."""
    return templates.TemplateResponse("billback_summary.html", {"request": request, "user": user})


@app.get("/master-bills", response_class=HTMLResponse)
def master_bills_view(request: Request, user: str = Depends(require_user)):
    """UBI Master Bills page - view aggregated UBI billback master bills."""
    return templates.TemplateResponse("master-bills.html", {"request": request, "user": user})


@app.get("/ubi-batch", response_class=HTMLResponse)
def ubi_batch_view(request: Request, user: str = Depends(require_user)):
    """UBI Batch Management page - create and manage UBI billback batches for Snowflake export."""
    return templates.TemplateResponse("ubi-batch.html", {"request": request, "user": user})


@app.get("/history", response_class=HTMLResponse)
def history_view(request: Request, user: str = Depends(require_user)):
    """HISTORY page - view archived bills and posting history."""
    return templates.TemplateResponse("history.html", {"request": request, "user": user})


@app.get("/api/history/archived")
def api_history_archived(request: Request, user: str = Depends(require_user), start: str = "", end: str = ""):
    """Load archived line items from Historical Archive for history view."""
    if not start or not end:
        return JSONResponse({"error": "start and end dates required"}, status_code=400)

    try:
        # Parse dates
        start_date = dt.datetime.strptime(start, "%Y-%m-%d").date()
        end_date = dt.datetime.strptime(end, "%Y-%m-%d").date()

        # List all JSONL files in Historical Archive within date range
        line_items = []
        prefix = HIST_ARCHIVE_PREFIX

        # Iterate through dates in range
        current_date = start_date
        while current_date <= end_date:
            year = current_date.year
            month = current_date.month
            day = current_date.day

            # Build prefix for this day
            day_prefix = f"{prefix}yyyy={year}/mm={month:02d}/dd={day:02d}/"

            try:
                # List objects for this day
                paginator = s3.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=BUCKET, Prefix=day_prefix):
                    if 'Contents' not in page:
                        continue

                    for obj in page['Contents']:
                        key = obj['Key']
                        if not key.endswith('.jsonl'):
                            continue

                        # Download and parse JSONL
                        try:
                            response = s3.get_object(Bucket=BUCKET, Key=key)
                            content = response['Body'].read().decode('utf-8')

                            for line in content.strip().split('\n'):
                                if not line.strip():
                                    continue
                                try:
                                    item = json.loads(line)
                                    item['__s3_key__'] = key
                                    item['__archived_date__'] = f"{year}-{month:02d}-{day:02d}"
                                    line_items.append(item)
                                except Exception as e:
                                    print(f"Error parsing line from {key}: {e}")
                        except Exception as e:
                            print(f"Error reading {key}: {e}")
            except Exception as e:
                print(f"Error listing {day_prefix}: {e}")

            # Move to next day
            current_date += dt.timedelta(days=1)

        return {"line_items": line_items, "count": len(line_items)}

    except Exception as e:
        print(f"Error in api_history_archived: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/billback/archive")
async def api_billback_archive(request: Request, user: str = Depends(require_user)):
    """Archive selected billback line items from Stage 7 (PostEntrata) to Historical Archive."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    items = payload.get("items", [])
    if not items:
        return JSONResponse({"error": "No items provided"}, status_code=400)

    try:
        # Group items by S3 key
        from collections import defaultdict
        items_by_key = defaultdict(list)

        for item in items:
            s3_key = item.get("__s3_key__")
            if s3_key:
                items_by_key[s3_key].append(item)

        archived_count = 0
        errors = []

        # Archive each file
        for key, key_items in items_by_key.items():
            try:
                # Read the entire file
                body = _read_s3_text(BUCKET, key)
                all_rows = []
                remaining_rows = []

                # Parse all lines
                for ln in body.splitlines():
                    ln = (ln or '').strip()
                    if not ln:
                        continue
                    try:
                        row = json.loads(ln)
                        all_rows.append(row)
                    except Exception:
                        pass

                # Filter out the items to archive (match by account number and bill period)
                archived_items = []
                for row in all_rows:
                    should_archive = False
                    for item in key_items:
                        if (row.get('Account Number') == item.get('Account Number') and
                            row.get('Bill Period Start') == item.get('Bill Period Start') and
                            row.get('Bill Period End') == item.get('Bill Period End')):
                            should_archive = True
                            break

                    if should_archive:
                        archived_items.append(row)
                        archived_count += 1
                    else:
                        remaining_rows.append(row)

                # Archive the selected items
                if archived_items:
                    y, m, d = _extract_ymd_from_key(key)
                    base = _basename_from_key(key)
                    archive_key = _write_jsonl(HIST_ARCHIVE_PREFIX, y, m, d, base.replace('.jsonl',''), archived_items)
                    print(f"[BILLBACK ARCHIVE] Archived {len(archived_items)} items to {archive_key}")

                # Rewrite the original file with remaining items (or delete if empty)
                if remaining_rows:
                    y, m, d = _extract_ymd_from_key(key)
                    base = _basename_from_key(key)
                    _write_jsonl(POST_ENTRATA_PREFIX, y, m, d, base.replace('.jsonl',''), remaining_rows)
                else:
                    s3.delete_object(Bucket=BUCKET, Key=key)
                    print(f"[BILLBACK ARCHIVE] Deleted empty file {key}")

            except Exception as e:
                print(f"[BILLBACK ARCHIVE] Error archiving {key}: {e}")
                errors.append({"key": key, "error": str(e)})

        if errors:
            return {"ok": True, "archived": archived_count, "errors": errors}
        else:
            return {"ok": True, "archived": archived_count}

    except Exception as e:
        print(f"Error in api_billback_archive: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/billback/posted")
def api_billback_posted(request: Request, user: str = Depends(require_user), start: str = "", end: str = ""):
    """Load posted line items from Stage 7 (PostEntrata) for billback review."""
    if not start or not end:
        return JSONResponse({"error": "start and end dates required"}, status_code=400)

    try:
        # Parse dates
        start_date = dt.datetime.strptime(start, "%Y-%m-%d").date()
        end_date = dt.datetime.strptime(end, "%Y-%m-%d").date()

        # List all JSONL files in Stage 7 within date range
        line_items = []
        prefix = POST_ENTRATA_PREFIX

        # Iterate through dates in range
        current_date = start_date
        while current_date <= end_date:
            year = current_date.year
            month = current_date.month
            day = current_date.day

            # Build prefix for this day
            day_prefix = f"{prefix}yyyy={year}/mm={month:02d}/dd={day:02d}/"

            try:
                # List objects for this day
                paginator = s3.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=BUCKET, Prefix=day_prefix):
                    if 'Contents' not in page:
                        continue

                    for obj in page['Contents']:
                        key = obj['Key']
                        if not key.endswith('.jsonl'):
                            continue

                        # Download and parse JSONL
                        try:
                            response = s3.get_object(Bucket=BUCKET, Key=key)
                            content = response['Body'].read().decode('utf-8')

                            for line in content.strip().split('\n'):
                                if not line.strip():
                                    continue
                                try:
                                    item = json.loads(line)
                                    item['__s3_key__'] = key
                                    line_items.append(item)
                                except Exception as e:
                                    print(f"Error parsing line from {key}: {e}")
                        except Exception as e:
                            print(f"Error reading {key}: {e}")
            except Exception as e:
                print(f"Error listing {day_prefix}: {e}")

            # Move to next day
            current_date += dt.timedelta(days=1)

        return {"line_items": line_items, "count": len(line_items)}

    except Exception as e:
        print(f"Error in api_billback_posted: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/billback/save")
async def api_billback_save(request: Request, user: str = Depends(require_user)):
    """Save billback line items with notes and period assignments."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    line_items = payload.get("line_items", [])
    if not line_items:
        return JSONResponse({"error": "No line items provided"}, status_code=400)

    try:
        import uuid
        from decimal import Decimal

        for item in line_items:
            line_item_id = item.get("line_item_id") or str(uuid.uuid4())
            billback_period = item.get("billback_period")

            if not billback_period:
                continue  # Skip items without assigned period

            # Save to DynamoDB
            ddb.put_item(
                TableName="jrk-bill-billback-master",
                Item={
                    "billback_period": {"S": billback_period},
                    "line_item_id": {"S": line_item_id},
                    "property_id": {"S": str(item.get("property_id", ""))},
                    "property_name": {"S": str(item.get("property_name", ""))},
                    "vendor_id": {"S": str(item.get("vendor_id", ""))},
                    "vendor_name": {"S": str(item.get("vendor_name", ""))},
                    "account_number": {"S": str(item.get("account_number", ""))},
                    "service_address": {"S": str(item.get("service_address", ""))},
                    "utility_type": {"S": str(item.get("utility_type", ""))},
                    "charge_code": {"S": str(item.get("charge_code", ""))},
                    "bill_period_start": {"S": str(item.get("bill_period_start", ""))},
                    "bill_period_end": {"S": str(item.get("bill_period_end", ""))},
                    "line_charge": {"N": str(float(item.get("line_charge", 0)))},
                    "billback_amount": {"N": str(float(item.get("billback_amount", 0)))},
                    "notes": {"S": str(item.get("notes", ""))},
                    "status": {"S": "draft"},
                    "created_by": {"S": user},
                    "created_utc": {"S": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")},
                    "updated_by": {"S": user},
                    "updated_utc": {"S": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")},
                }
            )

        return {"ok": True, "saved": len([i for i in line_items if i.get("billback_period")])}
    except Exception as e:
        print(f"Error in api_billback_save: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/billback/submit")
async def api_billback_submit(request: Request, user: str = Depends(require_user)):
    """Submit billback line items to master bills (mark as submitted)."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    line_item_ids = payload.get("line_item_ids", [])
    if not line_item_ids:
        return JSONResponse({"error": "No line items provided"}, status_code=400)

    try:
        # First, query for all items to update
        submitted_count = 0
        for line_item_id in line_item_ids:
            # We need to find the item by line_item_id across all periods
            # This is inefficient, but works for now. Consider adding a GSI in production.

            # For now, we'll assume the client sends both line_item_id and billback_period
            # Let's update the API to require billback_period as well
            pass

        return {"ok": True, "submitted": submitted_count}
    except Exception as e:
        print(f"Error in api_billback_submit: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/billback/summary")
def api_billback_summary(user: str = Depends(require_user), group_by: str = "month"):
    """Get billback summary grouped by property, vendor, charge_code, or month."""
    if group_by not in ["property", "vendor", "charge_code", "month"]:
        return JSONResponse({"error": "Invalid group_by parameter"}, status_code=400)

    try:
        # Scan the billback master table
        response = ddb.scan(TableName="jrk-bill-billback-master")
        items = response.get("Items", [])

        print(f"[BILLBACK SUMMARY] Found {len(items)} items in table, grouping by {group_by}")

        # Process and group items
        from collections import defaultdict
        summary = defaultdict(lambda: {"total_amount": 0, "count": 0, "items": []})

        for item in items:
            try:
                key = ""
                if group_by == "property":
                    key = item.get("property_name", {}).get("S", "Unknown")
                elif group_by == "vendor":
                    key = item.get("vendor_name", {}).get("S", "Unknown")
                elif group_by == "charge_code":
                    key = item.get("charge_code", {}).get("S", "Unknown")
                elif group_by == "month":
                    key = item.get("billback_period", {}).get("S", "Unknown")

                amount = float(item.get("billback_amount", {}).get("N", "0"))
                summary[key]["total_amount"] += amount
                summary[key]["count"] += 1
                summary[key]["items"].append({
                    "line_item_id": item.get("line_item_id", {}).get("S", ""),
                    "property_name": item.get("property_name", {}).get("S", ""),
                    "vendor_name": item.get("vendor_name", {}).get("S", ""),
                    "charge_code": item.get("charge_code", {}).get("S", ""),
                    "billback_period": item.get("billback_period", {}).get("S", ""),
                    "billback_amount": amount,
                    "notes": item.get("notes", {}).get("S", ""),
                })
            except Exception as item_error:
                print(f"[BILLBACK SUMMARY] Error processing item: {item_error}")
                continue

        # Convert to list
        result = []
        for key, data in summary.items():
            result.append({
                "group_key": key,
                "total_amount": round(data["total_amount"], 2),
                "count": data["count"],
                "items": data["items"]
            })

        # Sort by total_amount descending
        result.sort(key=lambda x: x["total_amount"], reverse=True)

        print(f"[BILLBACK SUMMARY] Returning {len(result)} groups")
        return {"summary": result, "group_by": group_by}
    except Exception as e:
        print(f"[BILLBACK SUMMARY] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/billback/ubi/unassigned")
def api_billback_ubi_unassigned(
    user: str = Depends(require_user),
    page: int = 1,
    page_size: int = 50,
    days_back: int = 90
):
    """Load line items from Stage 7 that haven't been assigned to UBI periods.

    Pagination and date filtering to handle large datasets:
    - page: Page number (1-indexed)
    - page_size: Number of bills per page (default 50)
    - days_back: Only look at files from the last N days (default 90)
    """
    try:
        import hashlib
        from datetime import datetime, timedelta
        from concurrent.futures import ThreadPoolExecutor, as_completed

        start_time = datetime.now()

        # Get all assigned line items from DynamoDB (with pagination!)
        assigned_hashes = set()
        try:
            response = ddb.scan(TableName="jrk-bill-ubi-assignments")
            for item in response.get("Items", []):
                line_hash = item.get("line_hash", {}).get("S", "")
                if line_hash:
                    assigned_hashes.add(line_hash)
            # CRITICAL: Paginate through all results
            while "LastEvaluatedKey" in response:
                response = ddb.scan(
                    TableName="jrk-bill-ubi-assignments",
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                for item in response.get("Items", []):
                    line_hash = item.get("line_hash", {}).get("S", "")
                    if line_hash:
                        assigned_hashes.add(line_hash)
            print(f"[UBI UNASSIGNED] Found {len(assigned_hashes)} already-assigned line items")
        except Exception as ddb_error:
            print(f"[UBI UNASSIGNED] Error loading assigned items: {ddb_error}")

        # Get all archived line items (with pagination!)
        archived_hashes = set()
        try:
            response = ddb.scan(TableName="jrk-bill-ubi-archived")
            for item in response.get("Items", []):
                line_hash = item.get("line_hash", {}).get("S", "")
                if line_hash:
                    archived_hashes.add(line_hash)
            # CRITICAL: Paginate through all results
            while "LastEvaluatedKey" in response:
                response = ddb.scan(
                    TableName="jrk-bill-ubi-archived",
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                for item in response.get("Items", []):
                    line_hash = item.get("line_hash", {}).get("S", "")
                    if line_hash:
                        archived_hashes.add(line_hash)
            print(f"[UBI UNASSIGNED] Found {len(archived_hashes)} archived line items")
        except Exception as arch_error:
            print(f"[UBI UNASSIGNED] Error loading archived line items: {arch_error}")

        # Build date-partitioned prefixes for the last N days to avoid scanning everything
        prefixes_to_scan = []
        today = datetime.now()
        for i in range(days_back):
            d = today - timedelta(days=i)
            prefix = f"{POST_ENTRATA_PREFIX}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"
            prefixes_to_scan.append(prefix)

        print(f"[UBI UNASSIGNED] Scanning {len(prefixes_to_scan)} date partitions (last {days_back} days)")

        # Collect all S3 keys first
        all_keys = []
        for prefix in prefixes_to_scan:
            try:
                paginator = s3.get_paginator('list_objects_v2')
                s3_pages = paginator.paginate(Bucket=BUCKET, Prefix=prefix)
                for s3_page in s3_pages:
                    for obj in s3_page.get('Contents', []):
                        key = obj['Key']
                        if key.endswith('.jsonl'):
                            all_keys.append(key)
            except Exception:
                continue

        print(f"[UBI UNASSIGNED] Found {len(all_keys)} JSONL files to process")

        # Helper function to safely parse charge values
        def safe_parse_charge(charge_val):
            """Safely parse a charge value, handling dates and invalid data."""
            if charge_val is None:
                return 0.0
            charge_str = str(charge_val).replace("$", "").replace(",", "").strip()
            if not charge_str:
                return 0.0
            # Skip if it looks like a date (contains /)
            if "/" in charge_str or "-" in charge_str:
                # Try to detect date-like patterns
                import re
                if re.match(r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$', charge_str):
                    return 0.0
            try:
                return float(charge_str)
            except (ValueError, TypeError):
                return 0.0

        # Process a single S3 file
        def process_file(key):
            try:
                obj_data = s3.get_object(Bucket=BUCKET, Key=key)
                txt = obj_data['Body'].read().decode('utf-8', errors='ignore')
                lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]

                if not lines:
                    return None

                try:
                    first_rec = json.loads(lines[0])
                except json.JSONDecodeError as e:
                    print(f"[UBI UNASSIGNED] Skipping corrupted file {key}: {e}")
                    return None

                bill_info = {
                    "s3_key": key,
                    "vendor": first_rec.get("Vendor Name", ""),
                    "account": first_rec.get("Account Number", ""),
                    "pdf_id": first_rec.get("pdf_id", ""),
                    "invoice_no": first_rec.get("Invoice Number", ""),
                    "total_amount": 0.0,
                    "line_count": 0,
                    "unassigned_lines": []
                }

                for line in lines:
                    try:
                        rec = json.loads(line)
                        line_hash = _compute_stable_line_hash(rec)

                        if line_hash in assigned_hashes or line_hash in archived_hashes:
                            continue

                        charge = safe_parse_charge(rec.get("Line Item Charge", "0"))

                        # Sanitize line_data to ensure it's JSON-serializable
                        # and exclude large fields that bloat the response
                        EXCLUDE_FIELDS = {'__pdf_b64__', '__pdf_filename__', 'EnrichedProperty'}
                        sanitized_rec = {}
                        for k, v in rec.items():
                            if k in EXCLUDE_FIELDS:
                                continue  # Skip large embedded fields
                            if isinstance(v, str):
                                # Remove any null bytes or other problematic characters
                                sanitized_rec[k] = v.replace('\x00', '').replace('\ufffd', '')
                            else:
                                sanitized_rec[k] = v

                        bill_info["unassigned_lines"].append({
                            "line_hash": line_hash,
                            "line_data": sanitized_rec,
                            "charge": charge
                        })
                        bill_info["total_amount"] += charge
                        bill_info["line_count"] += 1
                    except (json.JSONDecodeError, ValueError, TypeError):
                        continue

                if bill_info["unassigned_lines"]:
                    return bill_info
                return None
            except Exception as e:
                print(f"[UBI UNASSIGNED] Error processing {key}: {e}")
                return None

        # Process files concurrently with ThreadPoolExecutor
        unassigned_bills = []
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(process_file, key): key for key in all_keys}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    unassigned_bills.append(result)

        # Sort by total amount descending
        unassigned_bills.sort(key=lambda x: x["total_amount"], reverse=True)

        # Calculate pagination
        total_bills = len(unassigned_bills)
        total_pages = (total_bills + page_size - 1) // page_size if total_bills > 0 else 1
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        # Get the requested page
        paginated_bills = unassigned_bills[start_idx:end_idx]

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"[UBI UNASSIGNED] Returning page {page}/{total_pages} ({len(paginated_bills)} of {total_bills} bills) in {elapsed:.1f}s")

        return {
            "bills": paginated_bills,
            "total_bills": total_bills,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_more": page < total_pages,
            "processing_time_seconds": round(elapsed, 1)
        }

    except Exception as e:
        print(f"[UBI UNASSIGNED] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/billback/ubi/assign")
async def api_billback_ubi_assign(request: Request, user: str = Depends(require_user)):
    """Assign line items to a UBI period with amounts and notes."""
    try:
        import uuid
        from datetime import datetime

        form = await request.form()
        ubi_period = form.get("ubi_period", "").strip()
        line_hashes_str = form.get("line_hashes", "")
        s3_key = form.get("s3_key", "").strip()
        amounts_str = form.get("amounts", "")
        notes_str = form.get("notes", "")
        months_total = form.get("months_total", "1")

        if not ubi_period:
            return JSONResponse({"error": "UBI period is required"}, status_code=400)

        if not line_hashes_str:
            return JSONResponse({"error": "No line items selected"}, status_code=400)

        line_hashes = [h.strip() for h in line_hashes_str.split(",") if h.strip()]

        # Parse amounts (comma-separated)
        amounts = []
        if amounts_str:
            amounts = [float(a.strip()) if a.strip() else 0.0 for a in amounts_str.split(",")]

        # Parse notes (|||  separated)
        notes = []
        if notes_str:
            notes = notes_str.split("|||")

        if not line_hashes:
            return JSONResponse({"error": "No line items selected"}, status_code=400)

        # Save each assignment to DynamoDB
        saved_count = 0
        now_utc = datetime.utcnow().isoformat() + "Z"

        print(f"[UBI ASSIGN] Attempting to assign {len(line_hashes)} line(s) to period {ubi_period}")
        print(f"[UBI ASSIGN] S3 Key: {s3_key}")
        print(f"[UBI ASSIGN] User: {user}")

        for idx, line_hash in enumerate(line_hashes):
            try:
                assignment_id = str(uuid.uuid4())

                # Get amount and notes for this line (if provided)
                # User enters per-period amounts directly - save as-is
                amount = amounts[idx] if idx < len(amounts) else 0.0
                line_notes = notes[idx] if idx < len(notes) else ""

                item = {
                    "assignment_id": {"S": assignment_id},
                    "ubi_period": {"S": ubi_period},
                    "line_hash": {"S": line_hash},
                    "s3_key": {"S": s3_key},
                    "assigned_date": {"S": now_utc},
                    "assigned_by": {"S": user},
                    "amount": {"N": str(amount)},
                    "months_total": {"N": months_total}
                }

                # Add notes if provided
                if line_notes:
                    item["notes"] = {"S": line_notes}

                print(f"[UBI ASSIGN] Writing item {idx+1}/{len(line_hashes)}: hash={line_hash[:16]}..., period={ubi_period}, amount={amount}, months_total={months_total}")
                ddb.put_item(TableName="jrk-bill-ubi-assignments", Item=item)
                print(f"[UBI ASSIGN] Successfully wrote item {idx+1}")
                saved_count += 1

            except Exception as item_error:
                print(f"[UBI ASSIGN] ERROR saving assignment for {line_hash}: {item_error}")
                import traceback
                traceback.print_exc()
                continue

        print(f"[UBI ASSIGN] COMPLETED: Saved {saved_count}/{len(line_hashes)} assignments to period {ubi_period}")
        return {"ok": True, "assigned": saved_count, "ubi_period": ubi_period}

    except Exception as e:
        print(f"[UBI ASSIGN] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/billback/ubi/assigned")
def api_billback_ubi_assigned(user: str = Depends(require_user), period: str = ""):
    """Load line items assigned to a specific UBI period, or all if no period specified."""
    try:
        import hashlib

        # Query DynamoDB
        assignments = []

        if period:
            # Query by GSI for specific period
            try:
                response = ddb.query(
                    TableName="jrk-bill-ubi-assignments",
                    IndexName="ubi-period-index",
                    KeyConditionExpression="ubi_period = :period",
                    ExpressionAttributeValues={":period": {"S": period}}
                )
                assignments = response.get("Items", [])
            except Exception as query_error:
                print(f"[UBI ASSIGNED] Error querying period {period}: {query_error}")
        else:
            # Scan all assignments
            try:
                response = ddb.scan(TableName="jrk-bill-ubi-assignments")
                assignments = response.get("Items", [])
            except Exception as scan_error:
                print(f"[UBI ASSIGNED] Error scanning assignments: {scan_error}")

        print(f"[UBI ASSIGNED] Found {len(assignments)} assignments" + (f" for period {period}" if period else ""))

        # Group by period, then by S3 key
        from collections import defaultdict
        by_period = defaultdict(lambda: {"total_amount": 0.0, "line_count": 0, "bills": {}})

        # Create map of line_hash -> assignment info
        line_hash_to_assignment = {}
        for assignment in assignments:
            line_hash = assignment.get("line_hash", {}).get("S", "")
            ubi_period = assignment.get("ubi_period", {}).get("S", "")
            s3_key = assignment.get("s3_key", {}).get("S", "")

            line_hash_to_assignment[line_hash] = {
                "assignment_id": assignment.get("assignment_id", {}).get("S", ""),
                "ubi_period": ubi_period,
                "s3_key": s3_key,
                "assigned_date": assignment.get("assigned_date", {}).get("S", ""),
                "assigned_by": assignment.get("assigned_by", {}).get("S", ""),
                "saved_amount": float(assignment.get("amount", {}).get("N", "0") or "0")
            }

        # Now load the actual line data from S3
        processed_keys = set()

        for line_hash, assignment_info in line_hash_to_assignment.items():
            s3_key = assignment_info["s3_key"]
            ubi_period = assignment_info["ubi_period"]

            # Only read each S3 file once
            if s3_key not in processed_keys:
                processed_keys.add(s3_key)

                try:
                    obj_data = s3.get_object(Bucket=BUCKET, Key=s3_key)
                    txt = obj_data['Body'].read().decode('utf-8', errors='ignore')
                    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]

                    # Parse bill info
                    if lines:
                        first_rec = json.loads(lines[0])
                        bill_key = s3_key

                        if bill_key not in by_period[ubi_period]["bills"]:
                            by_period[ubi_period]["bills"][bill_key] = {
                                "s3_key": s3_key,
                                "vendor": first_rec.get("Vendor Name", ""),
                                "account": first_rec.get("Account Number", ""),
                                "pdf_id": first_rec.get("pdf_id", ""),
                                "invoice_no": first_rec.get("Invoice Number", ""),
                                "assigned_lines": []
                            }

                        # Process each line
                        for line in lines:
                            rec = json.loads(line)
                            current_hash = _compute_stable_line_hash(rec)

                            # Check if this line is in our assignments
                            if current_hash in line_hash_to_assignment:
                                assignment = line_hash_to_assignment[current_hash]
                                if assignment["s3_key"] == s3_key:  # Ensure it's from this bill
                                    # Use saved_amount from DynamoDB (already divided for multi-month)
                                    # Fall back to original line charge if no saved amount
                                    charge = assignment.get("saved_amount", 0.0)
                                    if charge == 0.0:
                                        # Fallback for old assignments without saved amount
                                        charge_str = str(rec.get("Line Item Charge", "0") or "0").replace("$", "").replace(",", "").strip()
                                        charge = float(charge_str) if charge_str else 0.0

                                    # Exclude large fields from line_data to reduce response size
                                    EXCLUDE_FIELDS = {'__pdf_b64__', '__pdf_filename__', 'EnrichedProperty'}
                                    filtered_rec = {k: v for k, v in rec.items() if k not in EXCLUDE_FIELDS}

                                    by_period[ubi_period]["bills"][bill_key]["assigned_lines"].append({
                                        "line_hash": current_hash,
                                        "line_data": filtered_rec,
                                        "charge": charge,
                                        "assignment_id": assignment["assignment_id"]
                                    })

                                    by_period[ubi_period]["total_amount"] += charge
                                    by_period[ubi_period]["line_count"] += 1

                except Exception as s3_error:
                    print(f"[UBI ASSIGNED] Error reading S3 key {s3_key}: {s3_error}")
                    continue

        # Convert to list format
        result = []
        for ubi_period, data in by_period.items():
            bills_list = list(data["bills"].values())
            result.append({
                "ubi_period": ubi_period,
                "total_amount": round(data["total_amount"], 2),
                "line_count": data["line_count"],
                "bills": bills_list
            })

        result.sort(key=lambda x: x["ubi_period"], reverse=True)

        print(f"[UBI ASSIGNED] Returning {len(result)} periods with assignments")
        return {"periods": result}

    except Exception as e:
        print(f"[UBI ASSIGNED] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/billback/ubi/unassign")
async def api_billback_ubi_unassign(request: Request, user: str = Depends(require_user)):
    """Remove assignments from UBI period."""
    try:
        form = await request.form()
        assignment_ids_str = form.get("assignment_ids", "")

        if not assignment_ids_str:
            return JSONResponse({"error": "No assignments selected"}, status_code=400)

        assignment_ids = [aid.strip() for aid in assignment_ids_str.split(",") if aid.strip()]

        if not assignment_ids:
            return JSONResponse({"error": "No assignments selected"}, status_code=400)

        # Delete each assignment
        deleted_count = 0
        for assignment_id in assignment_ids:
            try:
                ddb.delete_item(
                    TableName="jrk-bill-ubi-assignments",
                    Key={"assignment_id": {"S": assignment_id}}
                )
                deleted_count += 1
            except Exception as del_error:
                print(f"[UBI UNASSIGN] Error deleting {assignment_id}: {del_error}")
                continue

        print(f"[UBI UNASSIGN] Deleted {deleted_count}/{len(assignment_ids)} assignments")
        return {"ok": True, "unassigned": deleted_count}

    except Exception as e:
        print(f"[UBI UNASSIGN] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/billback/ubi/reassign")
async def api_billback_ubi_reassign(request: Request, user: str = Depends(require_user)):
    """Reassign line items to a different UBI period."""
    try:
        form = await request.form()
        assignment_ids_str = form.get("assignment_ids", "")
        new_period = form.get("new_period", "").strip()

        if not assignment_ids_str:
            return JSONResponse({"error": "No assignments selected"}, status_code=400)

        if not new_period:
            return JSONResponse({"error": "New period is required"}, status_code=400)

        assignment_ids = [aid.strip() for aid in assignment_ids_str.split(",") if aid.strip()]

        if not assignment_ids:
            return JSONResponse({"error": "No assignments selected"}, status_code=400)

        # Update each assignment's ubi_period
        updated_count = 0
        now_utc = dt.datetime.utcnow().isoformat() + "Z"

        for assignment_id in assignment_ids:
            try:
                ddb.update_item(
                    TableName="jrk-bill-ubi-assignments",
                    Key={"assignment_id": {"S": assignment_id}},
                    UpdateExpression="SET ubi_period = :period, reassigned_date = :date, reassigned_by = :user",
                    ExpressionAttributeValues={
                        ":period": {"S": new_period},
                        ":date": {"S": now_utc},
                        ":user": {"S": user}
                    }
                )
                updated_count += 1
            except Exception as upd_error:
                print(f"[UBI REASSIGN] Error updating {assignment_id}: {upd_error}")
                continue

        print(f"[UBI REASSIGN] Updated {updated_count}/{len(assignment_ids)} assignments to period {new_period}")
        return {"ok": True, "reassigned": updated_count, "new_period": new_period}

    except Exception as e:
        print(f"[UBI REASSIGN] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/billback/ubi/archive")
async def api_billback_ubi_archive(request: Request, user: str = Depends(require_user)):
    """Archive line items by marking them in DynamoDB."""
    try:
        form = await request.form()
        line_hashes_str = form.get("line_hashes", "").strip()

        if not line_hashes_str:
            return JSONResponse({"error": "Line hashes are required"}, status_code=400)

        line_hashes = [h.strip() for h in line_hashes_str.split(",") if h.strip()]

        if not line_hashes:
            return JSONResponse({"error": "No valid line hashes provided"}, status_code=400)

        import uuid
        from datetime import datetime

        now_utc = datetime.utcnow().isoformat() + "Z"
        archived_count = 0

        # Archive each line hash
        for line_hash in line_hashes:
            try:
                archive_id = str(uuid.uuid4())
                item = {
                    "archive_id": {"S": archive_id},
                    "line_hash": {"S": line_hash},
                    "archived_date": {"S": now_utc},
                    "archived_by": {"S": user}
                }

                ddb.put_item(TableName="jrk-bill-ubi-archived", Item=item)
                archived_count += 1
            except Exception as item_error:
                print(f"[UBI ARCHIVE] Error archiving line {line_hash}: {item_error}")
                continue

        print(f"[UBI ARCHIVE] Archived {archived_count} line items")
        return {"ok": True, "archived": archived_count}

    except Exception as e:
        print(f"[UBI ARCHIVE] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/config", response_class=HTMLResponse)
def config_menu_view(request: Request, user: str = Depends(require_user)):
    user_data = auth.get_user(user)
    user_role = user_data.get("role") if user_data else None
    return templates.TemplateResponse("config_menu.html", {
        "request": request,
        "user": user,
        "user_role": user_role
    })


@app.get("/config/gl-code-mapping", response_class=HTMLResponse)
def config_gl_code_mapping_view(request: Request, user: str = Depends(require_user)):
    """GL Code to Charge Code Mapping (Property-Aware) - for automatic charge code lookup during billback."""
    return templates.TemplateResponse("gl_code_mapping.html", {"request": request, "user": user})


@app.get("/config/account-tracking", response_class=HTMLResponse)
def config_account_tracking_view(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse("config.html", {"request": request, "user": user})


@app.get("/config/ap-team", response_class=HTMLResponse)
def config_ap_team_view(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse("ap_team.html", {"request": request, "user": user})


@app.get("/config/ap-mapping", response_class=HTMLResponse)
def config_ap_mapping_view(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse("ap_mapping.html", {"request": request, "user": user})


@app.get("/config/ubi-mapping", response_class=HTMLResponse)
def config_ubi_mapping_view(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse("ubi_mapping.html", {"request": request, "user": user})


@app.get("/config/charge-codes", response_class=HTMLResponse)
def config_charge_codes_view(request: Request, user: str = Depends(require_user)):
    """Charge codes configuration page - manage charge codes and utility names."""
    return templates.TemplateResponse("charge_codes.html", {"request": request, "user": user})


@app.get("/api/config/charge-codes")
def api_get_charge_codes(user: str = Depends(require_user)):
    """Get charge codes configuration."""
    arr = _ddb_get_config("charge-codes")
    if not isinstance(arr, list):
        arr = []
    # normalize to {chargeCode, utilityName}
    out = []
    for r in arr:
        if isinstance(r, dict):
            out.append({
                "chargeCode": str(r.get("chargeCode") or "").strip(),
                "utilityName": str(r.get("utilityName") or "").strip()
            })
    return {"items": out}


@app.post("/api/config/charge-codes")
async def api_save_charge_codes(request: Request, user: str = Depends(require_user)):
    """Save charge codes configuration."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return JSONResponse({"error": "items must be a list"}, status_code=400)
    norm = []
    for r in items:
        if not isinstance(r, dict):
            continue
        charge_code = str(r.get("chargeCode") or "").strip()
        utility_name = str(r.get("utilityName") or "").strip()
        if charge_code:  # Only save if charge code is not empty
            norm.append({
                "chargeCode": charge_code,
                "utilityName": utility_name
            })
    ok = _ddb_put_config("charge-codes", norm)
    if not ok:
        return JSONResponse({"error": "save_failed"}, status_code=500)
    return {"ok": True, "saved": len(norm)}


@app.get("/config/uom-mapping", response_class=HTMLResponse)
def config_uom_mapping_view(request: Request, user: str = Depends(require_user)):
    """UOM (Unit of Measure) conversion mapping configuration page."""
    return templates.TemplateResponse("uom_mapping.html", {"request": request, "user": user})


@app.get("/track", response_class=HTMLResponse)
def track_view(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse("track.html", {"request": request, "user": user})


@app.get("/debug", response_class=HTMLResponse)
def debug_view(request: Request, user: str = Depends(require_user)):
    """DEBUG page - Triage bug reports and enhancement requests."""
    return templates.TemplateResponse("debug.html", {"request": request, "user": user})


@app.get("/failed", response_class=HTMLResponse)
def failed_jobs_view(request: Request, user: str = Depends(require_user)):
    """FAILED JOBS page - View and manage failed parsing jobs."""
    return templates.TemplateResponse("failed.html", {"request": request, "user": user})


@app.get("/metrics", response_class=HTMLResponse)
def metrics_view(request: Request, user: str = Depends(require_user)):
    """METRICS page - View processing time and productivity statistics."""
    return templates.TemplateResponse("metrics.html", {"request": request, "user": user})


@app.get("/api/metrics/user-timing")
def api_metrics_user_timing(date: str = "", user: str = Depends(require_user)):
    """Get all user timing data across all users for metrics."""
    try:
        # Scan for all timing records
        response = ddb.scan(
            TableName=DRAFTS_TABLE,
            FilterExpression="begins_with(pk, :prefix)",
            ExpressionAttributeValues={
                ":prefix": {"S": "timing#"},
            },
        )
        # Group by user
        by_user = {}
        for item in response.get("Items", []):
            u = item.get("user", {}).get("S", "unknown")
            invoice_id = item.get("invoice_id", {}).get("S", "")
            total_secs = int(item.get("total_seconds", {}).get("N", 0))
            updated = item.get("updated_utc", {}).get("S", "")
            # Filter by date if provided
            if date and updated and not updated.startswith(date):
                continue
            if u not in by_user:
                by_user[u] = {"user": u, "invoices": [], "total_seconds": 0}
            by_user[u]["invoices"].append({
                "invoice_id": invoice_id,
                "seconds": total_secs,
                "updated": updated,
            })
            by_user[u]["total_seconds"] += total_secs

        # Format results
        users = []
        for u, data in by_user.items():
            users.append({
                "user": u,
                "invoice_count": len(data["invoices"]),
                "total_seconds": data["total_seconds"],
                "total_minutes": round(data["total_seconds"] / 60, 1),
                "total_hours": round(data["total_seconds"] / 3600, 2),
                "avg_seconds_per_invoice": round(data["total_seconds"] / len(data["invoices"]), 1) if data["invoices"] else 0,
            })
        users.sort(key=lambda x: x["total_seconds"], reverse=True)

        total_all = sum(u["total_seconds"] for u in users)
        return {
            "date_filter": date,
            "users": users,
            "user_count": len(users),
            "total_seconds": total_all,
            "total_hours": round(total_all / 3600, 2),
        }
    except Exception as e:
        print(f"[METRICS] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/metrics/parsing-volume")
def api_metrics_parsing_volume(days: int = 7, user: str = Depends(require_user)):
    """Get parsing volume metrics by day."""
    try:
        results = []
        today = dt.datetime.utcnow().date()
        for i in range(days):
            target_date = today - dt.timedelta(days=i)
            y, m, d = target_date.strftime('%Y'), target_date.strftime('%m'), target_date.strftime('%d')
            prefix = f"{ENRICH_PREFIX}yyyy={y}/mm={m}/dd={d}/"

            # Count files in enriched outputs for this day
            file_count = 0
            total_lines = 0
            try:
                paginator = s3.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
                    for obj in page.get("Contents", []) or []:
                        k = obj.get("Key", "")
                        if k.endswith('.jsonl'):
                            file_count += 1
                            # Optionally count lines (can be slow for many files)
            except Exception:
                pass

            results.append({
                "date": str(target_date),
                "invoices_parsed": file_count,
            })

        results.reverse()  # Chronological order
        return {"days": results, "total_invoices": sum(r["invoices_parsed"] for r in results)}
    except Exception as e:
        print(f"[METRICS] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/metrics/pipeline-summary")
def api_metrics_pipeline_summary(user: str = Depends(require_user)):
    """Get pipeline summary - count of files in each processing stage."""
    try:
        stages = [
            {"id": "pending_parsing", "name": "Pending Parsing", "prefix": "Bill_Parser_1_Pending_Parsing/", "color": "#f59e0b"},
            {"id": "rework", "name": "Rework Queue", "prefix": "Bill_Parser_Rework_Input/", "color": "#8b5cf6"},
            {"id": "in_review", "name": "Ready for Review", "prefix": ENRICH_PREFIX, "color": "#3b82f6", "is_stage4": True},
            {"id": "posted", "name": "Posted (Stage 6)", "prefix": STAGE6_PREFIX, "color": "#10b981", "is_stage6": True},
            {"id": "billback", "name": "Billback (Stage 7)", "prefix": POST_ENTRATA_PREFIX, "color": "#06b6d4", "is_stage7": True},
            {"id": "failed", "name": "Failed Jobs", "prefix": FAILED_JOBS_PREFIX, "color": "#ef4444"},
        ]

        results = []
        today = dt.datetime.utcnow().date()

        for stage in stages:
            count = 0
            oldest_ts = None
            prefix = stage["prefix"]

            try:
                paginator = s3.get_paginator('list_objects_v2')

                # For Stage 4/6/7, count only last 30 days by date partition
                if stage.get("is_stage4") or stage.get("is_stage6") or stage.get("is_stage7"):
                    for i in range(30):
                        target_date = today - dt.timedelta(days=i)
                        y, m, d = target_date.strftime('%Y'), target_date.strftime('%m'), target_date.strftime('%d')
                        day_prefix = f"{prefix}yyyy={y}/mm={m}/dd={d}/"
                        for page in paginator.paginate(Bucket=BUCKET, Prefix=day_prefix):
                            for obj in page.get("Contents", []) or []:
                                k = obj.get("Key", "")
                                if k.endswith('.jsonl'):
                                    count += 1
                                    mod_time = obj.get("LastModified")
                                    if mod_time and (oldest_ts is None or mod_time < oldest_ts):
                                        oldest_ts = mod_time
                else:
                    # For other stages, just list files directly
                    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
                        for obj in page.get("Contents", []) or []:
                            k = obj.get("Key", "")
                            # Skip metadata files and diagnostics
                            if k.endswith('.json') or '/diagnostics/' in k or k == prefix:
                                continue
                            if k.endswith('.pdf') or k.endswith('.jsonl'):
                                count += 1
                                mod_time = obj.get("LastModified")
                                if mod_time and (oldest_ts is None or mod_time < oldest_ts):
                                    oldest_ts = mod_time
            except Exception as e:
                print(f"[PIPELINE] Error counting {stage['id']}: {e}")

            results.append({
                "id": stage["id"],
                "name": stage["name"],
                "count": count,
                "color": stage["color"],
                "oldest_age_days": (dt.datetime.now(dt.timezone.utc) - oldest_ts).days if oldest_ts else None,
            })

        return {"stages": results}
    except Exception as e:
        print(f"[METRICS] Pipeline summary error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/metrics/submitter-stats")
def api_metrics_submitter_stats(date: str = "", start_date: str = "", end_date: str = "", user: str = Depends(require_user)):
    """Get submitter productivity stats - invoices, lines, and dollars per submitter.

    - Submitted (Parse→POST): Files where SubmittedAt matches target date/range (Pacific time)
    - Posted (POST→Billback): Files in Stage 7 where PostedAt (or S3 LastModified) matches target date/range
    - Aggregate: UNIQUE invoices per submitter (no double counting)

    Supports either:
    - Single date: ?date=YYYY-MM-DD
    - Date range: ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
    """
    try:
        from zoneinfo import ZoneInfo
        pacific = ZoneInfo("America/Los_Angeles")
        utc = ZoneInfo("UTC")

        # Parse date filter - support both single date and date range
        if start_date and end_date:
            # Date range mode (for weekly view)
            try:
                range_start = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
                range_end = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                range_start = dt.datetime.now(pacific).date()
                range_end = range_start
            target_date_str = f"{range_start.strftime('%Y-%m-%d')} to {range_end.strftime('%Y-%m-%d')}"
        elif date:
            # Single date mode
            try:
                range_start = dt.datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                range_start = dt.datetime.now(pacific).date()
            range_end = range_start
            target_date_str = range_start.strftime('%Y-%m-%d')
        else:
            # Default to today
            range_start = dt.datetime.now(pacific).date()
            range_end = range_start
            target_date_str = range_start.strftime('%Y-%m-%d')

        # Calculate UTC time range for the Pacific date(s)
        pacific_start = dt.datetime.combine(range_start, dt.time.min).replace(tzinfo=pacific)
        pacific_end = dt.datetime.combine(range_end, dt.time.max).replace(tzinfo=pacific)
        utc_start = pacific_start.astimezone(utc)
        utc_end = pacific_end.astimezone(utc)

        # Track stats per submitter
        submitted_stats: dict = {}  # submitter -> {invoices, lines, dollars}
        posted_stats: dict = {}     # poster -> {invoices, lines, dollars}
        # Track unique invoices per submitter for aggregate (no double counting)
        aggregate_invoices: dict = {}  # submitter -> set of file basenames
        aggregate_stats: dict = {}     # submitter -> {lines, dollars}
        seen_submitted_keys: set = set()  # Avoid counting same file twice in submitted
        seen_posted_keys: set = set()  # Avoid counting same file twice in posted

        def parse_utc_timestamp(ts_str: str) -> dt.datetime | None:
            """Parse a timestamp string as UTC datetime."""
            if not ts_str:
                return None
            try:
                # Handle both with and without microseconds
                if '.' in ts_str:
                    parsed = dt.datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                else:
                    parsed = dt.datetime.fromisoformat(ts_str)
                # If no timezone info, assume UTC
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=utc)
                return parsed
            except (ValueError, TypeError):
                return None

        def get_file_stats(content: str) -> tuple:
            """Count lines and sum dollars from file content. Returns (line_count, total_dollars)."""
            lines = content.strip().split('\n')
            line_count = 0
            total_dollars = 0.0
            for line in lines:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    line_count += 1
                    amt = rec.get("Line Item Charge") or rec.get("AMOUNT") or rec.get("amount") or rec.get("Amount") or 0
                    try:
                        total_dollars += float(str(amt).replace('$', '').replace(',', ''))
                    except (ValueError, TypeError):
                        pass
                except json.JSONDecodeError:
                    continue
            return line_count, total_dollars

        # File cache to avoid re-reading files (key -> content)
        file_cache: dict = {}

        def get_cached_content(k: str) -> str | None:
            """Get file content with caching to avoid duplicate S3 reads."""
            if k in file_cache:
                return file_cache[k]
            try:
                file_obj = s3.get_object(Bucket=BUCKET, Key=k)
                content = file_obj['Body'].read().decode('utf-8', errors='ignore')
                file_cache[k] = content
                return content
            except Exception as e:
                print(f"[SUBMITTER_STATS] Error reading {k}: {e}")
                file_cache[k] = None
                return None

        def process_file_for_submitted(k: str) -> bool:
            """Process a file, checking if SubmittedAt falls within target Pacific date."""
            file_basename = k.split('/')[-1]
            if file_basename in seen_submitted_keys:
                return False
            seen_submitted_keys.add(file_basename)

            try:
                content = get_cached_content(k)
                if not content:
                    return False
                first_line = content.split('\n')[0].strip()
                if not first_line:
                    return False

                first_rec = json.loads(first_line)
                submitted_at_str = str(first_rec.get("SubmittedAt", "") or "").strip()

                # Parse and check if SubmittedAt falls within target Pacific date
                submitted_at_utc = parse_utc_timestamp(submitted_at_str)
                if not submitted_at_utc:
                    return False
                if not (utc_start <= submitted_at_utc <= utc_end):
                    return False

                submitter = str(first_rec.get("Submitter", "") or "").strip() or "Unknown"
                line_count, total_dollars = get_file_stats(content)

                # Track in submitted_stats (who submitted)
                if submitter not in submitted_stats:
                    submitted_stats[submitter] = {"invoices": 0, "lines": 0, "dollars": 0.0}
                submitted_stats[submitter]["invoices"] += 1
                submitted_stats[submitter]["lines"] += line_count
                submitted_stats[submitter]["dollars"] += total_dollars

                # Track in aggregate (unique invoices per submitter)
                if submitter not in aggregate_invoices:
                    aggregate_invoices[submitter] = set()
                    aggregate_stats[submitter] = {"lines": 0, "dollars": 0.0}
                if file_basename not in aggregate_invoices[submitter]:
                    aggregate_invoices[submitter].add(file_basename)
                    aggregate_stats[submitter]["lines"] += line_count
                    aggregate_stats[submitter]["dollars"] += total_dollars

                return True
            except Exception as e:
                print(f"[SUBMITTER_STATS] Error processing {k}: {e}")
                return False

        def process_file_for_posted(k: str, s3_last_modified: dt.datetime) -> bool:
            """Process a Stage 7 file for posted stats, using PostedBy/PostedAt if available."""
            file_basename = k.split('/')[-1]
            if file_basename in seen_posted_keys:
                return False
            seen_posted_keys.add(file_basename)

            try:
                content = get_cached_content(k)
                if not content:
                    return False
                first_line = content.split('\n')[0].strip()
                if not first_line:
                    return False

                first_rec = json.loads(first_line)

                # Check PostedAt field first, fall back to S3 LastModified
                posted_at_str = str(first_rec.get("PostedAt", "") or "").strip()
                posted_at_utc = parse_utc_timestamp(posted_at_str)

                # If PostedAt matches target date, use it; otherwise check S3 LastModified
                if posted_at_utc:
                    if not (utc_start <= posted_at_utc <= utc_end):
                        return False
                else:
                    # Fall back to S3 LastModified
                    if not (utc_start <= s3_last_modified <= utc_end):
                        return False

                # Use PostedBy if available, otherwise fall back to Submitter (legacy)
                poster = str(first_rec.get("PostedBy", "") or "").strip()
                if not poster:
                    poster = str(first_rec.get("Submitter", "") or "").strip() or "Unknown"

                line_count, total_dollars = get_file_stats(content)

                # Track in posted_stats (who POSTED, not who submitted)
                if poster not in posted_stats:
                    posted_stats[poster] = {"invoices": 0, "lines": 0, "dollars": 0.0}
                posted_stats[poster]["invoices"] += 1
                posted_stats[poster]["lines"] += line_count
                posted_stats[poster]["dollars"] += total_dollars

                # Track in aggregate for the POSTER (unique invoices)
                if poster not in aggregate_invoices:
                    aggregate_invoices[poster] = set()
                    aggregate_stats[poster] = {"lines": 0, "dollars": 0.0}
                if file_basename not in aggregate_invoices[poster]:
                    aggregate_invoices[poster].add(file_basename)
                    aggregate_stats[poster]["lines"] += line_count
                    aggregate_stats[poster]["dollars"] += total_dollars

                return True
            except Exception as e:
                print(f"[SUBMITTER_STATS] Error processing {k}: {e}")
                return False

        paginator = s3.get_paginator('list_objects_v2')

        # Calculate dates to scan based on range (plus buffer for timezone edge cases)
        # For date ranges, scan all days in the range plus 1 day buffer on each end
        num_days = (range_end - range_start).days + 1
        scan_dates = [range_start + dt.timedelta(days=i) for i in range(-1, num_days + 1)]

        # Scan Stage 6 for SUBMITTED stats (filter by SubmittedAt)
        for check_date in scan_dates:
            y, m, d = check_date.strftime('%Y'), check_date.strftime('%m'), check_date.strftime('%d')
            day_prefix = f"{STAGE6_PREFIX}yyyy={y}/mm={m}/dd={d}/"

            try:
                for page in paginator.paginate(Bucket=BUCKET, Prefix=day_prefix):
                    for obj in page.get("Contents", []) or []:
                        k = obj.get("Key", "")
                        if k.endswith('.jsonl'):
                            process_file_for_submitted(k)
            except Exception as e:
                print(f"[SUBMITTER_STATS] Error scanning {day_prefix}: {e}")
                continue

        # Scan Stage 7 for POSTED stats only
        # Stage 7 = already posted, so these should NOT appear in "Submitted" column
        # This prevents double-counting: Submitted = Stage 6 only, Posted = Stage 7 only
        for check_date in scan_dates:
            y, m, d = check_date.strftime('%Y'), check_date.strftime('%m'), check_date.strftime('%d')
            day_prefix = f"{POST_ENTRATA_PREFIX}yyyy={y}/mm={m}/dd={d}/"

            try:
                for page in paginator.paginate(Bucket=BUCKET, Prefix=day_prefix):
                    for obj in page.get("Contents", []) or []:
                        k = obj.get("Key", "")
                        if not k.endswith('.jsonl'):
                            continue
                        last_modified = obj.get("LastModified")
                        # Only process for POSTED stats - do NOT add to submitted_stats
                        if last_modified:
                            process_file_for_posted(k, last_modified)
            except Exception as e:
                print(f"[SUBMITTER_STATS] Error scanning {day_prefix}: {e}")
                continue

        # Format results
        submitted_list = [
            {"submitter": k, "invoices": v["invoices"], "lines": v["lines"], "dollars": round(v["dollars"], 2)}
            for k, v in sorted(submitted_stats.items(), key=lambda x: x[1]["invoices"], reverse=True)
        ]
        posted_list = [
            {"submitter": k, "invoices": v["invoices"], "lines": v["lines"], "dollars": round(v["dollars"], 2)}
            for k, v in sorted(posted_stats.items(), key=lambda x: x[1]["invoices"], reverse=True)
        ]

        # Calculate totals
        submitted_totals = {
            "invoices": sum(s["invoices"] for s in submitted_list),
            "lines": sum(s["lines"] for s in submitted_list),
            "dollars": round(sum(s["dollars"] for s in submitted_list), 2)
        }
        posted_totals = {
            "invoices": sum(s["invoices"] for s in posted_list),
            "lines": sum(s["lines"] for s in posted_list),
            "dollars": round(sum(s["dollars"] for s in posted_list), 2)
        }

        # Calculate aggregate by submitter (unique invoices, no double counting)
        aggregate_list = [
            {"submitter": k, "invoices": len(aggregate_invoices[k]), "lines": aggregate_stats[k]["lines"], "dollars": round(aggregate_stats[k]["dollars"], 2)}
            for k in sorted(aggregate_invoices.keys(), key=lambda x: len(aggregate_invoices[x]), reverse=True)
        ]

        # Calculate aggregate totals
        aggregate_totals = {
            "invoices": sum(a["invoices"] for a in aggregate_list),
            "lines": sum(a["lines"] for a in aggregate_list),
            "dollars": round(sum(a["dollars"] for a in aggregate_list), 2)
        }

        return {
            "date": target_date_str,
            "start_date": range_start.strftime('%Y-%m-%d'),
            "end_date": range_end.strftime('%Y-%m-%d'),
            "submitted": submitted_list,
            "submitted_totals": submitted_totals,
            "posted": posted_list,
            "posted_totals": posted_totals,
            "aggregate_totals": aggregate_totals,
            "aggregate_by_submitter": aggregate_list
        }
    except Exception as e:
        import traceback
        print(f"[METRICS] Submitter stats error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/metrics/week-over-week")
def api_metrics_week_over_week(weeks: int = 6, submitter: str = "", user: str = Depends(require_user)):
    """Get week-over-week stats for the last N weeks.

    Returns weekly aggregates of invoices, lines, dollars, and late fees.
    Shows change vs prior week for trend analysis.
    """
    try:
        from zoneinfo import ZoneInfo
        pacific = ZoneInfo("America/Los_Angeles")
        utc = ZoneInfo("UTC")

        # Calculate week boundaries (Monday to Sunday)
        today = dt.datetime.now(pacific).date()
        day_of_week = today.weekday()  # Monday = 0
        current_monday = today - dt.timedelta(days=day_of_week)

        weeks_data = []
        all_submitters = set()

        def get_file_stats_with_late_fees(content: str) -> tuple:
            """Count lines, sum dollars, and extract late fees. Returns (line_count, total_dollars, late_fees)."""
            lines = content.strip().split('\n')
            line_count = 0
            total_dollars = 0.0
            late_fees = 0.0
            for line in lines:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    line_count += 1
                    amt = rec.get("Line Item Charge") or rec.get("AMOUNT") or rec.get("amount") or rec.get("Amount") or 0
                    try:
                        amt_float = float(str(amt).replace('$', '').replace(',', ''))
                        total_dollars += amt_float
                        # Check if this is a late fee line
                        desc = str(rec.get("Line Item Description", "") or "").lower()
                        if any(k in desc for k in ["late fee", "late charge", "late payment", "penalty", "penalties"]):
                            late_fees += amt_float
                    except (ValueError, TypeError):
                        pass
                except json.JSONDecodeError:
                    continue
            return line_count, total_dollars, late_fees

        def parse_utc_timestamp(ts_str: str) -> dt.datetime | None:
            """Parse a timestamp string as UTC datetime."""
            if not ts_str:
                return None
            try:
                if '.' in ts_str:
                    parsed = dt.datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                else:
                    parsed = dt.datetime.fromisoformat(ts_str)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=utc)
                return parsed
            except (ValueError, TypeError):
                return None

        paginator = s3.get_paginator('list_objects_v2')

        # Process each week (going backwards from current week)
        for week_idx in range(weeks):
            week_start = current_monday - dt.timedelta(days=week_idx * 7)
            week_end = week_start + dt.timedelta(days=6)

            # Calculate UTC bounds for this week
            pacific_start = dt.datetime.combine(week_start, dt.time.min).replace(tzinfo=pacific)
            pacific_end = dt.datetime.combine(week_end, dt.time.max).replace(tzinfo=pacific)
            utc_start = pacific_start.astimezone(utc)
            utc_end = pacific_end.astimezone(utc)

            week_stats = {
                "start": week_start.strftime('%Y-%m-%d'),
                "end": week_end.strftime('%Y-%m-%d'),
                "label": f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}",
                "invoices": 0,
                "lines": 0,
                "dollars": 0.0,
                "late_fees": 0.0
            }

            seen_keys = set()
            file_cache = {}

            # Scan all days in the week
            scan_dates = [week_start + dt.timedelta(days=i) for i in range(7)]

            # Scan Stage 6 (submitted) and Stage 7 (posted) for this week
            for check_date in scan_dates:
                y, m, d = check_date.strftime('%Y'), check_date.strftime('%m'), check_date.strftime('%d')

                for prefix in [STAGE6_PREFIX, POST_ENTRATA_PREFIX]:
                    day_prefix = f"{prefix}yyyy={y}/mm={m}/dd={d}/"

                    try:
                        for page in paginator.paginate(Bucket=BUCKET, Prefix=day_prefix):
                            for obj in page.get("Contents", []) or []:
                                k = obj.get("Key", "")
                                if not k.endswith('.jsonl'):
                                    continue

                                file_basename = k.split('/')[-1]
                                if file_basename in seen_keys:
                                    continue

                                try:
                                    if k not in file_cache:
                                        file_obj = s3.get_object(Bucket=BUCKET, Key=k)
                                        file_cache[k] = file_obj['Body'].read().decode('utf-8', errors='ignore')

                                    content = file_cache[k]
                                    first_line = content.split('\n')[0].strip()
                                    if not first_line:
                                        continue

                                    first_rec = json.loads(first_line)

                                    # Check SubmittedAt timestamp
                                    submitted_at_str = str(first_rec.get("SubmittedAt", "") or "").strip()
                                    submitted_at_utc = parse_utc_timestamp(submitted_at_str)

                                    if not submitted_at_utc or not (utc_start <= submitted_at_utc <= utc_end):
                                        continue

                                    # Apply submitter filter if specified
                                    file_submitter = str(first_rec.get("Submitter", "") or "").strip() or "Unknown"
                                    all_submitters.add(file_submitter)

                                    if submitter and file_submitter.lower() != submitter.lower():
                                        continue

                                    seen_keys.add(file_basename)
                                    line_count, total_dollars, late_fees = get_file_stats_with_late_fees(content)

                                    week_stats["invoices"] += 1
                                    week_stats["lines"] += line_count
                                    week_stats["dollars"] += total_dollars
                                    week_stats["late_fees"] += late_fees

                                except Exception as e:
                                    continue
                    except Exception as e:
                        continue

            week_stats["dollars"] = round(week_stats["dollars"], 2)
            week_stats["late_fees"] = round(week_stats["late_fees"], 2)
            weeks_data.append(week_stats)

        # Calculate change vs prior week
        for i, week in enumerate(weeks_data):
            if i < len(weeks_data) - 1:
                prior = weeks_data[i + 1]
                week["change"] = {
                    "invoices": week["invoices"] - prior["invoices"],
                    "lines": week["lines"] - prior["lines"],
                    "dollars": round(week["dollars"] - prior["dollars"], 2)
                }
            else:
                week["change"] = None

        return {
            "weeks": weeks_data,
            "all_submitters": sorted(all_submitters),
            "submitter_filter": submitter if submitter else None
        }
    except Exception as e:
        import traceback
        print(f"[METRICS] Week-over-week error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/metrics/activity-detail")
def api_metrics_activity_detail(date: str = "", start_date: str = "", end_date: str = "", user: str = Depends(require_user)):
    """Get detailed activity log showing each activity (submission OR posting) with timestamp.

    Returns separate events for:
    - Submitted: When invoice was submitted (SubmittedAt matches target date/range)
    - Posted: When invoice was posted to billback (PostedAt or S3 LastModified matches target date/range)

    Supports either:
    - Single date: ?date=YYYY-MM-DD
    - Date range: ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
    """
    try:
        from zoneinfo import ZoneInfo

        pacific = ZoneInfo("America/Los_Angeles")
        utc = ZoneInfo("UTC")

        # Parse date filter - support both single date and date range
        if start_date and end_date:
            # Date range mode (for weekly view)
            try:
                range_start = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
                range_end = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                range_start = dt.datetime.now(pacific).date()
                range_end = range_start
            target_date_str = f"{range_start.strftime('%Y-%m-%d')} to {range_end.strftime('%Y-%m-%d')}"
        elif date:
            # Single date mode
            try:
                range_start = dt.datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                range_start = dt.datetime.now(pacific).date()
            range_end = range_start
            target_date_str = range_start.strftime('%Y-%m-%d')
        else:
            # Default to today
            range_start = dt.datetime.now(pacific).date()
            range_end = range_start
            target_date_str = range_start.strftime('%Y-%m-%d')

        # Calculate UTC time range for the Pacific date(s)
        pacific_start = dt.datetime.combine(range_start, dt.time.min).replace(tzinfo=pacific)
        pacific_end = dt.datetime.combine(range_end, dt.time.max).replace(tzinfo=pacific)
        utc_start = pacific_start.astimezone(utc)
        utc_end = pacific_end.astimezone(utc)

        activities = []
        seen_submitted_keys = set()  # Avoid duplicate submitted events
        seen_posted_keys = set()  # Avoid duplicate posted events

        def parse_utc_timestamp(ts_str: str) -> dt.datetime | None:
            """Parse a timestamp string as UTC datetime."""
            if not ts_str:
                return None
            try:
                if '.' in ts_str:
                    parsed = dt.datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                else:
                    parsed = dt.datetime.fromisoformat(ts_str)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=utc)
                return parsed
            except (ValueError, TypeError):
                return None

        def get_file_stats(content: str) -> tuple:
            """Count lines and sum dollars. Returns (line_count, total_dollars)."""
            lines = content.strip().split('\n')
            line_count = 0
            total_dollars = 0.0
            for line in lines:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    line_count += 1
                    amt = rec.get("Line Item Charge") or rec.get("AMOUNT") or rec.get("amount") or rec.get("Amount") or 0
                    try:
                        total_dollars += float(str(amt).replace('$', '').replace(',', ''))
                    except (ValueError, TypeError):
                        pass
                except json.JSONDecodeError:
                    continue
            return line_count, total_dollars

        def extract_submitted_activity(k: str, content: str, first_rec: dict) -> dict | None:
            """Extract submitted activity if SubmittedAt matches target date."""
            file_basename = k.split('/')[-1]
            if file_basename in seen_submitted_keys:
                return None

            submitted_at_str = str(first_rec.get("SubmittedAt", "") or "").strip()
            if not submitted_at_str:
                return None

            submitted_at_utc = parse_utc_timestamp(submitted_at_str)
            if not submitted_at_utc or not (utc_start <= submitted_at_utc <= utc_end):
                return None

            seen_submitted_keys.add(file_basename)
            submitter = str(first_rec.get("Submitter", "") or "").strip() or "Unknown"
            property_name = first_rec.get("EnrichedPropertyName") or first_rec.get("Property Name") or ""
            vendor_name = first_rec.get("EnrichedVendorName") or first_rec.get("Vendor Name") or ""
            line_count, total_dollars = get_file_stats(content)

            return {
                "submitter": submitter,
                "submitted_at": submitted_at_str,
                "property": property_name,
                "vendor": vendor_name,
                "lines": line_count,
                "dollars": round(total_dollars, 2),
                "type": "submitted",
                "s3_key": k
            }

        def extract_posted_activity(k: str, content: str, first_rec: dict, s3_last_modified: dt.datetime) -> dict | None:
            """Extract posted activity if PostedAt or S3 LastModified matches target date."""
            file_basename = k.split('/')[-1]
            if file_basename in seen_posted_keys:
                return None

            # Check PostedAt field first, fall back to S3 LastModified
            posted_at_str = str(first_rec.get("PostedAt", "") or "").strip()
            posted_at_utc = parse_utc_timestamp(posted_at_str)

            # Determine if this was posted on the target date
            if posted_at_utc:
                if not (utc_start <= posted_at_utc <= utc_end):
                    return None
                timestamp_str = posted_at_str
            else:
                # Fall back to S3 LastModified
                if not (utc_start <= s3_last_modified <= utc_end):
                    return None
                timestamp_str = s3_last_modified.strftime('%Y-%m-%dT%H:%M:%S')

            seen_posted_keys.add(file_basename)

            # Use PostedBy if available, otherwise fall back to Submitter (legacy)
            poster = str(first_rec.get("PostedBy", "") or "").strip()
            if not poster:
                poster = str(first_rec.get("Submitter", "") or "").strip() or "Unknown"

            property_name = first_rec.get("EnrichedPropertyName") or first_rec.get("Property Name") or ""
            vendor_name = first_rec.get("EnrichedVendorName") or first_rec.get("Vendor Name") or ""
            line_count, total_dollars = get_file_stats(content)

            return {
                "submitter": poster,  # For posted events, show who posted
                "submitted_at": timestamp_str,  # Reuse field for timeline sorting
                "property": property_name,
                "vendor": vendor_name,
                "lines": line_count,
                "dollars": round(total_dollars, 2),
                "type": "posted",
                "s3_key": k
            }

        paginator = s3.get_paginator('list_objects_v2')

        # Cache file contents to avoid reading the same file twice
        file_cache: dict = {}  # key -> (content, first_rec)

        def get_file_data(k: str) -> tuple | None:
            """Get file content and first record, using cache."""
            if k in file_cache:
                return file_cache[k]
            try:
                file_obj = s3.get_object(Bucket=BUCKET, Key=k)
                content = file_obj['Body'].read().decode('utf-8', errors='ignore')
                first_line = content.split('\n')[0].strip()
                if not first_line:
                    return None
                first_rec = json.loads(first_line)
                file_cache[k] = (content, first_rec)
                return content, first_rec
            except Exception as e:
                print(f"[ACTIVITY_DETAIL] Error reading {k}: {e}")
                return None

        # Calculate dates to scan based on range (plus buffer for timezone edge cases)
        # For date ranges, scan all days in the range plus 1 day buffer on each end
        num_days = (range_end - range_start).days + 1
        scan_dates = [range_start + dt.timedelta(days=i) for i in range(-1, num_days + 1)]

        # Scan Stage 6 (POST) for submitted activities
        for check_date in scan_dates:
            y, m, d = check_date.strftime('%Y'), check_date.strftime('%m'), check_date.strftime('%d')
            day_prefix = f"{STAGE6_PREFIX}yyyy={y}/mm={m}/dd={d}/"

            try:
                for page in paginator.paginate(Bucket=BUCKET, Prefix=day_prefix):
                    for obj in page.get("Contents", []) or []:
                        k = obj.get("Key", "")
                        if k.endswith('.jsonl'):
                            data = get_file_data(k)
                            if data:
                                content, first_rec = data
                                activity = extract_submitted_activity(k, content, first_rec)
                                if activity:
                                    activities.append(activity)
            except Exception as e:
                print(f"[ACTIVITY_DETAIL] Error scanning {day_prefix}: {e}")
                continue

        # Scan Stage 7 (Billback) for both submitted AND posted activities
        for check_date in scan_dates:
            y, m, d = check_date.strftime('%Y'), check_date.strftime('%m'), check_date.strftime('%d')
            day_prefix = f"{POST_ENTRATA_PREFIX}yyyy={y}/mm={m}/dd={d}/"

            try:
                for page in paginator.paginate(Bucket=BUCKET, Prefix=day_prefix):
                    for obj in page.get("Contents", []) or []:
                        k = obj.get("Key", "")
                        if not k.endswith('.jsonl'):
                            continue
                        last_modified = obj.get("LastModified")
                        data = get_file_data(k)
                        if data:
                            content, first_rec = data
                            # Check for submitted activity
                            submitted_activity = extract_submitted_activity(k, content, first_rec)
                            if submitted_activity:
                                activities.append(submitted_activity)
                            # Check for posted activity
                            if last_modified:
                                posted_activity = extract_posted_activity(k, content, first_rec, last_modified)
                                if posted_activity:
                                    activities.append(posted_activity)
            except Exception as e:
                print(f"[ACTIVITY_DETAIL] Error scanning {day_prefix}: {e}")
                continue

        # Sort by timestamp (earliest first for chronological view)
        activities.sort(key=lambda x: x.get("submitted_at", ""), reverse=False)

        return {
            "date": target_date_str,
            "start_date": range_start.strftime('%Y-%m-%d'),
            "end_date": range_end.strftime('%Y-%m-%d'),
            "activities": activities,
            "count": len(activities)
        }
    except Exception as e:
        import traceback
        print(f"[METRICS] Activity detail error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/metrics/job-log")
def api_metrics_job_log(limit: int = 100, stage: str = "", user: str = Depends(require_user)):
    """Get individual job log showing recent invoices with their status."""
    try:
        jobs = []
        today = dt.datetime.utcnow().date()

        # Define stages to check for each job's status
        stage_prefixes = {
            "in_review": (ENRICH_PREFIX, True),  # (prefix, is_date_partitioned)
            "posted": (STAGE6_PREFIX, True),
            "billback": (POST_ENTRATA_PREFIX, True),
            "pending_parsing": ("Bill_Parser_1_Pending_Parsing/", False),
            "rework": ("Bill_Parser_Rework_Input/", False),
            "failed": (FAILED_JOBS_PREFIX, False),
        }

        # If specific stage requested, only check that stage
        if stage and stage in stage_prefixes:
            check_stages = {stage: stage_prefixes[stage]}
        else:
            # Default: check review stage (Stage 4) for recent jobs
            check_stages = {"in_review": stage_prefixes["in_review"]}

        for stage_id, (prefix, is_partitioned) in check_stages.items():
            if is_partitioned:
                # Check last 7 days for partitioned stages
                for i in range(7):
                    if len(jobs) >= limit:
                        break
                    target_date = today - dt.timedelta(days=i)
                    y, m, d = target_date.strftime('%Y'), target_date.strftime('%m'), target_date.strftime('%d')
                    day_prefix = f"{prefix}yyyy={y}/mm={m}/dd={d}/"

                    try:
                        paginator = s3.get_paginator('list_objects_v2')
                        for page in paginator.paginate(Bucket=BUCKET, Prefix=day_prefix):
                            for obj in page.get("Contents", []) or []:
                                if len(jobs) >= limit:
                                    break
                                k = obj.get("Key", "")
                                if not k.endswith('.jsonl'):
                                    continue

                                filename = k.split("/")[-1] if "/" in k else k
                                mod_time = obj.get("LastModified")
                                age_hours = round((dt.datetime.now(dt.timezone.utc) - mod_time).total_seconds() / 3600, 1) if mod_time else None

                                # Extract job ID from filename (first part before first underscore or whole name)
                                job_id = filename.replace('.jsonl', '')

                                jobs.append({
                                    "job_id": job_id,
                                    "filename": filename,
                                    "stage": stage_id,
                                    "stage_name": {"in_review": "In Review", "posted": "Posted", "billback": "Billback", "pending_parsing": "Pending", "rework": "Rework", "failed": "Failed"}.get(stage_id, stage_id),
                                    "size": obj.get("Size", 0),
                                    "last_modified": mod_time.isoformat() if mod_time else "",
                                    "age_hours": age_hours,
                                    "date": str(target_date),
                                })
                    except Exception as e:
                        print(f"[JOB LOG] Error listing {day_prefix}: {e}")
            else:
                # Non-partitioned stages
                try:
                    paginator = s3.get_paginator('list_objects_v2')
                    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
                        for obj in page.get("Contents", []) or []:
                            if len(jobs) >= limit:
                                break
                            k = obj.get("Key", "")
                            if k == prefix or '/diagnostics/' in k:
                                continue
                            if k.endswith('.json'):  # Skip metadata
                                continue
                            if not (k.endswith('.pdf') or k.endswith('.jsonl')):
                                continue

                            filename = k.split("/")[-1] if "/" in k else k
                            mod_time = obj.get("LastModified")
                            age_hours = round((dt.datetime.now(dt.timezone.utc) - mod_time).total_seconds() / 3600, 1) if mod_time else None

                            job_id = filename.rsplit('.', 1)[0] if '.' in filename else filename

                            jobs.append({
                                "job_id": job_id,
                                "filename": filename,
                                "stage": stage_id,
                                "stage_name": {"in_review": "In Review", "posted": "Posted", "billback": "Billback", "pending_parsing": "Pending", "rework": "Rework", "failed": "Failed"}.get(stage_id, stage_id),
                                "size": obj.get("Size", 0),
                                "last_modified": mod_time.isoformat() if mod_time else "",
                                "age_hours": age_hours,
                                "date": mod_time.strftime("%Y-%m-%d") if mod_time else "",
                            })
                except Exception as e:
                    print(f"[JOB LOG] Error listing {prefix}: {e}")

        # Sort by last_modified descending
        jobs.sort(key=lambda x: x.get("last_modified", ""), reverse=True)
        return {"jobs": jobs[:limit], "count": len(jobs[:limit])}
    except Exception as e:
        print(f"[METRICS] Job log error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/failed/jobs")
def api_get_failed_jobs(user: str = Depends(require_user)):
    """Get list of failed parsing jobs from S3 with error info from .notes.json files."""
    try:
        jobs = []
        all_files = {}  # key -> obj info
        notes_files = {}  # base_name -> notes content
        paginator = s3.get_paginator('list_objects_v2')

        # List all files in failed jobs folder (including .notes.json)
        for page in paginator.paginate(Bucket=BUCKET, Prefix=FAILED_JOBS_PREFIX):
            for obj in page.get("Contents", []) or []:
                key = obj.get("Key", "")
                # Skip the folder itself
                if key == FAILED_JOBS_PREFIX or "/diagnostics/" in key:
                    continue
                filename = key.split("/")[-1] if "/" in key else key
                all_files[filename] = {"key": key, "obj": obj}

        # Find .notes.json and .error.json files and load their content
        for filename, info in all_files.items():
            if filename.endswith(".notes.json"):
                try:
                    notes_obj = s3.get_object(Bucket=BUCKET, Key=info["key"])
                    notes_data = json.loads(notes_obj["Body"].read().decode("utf-8"))
                    # Get the base name (remove .notes.json)
                    base_name = filename[:-11]  # Remove ".notes.json"
                    notes_files[base_name] = notes_data
                except Exception as e:
                    print(f"[FAILED JOBS] Error reading notes file {filename}: {e}")
            elif filename.endswith(".error.json"):
                try:
                    error_obj = s3.get_object(Bucket=BUCKET, Key=info["key"])
                    error_data = json.loads(error_obj["Body"].read().decode("utf-8"))
                    # Get the base name (remove .error.json)
                    base_name = filename[:-11]  # Remove ".error.json"
                    # Merge into notes_files format
                    notes_files[base_name] = {
                        "notes": error_data.get("error_message", ""),
                        "error_type": error_data.get("error_type", "PARSE_ERROR"),
                        "source": {"key": error_data.get("source_key", error_data.get("original_key", ""))},
                        "pipeline_stage": error_data.get("pipeline_stage", ""),
                        "failed_at": error_data.get("failed_at", ""),
                    }
                except Exception as e:
                    print(f"[FAILED JOBS] Error reading error file {filename}: {e}")

        # Build job list from PDF files
        for filename, info in all_files.items():
            # Only process PDF files (skip metadata files)
            if filename.endswith(".notes.json") or filename.endswith(".rework.json") or filename.endswith(".error.json"):
                continue
            # Must end with .pdf to be included
            if not filename.lower().endswith(".pdf"):
                continue

            key = info["key"]
            obj = info["obj"]

            # Generate presigned URL for PDF viewing
            pdf_url = ""
            try:
                pdf_url = s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': BUCKET, 'Key': key},
                    ExpiresIn=3600  # 1 hour
                )
            except Exception:
                pass

            # Look for corresponding .notes.json file
            base_name = filename.rsplit(".", 1)[0] if "." in filename else filename
            notes = notes_files.get(base_name, {})

            # Extract error info from notes
            error_type = "REWORK" if "REWORK" in filename else "PARSE_ERROR"
            error_details = notes.get("notes", "")
            vendor = notes.get("Bill From", "")
            if vendor and error_details:
                error_details = f"[{vendor}] {error_details}"
            elif vendor:
                error_details = f"Vendor: {vendor}"

            jobs.append({
                "key": key,
                "filename": filename,
                "size": obj.get("Size", 0),
                "last_modified": obj.get("LastModified").isoformat() if obj.get("LastModified") else "",
                "pdf_url": pdf_url,
                "error_type": error_type,
                "error_details": error_details,
                "vendor": vendor,
            })
        # Sort by last_modified descending (newest first)
        jobs.sort(key=lambda x: x.get("last_modified", ""), reverse=True)
        return {"jobs": jobs, "count": len(jobs)}
    except Exception as e:
        print(f"[FAILED JOBS] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/failed/errors")
def api_get_parser_errors(limit: int = 100, user: str = Depends(require_user)):
    """Get parser errors from .notes.json and .error.json files in Failed Jobs folder."""
    try:
        items = []
        paginator = s3.get_paginator('list_objects_v2')

        # List all .notes.json and .error.json files in failed jobs folder
        for page in paginator.paginate(Bucket=BUCKET, Prefix=FAILED_JOBS_PREFIX):
            for obj in page.get("Contents", []) or []:
                key = obj.get("Key", "")
                filename = key.split("/")[-1] if "/" in key else key

                # Process .notes.json files (REWORK rejections)
                if filename.endswith(".notes.json"):
                    try:
                        notes_obj = s3.get_object(Bucket=BUCKET, Key=key)
                        notes_data = json.loads(notes_obj["Body"].read().decode("utf-8"))

                        # Get source PDF info
                        source = notes_data.get("source", {})
                        pdf_key = source.get("key", "")

                        # Generate presigned URL for PDF viewing
                        pdf_url = ""
                        if pdf_key:
                            try:
                                pdf_url = s3.generate_presigned_url(
                                    'get_object',
                                    Params={'Bucket': BUCKET, 'Key': pdf_key},
                                    ExpiresIn=3600
                                )
                            except Exception:
                                pass

                        items.append({
                            "pdf_key": pdf_key,
                            "pdf_url": pdf_url,
                            "error_type": "REWORK",
                            "error_details": notes_data.get("notes", ""),
                            "vendor": notes_data.get("Bill From", ""),
                            "timestamp": notes_data.get("generated_utc", ""),
                            "last_modified": obj.get("LastModified").isoformat() if obj.get("LastModified") else "",
                            "source_key": pdf_key,
                            "pipeline_stage": "rework",
                        })
                    except Exception as e:
                        print(f"[FAILED ERRORS] Error reading notes {filename}: {e}")

                # Process .error.json files (parser failures/timeouts)
                elif filename.endswith(".error.json"):
                    try:
                        error_obj = s3.get_object(Bucket=BUCKET, Key=key)
                        error_data = json.loads(error_obj["Body"].read().decode("utf-8"))

                        # Get PDF key from error data
                        source_key = error_data.get("source_key", "")
                        pdf_key = error_data.get("original_key", source_key)

                        # Generate presigned URL for PDF viewing
                        pdf_url = ""
                        if pdf_key:
                            try:
                                pdf_url = s3.generate_presigned_url(
                                    'get_object',
                                    Params={'Bucket': BUCKET, 'Key': pdf_key},
                                    ExpiresIn=3600
                                )
                            except Exception:
                                pass

                        items.append({
                            "pdf_key": pdf_key,
                            "pdf_url": pdf_url,
                            "error_type": error_data.get("error_type", "PARSE_ERROR"),
                            "error_details": error_data.get("error_message", ""),
                            "vendor": "",  # Not available in error files
                            "timestamp": error_data.get("failed_at", ""),
                            "last_modified": obj.get("LastModified").isoformat() if obj.get("LastModified") else "",
                            "source_key": source_key,
                            "pipeline_stage": error_data.get("pipeline_stage", "parser"),
                        })
                    except Exception as e:
                        print(f"[FAILED ERRORS] Error reading error file {filename}: {e}")

                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break

        # Sort by last_modified descending (newest first)
        items.sort(key=lambda x: x.get("last_modified", ""), reverse=True)
        return {"errors": items, "count": len(items)}
    except Exception as e:
        print(f"[FAILED JOBS] Error fetching errors: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/failed/retry")
def api_retry_failed_job(key: str = Form(...), user: str = Depends(require_user)):
    """Retry a failed job by moving it back to the pending parsing queue."""
    try:
        # Copy the failed file back to pending parsing
        filename = key.split("/")[-1] if "/" in key else key
        new_key = f"{INPUT_PREFIX}{filename}"
        s3.copy_object(
            Bucket=BUCKET,
            CopySource={"Bucket": BUCKET, "Key": key},
            Key=new_key
        )
        # Delete the failed copy
        s3.delete_object(Bucket=BUCKET, Key=key)
        return {"success": True, "message": f"Moved to pending: {new_key}"}
    except Exception as e:
        print(f"[FAILED JOBS] Retry error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/failed/delete")
def api_delete_failed_job(key: str = Form(...), user: str = Depends(require_user)):
    """Delete a failed job from S3."""
    try:
        s3.delete_object(Bucket=BUCKET, Key=key)
        return {"success": True, "message": f"Deleted: {key}"}
    except Exception as e:
        print(f"[FAILED JOBS] Delete error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# -------- Catalog + Config APIs --------
def _find_latest_data(prefix: str) -> str | None:
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET, Prefix=prefix)
    best_key = None
    best_ts = None
    for page in pages:
        for o in page.get("Contents", []) or []:
            k = o.get("Key", "")
            if not (k.endswith("data.json") or k.endswith("data.json.gz")):
                continue
            lm = o.get("LastModified")
            if best_ts is None or (lm and lm > best_ts):
                best_ts = lm
                best_key = k
    return best_key


def _read_s3_text(bucket: str, key: str) -> str:
    obj = s3.get_object(Bucket=bucket, Key=key)
    raw = obj["Body"].read()
    if key.lower().endswith(".gz"):
        try:
            return gzip.decompress(raw).decode("utf-8", errors="ignore")
        except Exception:
            pass
    try:
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return raw.decode("latin-1", errors="ignore")


def _load_dim_records(prefix: str) -> list[dict]:
    # Try standardized filename first to avoid expensive S3 pagination
    standard_key = f"{prefix}latest.json.gz"
    try:
        txt = _read_s3_text(BUCKET, standard_key)
        print(f"[DIM LOAD] Using standard key: {standard_key}")
    except Exception:
        # Fall back to pagination to find latest file (for backward compatibility)
        print(f"[DIM LOAD] Standard key not found, falling back to pagination for prefix: {prefix}")
        key = _find_latest_data(prefix)
        if not key:
            return []
        txt = _read_s3_text(BUCKET, key)
    # Try JSON array or object with records
    try:
        parsed = json.loads(txt)
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
        if isinstance(parsed, dict):
            recs = parsed.get("records")
            if isinstance(recs, list):
                return [r for r in recs if isinstance(r, dict)]
    except Exception:
        pass
    # Fallback: JSONL
    out = []
    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
            if isinstance(r, dict):
                out.append(r)
        except Exception:
            continue
    if out:
        return out
    # Fallback: CSV (comma or tab)
    try:
        import csv
        from io import StringIO
        # pick delimiter by sniffing
        sample = "\n".join(txt.splitlines()[:5])
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t|")
        reader = csv.DictReader(StringIO(txt), dialect=dialect)
        rows = []
        for i, row in enumerate(reader):
            if not row:
                continue
            rows.append({k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()})
            if i >= 5000:
                break
        return rows
    except Exception:
        return []


def _s3_get_json(bucket: str, key: str) -> Any:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read().decode("utf-8", errors="ignore")
        return json.loads(body)
    except Exception:
        return None


def _s3_put_json(bucket: str, key: str, data: Any) -> None:
    body = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")


# -------- GL Override helpers --------
def _load_gl_number_to_id_map() -> dict:
    """Build a map from GL account number (formatted or raw) to Entrata GL Account ID using DIM_GL."""
    recs = _load_dim_records(DIM_GL_PREFIX)
    m: dict[str, str] = {}
    for r in recs:
        # candidate ids
        gid = (
            r.get("glAccountId") or r.get("GL_ACCOUNT_ID") or r.get("id") or r.get("GlAccountId") or r.get("GL_ID")
        )
        if not gid:
            continue
        gid = str(gid).strip()
        # numbers
        num_raw = (
            r.get("glAccountNumber") or r.get("GL Account Number") or r.get("GL_ACCOUNT_NUMBER") or r.get("number") or r.get("ACCOUNT_NO") or r.get("GL_NUMBER") or r.get("ACCOUNT_NUMBER")
        )
        num_fmt = (
            r.get("formattedGlAccountNumber") or r.get("Formatted GL Account Number") or r.get("FORMATTED_GL_ACCOUNT_NUMBER") or r.get("FORMATTED_ACCOUNT_NUMBER") or r.get("formatted")
        )
        for n in [num_fmt, num_raw]:
            if n:
                key = str(n).strip()
                if key:
                    m[key] = gid
    return m


def _load_gl_name_to_id_map() -> dict:
    """Build a map from GL account name to Entrata GL Account ID using DIM_GL."""
    recs = _load_dim_records(DIM_GL_PREFIX)
    m: dict[str, str] = {}
    for r in recs:
        gid = (
            r.get("glAccountId") or r.get("GL_ACCOUNT_ID") or r.get("id") or r.get("GlAccountId") or r.get("GL_ID")
        )
        if not gid:
            continue
        gid = str(gid).strip()
        name = (
            r.get("glAccountName") or r.get("GL Account Name") or r.get("GL_ACCOUNT_NAME") or r.get("name") or r.get("NAME") or r.get("DESCRIPTION") or r.get("Account Name") or r.get("ACCOUNT_NAME") or r.get("GLAccountDescription") or r.get("DESCRIPTION_LONG")
        )
        if name:
            raw = str(name).strip()
            key = raw.upper()
            if key:
                m[key] = gid
    return m

def _index_accounts_to_track_by_key() -> dict[tuple[str, str, str], dict]:
    """Return a dict keyed by (propertyId,vendorId,accountNumber) from Accounts-To-Track config rows."""
    base = _ddb_get_config("accounts-to-track")
    if base is None:
        base = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
    out: dict[tuple[str, str, str], dict] = {}
    if isinstance(base, list):
        for r in base:
            if not isinstance(r, dict):
                continue
            pid = str(r.get("propertyId") or "").strip()
            vid = str(r.get("vendorId") or "").strip()
            acct = str(r.get("accountNumber") or "").strip()
            if pid or vid or acct:
                out[(pid, vid, acct)] = r
    return out


def _parse_date_any(s: str) -> dt.date | None:
    if not s:
        return None
    s = str(s).strip()
    fmts = ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"]
    for f in fmts:
        try:
            return datetime.strptime(s, f).date()
        except Exception:
            pass
    return None


def _month_key(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _iter_stage_objects(prefix_root: str, start: dt.date, end: dt.date):
    # Prefix layout supports both simple prefix (stage4) and y/m/d partition (stage6 has yyyy=...)
    # We'll try both yyyy=YYYY/mm=MM/dd=DD and YYYY/MM/DD patterns.
    # Limit to BUCKET
    cur = start
    while cur <= end:
        y = cur.year
        m = cur.month
        d = cur.day
        prefixes = [
            f"{prefix_root}yyyy={y}/mm={m:02d}/dd={d:02d}/",
            f"{prefix_root}{y}/{m:02d}/{d:02d}/",
        ]
        for p in prefixes:
            try:
                resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=p, MaxKeys=200)
                for obj in resp.get("Contents", []) or []:
                    yield obj["Key"]
            except Exception:
                pass
        cur += dt.timedelta(days=1)


def _read_json_records_from_s3(keys: list[str]) -> list[dict]:
    out = []
    for key in keys:
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
            body = obj["Body"].read()
            # jsonl or json
            txt = body.decode("utf-8", errors="ignore")
            if "\n" in txt:
                for line in txt.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
            else:
                try:
                    data = json.loads(txt)
                    if isinstance(data, list):
                        out.extend([x for x in data if isinstance(x, dict)])
                    elif isinstance(data, dict):
                        out.append(data)
                except Exception:
                    pass
        except Exception:
            continue
    return out


# -------- Snowflake helpers --------
SNOWFLAKE_CREDENTIALS_CACHE = None

def _get_snowflake_credentials() -> dict | None:
    """Fetch Snowflake credentials from AWS Secrets Manager (with caching)"""
    global SNOWFLAKE_CREDENTIALS_CACHE

    if SNOWFLAKE_CREDENTIALS_CACHE:
        return SNOWFLAKE_CREDENTIALS_CACHE

    try:
        secrets_client = boto3.client('secretsmanager', region_name='us-east-1')
        secret_name = 'jrk-bill-review/snowflake'

        response = secrets_client.get_secret_value(SecretId=secret_name)
        secret_string = response.get('SecretString')

        if not secret_string:
            print("[SNOWFLAKE] No secret string found")
            return None

        credentials = json.loads(secret_string)
        SNOWFLAKE_CREDENTIALS_CACHE = credentials
        print(f"[SNOWFLAKE] Loaded credentials for account: {credentials.get('account')}")
        return credentials

    except Exception as e:
        print(f"[SNOWFLAKE] Error fetching credentials: {e}")
        return None


def _write_to_snowflake(batch_id: str, master_bills: list[dict], memo: str, run_date: str) -> tuple[bool, str, int]:
    """Write master bills to Snowflake. Returns (success, message, rows_inserted)"""
    try:
        credentials = _get_snowflake_credentials()
        if not credentials:
            return False, "Failed to load Snowflake credentials", 0

        # Connect to Snowflake
        conn = snowflake.connector.connect(
            account=credentials.get('account'),
            user=credentials.get('user'),
            password=credentials.get('password'),
            database=credentials.get('database'),
            schema=credentials.get('schema'),
            warehouse=credentials.get('warehouse'),
            role=credentials.get('role') if credentials.get('role') else None
        )

        cursor = conn.cursor()

        # Prepare data for batch insert (with Source_Type column)
        rows_to_insert = []
        for mb in master_bills:
            # Determine Source_Type from source line items
            source_type = None  # NULL = ACTUAL (existing behavior)
            if mb.get('has_non_actual'):
                entry_types = set()
                for sl in mb.get('source_line_items', []):
                    et = sl.get('entry_type', '')
                    if et:
                        entry_types.add(et)
                if entry_types:
                    source_type = "MIXED" if len(entry_types) > 1 or (entry_types and any(not sl.get('entry_type') for sl in mb.get('source_line_items', []))) else entry_types.pop()

            row = (
                str(mb.get('property_id', '')),
                str(mb.get('ar_code_mapping', '')),
                str(mb.get('utility_name', '')),
                str(mb.get('utility_amount', 0)),  # Keep as string to match existing schema
                str(mb.get('billback_month_start', '')),
                str(mb.get('billback_month_end', '')),
                str(run_date),
                str(memo),
                str(batch_id),  # Add Batch_ID for traceability
                source_type  # Source_Type: NULL=ACTUAL, ACCRUAL, MANUAL, TRUE-UP, MIXED
            )
            rows_to_insert.append(row)

        # Insert into Snowflake (with Source_Type column)
        insert_sql = """
        INSERT INTO "_Master_Bills_Prod"
        ("Property_ID", "AR_Code_Mapping", "Utility_Name", "Utility_Amount",
         "Billback_Month_Start", "Billback_Month_End", "RunDate", "Memo", "Batch_ID",
         "Source_Type")
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        cursor.executemany(insert_sql, rows_to_insert)
        conn.commit()

        rows_inserted = len(rows_to_insert)

        cursor.close()
        conn.close()

        print(f"[SNOWFLAKE] Successfully inserted {rows_inserted} rows for batch {batch_id}")
        return True, f"Inserted {rows_inserted} rows", rows_inserted

    except Exception as e:
        print(f"[SNOWFLAKE] Error writing to Snowflake: {e}")
        import traceback
        traceback.print_exc()
        return False, str(e), 0


# -------- Accrual / Manual Entry Helpers --------

def _read_historical_from_snowflake(property_id: str, account_number: str, charge_code: str, utility_name: str) -> list[dict]:
    """Query Snowflake _Master_Bills_Prod for historical amounts matching property + charge code + utility."""
    try:
        credentials = _get_snowflake_credentials()
        if not credentials:
            return []

        conn = snowflake.connector.connect(
            account=credentials.get('account'),
            user=credentials.get('user'),
            password=credentials.get('password'),
            database=credentials.get('database'),
            schema=credentials.get('schema'),
            warehouse=credentials.get('warehouse'),
            role=credentials.get('role') if credentials.get('role') else None
        )

        cursor = conn.cursor()
        query = """
        SELECT "Billback_Month_Start", "Utility_Amount"
        FROM "_Master_Bills_Prod"
        WHERE "Property_ID" = %s
          AND "AR_Code_Mapping" = %s
          AND "Utility_Name" = %s
        ORDER BY "Billback_Month_Start" ASC
        """
        cursor.execute(query, (property_id, charge_code, utility_name))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        results = []
        for row in rows:
            billback_start = str(row[0]) if row[0] else ""
            amount = float(row[1]) if row[1] else 0.0
            # Convert billback_month_start (MM/DD/YYYY) to period (MM/YYYY)
            period = ""
            if billback_start:
                parts = billback_start.split("/")
                if len(parts) == 3:
                    period = f"{parts[0]}/{parts[2]}"
            results.append({"period": period, "amount": amount})

        print(f"[ACCRUAL] Snowflake returned {len(results)} historical records for {property_id}/{charge_code}/{utility_name}")
        return results

    except Exception as e:
        print(f"[ACCRUAL] Snowflake historical query error: {e}")
        return []


def _get_historical_from_assignments(property_id: str, account_number: str, vendor_name: str) -> list[dict]:
    """Fallback: scan jrk-bill-ubi-assignments and load S3 files to find historical amounts."""
    try:
        response = ddb.scan(TableName="jrk-bill-ubi-assignments")
        assignments = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = ddb.scan(
                TableName="jrk-bill-ubi-assignments",
                ExclusiveStartKey=response["LastEvaluatedKey"]
            )
            assignments.extend(response.get("Items", []))

        # Group assignments by S3 key
        s3_cache = {}
        period_amounts = {}  # period -> total_amount

        for assignment in assignments:
            s3_key = assignment.get("s3_key", {}).get("S", "")
            line_hash = assignment.get("line_hash", {}).get("S", "")
            ubi_period = assignment.get("ubi_period", {}).get("S", "")
            amount = float(assignment.get("amount", {}).get("N", "0"))

            if not s3_key or not line_hash:
                continue

            # Load S3 file
            if s3_key not in s3_cache:
                try:
                    obj = s3.get_object(Bucket=BUCKET, Key=s3_key)
                    body = obj["Body"].read()
                    if s3_key.endswith('.gz'):
                        import gzip
                        body = gzip.decompress(body)
                    s3_cache[s3_key] = body.decode('utf-8')
                except Exception:
                    continue

            # Parse lines and match
            for line_str in s3_cache[s3_key].strip().split("\n"):
                try:
                    parsed = json.loads(line_str)
                    computed_hash = _compute_stable_line_hash(parsed)
                    if computed_hash != line_hash:
                        continue
                    p_id = parsed.get("EnrichedPropertyID", parsed.get("Property ID", ""))
                    acc_num = parsed.get("Account Number", parsed.get("AccountNumber", ""))
                    v_name = parsed.get("EnrichedVendorName", parsed.get("Vendor Name", ""))
                    if p_id == property_id and acc_num == account_number:
                        # Extract period from ubi_period ("MM/YYYY" or "MM/YYYY to MM/YYYY")
                        period_key = ubi_period.split(" to ")[0].strip() if ubi_period else ""
                        if period_key:
                            period_amounts[period_key] = period_amounts.get(period_key, 0) + amount
                except Exception:
                    continue

        results = [{"period": p, "amount": a} for p, a in sorted(period_amounts.items())]
        print(f"[ACCRUAL] Assignments fallback returned {len(results)} historical records for {property_id}/{account_number}")
        return results

    except Exception as e:
        print(f"[ACCRUAL] Assignments historical query error: {e}")
        return []


def _calculate_accrual(historical_amounts: list[dict], annual_inflation_rate: float = 0.03) -> dict:
    """Calculate accrual from historical amounts with annual inflation adjustment."""
    if not historical_amounts:
        return {
            "calculated_amount": 0,
            "historical_months": 0,
            "avg_amount": 0,
            "inflation_amount": 0,
            "monthly_amounts": []
        }

    amounts = [h["amount"] for h in historical_amounts if h.get("amount", 0) != 0]
    if not amounts:
        return {
            "calculated_amount": 0,
            "historical_months": len(historical_amounts),
            "avg_amount": 0,
            "inflation_amount": 0,
            "monthly_amounts": historical_amounts
        }

    avg = sum(amounts) / len(amounts)
    monthly_inflation = annual_inflation_rate / 12
    calculated = round(avg * (1 + monthly_inflation), 2)
    inflation_amount = round(calculated - avg, 2)

    return {
        "calculated_amount": calculated,
        "historical_months": len(amounts),
        "avg_amount": round(avg, 2),
        "inflation_amount": inflation_amount,
        "monthly_amounts": historical_amounts
    }


# -------- DynamoDB helpers for config --------
def _ddb_get_config(config_id: str) -> list[dict] | None:
    try:
        resp = ddb.get_item(
            TableName=CONFIG_TABLE,
            Key={
                "PK": {"S": f"CONFIG#{config_id}"},
                "SK": {"S": "v1"}
            }
        )
        if "Item" not in resp:
            return None
        item = resp["Item"]
        data_str = item.get("Data", {}).get("S") or item.get("Data", {}).get("S")
        if not data_str:
            return None
        parsed = json.loads(data_str)
        return parsed if isinstance(parsed, list) else None
    except Exception:
        return None


def _ddb_put_config(config_id: str, arr: list[dict]) -> bool:
    try:
        data_json = json.dumps(arr, ensure_ascii=False)
        data_size = len(data_json.encode('utf-8'))
        print(f"[DDB PUT CONFIG] Saving config '{config_id}' with {len(arr)} items, {data_size} bytes")

        if data_size > 400000:
            print(f"[DDB PUT CONFIG] WARNING: Data size {data_size} bytes may exceed DynamoDB limit!")

        ddb.put_item(
            TableName=CONFIG_TABLE,
            Item={
                "PK": {"S": f"CONFIG#{config_id}"},
                "SK": {"S": "v1"},
                "UpdatedAt": {"S": datetime.utcnow().isoformat() + "Z"},
                "Data": {"S": data_json}
            }
        )
        print(f"[DDB PUT CONFIG] Successfully saved config '{config_id}'")
        return True
    except Exception as e:
        print(f"[DDB PUT CONFIG] ERROR saving config '{config_id}': {e}")
        import traceback
        traceback.print_exc()
        return False


def _ddb_get_draft(bill_id: str) -> dict | None:
    """Load bill draft from S3 JSONL file"""
    try:
        # bill_id is the S3 key
        obj = s3.get_object(Bucket=BUCKET, Key=bill_id)
        body = obj["Body"].read().decode("utf-8")

        # Parse JSONL (each line is a JSON record)
        line_data = []
        for line in body.strip().split("\n"):
            if line.strip():
                try:
                    rec = json.loads(line)
                    line_data.append(rec)
                except:
                    pass

        return {
            "bill_id": bill_id,
            "line_data": line_data,
            "updated_utc": datetime.utcnow().isoformat(),
            "updated_by": ""
        }
    except Exception as e:
        print(f"[_ddb_get_draft] Error loading {bill_id}: {e}")
        return None


def _ddb_put_draft(draft: dict) -> bool:
    """Save bill draft back to S3 JSONL file"""
    try:
        bill_id = draft.get("bill_id", "")
        line_data = draft.get("line_data", [])

        if not bill_id:
            return False

        # Convert to JSONL (one JSON record per line)
        jsonl_lines = [json.dumps(rec, ensure_ascii=False) for rec in line_data]
        jsonl_content = "\n".join(jsonl_lines)

        # Write back to S3
        s3.put_object(
            Bucket=BUCKET,
            Key=bill_id,
            Body=jsonl_content.encode("utf-8"),
            ContentType="application/x-ndjson"
        )
        return True
    except Exception as e:
        print(f"[_ddb_put_draft] Error saving {draft.get('bill_id', 'unknown')}: {e}")
        return False


@app.get("/api/catalog/vendors")
def api_catalog_vendors(user: str = Depends(require_user), response: Response = None, refresh: str = "0"):
    cache_key = ("catalog_vendors",)
    now = time.time()
    ent = _CACHE.get(cache_key)
    if refresh != "1" and ent and (now - ent.get("ts", 0) < CACHE_TTL_SECONDS):
        try:
            if response is not None:
                response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
        except Exception:
            pass
        return {"items": ent.get("data", [])}
    # Try to load from vendor cache (has vendorCode) with fallback to dim_vendor
    out = []
    seen_keys = set()
    try:
        vend_cache_obj = s3.get_object(Bucket="api-vendor", Key="vendors/latest.json")
        vend_cache_data = json.loads(vend_cache_obj["Body"].read().decode("utf-8"))
        vendor_list = vend_cache_data.get("vendors", [])
        for v in vendor_list:
            # Use displayName for dropdown (includes location and code)
            display = str(v.get("displayName") or v.get("name", "")).strip()
            vid = str(v.get("vendorId", "")).strip()
            vcode = str(v.get("vendorCode", "")).strip()
            loc_id = str(v.get("locationId", "")).strip()
            # Dedupe by vendorId+locationId (each vendor+location combo is unique)
            dedup_key = f"{vid}|{loc_id}"
            if (vid or display) and dedup_key not in seen_keys:
                out.append({"id": vid, "name": display, "code": vcode, "locationId": loc_id})
                seen_keys.add(dedup_key)
    except Exception as e:
        print(f"[api_catalog_vendors] ERROR loading vendor cache: {e}, falling back to dim_vendor")
        rows = _load_dim_records(DIM_VENDOR_PREFIX)
        for r in rows:
            vid = (
                r.get("vendorId") or r.get("VENDOR_ID") or r.get("Vendor ID") or r.get("id") or r.get("vendor_id") or r.get("VENDORID")
            )
            name = (
                r.get("name") or r.get("NAME") or r.get("vendorName") or r.get("Vendor Name") or r.get("Vendor") or r.get("VENDOR_NAME") or r.get("VENDOR")
            )
            code = r.get("vendorCode") or r.get("VENDOR_CODE") or r.get("code") or r.get("Code") or r.get("VENDORCODE")
            vid_str = str(vid or "").strip()
            name_str = str(name or "").strip()
            # Dedupe by vendor ID (not name) - multiple vendors can have same name (e.g. City of Tacoma)
            if (vid or name) and vid_str not in seen_keys:
                out.append({"id": vid_str, "name": name_str, "code": str(code or "").strip()})
                seen_keys.add(vid_str)
    out = sorted(out, key=lambda x: x["name"].upper())
    _CACHE[cache_key] = {"ts": now, "data": out}
    try:
        if response is not None:
            response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
    except Exception:
        pass
    return {"items": out}


@app.get("/api/catalog/properties")
def api_catalog_properties(user: str = Depends(require_user), response: Response = None, refresh: str = "0"):
    cache_key = ("catalog_properties",)
    now = time.time()
    ent = _CACHE.get(cache_key)
    if refresh != "1" and ent and (now - ent.get("ts", 0) < CACHE_TTL_SECONDS):
        try:
            if response is not None:
                response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
        except Exception:
            pass
        return {"items": ent.get("data", [])}
    rows = _load_dim_records(DIM_PROPERTY_PREFIX)
    items = []
    for r in rows:
        pid = (
            r.get("propertyId") or r.get("PROPERTY_ID") or r.get("Property ID") or r.get("id") or r.get("PROPERTYID") or r.get("PROP_ID")
        )
        name = (
            r.get("name") or r.get("NAME") or r.get("propertyName") or r.get("Property Name") or r.get("PROPERTY_NAME") or r.get("Property") or r.get("PROPERTY")
        )
        if pid or name:
            items.append({"id": str(pid or "").strip(), "name": str(name or "").strip()})
    _CACHE[cache_key] = {"ts": now, "data": items}
    try:
        if response is not None:
            response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
    except Exception:
        pass
    return {"items": items}


@app.get("/api/catalog/gl-accounts")
def api_catalog_gl_accounts(user: str = Depends(require_user), response: Response = None, refresh: str = "0"):
    cache_key = ("catalog_gl_accounts",)
    now = time.time()
    ent = _CACHE.get(cache_key)
    if refresh != "1" and ent and (now - ent.get("ts", 0) < CACHE_TTL_SECONDS):
        try:
            if response is not None:
                response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
        except Exception:
            pass
        return {"items": ent.get("data", [])}
    rows = _load_dim_records(DIM_GL_PREFIX)
    items = []
    for r in rows:
        gl_id = (
            r.get("glAccountId") or r.get("GL_ACCOUNT_ID") or r.get("GL Account ID") or r.get("id") or r.get("ID") or r.get("GLACCOUNTID")
        )
        num_raw = (
            r.get("glAccountNumber") or r.get("GL Account Number") or r.get("GL_ACCOUNT_NUMBER") or r.get("number") or r.get("ACCOUNT_NO") or r.get("GL_NUMBER") or r.get("ACCOUNT_NUMBER")
        )
        num_fmt = (
            r.get("formattedGlAccountNumber") or r.get("Formatted GL Account Number") or r.get("FORMATTED_GL_ACCOUNT_NUMBER") or r.get("FORMATTED_ACCOUNT_NUMBER") or r.get("formatted")
        )
        name = (
            r.get("glAccountName") or r.get("GL Account Name") or r.get("GL_ACCOUNT_NAME") or r.get("name") or r.get("NAME") or r.get("DESCRIPTION") or r.get("Account Name") or r.get("ACCOUNT_NAME") or r.get("GLAccountDescription") or r.get("DESCRIPTION_LONG")
        )
        if (num_raw or num_fmt or name):
            items.append({
                "id": str(gl_id or "").strip(),
                "number": str((num_fmt or num_raw or "")).strip(),  # prefer formatted for selection
                "rawNumber": str(num_raw or "").strip(),
                "formatted": str(num_fmt or (num_raw or "")).strip(),
                "name": str(name or "").strip()
            })
    # Sort by name for consistent alphabetical display
    items = sorted(items, key=lambda x: x["name"].upper())
    _CACHE[cache_key] = {"ts": now, "data": items}
    try:
        if response is not None:
            response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
    except Exception:
        pass
    return {"items": items}


@app.get("/api/config/accounts-to-track")
def api_get_accounts_to_track(user: str = Depends(require_user), response: Response = None, refresh: str = "0"):
    cache_key = ("accounts_to_track",)
    now = time.time()
    ent = _CACHE.get(cache_key)
    if refresh != "1" and ent and (now - ent.get("ts", 0) < CACHE_TTL_SECONDS):
        try:
            if response is not None:
                response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
        except Exception:
            pass
        return {"items": ent.get("data", [])}
    arr = _ddb_get_config("accounts-to-track")
    if arr is None:
        arr = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
    if not isinstance(arr, list):
        arr = []
    # Ensure is_tracked and is_ubi flags exist
    for item in arr:
        if "is_tracked" not in item:
            item["is_tracked"] = True  # Default: tracked
        if "is_ubi" not in item:
            item["is_ubi"] = False  # Default: not UBI
    _CACHE[cache_key] = {"ts": now, "data": arr}
    try:
        if response is not None:
            response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
    except Exception:
        pass
    return {"items": arr}


@app.post("/api/config/accounts-to-track")
async def api_save_accounts_to_track(request: Request, user: str = Depends(require_user)):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return JSONResponse({"error": "items must be a list"}, status_code=400)
    # Basic normalization
    norm = []
    for r in items:
        norm.append({
            "vendorId": str(r.get("vendorId") or "").strip(),
            "vendorName": str(r.get("vendorName") or "").strip(),
            "accountNumber": str(r.get("accountNumber") or "").strip(),
            "propertyId": str(r.get("propertyId") or "").strip(),
            "propertyName": str(r.get("propertyName") or "").strip(),
            "glAccountNumber": str(r.get("glAccountNumber") or "").strip(),
            "glAccountName": str(r.get("glAccountName") or "").strip(),
            "daysBetweenBills": int(str(r.get("daysBetweenBills") or "0").strip() or 0),
            "is_tracked": bool(r.get("is_tracked", True)),
            "is_ubi": bool(r.get("is_ubi", False))
        })
    # Try to write to both stores (best effort), succeed if at least one works
    ddb_ok = False
    s3_ok = False
    ddb_err = None
    s3_err = None
    try:
        ddb_ok = _ddb_put_config("accounts-to-track", norm)
    except Exception as e:
        ddb_ok = False
        ddb_err = str(e)
    try:
        _s3_put_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY, norm)
        s3_ok = True
    except Exception as e:
        s3_ok = False
        s3_err = str(e)
    if not (ddb_ok or s3_ok):
        return JSONResponse({"error": "save_failed", "ddb_ok": ddb_ok, "s3_ok": s3_ok, "bucket": CONFIG_BUCKET, "key": ACCOUNTS_TRACK_KEY, "ddb_error": ddb_err, "s3_error": s3_err}, status_code=500)
    return {"ok": True, "saved": len(norm), "ddb_ok": ddb_ok, "s3_ok": s3_ok}


@app.post("/api/config/toggle-ubi-tracking")
async def api_toggle_ubi_tracking(request: Request, user: str = Depends(require_user)):
    """Toggle UBI tracking for a single account."""
    try:
        form = await request.form()
        account_number = form.get("account_number", "").strip()
        vendor_name = form.get("vendor_name", "").strip()
        property_name = form.get("property_name", "").strip()

        if not account_number:
            return JSONResponse({"error": "account_number is required"}, status_code=400)

        # Load current config
        arr = _ddb_get_config("accounts-to-track")
        if arr is None:
            arr = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
        if not isinstance(arr, list):
            arr = []

        # Find existing account or create new one
        found = False
        for item in arr:
            if item.get("account_number") == account_number:
                # Toggle ubi_tracking
                current = item.get("ubi_tracking", False)
                item["ubi_tracking"] = not current
                found = True
                break

        # If not found, add new account with UBI tracking enabled
        if not found:
            new_account = {
                "account_number": account_number,
                "vendor_name": vendor_name,
                "property_name": property_name,
                "ubi_tracking": True
            }
            arr.append(new_account)

        # Save back to both stores
        ddb_ok = False
        s3_ok = False
        try:
            ddb_ok = _ddb_put_config("accounts-to-track", arr)
        except Exception as e:
            print(f"[UBI TOGGLE] DDB error: {e}")
        try:
            _s3_put_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY, arr)
            s3_ok = True
        except Exception as e:
            print(f"[UBI TOGGLE] S3 error: {e}")

        if not (ddb_ok or s3_ok):
            return JSONResponse({"error": "save_failed"}, status_code=500)

        # Clear cache
        cache_key = ("accounts_to_track",)
        _CACHE.pop(cache_key, None)

        # Get updated status
        updated_item = next((item for item in arr if item.get("account_number") == account_number), None)
        ubi_status = updated_item.get("ubi_tracking", False) if updated_item else False

        return {"ok": True, "account_number": account_number, "ubi_tracking": ubi_status}

    except Exception as e:
        print(f"[UBI TOGGLE] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/config/add-to-tracker")
async def api_add_to_tracker(request: Request, user: str = Depends(require_user)):
    """Add an account to the accounts-to-track configuration."""
    try:
        form = await request.form()
        account_number = form.get("account_number", "").strip()
        vendor_name = form.get("vendor_name", "").strip()
        property_name = form.get("property_name", "").strip()
        gl_account = form.get("gl_account", "").strip()
        gl_account_name = form.get("gl_account_name", "").strip()
        days_between_bills_str = form.get("days_between_bills", "").strip()

        if not account_number or not gl_account or not days_between_bills_str:
            return JSONResponse({"error": "account_number, gl_account, and days_between_bills are required"}, status_code=400)

        try:
            days_between_bills = int(days_between_bills_str)
        except ValueError:
            return JSONResponse({"error": "days_between_bills must be a number"}, status_code=400)

        # Load current config
        arr = _ddb_get_config("accounts-to-track")
        if arr is None:
            arr = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
        if not isinstance(arr, list):
            arr = []

        # Check if account already exists and update or add new
        found = False
        for item in arr:
            if item.get("account_number") == account_number:
                # Update existing
                item["vendor_name"] = vendor_name
                item["property_name"] = property_name
                item["gl_account"] = gl_account
                item["gl_account_name"] = gl_account_name
                item["days_between_bills"] = days_between_bills
                if "ubi_tracking" not in item:
                    item["ubi_tracking"] = False  # Default to False
                found = True
                break

        if not found:
            # Add new account
            new_account = {
                "account_number": account_number,
                "vendor_name": vendor_name,
                "property_name": property_name,
                "gl_account": gl_account,
                "gl_account_name": gl_account_name,
                "days_between_bills": days_between_bills,
                "ubi_tracking": False
            }
            arr.append(new_account)

        # Save to both stores
        ddb_ok = False
        s3_ok = False
        try:
            ddb_ok = _ddb_put_config("accounts-to-track", arr)
        except Exception as e:
            print(f"[ADD TO TRACKER] DDB error: {e}")
        try:
            _s3_put_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY, arr)
            s3_ok = True
        except Exception as e:
            print(f"[ADD TO TRACKER] S3 error: {e}")

        if not (ddb_ok or s3_ok):
            return JSONResponse({"error": "save_failed"}, status_code=500)

        # Clear cache
        cache_key = ("accounts_to_track",)
        _CACHE.pop(cache_key, None)

        return {"ok": True, "account_number": account_number}

    except Exception as e:
        print(f"[ADD TO TRACKER] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/config/add-to-ubi")
async def api_add_to_ubi(request: Request, user: str = Depends(require_user)):
    """Add charge code to UBI mapping configuration with full 4-field composite key."""
    try:
        form = await request.form()
        # Get all required fields for 4-field composite key
        vendor_id = form.get("vendor_id", "").strip()
        vendor_name = form.get("vendor_name", "").strip()
        account_number = form.get("account_number", "").strip()
        property_id = form.get("property_id", "").strip()
        property_name = form.get("property_name", "").strip()
        gl_account = form.get("gl_account", "").strip()
        gl_account_name = form.get("gl_account_name", "").strip()
        charge_code = form.get("charge_code", "").strip()
        utility_name = form.get("utility_name", "").strip()
        notes = form.get("notes", "").strip()

        # Validate required fields for 4-field composite key
        if not vendor_id or not account_number or not property_id or not gl_account or not charge_code:
            return JSONResponse({
                "error": "vendor_id, account_number, property_id, gl_account, and charge_code are required"
            }, status_code=400)

        print(f"[ADD TO UBI] Adding mapping: vendor={vendor_id}, account={account_number}, property={property_id}, gl={gl_account}, charge_code={charge_code}")

        # Load current ubi-mapping config
        arr = _ddb_get_config("ubi-mapping")
        if arr is None:
            arr = []
        if not isinstance(arr, list):
            arr = []

        # Check if this exact combination already exists (4-field composite key)
        exists = any(
            str(item.get("vendorId", "")).strip() == vendor_id and
            str(item.get("accountNumber", "")).strip() == account_number and
            str(item.get("propertyId", "")).strip() == property_id and
            str(item.get("glAccountNumber", "")).strip() == gl_account
            for item in arr
        )

        if exists:
            print(f"[ADD TO UBI] Mapping already exists, skipping")
            return {"ok": True, "message": "UBI mapping already exists", "added": 0}

        # Add the new UBI mapping entry with ALL fields using camelCase (to match ubi-mapping GET endpoint)
        new_mapping = {
            "vendorId": vendor_id,
            "vendorName": vendor_name,
            "accountNumber": account_number,
            "propertyId": property_id,
            "propertyName": property_name,
            "glAccountNumber": gl_account,
            "glAccountName": gl_account_name,
            "chargeCode": charge_code,
            "utilityName": utility_name,
            "isUbi": True,  # Mark as UBI when adding via this endpoint
            "notes": notes
        }
        arr.append(new_mapping)

        print(f"[ADD TO UBI] Saving {len(arr)} total mappings to DDB/S3")

        # Save UBI mapping to DDB
        ddb_ok = False
        try:
            ddb_ok = _ddb_put_config("ubi-mapping", arr)
            print(f"[ADD TO UBI] DDB save: {ddb_ok}")
        except Exception as e:
            print(f"[ADD TO UBI] DDB error: {e}")

        # Clear UBI mapping cache
        cache_key = ("ubi_mapping",)
        _CACHE.pop(cache_key, None)

        if not ddb_ok:
            return JSONResponse({"error": "save_failed"}, status_code=500)

        return {"ok": True, "added": 1, "mapping": new_mapping}

    except Exception as e:
        print(f"[ADD TO UBI] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


# -------- UBI Account Management --------
@app.post("/api/ubi/add-to-tracker")
async def api_add_to_tracker(request: Request, user: str = Depends(require_user)):
    """Add account to tracker (monitoring only) - sets is_tracked=true, is_ubi=false"""
    try:
        form = await request.form()
        vendor_id = form.get("vendor_id", "").strip()
        vendor_name = form.get("vendor_name", "").strip()
        account_number = form.get("account_number", "").strip()
        property_id = form.get("property_id", "").strip()
        property_name = form.get("property_name", "").strip()

        # Look up property_id from property_name if not provided
        if not property_id and property_name:
            rows = _load_dim_records(DIM_PROPERTY_PREFIX)
            props = []
            for r in rows:
                pid = (r.get("propertyId") or r.get("PROPERTY_ID") or r.get("Property ID") or r.get("id") or r.get("PROPERTYID") or r.get("PROP_ID"))
                name = (r.get("name") or r.get("NAME") or r.get("propertyName") or r.get("Property Name") or r.get("PROPERTY_NAME") or r.get("Property") or r.get("PROPERTY"))
                if pid or name:
                    props.append({"id": str(pid or "").strip(), "name": str(name or "").strip()})

            print(f"[ADD TO TRACKER] Looking up property_id for '{property_name}' in {len(props)} properties")
            for p in props:
                if p.get("name", "").strip() == property_name:
                    property_id = p.get("id", "").strip()
                    print(f"[ADD TO TRACKER] Found property_id: {property_id}")
                    break
            if not property_id:
                print(f"[ADD TO TRACKER] Could not find property_id for '{property_name}'")

        # Look up vendor_id from vendor_name if not provided
        if not vendor_id and vendor_name:
            rows = _load_dim_records(DIM_VENDOR_PREFIX)
            vendors = []
            for r in rows:
                vid = (r.get("vendorId") or r.get("VENDOR_ID") or r.get("Vendor ID") or r.get("id") or r.get("vendor_id") or r.get("VENDORID"))
                name = (r.get("name") or r.get("NAME") or r.get("vendorName") or r.get("Vendor Name") or r.get("Vendor") or r.get("VENDOR_NAME") or r.get("VENDOR"))
                if vid or name:
                    vendors.append({"id": str(vid or "").strip(), "name": str(name or "").strip()})

            print(f"[ADD TO TRACKER] Looking up vendor_id for '{vendor_name}' in {len(vendors)} vendors")
            for v in vendors:
                if v.get("name", "").strip() == vendor_name:
                    vendor_id = v.get("id", "").strip()
                    print(f"[ADD TO TRACKER] Found vendor_id: {vendor_id}")
                    break
            if not vendor_id:
                print(f"[ADD TO TRACKER] Could not find vendor_id for '{vendor_name}'")

        print(f"[ADD TO TRACKER] Final values: vendor_id={vendor_id}, account_number={account_number}, property_id={property_id}")

        if not vendor_id or not account_number or not property_id:
            error_msg = f"vendor_id={vendor_id or 'MISSING'}, account_number={account_number or 'MISSING'}, property_id={property_id or 'MISSING'} (could not resolve from names: vendor_name={vendor_name}, property_name={property_name})"
            print(f"[ADD TO TRACKER] ERROR: {error_msg}")
            return JSONResponse({"error": error_msg}, status_code=400)

        # Load accounts-to-track
        arr = _ddb_get_config("accounts-to-track")
        if arr is None:
            arr = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
        if not isinstance(arr, list):
            arr = []

        # Check if account exists
        found = False
        for item in arr:
            if (str(item.get("vendorId", "")).strip() == vendor_id and
                str(item.get("accountNumber", "")).strip() == account_number and
                str(item.get("propertyId", "")).strip() == property_id):
                # Update existing account
                item["is_tracked"] = True
                found = True
                break

        if not found:
            # Create new account
            arr.append({
                "vendorId": vendor_id,
                "vendorName": vendor_name,
                "accountNumber": account_number,
                "propertyId": property_id,
                "propertyName": property_name,
                "glAccountNumber": "",
                "glAccountName": "",
                "daysBetweenBills": 30,
                "is_tracked": True,
                "is_ubi": False,
                "notes": f"Added to tracker by {user}"
            })

        # Save to DynamoDB
        ddb_ok = _ddb_put_config("accounts-to-track", arr)
        if not ddb_ok:
            return JSONResponse({"error": "save_failed"}, status_code=500)

        # Also save to S3 as backup (critical for persistence)
        try:
            _s3_put_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY, arr)
            print(f"[ADD TO TRACKER] Saved to S3 backup: {ACCOUNTS_TRACK_KEY}")
        except Exception as s3_err:
            print(f"[ADD TO TRACKER] S3 backup error (non-fatal): {s3_err}")

        # Clear cache
        cache_key = ("accounts_to_track",)
        _CACHE.pop(cache_key, None)

        return {"ok": True, "existed": found}

    except Exception as e:
        print(f"[ADD TO TRACKER] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ubi/add-to-ubi")
async def api_add_account_to_ubi(request: Request, user: str = Depends(require_user)):
    """Add account to UBI program - sets is_ubi=true (creates account if doesn't exist)"""
    try:
        form = await request.form()
        vendor_id = form.get("vendor_id", "").strip()
        vendor_name = form.get("vendor_name", "").strip()
        account_number = form.get("account_number", "").strip()
        property_id = form.get("property_id", "").strip()
        property_name = form.get("property_name", "").strip()

        # Look up property_id from property_name if not provided
        if not property_id and property_name:
            rows = _load_dim_records(DIM_PROPERTY_PREFIX)
            props = []
            for r in rows:
                pid = (r.get("propertyId") or r.get("PROPERTY_ID") or r.get("Property ID") or r.get("id") or r.get("PROPERTYID") or r.get("PROP_ID"))
                name = (r.get("name") or r.get("NAME") or r.get("propertyName") or r.get("Property Name") or r.get("PROPERTY_NAME") or r.get("Property") or r.get("PROPERTY"))
                if pid or name:
                    props.append({"id": str(pid or "").strip(), "name": str(name or "").strip()})

            print(f"[ADD TO UBI] Looking up property_id for '{property_name}' in {len(props)} properties")
            for p in props:
                if p.get("name", "").strip() == property_name:
                    property_id = p.get("id", "").strip()
                    print(f"[ADD TO UBI] Found property_id: {property_id}")
                    break
            if not property_id:
                print(f"[ADD TO UBI] Could not find property_id for '{property_name}'")

        # Look up vendor_id from vendor_name if not provided
        if not vendor_id and vendor_name:
            rows = _load_dim_records(DIM_VENDOR_PREFIX)
            vendors = []
            for r in rows:
                vid = (r.get("vendorId") or r.get("VENDOR_ID") or r.get("Vendor ID") or r.get("id") or r.get("vendor_id") or r.get("VENDORID"))
                name = (r.get("name") or r.get("NAME") or r.get("vendorName") or r.get("Vendor Name") or r.get("Vendor") or r.get("VENDOR_NAME") or r.get("VENDOR"))
                if vid or name:
                    vendors.append({"id": str(vid or "").strip(), "name": str(name or "").strip()})

            print(f"[ADD TO UBI] Looking up vendor_id for '{vendor_name}' in {len(vendors)} vendors")
            for v in vendors:
                if v.get("name", "").strip() == vendor_name:
                    vendor_id = v.get("id", "").strip()
                    print(f"[ADD TO UBI] Found vendor_id: {vendor_id}")
                    break
            if not vendor_id:
                print(f"[ADD TO UBI] Could not find vendor_id for '{vendor_name}'")

        print(f"[ADD TO UBI] Final values: vendor_id={vendor_id}, account_number={account_number}, property_id={property_id}")

        if not vendor_id or not account_number or not property_id:
            error_msg = f"vendor_id={vendor_id or 'MISSING'}, account_number={account_number or 'MISSING'}, property_id={property_id or 'MISSING'} (could not resolve from names: vendor_name={vendor_name}, property_name={property_name})"
            print(f"[ADD TO UBI] ERROR: {error_msg}")
            return JSONResponse({"error": error_msg}, status_code=400)

        # Load accounts-to-track
        arr = _ddb_get_config("accounts-to-track")
        if arr is None:
            arr = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
        if not isinstance(arr, list):
            arr = []

        # Check if account exists
        found = False
        for item in arr:
            if (str(item.get("vendorId", "")).strip() == vendor_id and
                str(item.get("accountNumber", "")).strip() == account_number and
                str(item.get("propertyId", "")).strip() == property_id):
                # Update existing account
                item["is_ubi"] = True
                found = True
                break

        if not found:
            # Create new account with is_ubi=True
            arr.append({
                "vendorId": vendor_id,
                "vendorName": vendor_name,
                "accountNumber": account_number,
                "propertyId": property_id,
                "propertyName": property_name,
                "glAccountNumber": "",
                "glAccountName": "",
                "daysBetweenBills": 30,
                "is_tracked": False,  # Not tracked (yet)
                "is_ubi": True,        # UBI enabled
                "notes": f"Added to UBI by {user}"
            })

        # Save to DynamoDB
        ddb_ok = _ddb_put_config("accounts-to-track", arr)
        if not ddb_ok:
            return JSONResponse({"error": "save_failed"}, status_code=500)

        # Also save to S3 as backup (critical for persistence)
        try:
            _s3_put_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY, arr)
            print(f"[ADD TO UBI] Saved to S3 backup: {ACCOUNTS_TRACK_KEY}")
        except Exception as s3_err:
            print(f"[ADD TO UBI] S3 backup error (non-fatal): {s3_err}")

        # Clear cache
        cache_key = ("accounts_to_track",)
        _CACHE.pop(cache_key, None)

        return {"ok": True, "message": "Account added to UBI program"}

    except Exception as e:
        print(f"[ADD TO UBI] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ubi/remove-from-tracker")
async def api_remove_from_tracker(request: Request, user: str = Depends(require_user)):
    """Remove account from tracker - sets is_tracked=false"""
    try:
        form = await request.form()
        vendor_id = form.get("vendor_id", "").strip()
        vendor_name = form.get("vendor_name", "").strip()
        account_number = form.get("account_number", "").strip()
        property_id = form.get("property_id", "").strip()
        property_name = form.get("property_name", "").strip()

        # Load accounts-to-track
        arr = _ddb_get_config("accounts-to-track")
        if arr is None:
            arr = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
        if not isinstance(arr, list):
            arr = []

        # Find and update account
        found = False
        for item in arr:
            # Match by account_number + vendor_name + property_name
            if (str(item.get("accountNumber", "")).strip() == account_number and
                str(item.get("vendorName", "")).strip() == vendor_name and
                str(item.get("propertyName", "")).strip() == property_name):
                item["is_tracked"] = False
                found = True
                break

        if not found:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        # Save to DynamoDB
        ddb_ok = _ddb_put_config("accounts-to-track", arr)
        if not ddb_ok:
            return JSONResponse({"error": "save_failed"}, status_code=500)

        # Also save to S3 as backup
        try:
            _s3_put_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY, arr)
            print(f"[REMOVE FROM TRACKER] Saved to S3 backup: {ACCOUNTS_TRACK_KEY}")
        except Exception as s3_err:
            print(f"[REMOVE FROM TRACKER] S3 backup error (non-fatal): {s3_err}")

        # Clear cache
        cache_key = ("accounts_to_track",)
        _CACHE.pop(cache_key, None)

        return {"ok": True}

    except Exception as e:
        print(f"[REMOVE FROM TRACKER] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ubi/remove-from-ubi")
async def api_remove_from_ubi(request: Request, user: str = Depends(require_user)):
    """Remove account from UBI - sets is_ubi=false"""
    try:
        form = await request.form()
        vendor_id = form.get("vendor_id", "").strip()
        vendor_name = form.get("vendor_name", "").strip()
        account_number = form.get("account_number", "").strip()
        property_id = form.get("property_id", "").strip()
        property_name = form.get("property_name", "").strip()

        # Load accounts-to-track
        arr = _ddb_get_config("accounts-to-track")
        if arr is None:
            arr = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
        if not isinstance(arr, list):
            arr = []

        # Find and update account
        found = False
        for item in arr:
            # Match by account_number + vendor_name + property_name
            if (str(item.get("accountNumber", "")).strip() == account_number and
                str(item.get("vendorName", "")).strip() == vendor_name and
                str(item.get("propertyName", "")).strip() == property_name):
                item["is_ubi"] = False
                found = True
                break

        if not found:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        # Save to DynamoDB
        ddb_ok = _ddb_put_config("accounts-to-track", arr)
        if not ddb_ok:
            return JSONResponse({"error": "save_failed"}, status_code=500)

        # Also save to S3 as backup
        try:
            _s3_put_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY, arr)
            print(f"[REMOVE FROM UBI] Saved to S3 backup: {ACCOUNTS_TRACK_KEY}")
        except Exception as s3_err:
            print(f"[REMOVE FROM UBI] S3 backup error (non-fatal): {s3_err}")

        # Clear cache
        cache_key = ("accounts_to_track",)
        _CACHE.pop(cache_key, None)

        return {"ok": True}

    except Exception as e:
        print(f"[REMOVE FROM UBI] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


# -------- UBI Billback Line Item Management --------
@app.post("/api/billback/update-line-item")
async def api_update_line_item(request: Request, user: str = Depends(require_user)):
    """Update line item with charge code, amount overrides, and exclusions"""
    try:
        form = await request.form()
        bill_id = form.get("bill_id", "").strip()
        line_index = int(form.get("line_index", "-1"))

        if not bill_id or line_index < 0:
            return JSONResponse({"error": "bill_id and line_index required"}, status_code=400)

        # Get draft
        draft = _ddb_get_draft(bill_id)
        if not draft or "line_data" not in draft:
            return JSONResponse({"error": "bill not found"}, status_code=404)

        line_data = draft.get("line_data", [])
        if line_index >= len(line_data):
            return JSONResponse({"error": "line_index out of range"}, status_code=400)

        line = line_data[line_index]

        # Update charge code fields
        if "charge_code" in form:
            line["Charge Code"] = form.get("charge_code", "").strip()
        if "charge_code_source" in form:
            line["Charge Code Source"] = form.get("charge_code_source", "mapping").strip()
        if "charge_code_overridden" in form:
            line["Charge Code Overridden"] = form.get("charge_code_overridden", "").lower() in ["true", "1"]
        if "charge_code_override_reason" in form:
            line["Charge Code Override Reason"] = form.get("charge_code_override_reason", "").strip()
        # Store mapped utility name for disambiguating duplicate charge codes (e.g., UBILL entries)
        if "utility_name" in form:
            line["Mapped Utility Name"] = form.get("utility_name", "").strip()

        # Update amount override fields
        if "current_amount" in form:
            try:
                line["Current Amount"] = float(form.get("current_amount", "0"))
            except:
                pass
        if "amount_overridden" in form:
            line["Amount Overridden"] = form.get("amount_overridden", "").lower() in ["true", "1"]
        if "amount_override_reason" in form:
            line["Amount Override Reason"] = form.get("amount_override_reason", "").strip()

        # Update exclusion fields
        if "is_excluded_from_ubi" in form:
            line["Is Excluded From UBI"] = int(form.get("is_excluded_from_ubi", "0"))
        if "exclusion_reason" in form:
            line["Exclusion Reason"] = form.get("exclusion_reason", "").strip()

        # Update line and save
        line_data[line_index] = line
        draft["line_data"] = line_data
        draft["updated_utc"] = datetime.utcnow().isoformat()
        draft["updated_by"] = user

        _ddb_put_draft(draft)

        return {"ok": True, "line": line}

    except Exception as e:
        print(f"[UPDATE LINE ITEM] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/billback/assign-periods")
async def api_assign_billback_periods(request: Request, user: str = Depends(require_user)):
    """Assign billback periods to a line item"""
    try:
        form = await request.form()
        bill_id = form.get("bill_id", "").strip()
        line_index = int(form.get("line_index", "-1"))
        assignments_json = form.get("assignments", "[]")

        if not bill_id or line_index < 0:
            return JSONResponse({"error": "bill_id and line_index required"}, status_code=400)

        # Parse assignments
        import json
        try:
            assignments = json.loads(assignments_json)
        except:
            return JSONResponse({"error": "invalid assignments JSON"}, status_code=400)

        if not isinstance(assignments, list):
            return JSONResponse({"error": "assignments must be a list"}, status_code=400)

        # Get draft
        draft = _ddb_get_draft(bill_id)
        if not draft or "line_data" not in draft:
            return JSONResponse({"error": "bill not found"}, status_code=404)

        line_data = draft.get("line_data", [])
        if line_index >= len(line_data):
            return JSONResponse({"error": "line_index out of range"}, status_code=400)

        # Update billback assignments
        line_data[line_index]["billback_assignments"] = assignments
        line_data[line_index]["updated_by"] = user
        line_data[line_index]["updated_utc"] = datetime.utcnow().isoformat()

        draft["line_data"] = line_data
        draft["updated_utc"] = datetime.utcnow().isoformat()
        draft["updated_by"] = user

        _ddb_put_draft(draft)

        return {"ok": True}

    except Exception as e:
        print(f"[ASSIGN PERIODS] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/billback/send-to-post")
async def api_send_bill_to_post(request: Request, user: str = Depends(require_user)):
    """Move a bill back from Stage 7 (PostEntrata) to Stage 6 (PreEntrata) for reprocessing."""
    try:
        form = await request.form()
        bill_id = form.get("bill_id", "").strip()

        if not bill_id:
            return JSONResponse({"error": "bill_id required"}, status_code=400)

        if not bill_id.startswith(POST_ENTRATA_PREFIX):
            return JSONResponse({"error": "Bill is not in PostEntrata stage"}, status_code=400)

        # Build Stage 6 destination key
        stage6_key = bill_id.replace(POST_ENTRATA_PREFIX, STAGE6_PREFIX)

        # Copy from Stage 7 to Stage 6
        s3.copy_object(
            Bucket=BUCKET,
            CopySource={"Bucket": BUCKET, "Key": bill_id},
            Key=stage6_key
        )

        # Delete from Stage 7 (BILLBACK) to complete the move
        s3.delete_object(Bucket=BUCKET, Key=bill_id)

        print(f"[SEND TO POST] Moved {bill_id} to {stage6_key} by {user}")

        return {"ok": True, "stage6_key": stage6_key, "moved": True}
    except Exception as e:
        print(f"[SEND TO POST] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


# -------- GL Code to Charge Code Mapping (Property-Aware) --------
@app.get("/api/config/gl-charge-code-mapping")
def api_get_gl_charge_code_mapping(user: str = Depends(require_user)):
    """Get property-aware GL code to charge code mappings"""
    arr = _ddb_get_config("gl-charge-code-mapping")
    if arr is None:
        arr = []
    if not isinstance(arr, list):
        arr = []
    return {"items": arr}


@app.post("/api/config/gl-charge-code-mapping")
async def api_save_gl_charge_code_mapping(request: Request, user: str = Depends(require_user)):
    """Save property-aware GL code to charge code mappings"""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return JSONResponse({"error": "items must be a list"}, status_code=400)

    # Normalize items
    norm = []
    for r in items:
        norm.append({
            "property_id": str(r.get("property_id") or "").strip(),
            "property_name": str(r.get("property_name") or "").strip(),
            "gl_code": str(r.get("gl_code") or "").strip(),
            "gl_code_name": str(r.get("gl_code_name") or "").strip(),
            "gl_account_id": str(r.get("gl_account_id") or "").strip(),
            "charge_code": str(r.get("charge_code") or "").strip(),
            "utility_name": str(r.get("utility_name") or "").strip(),
            "is_billable": bool(r.get("is_billable", True)),
            "notes": str(r.get("notes") or "").strip()
        })

    # Save to DDB
    ddb_ok = False
    try:
        ddb_ok = _ddb_put_config("gl-charge-code-mapping", norm)
    except Exception as e:
        print(f"[GL MAPPING] Error: {e}")

    if not ddb_ok:
        return JSONResponse({"error": "save_failed"}, status_code=500)

    return {"ok": True}


# -------- Master Bills (Aggregation) --------
@app.post("/api/master-bills/generate")
async def api_generate_master_bills(request: Request, user: str = Depends(require_user)):
    """Generate master bills by aggregating line items"""
    try:
        import hashlib
        print("[GENERATE MASTER BILLS] Starting generation...")
        payload = await request.json()
        # Now uses MM/YYYY format directly from dropdown
        start_period = payload.get("start_period", "").strip() if isinstance(payload, dict) else ""
        end_period = payload.get("end_period", "").strip() if isinstance(payload, dict) else ""
        print(f"[GENERATE MASTER BILLS] Period filter: {start_period} to {end_period}")

        # Scan jrk-bill-ubi-assignments table for assigned line items
        print("[GENERATE MASTER BILLS] Scanning ubi assignments...")
        assignments = []
        try:
            response = ddb.scan(TableName="jrk-bill-ubi-assignments")
            assignments = response.get("Items", [])

            while "LastEvaluatedKey" in response:
                response = ddb.scan(
                    TableName="jrk-bill-ubi-assignments",
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                assignments.extend(response.get("Items", []))

            print(f"[GENERATE MASTER BILLS] Found {len(assignments)} assignments")
        except Exception as e:
            print(f"[GENERATE MASTER BILLS] Error scanning assignments: {e}")
            import traceback
            traceback.print_exc()

        # Load line item data from S3 for each assignment
        s3_cache = {}  # Cache S3 files to avoid re-reading
        master_bills = {}  # key: property_id|charge_code|utility_name|ubi_period

        print("[GENERATE MASTER BILLS] Processing assignments...")
        for idx, assignment in enumerate(assignments):
            try:
                # Extract assignment data from DynamoDB item
                ubi_period = assignment.get("ubi_period", {}).get("S", "")
                line_hash = assignment.get("line_hash", {}).get("S", "")
                s3_key = assignment.get("s3_key", {}).get("S", "")
                bill_id = assignment.get("bill_id", {}).get("S", "")
                line_index = int(assignment.get("line_index", {}).get("N", "0"))
                amount = float(assignment.get("amount", {}).get("N", "0"))
                months = int(assignment.get("months", {}).get("N", "1"))

                if not s3_key or not line_hash or not ubi_period:
                    print(f"[GENERATE MASTER BILLS] Skipping assignment {idx}: missing required fields")
                    continue

                # Load S3 file if not cached
                if s3_key not in s3_cache:
                    print(f"[GENERATE MASTER BILLS] Loading S3 file: {s3_key}")
                    try:
                        obj = s3.get_object(Bucket=BUCKET, Key=s3_key)
                        content = obj["Body"].read().decode("utf-8")
                        s3_cache[s3_key] = content
                    except Exception as e:
                        print(f"[GENERATE MASTER BILLS] Error loading S3 file {s3_key}: {e}")
                        continue

                # Parse JSONL and find the line by hash
                lines = s3_cache[s3_key].strip().split("\n")
                line_data = None
                for line_str in lines:
                    try:
                        parsed_line = json.loads(line_str)
                        # Compute hash of this line to compare (same method as assignment)
                        computed_hash = _compute_stable_line_hash(parsed_line)
                        if computed_hash == line_hash:
                            line_data = parsed_line
                            break
                    except:
                        continue

                if not line_data:
                    print(f"[GENERATE MASTER BILLS] Could not find line with hash {line_hash[:16]}... in {s3_key}")
                    continue

                # Extract line item details (use enriched fields from Stage 7)
                property_id = line_data.get("EnrichedPropertyID", line_data.get("Property ID", ""))
                property_name = line_data.get("EnrichedPropertyName", line_data.get("Property Name", ""))
                charge_code = line_data.get("Charge Code", "")
                utility_name = line_data.get("Utility Type", line_data.get("Utility Name", ""))
                gl_code = line_data.get("EnrichedGLAccountNumber", line_data.get("GL Account Number", ""))
                gl_name = line_data.get("EnrichedGLAccountName", line_data.get("GL Account Name", ""))
                description = line_data.get("Line Item Description", "")
                bill_id_from_s3 = line_data.get("Bill ID", "")
                line_index_from_s3 = int(line_data.get("Line Index", 0))
                account_number = line_data.get("Account Number", line_data.get("AccountNumber", ""))
                vendor_name = line_data.get("EnrichedVendorName", line_data.get("Vendor Name", ""))

                # Skip excluded line items
                is_excluded = line_data.get("Is Excluded From UBI", 0)
                exclusion_reason = line_data.get("Exclusion Reason", "")
                if is_excluded:
                    print(f"[GENERATE MASTER BILLS] Skipping excluded line: {line_hash[:16]}... (Reason: {exclusion_reason})")
                    continue

                # Check for overrides
                amount_overridden = line_data.get("Amount Overridden", False)
                charge_code_overridden = line_data.get("Charge Code Overridden", False)
                amount_override_reason = line_data.get("Amount Override Reason", "")
                charge_code_override_reason = line_data.get("Charge Code Override Reason", "")

                if not property_id or not charge_code or charge_code == "N/A":
                    print(f"[GENERATE MASTER BILLS] Skipping line: missing property_id or charge_code")
                    continue

                # Parse period (format: "12/2025 to 12/2025" or "01/2025 to 03/2025")
                period_parts = ubi_period.split(" to ")
                period_start_str = period_parts[0].strip() if len(period_parts) > 0 else ""
                period_end_str = period_parts[1].strip() if len(period_parts) > 1 else period_start_str

                # Convert to actual dates (first day of first month, last day of last month)
                import calendar

                try:
                    # Parse MM/YYYY format
                    start_month, start_year = period_start_str.split("/")
                    end_month, end_year = period_end_str.split("/")

                    # First day of start month
                    period_start = f"{start_month}/01/{start_year}"

                    # Last day of end month
                    last_day = calendar.monthrange(int(end_year), int(end_month))[1]
                    period_end = f"{end_month}/{last_day:02d}/{end_year}"

                except (ValueError, IndexError) as e:
                    print(f"[GENERATE MASTER BILLS] Error parsing period {ubi_period}: {e}")
                    continue

                # Filter by period range if provided
                # Both start_period, end_period, and period_start_str are MM/YYYY format
                # Convert all to YYYY-MM for proper string comparison
                if start_period or end_period:
                    # Convert period_start_str (MM/YYYY) to YYYY-MM for comparison
                    try:
                        p_month, p_year = period_start_str.split("/")
                        period_yyyymm = f"{p_year}-{p_month.zfill(2)}"
                    except:
                        continue

                    if start_period:
                        # Convert start_period (MM/YYYY) to YYYY-MM
                        try:
                            s_month, s_year = start_period.split("/")
                            start_yyyymm = f"{s_year}-{s_month.zfill(2)}"
                            if period_yyyymm < start_yyyymm:
                                continue
                        except:
                            pass
                    if end_period:
                        # Convert end_period (MM/YYYY) to YYYY-MM
                        try:
                            e_month, e_year = end_period.split("/")
                            end_yyyymm = f"{e_year}-{e_month.zfill(2)}"
                            if period_yyyymm > end_yyyymm:
                                continue
                        except:
                            pass

                # Create master bill key using original period strings for consistency
                mb_key = f"{property_id}|{charge_code}|{utility_name}|{period_start_str}|{period_end_str}"

                if mb_key not in master_bills:
                    master_bills[mb_key] = {
                        "master_bill_id": mb_key,
                        "property_id": property_id,
                        "property_name": property_name,
                        "ar_code_mapping": charge_code,
                        "utility_name": utility_name,
                        "billback_month_start": period_start,
                        "billback_month_end": period_end,
                        "utility_amount": 0,
                        "source_line_items": [],
                        "created_utc": datetime.utcnow().isoformat(),
                        "created_by": user,
                        "status": "draft"
                    }

                # Add to aggregate amount
                master_bills[mb_key]["utility_amount"] += amount

                # Add source line item
                master_bills[mb_key]["source_line_items"].append({
                    "bill_id": bill_id_from_s3,
                    "line_index": line_index_from_s3,
                    "account_number": account_number,
                    "vendor_name": vendor_name,
                    "gl_code": gl_code,
                    "gl_code_name": gl_name,
                    "description": description,
                    "amount": amount,
                    "overridden": amount_overridden or charge_code_overridden,
                    "override_reason": " | ".join(filter(None, [
                        amount_override_reason,
                        charge_code_override_reason
                    ]))
                })

                if (idx + 1) % 100 == 0:
                    print(f"[GENERATE MASTER BILLS] Processed {idx + 1}/{len(assignments)} assignments...")

            except Exception as e:
                print(f"[GENERATE MASTER BILLS] Error processing assignment {idx}: {e}")
                import traceback
                traceback.print_exc()
                continue

        # Merge manual/accrual entries for the same period range
        try:
            me_items = []
            # Scan manual entries and filter by period range (same logic as assignments)
            me_response = ddb.scan(TableName=MANUAL_ENTRIES_TABLE)
            me_items = me_response.get("Items", [])
            while me_response.get("LastEvaluatedKey"):
                me_response = ddb.scan(
                    TableName=MANUAL_ENTRIES_TABLE,
                    ExclusiveStartKey=me_response["LastEvaluatedKey"]
                )
                me_items.extend(me_response.get("Items", []))

            manual_count = 0
            for me_item in me_items:
                me_period = me_item.get("period", {}).get("S", "")  # MM/YYYY
                me_amount = float(me_item.get("amount", {}).get("N", "0"))
                me_property_id = me_item.get("property_id", {}).get("S", "")
                me_property_name = me_item.get("property_name", {}).get("S", "")
                me_charge_code = me_item.get("charge_code", {}).get("S", "")
                me_utility_name = me_item.get("utility_name", {}).get("S", "")
                me_entry_type = me_item.get("entry_type", {}).get("S", "")
                me_reason_code = me_item.get("reason_code", {}).get("S", "")
                me_note = me_item.get("note", {}).get("S", "")
                me_account_number = me_item.get("account_number", {}).get("S", "")
                me_vendor_name = me_item.get("vendor_name", {}).get("S", "")
                me_entry_id = me_item.get("entry_id", {}).get("S", "")

                if not me_period or not me_property_id:
                    continue

                # Apply same period filter
                if start_period or end_period:
                    try:
                        p_month, p_year = me_period.split("/")
                        period_yyyymm = f"{p_year}-{p_month.zfill(2)}"
                    except:
                        continue
                    if start_period:
                        try:
                            s_month, s_year = start_period.split("/")
                            if period_yyyymm < f"{s_year}-{s_month.zfill(2)}":
                                continue
                        except:
                            pass
                    if end_period:
                        try:
                            e_month, e_year = end_period.split("/")
                            if period_yyyymm > f"{e_year}-{e_month.zfill(2)}":
                                continue
                        except:
                            pass

                # Build period dates
                import calendar
                try:
                    p_month, p_year = me_period.split("/")
                    period_start = f"{p_month}/01/{p_year}"
                    last_day = calendar.monthrange(int(p_year), int(p_month))[1]
                    period_end = f"{p_month}/{last_day:02d}/{p_year}"
                except:
                    continue

                # Create master bill key using same format
                mb_key = f"{me_property_id}|{me_charge_code}|{me_utility_name}|{me_period}|{me_period}"

                if mb_key not in master_bills:
                    master_bills[mb_key] = {
                        "master_bill_id": mb_key,
                        "property_id": me_property_id,
                        "property_name": me_property_name,
                        "ar_code_mapping": me_charge_code,
                        "utility_name": me_utility_name,
                        "billback_month_start": period_start,
                        "billback_month_end": period_end,
                        "utility_amount": 0,
                        "source_line_items": [],
                        "has_non_actual": False,
                        "created_utc": datetime.utcnow().isoformat(),
                        "created_by": user,
                        "status": "draft"
                    }

                master_bills[mb_key]["utility_amount"] += me_amount
                master_bills[mb_key]["has_non_actual"] = True

                master_bills[mb_key]["source_line_items"].append({
                    "bill_id": me_entry_id,
                    "line_index": 0,
                    "account_number": me_account_number,
                    "vendor_name": me_vendor_name,
                    "gl_code": me_item.get("gl_account_number", {}).get("S", ""),
                    "gl_code_name": me_item.get("gl_account_name", {}).get("S", ""),
                    "description": f"{me_entry_type}: {me_reason_code}",
                    "amount": me_amount,
                    "overridden": False,
                    "override_reason": "",
                    "entry_type": me_entry_type,
                    "reason_code": me_reason_code,
                    "note": me_note
                })
                manual_count += 1

            print(f"[GENERATE MASTER BILLS] Merged {manual_count} manual/accrual entries")

        except Exception as e:
            print(f"[GENERATE MASTER BILLS] Error merging manual entries: {e}")
            import traceback
            traceback.print_exc()

        # Convert to list and save to DynamoDB
        master_bills_list = list(master_bills.values())

        print(f"[GENERATE MASTER BILLS] Created {len(master_bills_list)} master bills from {len(assignments)} assignments + manual entries")

        # Store master bills in config for now (TODO: create dedicated table)
        save_ok = _ddb_put_config("master-bills-latest", master_bills_list)
        if not save_ok:
            print(f"[GENERATE MASTER BILLS] WARNING: Failed to save master bills to DynamoDB!")

        total_amount = sum(mb["utility_amount"] for mb in master_bills_list)
        print(f"[GENERATE MASTER BILLS] Total amount: ${total_amount:.2f}")

        return {
            "ok": True,
            "count": len(master_bills_list),
            "total_amount": total_amount
        }

    except Exception as e:
        print(f"[GENERATE MASTER BILLS] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/master-bills/list")
def api_list_master_bills(user: str = Depends(require_user)):
    """List all generated master bills"""
    master_bills = _ddb_get_config("master-bills-latest")
    if not isinstance(master_bills, list):
        master_bills = []

    # Debug: log all master bill IDs
    print(f"[MASTER BILLS LIST] Returning {len(master_bills)} master bills")
    for mb in master_bills:
        print(f"[MASTER BILLS LIST] ID: {mb.get('master_bill_id')}")

    return {"items": master_bills, "count": len(master_bills)}


@app.get("/api/master-bills/detail")
def api_master_bill_detail(id: str, user: str = Depends(require_user)):
    """Get detail of a specific master bill with drill-down"""
    master_bills = _ddb_get_config("master-bills-latest")
    if not isinstance(master_bills, list):
        return JSONResponse({"error": "not found"}, status_code=404)

    print(f"[MASTER BILL DETAIL] Looking for ID: {id}")
    print(f"[MASTER BILL DETAIL] Available IDs: {[mb.get('master_bill_id') for mb in master_bills]}")

    for mb in master_bills:
        if mb.get("master_bill_id") == id:
            return mb

    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/master-bills/exclude-line")
async def api_exclude_master_bill_line(request: Request, user: str = Depends(require_user)):
    """Exclude a line item from a master bill"""
    try:
        payload = await request.json()
        master_bill_id = payload.get("master_bill_id", "").strip()
        line_hash = payload.get("line_hash", "").strip()
        is_excluded = bool(payload.get("is_excluded", False))
        exclusion_reason = payload.get("exclusion_reason", "").strip()

        if not master_bill_id or not line_hash:
            return JSONResponse({"error": "master_bill_id and line_hash required"}, status_code=400)

        # Load master bills
        master_bills = _ddb_get_config("master-bills-latest")
        if not isinstance(master_bills, list):
            return JSONResponse({"error": "master bills not found"}, status_code=404)

        # Find and update the master bill
        updated = False
        for mb in master_bills:
            if mb.get("master_bill_id") == master_bill_id:
                # Find and update the line item
                source_lines = mb.get("source_line_items", [])
                for line in source_lines:
                    if line.get("line_hash") == line_hash:
                        line["is_excluded"] = is_excluded
                        line["exclusion_reason"] = exclusion_reason if is_excluded else ""
                        updated = True
                        break

                if updated:
                    # Recalculate utility_amount excluding excluded lines
                    new_total = sum(
                        line.get("amount", 0)
                        for line in source_lines
                        if not line.get("is_excluded", False)
                    )
                    mb["utility_amount"] = new_total
                break

        if not updated:
            return JSONResponse({"error": "line item not found"}, status_code=404)

        # Save updated master bills
        _ddb_put_config("master-bills-latest", master_bills)

        return {"ok": True, "message": "Line item exclusion updated"}

    except Exception as e:
        print(f"[EXCLUDE LINE] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/master-bills/completion-tracker")
def api_completion_tracker(period: str = "", user: str = Depends(require_user)):
    """
    Get UBI completion tracker data showing which accounts have bills for a given period.
    Returns rollup by property, charge code, and account.
    """
    print(f"[COMPLETION TRACKER] Starting for period: {period or 'all'}")

    # 1. Get all UBI accounts from accounts_track config
    accounts_track = _ddb_get_config("accounts-to-track")
    if accounts_track is None:
        accounts_track = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
    if not isinstance(accounts_track, list):
        accounts_track = []

    # Filter to only UBI accounts
    ubi_accounts = [acc for acc in accounts_track if acc.get("is_ubi", False)]
    print(f"[COMPLETION TRACKER] Found {len(ubi_accounts)} UBI accounts")

    # 2. Get assigned accounts from master bills for this period
    # This is much faster than loading individual S3 files
    assigned_accounts = set()  # Set of (property_id, account_number, vendor_name) tuples that have assignments
    assigned_accounts_info = {}  # (property_id, account_number, vendor_name) -> {service_period}

    try:
        # Query assignments for the period
        if period:
            response = ddb.query(
                TableName="jrk-bill-ubi-assignments",
                IndexName="ubi-period-index",
                KeyConditionExpression="ubi_period = :p",
                ExpressionAttributeValues={":p": {"S": period}}
            )
        else:
            response = ddb.scan(TableName="jrk-bill-ubi-assignments")

        assignments = response.get("Items", [])

        # Handle pagination
        while response.get("LastEvaluatedKey"):
            if period:
                response = ddb.query(
                    TableName="jrk-bill-ubi-assignments",
                    IndexName="ubi-period-index",
                    KeyConditionExpression="ubi_period = :p",
                    ExpressionAttributeValues={":p": {"S": period}},
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
            else:
                response = ddb.scan(
                    TableName="jrk-bill-ubi-assignments",
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
            assignments.extend(response.get("Items", []))

        print(f"[COMPLETION TRACKER] Found {len(assignments)} assignments for period {period or 'all'}")

        # Group assignments by s3_key to batch-load files
        assignments_by_s3_key = {}
        for assignment in assignments:
            s3_key = assignment.get("s3_key", {}).get("S", "")
            line_hash = assignment.get("line_hash", {}).get("S", "")
            if s3_key and line_hash:
                if s3_key not in assignments_by_s3_key:
                    assignments_by_s3_key[s3_key] = set()
                assignments_by_s3_key[s3_key].add(line_hash)

        print(f"[COMPLETION TRACKER] Loading {len(assignments_by_s3_key)} unique S3 files")

        # Load each S3 file once and extract account info for all matching line hashes
        for s3_key, line_hashes in assignments_by_s3_key.items():
            try:
                obj = s3.get_object(Bucket=BUCKET, Key=s3_key)
                body = obj["Body"].read()
                if s3_key.endswith('.gz'):
                    import gzip
                    body = gzip.decompress(body)
                lines = body.decode('utf-8').strip().split('\n')

                for line in lines:
                    try:
                        parsed = json.loads(line)
                        computed_hash = _compute_stable_line_hash(parsed)
                        if computed_hash in line_hashes:
                            property_id = parsed.get("EnrichedPropertyID", parsed.get("Property ID", ""))
                            account_number = parsed.get("Account Number", parsed.get("AccountNumber", ""))
                            vendor_name = parsed.get("EnrichedVendorName", parsed.get("Vendor Name", parsed.get("VendorName", "")))
                            bill_period_start = _format_date_compact(parsed.get("Bill Period Start", ""))
                            bill_period_end = _format_date_compact(parsed.get("Bill Period End", ""))
                            if property_id and account_number:
                                # Store as dict so we can track service dates per account
                                key = (property_id, account_number, vendor_name)
                                if key not in assigned_accounts_info:
                                    assigned_accounts_info[key] = {"service_period": ""}
                                if bill_period_start or bill_period_end:
                                    sp = f"{bill_period_start} - {bill_period_end}".strip(" -")
                                    assigned_accounts_info[key]["service_period"] = sp
                                assigned_accounts.add(key)
                    except:
                        continue
            except Exception as e:
                print(f"[COMPLETION TRACKER] Error loading S3 key {s3_key}: {e}")
                continue

    except Exception as e:
        print(f"[COMPLETION TRACKER] Error loading assignments: {e}")
        import traceback
        traceback.print_exc()

    print(f"[COMPLETION TRACKER] Found {len(assigned_accounts)} unique property+account+vendor combinations with assignments")

    # 2b. Query manual entries for this period
    manual_entries_map = {}  # (property_id, account_number, vendor_name) -> entry_type
    if period:
        try:
            me_response = ddb.query(
                TableName=MANUAL_ENTRIES_TABLE,
                IndexName="period-index",
                KeyConditionExpression="period = :p",
                ExpressionAttributeValues={":p": {"S": period}}
            )
            me_items = me_response.get("Items", [])
            while me_response.get("LastEvaluatedKey"):
                me_response = ddb.query(
                    TableName=MANUAL_ENTRIES_TABLE,
                    IndexName="period-index",
                    KeyConditionExpression="period = :p",
                    ExpressionAttributeValues={":p": {"S": period}},
                    ExclusiveStartKey=me_response["LastEvaluatedKey"]
                )
                me_items.extend(me_response.get("Items", []))

            for me_item in me_items:
                me_key = (
                    me_item.get("property_id", {}).get("S", ""),
                    me_item.get("account_number", {}).get("S", ""),
                    me_item.get("vendor_name", {}).get("S", "")
                )
                manual_entries_map[me_key] = me_item.get("entry_type", {}).get("S", "MANUAL")

            print(f"[COMPLETION TRACKER] Found {len(manual_entries_map)} manual entries for period {period}")
        except Exception as e:
            print(f"[COMPLETION TRACKER] Error loading manual entries: {e}")

    # 3. Build rollup structure
    # Group by property -> accounts
    properties = {}  # property_id -> {name, accounts: [{account_number, vendor_name, has_bill, has_manual_entry, manual_entry_type}]}

    for acc in ubi_accounts:
        property_id = str(acc.get("propertyId", "")).strip()
        property_name = str(acc.get("propertyName", "")).strip()
        account_number = str(acc.get("accountNumber", "")).strip()
        vendor_name = str(acc.get("vendorName", "")).strip()

        if not property_id or not account_number:
            continue

        if property_id not in properties:
            properties[property_id] = {
                "property_id": property_id,
                "property_name": property_name,
                "accounts": [],
                "total": 0,
                "complete": 0
            }

        acc_key = (property_id, account_number, vendor_name)
        has_bill = acc_key in assigned_accounts
        has_manual = acc_key in manual_entries_map
        manual_type = manual_entries_map.get(acc_key, "")
        service_period = assigned_accounts_info.get(acc_key, {}).get("service_period", "")

        properties[property_id]["accounts"].append({
            "account_number": account_number,
            "vendor_name": vendor_name,
            "has_bill": has_bill,
            "has_manual_entry": has_manual,
            "manual_entry_type": manual_type,
            "service_period": service_period
        })
        properties[property_id]["total"] += 1
        if has_bill or has_manual:
            properties[property_id]["complete"] += 1

    # Calculate percentages and convert to list
    properties_list = []
    for prop in properties.values():
        prop["percentage"] = round((prop["complete"] / prop["total"] * 100), 1) if prop["total"] > 0 else 0
        properties_list.append(prop)

    # Sort by property name
    properties_list.sort(key=lambda x: x.get("property_name", ""))

    # Calculate overall totals
    total_accounts = sum(p["total"] for p in properties_list)
    total_complete = sum(p["complete"] for p in properties_list)
    overall_percentage = round((total_complete / total_accounts * 100), 1) if total_accounts > 0 else 0

    result = {
        "period": period,
        "overall": {
            "total": total_accounts,
            "complete": total_complete,
            "percentage": overall_percentage
        },
        "properties": properties_list
    }

    print(f"[COMPLETION TRACKER] Returning {len(properties_list)} properties, {total_complete}/{total_accounts} complete ({overall_percentage}%)")

    return result


# -------- Accrual / Manual Entries API --------

@app.get("/api/accrual/calculate")
def api_accrual_calculate(property_id: str = "", account_number: str = "", vendor_name: str = "", period: str = "", user: str = Depends(require_user)):
    """Calculate a suggested accrual for a missing account based on historical data."""
    try:
        if not property_id or not account_number:
            return JSONResponse({"error": "property_id and account_number required"}, status_code=400)

        print(f"[ACCRUAL CALC] property_id={property_id}, account={account_number}, vendor={vendor_name}, period={period}")

        # Look up charge_code and utility_name from accounts-to-track config
        accounts_track = _ddb_get_config("accounts-to-track")
        if accounts_track is None:
            accounts_track = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
        if not isinstance(accounts_track, list):
            accounts_track = []

        charge_code = ""
        utility_name = ""
        gl_account_number = ""
        gl_account_name = ""
        vendor_id = ""

        for acc in accounts_track:
            if (str(acc.get("propertyId", "")).strip() == property_id and
                str(acc.get("accountNumber", "")).strip() == account_number):
                charge_code = str(acc.get("chargeCode", "")).strip()
                utility_name = str(acc.get("utilityType", acc.get("utilityName", ""))).strip()
                vendor_id = str(acc.get("vendorId", "")).strip()
                gl_account_number = str(acc.get("glAccountNumber", "")).strip()
                gl_account_name = str(acc.get("glAccountName", "")).strip()
                if not vendor_name:
                    vendor_name = str(acc.get("vendorName", "")).strip()
                break

        # If no charge_code from accounts-to-track, try GL mapping config
        if not charge_code:
            gl_mapping = _ddb_get_config("gl-charge-code-mapping")
            if isinstance(gl_mapping, list):
                for m in gl_mapping:
                    if (str(m.get("property_id", "")).strip() == property_id and
                        str(m.get("utility_name", "")).strip().lower() == utility_name.lower()):
                        charge_code = str(m.get("charge_code", "")).strip()
                        break

        # Try Snowflake first, then fall back to DDB assignments
        historical = []
        source = "none"
        if charge_code and utility_name:
            historical = _read_historical_from_snowflake(property_id, account_number, charge_code, utility_name)
            if historical:
                source = "snowflake"

        if not historical:
            historical = _get_historical_from_assignments(property_id, account_number, vendor_name)
            if historical:
                source = "assignments"

        # Calculate accrual
        accrual = _calculate_accrual(historical)

        return {
            "property_id": property_id,
            "account_number": account_number,
            "vendor_name": vendor_name,
            "vendor_id": vendor_id,
            "charge_code": charge_code,
            "utility_name": utility_name,
            "gl_account_number": gl_account_number,
            "gl_account_name": gl_account_name,
            "period": period,
            "source": source,
            "calculated_amount": accrual["calculated_amount"],
            "historical_months": accrual["historical_months"],
            "avg_amount": accrual["avg_amount"],
            "inflation_amount": accrual["inflation_amount"],
            "monthly_amounts": accrual["monthly_amounts"]
        }

    except Exception as e:
        print(f"[ACCRUAL CALC] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/accrual/create")
async def api_accrual_create(request: Request, user: str = Depends(require_user)):
    """Create a manual/accrual/true-up entry in DynamoDB."""
    try:
        payload = await request.json()

        entry_type = str(payload.get("entry_type", "")).strip().upper()
        if entry_type not in ("ACCRUAL", "MANUAL", "TRUE-UP"):
            return JSONResponse({"error": "entry_type must be ACCRUAL, MANUAL, or TRUE-UP"}, status_code=400)

        property_id = str(payload.get("property_id", "")).strip()
        account_number = str(payload.get("account_number", "")).strip()
        period = str(payload.get("period", "")).strip()
        amount = payload.get("amount")

        if not property_id or not account_number or not period:
            return JSONResponse({"error": "property_id, account_number, and period required"}, status_code=400)
        if amount is None:
            return JSONResponse({"error": "amount required"}, status_code=400)

        import uuid
        entry_id = str(uuid.uuid4())

        item = {
            "entry_id": {"S": entry_id},
            "property_id": {"S": property_id},
            "property_name": {"S": str(payload.get("property_name", "")).strip()},
            "account_number": {"S": account_number},
            "vendor_name": {"S": str(payload.get("vendor_name", "")).strip()},
            "vendor_id": {"S": str(payload.get("vendor_id", "")).strip()},
            "charge_code": {"S": str(payload.get("charge_code", "")).strip()},
            "utility_name": {"S": str(payload.get("utility_name", "")).strip()},
            "gl_account_number": {"S": str(payload.get("gl_account_number", "")).strip()},
            "gl_account_name": {"S": str(payload.get("gl_account_name", "")).strip()},
            "amount": {"N": str(float(amount))},
            "entry_type": {"S": entry_type},
            "reason_code": {"S": str(payload.get("reason_code", "")).strip()},
            "note": {"S": str(payload.get("note", "")).strip()},
            "period": {"S": period},
            "historical_months": {"N": str(int(payload.get("historical_months", 0)))},
            "historical_avg": {"N": str(float(payload.get("historical_avg", 0)))},
            "created_by": {"S": user},
            "created_utc": {"S": datetime.utcnow().isoformat()}
        }

        ddb.put_item(TableName=MANUAL_ENTRIES_TABLE, Item=item)

        print(f"[ACCRUAL CREATE] Created {entry_type} entry {entry_id} for {property_id}/{account_number} period {period} amount={amount}")

        return {"ok": True, "entry_id": entry_id}

    except Exception as e:
        print(f"[ACCRUAL CREATE] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/accrual/entries")
def api_accrual_entries(period: str = "", user: str = Depends(require_user)):
    """List manual/accrual entries for a period (or all if no period specified)."""
    try:
        items = []
        if period:
            response = ddb.query(
                TableName=MANUAL_ENTRIES_TABLE,
                IndexName="period-index",
                KeyConditionExpression="period = :p",
                ExpressionAttributeValues={":p": {"S": period}}
            )
            items = response.get("Items", [])
            while response.get("LastEvaluatedKey"):
                response = ddb.query(
                    TableName=MANUAL_ENTRIES_TABLE,
                    IndexName="period-index",
                    KeyConditionExpression="period = :p",
                    ExpressionAttributeValues={":p": {"S": period}},
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                items.extend(response.get("Items", []))
        else:
            response = ddb.scan(TableName=MANUAL_ENTRIES_TABLE)
            items = response.get("Items", [])
            while response.get("LastEvaluatedKey"):
                response = ddb.scan(
                    TableName=MANUAL_ENTRIES_TABLE,
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                items.extend(response.get("Items", []))

        # Convert DynamoDB items to plain dicts
        results = []
        for item in items:
            results.append({
                "entry_id": item.get("entry_id", {}).get("S", ""),
                "property_id": item.get("property_id", {}).get("S", ""),
                "property_name": item.get("property_name", {}).get("S", ""),
                "account_number": item.get("account_number", {}).get("S", ""),
                "vendor_name": item.get("vendor_name", {}).get("S", ""),
                "vendor_id": item.get("vendor_id", {}).get("S", ""),
                "charge_code": item.get("charge_code", {}).get("S", ""),
                "utility_name": item.get("utility_name", {}).get("S", ""),
                "gl_account_number": item.get("gl_account_number", {}).get("S", ""),
                "gl_account_name": item.get("gl_account_name", {}).get("S", ""),
                "amount": float(item.get("amount", {}).get("N", "0")),
                "entry_type": item.get("entry_type", {}).get("S", ""),
                "reason_code": item.get("reason_code", {}).get("S", ""),
                "note": item.get("note", {}).get("S", ""),
                "period": item.get("period", {}).get("S", ""),
                "historical_months": int(item.get("historical_months", {}).get("N", "0")),
                "historical_avg": float(item.get("historical_avg", {}).get("N", "0")),
                "created_by": item.get("created_by", {}).get("S", ""),
                "created_utc": item.get("created_utc", {}).get("S", "")
            })

        return {"items": results, "count": len(results)}

    except Exception as e:
        print(f"[ACCRUAL ENTRIES] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/accrual/entry")
def api_accrual_delete(entry_id: str = "", user: str = Depends(require_user)):
    """Delete a manual/accrual entry by entry_id."""
    try:
        if not entry_id:
            return JSONResponse({"error": "entry_id required"}, status_code=400)

        ddb.delete_item(
            TableName=MANUAL_ENTRIES_TABLE,
            Key={"entry_id": {"S": entry_id}}
        )

        print(f"[ACCRUAL DELETE] Deleted entry {entry_id} by {user}")
        return {"ok": True}

    except Exception as e:
        print(f"[ACCRUAL DELETE] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


# -------- UBI Batches --------
@app.post("/api/ubi-batch/create")
async def api_create_ubi_batch(request: Request, user: str = Depends(require_user)):
    """Create a new UBI billback batch"""
    try:
        form = await request.form()
        batch_name = form.get("batch_name", "").strip()
        period_start = form.get("period_start", "").strip()
        period_end = form.get("period_end", "").strip()
        memo = form.get("memo", "").strip()

        if not batch_name or not period_start or not period_end:
            return JSONResponse({"error": "batch_name, period_start, and period_end required"}, status_code=400)

        # Load master bills
        master_bills = _ddb_get_config("master-bills-latest")
        if not isinstance(master_bills, list):
            master_bills = []

        # Filter master bills by date range
        # Convert period dates from YYYY-MM-DD to datetime for comparison
        from datetime import datetime as dt
        try:
            period_start_dt = dt.strptime(period_start, "%Y-%m-%d")
            period_end_dt = dt.strptime(period_end, "%Y-%m-%d")
        except:
            return JSONResponse({"error": "Invalid date format"}, status_code=400)

        selected_master_bills = []
        total_amount = 0
        properties = set()

        for mb in master_bills:
            mb_start = mb.get("billback_month_start", "")  # Format: MM/DD/YYYY
            try:
                mb_start_dt = dt.strptime(mb_start, "%m/%d/%Y")
                if period_start_dt <= mb_start_dt <= period_end_dt:
                    selected_master_bills.append(mb.get("master_bill_id"))
                    total_amount += mb.get("utility_amount", 0)
                    properties.add(mb.get("property_id"))
            except:
                continue

        # Create batch ID (include batch_name to make it unique)
        import uuid
        batch_id = f"{batch_name}-{period_start}-{period_end}-{str(uuid.uuid4())[:8]}"

        # Create batch object
        batch = {
            "batch_id": batch_id,
            "batch_name": batch_name,
            "period_start": period_start,
            "period_end": period_end,
            "memo": memo,
            "master_bill_ids": selected_master_bills,
            "status": "draft",
            "created_utc": datetime.utcnow().isoformat(),
            "created_by": user,
            "reviewed_utc": None,
            "reviewed_by": None,
            "exported_utc": None,
            "exported_by": None,
            "run_date": None,
            "total_master_bills": len(selected_master_bills),
            "total_amount": total_amount,
            "properties_count": len(properties)
        }

        # Save batch
        batches = _ddb_get_config("ubi-batches")
        if not isinstance(batches, list):
            batches = []

        # Check if batch already exists
        exists = any(b.get("batch_id") == batch_id for b in batches)
        if exists:
            return JSONResponse({"error": "batch already exists"}, status_code=400)

        batches.append(batch)
        _ddb_put_config("ubi-batches", batches)

        return {"ok": True, "batch": batch}

    except Exception as e:
        print(f"[CREATE BATCH] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ubi-batch/finalize")
async def api_finalize_ubi_batch(request: Request, user: str = Depends(require_user)):
    """Finalize a batch (mark as reviewed and set run_date)"""
    try:
        form = await request.form()
        batch_id = form.get("batch_id", "").strip()

        if not batch_id:
            return JSONResponse({"error": "batch_id required"}, status_code=400)

        # Load batches
        batches = _ddb_get_config("ubi-batches")
        if not isinstance(batches, list):
            return JSONResponse({"error": "batch not found"}, status_code=404)

        # Find and update batch
        found = False
        for batch in batches:
            if batch.get("batch_id") == batch_id:
                batch["status"] = "finalized"
                batch["reviewed_utc"] = datetime.utcnow().isoformat()
                batch["reviewed_by"] = user
                batch["run_date"] = datetime.utcnow().isoformat()
                found = True
                break

        if not found:
            return JSONResponse({"error": "batch not found"}, status_code=404)

        # Save
        _ddb_put_config("ubi-batches", batches)

        return {"ok": True}

    except Exception as e:
        print(f"[FINALIZE BATCH] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/ubi-batch/delete")
async def api_delete_ubi_batch(request: Request, user: str = Depends(require_user)):
    """Delete a batch (only if status is draft)"""
    try:
        form = await request.form()
        batch_id = form.get("batch_id", "").strip()

        if not batch_id:
            return JSONResponse({"error": "batch_id required"}, status_code=400)

        # Load batches
        batches = _ddb_get_config("ubi-batches")
        if not isinstance(batches, list):
            return JSONResponse({"error": "batch not found"}, status_code=404)

        # Find batch and check if it's deletable
        batch_to_delete = None
        for batch in batches:
            if batch.get("batch_id") == batch_id:
                batch_to_delete = batch
                break

        if not batch_to_delete:
            return JSONResponse({"error": "batch not found"}, status_code=404)

        # Only allow deletion of draft batches
        if batch_to_delete.get("status") != "draft":
            return JSONResponse({"error": "can only delete draft batches"}, status_code=400)

        # Remove batch from list
        batches = [b for b in batches if b.get("batch_id") != batch_id]

        # Save
        _ddb_put_config("ubi-batches", batches)

        return {"ok": True}

    except Exception as e:
        print(f"[DELETE BATCH] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/ubi-batch/list")
def api_list_ubi_batches(user: str = Depends(require_user)):
    """List all UBI batches"""
    batches = _ddb_get_config("ubi-batches")
    if not isinstance(batches, list):
        batches = []

    return {"items": batches, "count": len(batches)}


@app.get("/api/ubi-batch/detail/{batch_id}")
def api_ubi_batch_detail(batch_id: str, user: str = Depends(require_user)):
    """Get detail of a specific batch"""
    batches = _ddb_get_config("ubi-batches")
    if not isinstance(batches, list):
        return JSONResponse({"error": "not found"}, status_code=404)

    # URL decode
    import urllib.parse
    decoded_id = urllib.parse.unquote(batch_id)

    for batch in batches:
        if batch.get("batch_id") == decoded_id:
            # Load master bills for this batch
            master_bills = _ddb_get_config("master-bills-latest")
            if isinstance(master_bills, list):
                batch_mb_ids = set(batch.get("master_bill_ids", []))
                batch["master_bills"] = [mb for mb in master_bills if mb.get("master_bill_id") in batch_mb_ids]

            return batch

    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/ubi-batch/export-snowflake")
async def api_export_batch_to_snowflake(request: Request, user: str = Depends(require_user)):
    """Export a batch to Snowflake _Master_Bills_Prod table"""
    try:
        form = await request.form()
        batch_id = form.get("batch_id", "").strip()

        if not batch_id:
            return JSONResponse({"error": "batch_id required"}, status_code=400)

        # Load batch
        batches = _ddb_get_config("ubi-batches")
        if not isinstance(batches, list):
            return JSONResponse({"error": "batch not found"}, status_code=404)

        batch = None
        for b in batches:
            if b.get("batch_id") == batch_id:
                batch = b
                break

        if not batch:
            return JSONResponse({"error": "batch not found"}, status_code=404)

        # Check if batch is finalized
        if batch.get("status") != "finalized":
            return JSONResponse({"error": "batch must be finalized before export"}, status_code=400)

        # Load master bills
        master_bills = _ddb_get_config("master-bills-latest")
        if not isinstance(master_bills, list):
            master_bills = []

        # Filter to batch master bills
        batch_mb_ids = set(batch.get("master_bill_ids", []))
        batch_master_bills = [mb for mb in master_bills if mb.get("master_bill_id") in batch_mb_ids]

        if not batch_master_bills:
            return JSONResponse({"error": "no master bills to export"}, status_code=400)

        memo = batch.get("memo", "")
        run_date = batch.get("run_date", datetime.utcnow().isoformat())

        # Write to Snowflake
        print(f"[EXPORT SNOWFLAKE] Writing {len(batch_master_bills)} rows to Snowflake for batch {batch_id}")
        success, message, rows_inserted = _write_to_snowflake(
            batch_id=batch_id,
            master_bills=batch_master_bills,
            memo=memo,
            run_date=run_date
        )

        if not success:
            return JSONResponse({"error": f"Snowflake export failed: {message}"}, status_code=500)

        # Generate SQL for preview/audit purposes (matching NEW schema with Batch_ID)
        sql_rows = []
        for mb in batch_master_bills:
            escaped_memo = memo.replace("'", "''")
            row = (
                f"('{mb.get('property_id')}', "
                f"'{mb.get('ar_code_mapping')}', "
                f"'{mb.get('utility_name')}', "
                f"'{mb.get('utility_amount')}', "
                f"'{mb.get('billback_month_start')}', "
                f"'{mb.get('billback_month_end')}', "
                f"'{run_date}', "
                f"'{escaped_memo}', "
                f"'{batch_id}'"
                f")"
            )
            sql_rows.append(row)

        sql = f"""-- UBI Billback Export (EXECUTED)
-- Batch: {batch.get('batch_name')} (ID: {batch_id})
-- Period: {batch.get('period_start')} to {batch.get('period_end')}
-- Run Date: {run_date}
-- Total Rows: {rows_inserted}
-- Status: Successfully exported to Snowflake table "_Master_Bills_Prod"

INSERT INTO LAITMAN.UBI."_Master_Bills_Prod"
("Property_ID", "AR_Code_Mapping", "Utility_Name", "Utility_Amount",
 "Billback_Month_Start", "Billback_Month_End", "RunDate", "Memo", "Batch_ID")
VALUES
{',\\n'.join(sql_rows)};
"""

        # Mark batch as exported
        batch["status"] = "exported"
        batch["exported_utc"] = datetime.utcnow().isoformat()
        batch["exported_by"] = user

        # Update batch in config
        for i, b in enumerate(batches):
            if b.get("batch_id") == batch_id:
                batches[i] = batch
                break

        _ddb_put_config("ubi-batches", batches)

        return {
            "ok": True,
            "sql": sql,
            "rows_exported": rows_inserted,
            "batch": batch,
            "snowflake_message": message
        }

    except Exception as e:
        print(f"[EXPORT SNOWFLAKE] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


# -------- AP Team Members (single column: name) --------
@app.get("/api/config/ap-team")
def api_get_ap_team(user: str = Depends(require_user)):
    arr = _ddb_get_config("ap-team")
    if not isinstance(arr, list):
        arr = []
    # normalize to {name}
    out = []
    for r in arr:
        if isinstance(r, dict):
            out.append({"name": str(r.get("name") or r.get("Name") or "").strip()})
        elif isinstance(r, str):
            out.append({"name": r})
    return {"items": out}


@app.post("/api/config/ap-team")
async def api_save_ap_team(request: Request, user: str = Depends(require_user)):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return JSONResponse({"error": "items must be a list"}, status_code=400)
    norm = []
    for r in items:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "").strip()
        if name:
            norm.append({"name": name})
    ok = _ddb_put_config("ap-team", norm)
    if not ok:
        return JSONResponse({"error": "save_failed"}, status_code=500)
    return {"ok": True, "saved": len(norm)}


@app.get("/api/config/ubi-mapping")
def api_get_ubi_mapping(user: str = Depends(require_user)):
    base = _ddb_get_config("accounts-to-track")
    if base is None:
        base = _s3_get_json(CONFIG_BUCKET, ACCOUNTS_TRACK_KEY)
    if not isinstance(base, list):
        base = []
    overlay = _ddb_get_config("ubi-mapping") or []
    by_key: dict[str, dict] = {}
    for r in overlay:
        if not isinstance(r, dict):
            continue
        k = "|".join([
            str(r.get("vendorId") or "").strip(),
            str(r.get("accountNumber") or "").strip(),
            str(r.get("propertyId") or "").strip(),
            str(r.get("glAccountNumber") or "").strip(),
        ])
        by_key[k] = {
            "isUbi": bool(r.get("isUbi") is True or str(r.get("isUbi")).lower() == "true"),
            "chargeCode": str(r.get("chargeCode") or "").strip(),
            "notes": str(r.get("notes") or "").strip(),
        }
    out = []
    for r in base:
        if not isinstance(r, dict):
            continue
        vendorId = str(r.get("vendorId") or "").strip()
        accountNumber = str(r.get("accountNumber") or "").strip()
        propertyId = str(r.get("propertyId") or "").strip()
        glAccountNumber = str(r.get("glAccountNumber") or "").strip()
        k = "|".join([vendorId, accountNumber, propertyId, glAccountNumber])
        ov = by_key.get(k) or {"isUbi": False, "chargeCode": "", "notes": ""}
        out.append({
            "vendorId": vendorId,
            "vendorName": str(r.get("vendorName") or "").strip(),
            "accountNumber": accountNumber,
            "propertyId": propertyId,
            "propertyName": str(r.get("propertyName") or "").strip(),
            "glAccountNumber": glAccountNumber,
            "glAccountName": str(r.get("glAccountName") or "").strip(),
            "daysBetweenBills": int(str(r.get("daysBetweenBills") or "0").strip() or 0),
            "isUbi": bool(ov.get("isUbi")),
            "chargeCode": str(ov.get("chargeCode") or "").strip(),
            "notes": str(ov.get("notes") or "").strip(),
        })
    return {"items": out}


@app.post("/api/config/ubi-mapping")
async def api_save_ubi_mapping(request: Request, user: str = Depends(require_user)):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return JSONResponse({"error": "items must be a list"}, status_code=400)
    to_save = []
    for r in items:
        if not isinstance(r, dict):
            continue
        isUbi = (r.get("isUbi") is True) or (str(r.get("isUbi")).lower() == "true")
        chargeCode = str(r.get("chargeCode") or "").strip()
        notes = str(r.get("notes") or "").strip()
        if not isUbi and not chargeCode and not notes:
            continue
        to_save.append({
            "vendorId": str(r.get("vendorId") or "").strip(),
            "accountNumber": str(r.get("accountNumber") or "").strip(),
            "propertyId": str(r.get("propertyId") or "").strip(),
            "glAccountNumber": str(r.get("glAccountNumber") or "").strip(),
            "isUbi": bool(isUbi),
            "chargeCode": chargeCode,
            "notes": notes,
        })
    ok = _ddb_put_config("ubi-mapping", to_save)
    if not ok:
        return JSONResponse({"error": "save_failed"}, status_code=500)
    return {"ok": True, "saved": len(to_save)}


# -------- UOM (Unit of Measure) Mapping --------
@app.get("/api/config/uom-mapping")
def api_get_uom_mapping(user: str = Depends(require_user)):
    """Get UOM conversion mappings. Returns list of mappings with structure:
    [{"original_uom": "CCF", "utility_type": "water", "conversion_factor": 748, "target_uom": "Gallons"}, ...]
    """
    try:
        # Try to load from S3
        standard_key = f"{DIM_UOM_PREFIX}latest.json.gz"
        try:
            txt = _read_s3_text(BUCKET, standard_key)
            data = json.loads(txt)
            if isinstance(data, list):
                return {"items": data}
        except Exception:
            pass
        # Try to find any file in the prefix
        key = _find_latest_data(DIM_UOM_PREFIX)
        if key:
            txt = _read_s3_text(BUCKET, key)
            data = json.loads(txt)
            if isinstance(data, list):
                return {"items": data}
    except Exception as e:
        print(f"[UOM MAPPING GET] Error: {e}")
    # Return empty list as default
    return {"items": []}


@app.post("/api/config/uom-mapping")
async def api_save_uom_mapping(request: Request, user: str = Depends(require_user)):
    """Save UOM conversion mappings to S3 for use by enricher Lambda."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return JSONResponse({"error": "items must be a list"}, status_code=400)

    # Validate and normalize items
    to_save = []
    for r in items:
        if not isinstance(r, dict):
            continue
        original_uom = str(r.get("original_uom") or "").strip()
        utility_type = str(r.get("utility_type") or "").strip()
        target_uom = str(r.get("target_uom") or "").strip()
        try:
            conversion_factor = float(r.get("conversion_factor", 1.0))
        except Exception:
            conversion_factor = 1.0

        if not original_uom or not target_uom:
            continue  # Skip invalid entries

        to_save.append({
            "original_uom": original_uom,
            "utility_type": utility_type,  # Can be empty for universal conversions
            "conversion_factor": conversion_factor,
            "target_uom": target_uom,
        })

    # Save to S3 as gzipped JSON
    try:
        dt_str = dt.datetime.utcnow().strftime("%Y%m%d")
        key = f"{DIM_UOM_PREFIX}dt={dt_str}/data.json.gz"
        json_str = json.dumps(to_save, ensure_ascii=False, indent=2)
        compressed = gzip.compress(json_str.encode("utf-8"))
        s3.put_object(Bucket=BUCKET, Key=key, Body=compressed, ContentType="application/json", ContentEncoding="gzip")

        # Also save to standardized filename for fast loading
        standard_key = f"{DIM_UOM_PREFIX}latest.json.gz"
        s3.put_object(Bucket=BUCKET, Key=standard_key, Body=compressed, ContentType="application/json", ContentEncoding="gzip")

        print(f"[UOM MAPPING SAVE] Saved {len(to_save)} mappings to {key} and {standard_key}")
        return {"ok": True, "saved": len(to_save)}
    except Exception as e:
        print(f"[UOM MAPPING SAVE] Error: {e}")
        return JSONResponse({"error": f"save_failed: {str(e)}"}, status_code=500)


# -------- AP to Property Mapping --------
@app.get("/api/config/ap-mapping")
def api_get_ap_mapping(user: str = Depends(require_user)):
    arr = _ddb_get_config("ap-mapping")
    if not isinstance(arr, list):
        arr = []
    # normalize fields
    out = []
    for r in arr:
        if not isinstance(r, dict):
            continue
        out.append({
            "name": str(r.get("name") or "").strip(),
            "propertyId": str(r.get("propertyId") or "").strip(),
            "propertyName": str(r.get("propertyName") or "").strip(),
        })
    return {"items": out}


@app.post("/api/config/ap-mapping")
async def api_save_ap_mapping(request: Request, user: str = Depends(require_user)):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return JSONResponse({"error": "items must be a list"}, status_code=400)
    norm = []
    for r in items:
        if not isinstance(r, dict):
            continue
        norm.append({
            "name": str(r.get("name") or "").strip(),
            "propertyId": str(r.get("propertyId") or "").strip(),
            "propertyName": str(r.get("propertyName") or "").strip(),
        })
    ok = _ddb_put_config("ap-mapping", norm)
    if not ok:
        return JSONResponse({"error": "save_failed"}, status_code=500)
    return {"ok": True, "saved": len(norm)}


# -------- DEBUG / IMPROVE Reporting APIs --------
@app.get("/api/debug/reports")
def api_get_debug_reports(user: str = Depends(require_user)):
    """Get all debug reports for triage page."""
    try:
        response = ddb.scan(TableName=DEBUG_TABLE)
        items = response.get("Items", [])

        reports = []
        for item in items:
            reports.append({
                "report_id": item.get("report_id", {}).get("S", ""),
                "title": item.get("title", {}).get("S", ""),
                "description": item.get("description", {}).get("S", ""),
                "page_url": item.get("page_url", {}).get("S", ""),
                "requestor": item.get("requestor", {}).get("S", ""),
                "status": item.get("status", {}).get("S", "Open"),
                "priority": item.get("priority", {}).get("S", "Medium"),
                "type": item.get("type", {}).get("S", "bug"),
                "created_utc": item.get("created_utc", {}).get("S", ""),
                "updated_utc": item.get("updated_utc", {}).get("S", ""),
                "completed_utc": item.get("completed_utc", {}).get("S", ""),
                "completed_by": item.get("completed_by", {}).get("S", ""),
                "resolution_notes": item.get("resolution_notes", {}).get("S", ""),
                "release_tag": item.get("release_tag", {}).get("S", ""),
            })

        # Sort by created_utc descending
        reports.sort(key=lambda x: x.get("created_utc", ""), reverse=True)
        return {"reports": reports}
    except Exception as e:
        print(f"[DEBUG API] Error loading reports: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/debug/stats")
def api_debug_stats(user: str = Depends(require_user)):
    """Get dashboard stats for debug reports."""
    try:
        response = ddb.scan(TableName=DEBUG_TABLE)
        items = response.get("Items", [])

        stats = {"open": 0, "in_progress": 0, "completed": 0, "rejected": 0, "deferred": 0,
                 "bugs": 0, "features": 0, "enhancements": 0,
                 "completed_this_week": 0, "completed_this_month": 0,
                 "by_requestor": {}, "by_priority": {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}}

        today = dt.date.today()
        week_start = today - dt.timedelta(days=today.weekday())
        month_start = today.replace(day=1)

        for item in items:
            status = item.get("status", {}).get("S", "Open")
            priority = item.get("priority", {}).get("S", "Medium")
            item_type = item.get("type", {}).get("S", "bug")
            requestor = item.get("requestor", {}).get("S", "unknown")
            completed_utc = item.get("completed_utc", {}).get("S", "")

            # Count by status
            if status == "Open":
                stats["open"] += 1
            elif status == "In Progress":
                stats["in_progress"] += 1
            elif status == "Completed":
                stats["completed"] += 1
            elif status == "Rejected":
                stats["rejected"] += 1
            elif status == "Deferred":
                stats["deferred"] += 1

            # Count by type
            if item_type == "bug":
                stats["bugs"] += 1
            elif item_type == "feature":
                stats["features"] += 1
            elif item_type == "enhancement":
                stats["enhancements"] += 1

            # Count by requestor
            stats["by_requestor"][requestor] = stats["by_requestor"].get(requestor, 0) + 1

            # Count by priority
            if priority in stats["by_priority"]:
                stats["by_priority"][priority] += 1

            # Count completions this week/month
            if completed_utc:
                try:
                    comp_date = dt.datetime.fromisoformat(completed_utc.replace("Z", "+00:00")).date()
                    if comp_date >= week_start:
                        stats["completed_this_week"] += 1
                    if comp_date >= month_start:
                        stats["completed_this_month"] += 1
                except Exception:
                    pass

        return stats
    except Exception as e:
        print(f"[DEBUG API] Error getting stats: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/debug/weekly-report")
def api_debug_weekly_report(week: str = "", user: str = Depends(require_user)):
    """Generate weekly report of completed items. Week format: YYYY-Wnn (e.g., 2024-W50)"""
    try:
        # Default to current week
        today = dt.date.today()
        if not week:
            week_num = today.isocalendar()[1]
            week = f"{today.year}-W{week_num:02d}"

        # Parse week to get date range
        try:
            year, w = week.split("-W")
            year = int(year)
            week_num = int(w)
            # Monday of that week
            week_start = dt.datetime.strptime(f"{year}-W{week_num:02d}-1", "%G-W%V-%u").date()
            week_end = week_start + dt.timedelta(days=6)
        except Exception:
            return JSONResponse({"error": "Invalid week format. Use YYYY-Wnn"}, status_code=400)

        response = ddb.scan(TableName=DEBUG_TABLE)
        items = response.get("Items", [])

        completed_items = {"bugs": [], "features": [], "enhancements": []}

        for item in items:
            status = item.get("status", {}).get("S", "")
            completed_utc = item.get("completed_utc", {}).get("S", "")

            if status != "Completed" or not completed_utc:
                continue

            try:
                comp_date = dt.datetime.fromisoformat(completed_utc.replace("Z", "+00:00")).date()
                if week_start <= comp_date <= week_end:
                    item_type = item.get("type", {}).get("S", "bug")
                    entry = {
                        "title": item.get("title", {}).get("S", ""),
                        "description": item.get("description", {}).get("S", ""),
                        "requestor": item.get("requestor", {}).get("S", ""),
                        "completed_by": item.get("completed_by", {}).get("S", ""),
                        "resolution_notes": item.get("resolution_notes", {}).get("S", ""),
                        "completed_utc": completed_utc,
                    }
                    if item_type == "bug":
                        completed_items["bugs"].append(entry)
                    elif item_type == "feature":
                        completed_items["features"].append(entry)
                    else:
                        completed_items["enhancements"].append(entry)
            except Exception:
                pass

        # Generate markdown report
        report_md = f"# Weekly Report: {week}\n"
        report_md += f"**Period:** {week_start.strftime('%b %d')} - {week_end.strftime('%b %d, %Y')}\n\n"

        total = len(completed_items["bugs"]) + len(completed_items["features"]) + len(completed_items["enhancements"])
        report_md += f"**Total Completed:** {total} items\n\n"

        if completed_items["bugs"]:
            report_md += f"## Bug Fixes ({len(completed_items['bugs'])})\n"
            for b in completed_items["bugs"]:
                report_md += f"- **{b['title']}**"
                if b["resolution_notes"]:
                    report_md += f": {b['resolution_notes']}"
                if b["requestor"]:
                    report_md += f" _(requested by {b['requestor']})_"
                report_md += "\n"
            report_md += "\n"

        if completed_items["features"]:
            report_md += f"## New Features ({len(completed_items['features'])})\n"
            for f in completed_items["features"]:
                report_md += f"- **{f['title']}**"
                if f["resolution_notes"]:
                    report_md += f": {f['resolution_notes']}"
                if f["requestor"]:
                    report_md += f" _(requested by {f['requestor']})_"
                report_md += "\n"
            report_md += "\n"

        if completed_items["enhancements"]:
            report_md += f"## Enhancements ({len(completed_items['enhancements'])})\n"
            for e in completed_items["enhancements"]:
                report_md += f"- **{e['title']}**"
                if e["resolution_notes"]:
                    report_md += f": {e['resolution_notes']}"
                if e["requestor"]:
                    report_md += f" _(requested by {e['requestor']})_"
                report_md += "\n"

        return {
            "week": week,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "items": completed_items,
            "report_markdown": report_md,
            "total": total
        }
    except Exception as e:
        print(f"[DEBUG API] Error generating weekly report: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/debug/release-notes")
def api_debug_release_notes(tag: str = "", user: str = Depends(require_user)):
    """Generate release notes for a specific release tag."""
    try:
        response = ddb.scan(TableName=DEBUG_TABLE)
        items = response.get("Items", [])

        # Get available release tags
        all_tags = set()
        for item in items:
            rtag = item.get("release_tag", {}).get("S", "")
            if rtag:
                all_tags.add(rtag)

        if not tag:
            return {"available_tags": sorted(all_tags, reverse=True), "notes": None}

        # Filter items by tag
        release_items = {"bugs": [], "features": [], "enhancements": []}
        for item in items:
            rtag = item.get("release_tag", {}).get("S", "")
            if rtag != tag:
                continue
            item_type = item.get("type", {}).get("S", "bug")
            entry = {
                "title": item.get("title", {}).get("S", ""),
                "resolution_notes": item.get("resolution_notes", {}).get("S", ""),
                "requestor": item.get("requestor", {}).get("S", ""),
            }
            if item_type == "bug":
                release_items["bugs"].append(entry)
            elif item_type == "feature":
                release_items["features"].append(entry)
            else:
                release_items["enhancements"].append(entry)

        # Generate markdown
        notes_md = f"# Release Notes: {tag}\n\n"
        if release_items["features"]:
            notes_md += "## New Features\n"
            for f in release_items["features"]:
                notes_md += f"- {f['title']}"
                if f["resolution_notes"]:
                    notes_md += f" - {f['resolution_notes']}"
                notes_md += "\n"
            notes_md += "\n"
        if release_items["enhancements"]:
            notes_md += "## Improvements\n"
            for e in release_items["enhancements"]:
                notes_md += f"- {e['title']}"
                if e["resolution_notes"]:
                    notes_md += f" - {e['resolution_notes']}"
                notes_md += "\n"
            notes_md += "\n"
        if release_items["bugs"]:
            notes_md += "## Bug Fixes\n"
            for b in release_items["bugs"]:
                notes_md += f"- {b['title']}"
                if b["resolution_notes"]:
                    notes_md += f" - {b['resolution_notes']}"
                notes_md += "\n"

        return {
            "tag": tag,
            "available_tags": sorted(all_tags, reverse=True),
            "items": release_items,
            "notes_markdown": notes_md,
            "total": len(release_items["bugs"]) + len(release_items["features"]) + len(release_items["enhancements"])
        }
    except Exception as e:
        print(f"[DEBUG API] Error generating release notes: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/debug/report")
async def api_create_debug_report(request: Request, user: str = Depends(require_user)):
    """Create a new debug/improvement report."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    title = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    page_url = str(payload.get("page_url") or "").strip()
    report_type = str(payload.get("type") or "bug").strip()
    priority = str(payload.get("priority") or "Medium").strip()

    if not title or not description:
        return JSONResponse({"error": "title and description required"}, status_code=400)

    import uuid
    report_id = str(uuid.uuid4())
    now_utc = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        ddb.put_item(
            TableName=DEBUG_TABLE,
            Item={
                "report_id": {"S": report_id},
                "title": {"S": title},
                "description": {"S": description},
                "page_url": {"S": page_url},
                "requestor": {"S": user},
                "status": {"S": "Open"},
                "priority": {"S": priority},
                "type": {"S": report_type},
                "created_utc": {"S": now_utc},
                "updated_utc": {"S": now_utc},
            }
        )
        return {"ok": True, "report_id": report_id}
    except Exception as e:
        print(f"[DEBUG API] Error creating report: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/debug/report/{report_id}/update")
async def api_update_debug_report(report_id: str, request: Request, user: str = Depends(require_user)):
    """Update a debug report - status, priority, type, resolution, release tag."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    status = payload.get("status")
    priority = payload.get("priority")
    report_type = payload.get("type")
    resolution_notes = payload.get("resolution_notes")
    release_tag = payload.get("release_tag")

    now_utc = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        update_expr = "SET updated_utc = :updated"
        expr_values = {":updated": {"S": now_utc}}
        expr_names = {}

        if status:
            update_expr += ", #status = :status"
            expr_values[":status"] = {"S": str(status)}
            expr_names["#status"] = "status"
            # If status is Completed, set completed_utc and completed_by
            if status == "Completed":
                update_expr += ", completed_utc = :completed_utc, completed_by = :completed_by"
                expr_values[":completed_utc"] = {"S": now_utc}
                expr_values[":completed_by"] = {"S": user}

        if priority:
            update_expr += ", priority = :priority"
            expr_values[":priority"] = {"S": str(priority)}

        if report_type:
            update_expr += ", #type = :type"
            expr_values[":type"] = {"S": str(report_type)}
            expr_names["#type"] = "type"

        if resolution_notes is not None:
            update_expr += ", resolution_notes = :resolution_notes"
            expr_values[":resolution_notes"] = {"S": str(resolution_notes)}

        if release_tag is not None:
            update_expr += ", release_tag = :release_tag"
            expr_values[":release_tag"] = {"S": str(release_tag)}

        ddb.update_item(
            TableName=DEBUG_TABLE,
            Key={"report_id": {"S": report_id}},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
            **({"ExpressionAttributeNames": expr_names} if expr_names else {})
        )
        return {"ok": True}
    except Exception as e:
        print(f"[DEBUG API] Error updating report: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/debug/report/{report_id}")
def api_delete_debug_report(report_id: str, user: str = Depends(require_user)):
    """Delete a debug report."""
    try:
        ddb.delete_item(
            TableName=DEBUG_TABLE,
            Key={"report_id": {"S": report_id}}
        )
        return {"ok": True}
    except Exception as e:
        print(f"[DEBUG API] Error deleting report: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# simple in-memory cache for track API
# Increased TTL to reduce expensive S3 operations (26+ list calls + downloads per uncached request)
_TRACK_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}
_TRACK_CACHE_TS: Dict[Tuple[str, str], float] = {}
_TRACK_TTL_SECONDS = 3600  # 1 hour - track data doesn't change frequently

@app.get("/api/track")
def api_track(request: Request, user: str = Depends(require_user)):
    """Return TRACK data: months headers and joined rows with per-month status and tooltip.
    Status rules:
      - POSTED if a Stage 7 record exists for (prop,vendor,acct,month)
      - Else PENDING if a Stage 6 (or Stage 4) record exists for that key
      - Else UPCOMING if today < expected_cutoff (1st of month + daysBetweenBills)
      - Else MISSING
    """
    today = dt.date.today()
    # Build months: prev 9 + current + next 3 (total 13)
    months: list[dt.date] = []
    ref = dt.date(today.year, today.month, 1)
    for i in range(9, 0, -1):
        y = (ref.year * 12 + ref.month - i - 1) // 12
        m = (ref.month - i - 1) % 12 + 1
        months.append(dt.date(y, m, 1))
    for i in range(0, 4):
        y = (ref.year * 12 + ref.month + i - 1) // 12
        m = (ref.month + i - 1) % 12 + 1
        months.append(dt.date(y, m, 1))
    month_labels = [d.strftime("%b").upper() for d in months]
    month_keys = [f"{d.year:04d}-{d.month:02d}" for d in months]

    # Load configs (before cache so we can key on accounts length)
    accounts = _ddb_get_config("accounts-to-track") or []
    mapping = _ddb_get_config("ap-mapping") or []
    map_by_pid = {str(r.get("propertyId") or "").strip(): r for r in mapping if isinstance(r, dict)}

    # Build vendor ID -> vendor code lookup from vendor cache
    vendor_code_map: dict[str, str] = {}
    try:
        vend_cache_obj = s3.get_object(Bucket="api-vendor", Key="vendors/latest.json")
        vend_cache_data = json.loads(vend_cache_obj["Body"].read().decode("utf-8"))
        for v in vend_cache_data.get("vendors", []):
            vid = str(v.get("vendorId", "")).strip()
            vcode = str(v.get("vendorCode", "")).strip()
            if vid and vcode:
                vendor_code_map[vid] = vcode
    except Exception as e:
        print(f"[api_track] Failed to load vendor cache for codes: {e}")
    # cache key by month range and accounts length; allow manual bypass
    cache_key = (month_keys[0], month_keys[-1], len(accounts))
    now_ts = dt.datetime.utcnow().timestamp()
    do_refresh = request.query_params.get("refresh") in ("1", "true", "yes")
    if (not do_refresh) and (cache_key in _TRACK_CACHE) and (now_ts - _TRACK_CACHE_TS.get(cache_key, 0) < _TRACK_TTL_SECONDS):
        cache_age = now_ts - _TRACK_CACHE_TS.get(cache_key, 0)
        print(f"[TRACK CACHE HIT] age={cache_age:.1f}s, accounts={len(accounts)}, months={month_keys[0]} to {month_keys[-1]}")
        return _TRACK_CACHE[cache_key]

    print(f"[TRACK CACHE MISS] Fetching from S3: accounts={len(accounts)}, months={month_keys[0]} to {month_keys[-1]}, refresh={do_refresh}")

    # Determine day range for S3 scans
    start_day = months[0]
    # to last day of last month cell
    end_month = months[-1]
    if end_month.month == 12:
        next_month = dt.date(end_month.year + 1, 1, 1)
    else:
        next_month = dt.date(end_month.year, end_month.month + 1, 1)
    end_day = next_month - dt.timedelta(days=1)

    # Collect keys and read records (month-level listing to reduce API calls)
    stage4_keys = list(_iter_stage_objects_by_month(STAGE4_PREFIX, months))
    stage6_keys = list(_iter_stage_objects_by_month(STAGE6_PREFIX, months))
    stage4 = _read_json_records_from_s3(stage4_keys)
    stage6 = _read_json_records_from_s3(stage6_keys)
    stage7_keys = list(_iter_stage_objects_by_month(POST_ENTRATA_PREFIX, months))
    stage7 = _read_json_records_from_s3(stage7_keys)

    # Also check Historical Archive for archived bills
    archive_keys = list(_iter_stage_objects_by_month(HIST_ARCHIVE_PREFIX, months))
    archive = _read_json_records_from_s3(archive_keys)

    # Merge archive into stage7 so archived bills show as POSTED
    stage7.extend(archive)

    def norm_rec(rec: dict) -> dict:
        pid = rec.get("EnrichedPropertyID") or rec.get("propertyId") or rec.get("PropertyID")
        vid = rec.get("EnrichedVendorID") or rec.get("vendorId") or rec.get("VendorID")
        acct = rec.get("Account Number") or rec.get("accountNumber") or rec.get("AccountNumber")
        bill_date = rec.get("Bill Date") or rec.get("billDate")
        pstart = rec.get("Bill Period Start") or rec.get("billPeriodStart")
        pend = rec.get("Bill Period End") or rec.get("billPeriodEnd")
        due = rec.get("Due Date") or rec.get("dueDate")
        bd = _parse_date_any(str(bill_date or ""))
        mk = f"{bd.year:04d}-{bd.month:02d}" if bd else None
        return {
            "propertyId": str(pid or "").strip(),
            "vendorId": str(vid or "").strip(),
            "accountNumber": str(acct or "").strip(),
            "billDate": bd.isoformat() if bd else "",
            "billPeriodStart": pstart,
            "billPeriodEnd": pend,
            "dueDate": due,
            "monthKey": mk,
        }

    idx4 = {}
    for r in stage4:
        n = norm_rec(r)
        if n["monthKey"]:
            idx4[(n["propertyId"], n["vendorId"], n["accountNumber"], n["monthKey"])] = n
    idx6 = {}
    for r in stage6:
        n = norm_rec(r)
        if n["monthKey"]:
            idx6[(n["propertyId"], n["vendorId"], n["accountNumber"], n["monthKey"])] = n

    idx7 = {}
    for r in stage7:
        n = norm_rec(r)
        if n["monthKey"]:
            idx7[(n["propertyId"], n["vendorId"], n["accountNumber"], n["monthKey"])] = n

    def status_for(key_tuple: tuple, cutoff: dt.date) -> tuple[str, dict|None]:
        r7 = idx7.get(key_tuple)
        r6 = idx6.get(key_tuple)
        r4 = idx4.get(key_tuple)
        if r7:
            return ("POSTED", r7)
        if r6:
            return ("PENDING", r6)
        if r4:
            return ("PENDING", r4)
        if dt.date.today() < cutoff:
            return ("UPCOMING", None)
        return ("MISSING", None)

    rows = []
    for a in accounts:
        pid = str(a.get("propertyId") or "").strip()
        vid = str(a.get("vendorId") or "").strip()
        acct = str(a.get("accountNumber") or "").strip()
        days = int(a.get("daysBetweenBills") or 0)
        mrow = map_by_pid.get(pid) or {}
        ap = str(mrow.get("name") or "").strip()
        # Determine latest bill date across the window for this (pid,vid,acct)
        latest_bill_glob: dt.date | None = None
        for mk in month_keys:
            rec0 = (
                idx7.get((pid, vid, acct, mk))
                or idx6.get((pid, vid, acct, mk))
                or idx4.get((pid, vid, acct, mk))
            )
            if rec0:
                b = rec0.get("billDate")
                if b:
                    try:
                        d = dt.datetime.strptime(b, "%Y-%m-%d").date() if "-" in b else dt.datetime.strptime(b, "%m/%d/%Y").date()
                        if (latest_bill_glob is None) or (d > latest_bill_glob):
                            latest_bill_glob = d
                    except Exception:
                        pass
        next_expected: dt.date | None = None
        if latest_bill_glob and days:
            next_expected = latest_bill_glob + dt.timedelta(days=int(days))

        cells = []
        for mk, md in zip(month_keys, months):
            cutoff = md + dt.timedelta(days=max(days, 0))
            if next_expected and next_expected.year == md.year and next_expected.month == md.month:
                cutoff = next_expected
            st, rec = status_for((pid, vid, acct, mk), cutoff)
            tip = None
            label = ""
            if rec:
                label = rec.get("billDate") or ""
                tip = {
                    "billDate": rec.get("billDate") or "",
                    "billPeriodStart": rec.get("billPeriodStart") or "",
                    "billPeriodEnd": rec.get("billPeriodEnd") or "",
                    "dueDate": rec.get("dueDate") or "",
                }
            cells.append({"key": mk, "status": st, "label": label, "tooltip": tip})
        exp_str = ""
        if next_expected:
            exp = next_expected
            exp_str = exp.strftime("%Y-%m-%d")

        rows.append({
            "apName": ap,
            "vendorId": vid,
            "vendorCode": vendor_code_map.get(vid, ""),
            "vendorName": a.get("vendorName") or "",
            "accountNumber": acct,
            "propertyId": pid,
            "propertyName": a.get("propertyName") or "",
            "glAccountName": a.get("glAccountName") or "",
            "expectedBillDate": exp_str,
            "daysBetweenBills": days,
            "months": cells,
        })

    out = {"months": month_labels, "rows": rows}
    _TRACK_CACHE[cache_key] = out
    _TRACK_CACHE_TS[cache_key] = now_ts
    return out
@app.post("/api/delete_preentrata")
def api_delete_preentrata(key: str = Form(...), user: str = Depends(require_user)):
    """Delete a single Pre-Entrata merged file by its full S3 key.
    Returns {ok:true, deleted:1} on success. Missing files are treated as success with deleted:0.
    Also clears the 'Submitted' status so PARSE page shows correct status.
    """
    try:
        if not key or not key.startswith(PRE_ENTRATA_PREFIX):
            return JSONResponse({"error": "bad key"}, status_code=400)
        # If it doesn't exist, treat as success (idempotent)
        try:
            s3.head_object(Bucket=BUCKET, Key=key)
        except Exception:
            return {"ok": True, "deleted": 0}

        # Read the file BEFORE deleting to get line IDs for status reset
        cleared_count = 0
        y, m, d = None, None, None
        try:
            txt = _read_s3_text(BUCKET, key)
            lines = [json.loads(l) for l in txt.strip().split('\n') if l.strip()]
            for rec in lines:
                # Get the original S3 key and row index to build the review table PK
                orig_key = rec.get("__s3_key__", "")
                row_idx = rec.get("__row_idx__", 0)
                if orig_key:
                    line_id = line_id_from(orig_key, row_idx)
                    # Delete from review table to clear Submitted status
                    review_pk = f"review#{line_id}"
                    try:
                        ddb.delete_item(TableName=REVIEW_TABLE, Key={"pk": {"S": review_pk}})
                        cleared_count += 1
                    except Exception:
                        pass
                    # Extract date from key for cache invalidation
                    if not y:
                        y, m, d = _extract_ymd_from_key(orig_key)
        except Exception as e:
            print(f"[DELETE_PREENTRATA] Warning: Failed to clear Submitted status: {e}")

        s3.delete_object(Bucket=BUCKET, Key=key)
        # Invalidate post_view cache so deleted items don't reappear
        _CACHE.pop("post_view_items", None)
        # Also invalidate the day cache so PARSE page shows updated status
        if y and m and d:
            invalidate_day_cache(y, m, d)
        print(f"[DELETE_PREENTRATA] Deleted {key}, cleared {cleared_count} line statuses")
        return {"ok": True, "deleted": 1, "cleared": cleared_count}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/day", response_class=HTMLResponse)
def day_view(request: Request, date: str, user: str = Depends(require_user)):
    try:
        y, m, d = date.split("-")
    except ValueError:
        return RedirectResponse("/", status_code=302)
    rows = load_day(y, m, d)
    # show a limited set of columns
    cols = [
        "Invoice Number","Account Number","Line Item Account Number","Service Address","Utility Type",
        "Bill Period Start","Bill Period End","EnrichedGLAccountNumber","EnrichedGLAccountName","GL DESC_NEW",
        "ENRICHED CONSUMPTION","ENRICHED UOM","PDF_LINK","__id__","__s3_key__","__row_idx__"
    ]
    # filter
    view = [{k: r.get(k) for k in cols} for r in rows]
    return templates.TemplateResponse("day.html", {"request": request, "date": date, "rows": view, "user": user})


@app.get("/invoices", response_class=HTMLResponse)
def invoices_view(request: Request, date: str, user: str = Depends(require_user)):
    try:
        y, m, d = date.split("-")
    except ValueError:
        return RedirectResponse("/", status_code=302)
    rows = load_day(y, m, d)
    # group by (vendor, account, pdf_id) and collect ids; prefer header draft vendor override
    inv: Dict[tuple, Dict[str, Any]] = {}
    group_ids: Dict[tuple, List[str]] = {}
    header_vendor_cache: Dict[str, str] = {}
    # Cache the first valid vendor per pdf_id to ensure consistency across all rows from same PDF
    pdf_vendor_cache: Dict[str, str] = {}
    # Track accounts per pdf_id to detect multi-account PDFs
    pdf_accounts: Dict[str, set] = {}
    for r in rows:
        # Fall back from Account Number to Line Item Account Number for consistency
        account_no = str(r.get("Account Number", "") or r.get("Line Item Account Number", "") or "") or "(unknown)"
        # Use a stable unique id for the bill: hash of the source s3 key (pdf_id)
        pdf_id = pdf_id_from_key(r.get("__s3_key__", "")) if r.get("__s3_key__") else "(unknown)"
        invoice_no = str(r.get("Invoice Number", "")) or "(unknown)"  # retained only for display if needed
        # Try to load a header draft for this pdf to get the latest vendor name selected by the user
        vend_override = None
        try:
            if pdf_id and pdf_id != "(unknown)":
                if pdf_id in header_vendor_cache:
                    vend_override = header_vendor_cache[pdf_id]
                else:
                    dft = get_draft(pdf_id, "__header__", user)
                    if dft and isinstance(dft.get("fields"), dict):
                        vo = str(dft["fields"].get("EnrichedVendorName") or "").strip()
                        if vo:
                            vend_override = vo
                    header_vendor_cache[pdf_id] = vend_override or ""
        except Exception:
            pass
        # Check if we already have a vendor cached for this pdf_id (ensures all rows from same PDF group together)
        if pdf_id in pdf_vendor_cache and pdf_vendor_cache[pdf_id]:
            vendor = pdf_vendor_cache[pdf_id]
        else:
            vendor = (
                vend_override
                or str(r.get("EnrichedVendorName", ""))
                or str(r.get("Vendor Name", ""))
                or str(r.get("Vendor", ""))
                or str(r.get("Utility Type", ""))
                or "(unknown)"
            )
            # Cache the vendor for subsequent rows from the same PDF
            if pdf_id != "(unknown)" and vendor and vendor != "(unknown)":
                pdf_vendor_cache[pdf_id] = vendor
        key = (vendor, account_no, pdf_id)
        # Track accounts per pdf_id for multi-account detection
        pdf_accounts.setdefault(pdf_id, set()).add(account_no)
        # Extract property name for display in invoice list
        property_name = (
            str(r.get("EnrichedPropertyName", ""))
            or str(r.get("Property Name", ""))
            or str(r.get("PropertyName", ""))
            or ""
        )
        g = inv.setdefault(key, {
            "vendor": vendor,
            "account": account_no,
            "invoice": invoice_no,
            "parsed_date": date,
            "parsed_dt": None,
            "parsed_dt_fmt": None,
            "submitted_at": None,
            "submitted_at_fmt": None,
            "count": 0,
            "status": "REVIEW",
            "pdf_id": pdf_id,
            "total_amount": 0.0,
            "s3_key": r.get("__s3_key__", ""),  # Keep original S3 key for splitting
            "property": property_name
        })
        # Update property if not set (first row may not have it)
        if not g.get("property") and property_name:
            g["property"] = property_name
        g["count"] += 1
        # Sum up line item charges (exclude summary/aggregate rows like subtotals, taxes, totals)
        try:
            desc = str(r.get("Line Item Description", "")).upper().strip()
            # Skip rows with descriptions indicating they're summary/aggregate lines
            # Use word boundaries (\b) to avoid false positives like "FEES" matching "FEE"
            # Also check for exact matches of common summary-only terms
            summary_patterns = [
                r'\bSUBTOTAL\b', r'\bGRAND TOTAL\b', r'\bBALANCE DUE\b', r'\bAMOUNT DUE\b',
                r'\bTOTAL DUE\b', r'\bTOTAL CHARGES?\b', r'\bTOTAL AMOUNT\b'
            ]
            # Exact match for very short descriptions that are clearly summaries
            exact_summary = desc in ['TOTAL', 'SUBTOTAL', 'TAX', 'TAXES', 'BALANCE', 'AMOUNT DUE', 'TOTAL DUE']
            is_summary_row = exact_summary or any(re.search(p, desc) for p in summary_patterns)

            if not is_summary_row:
                charge_str = str(r.get("Line Item Charge", "0") or "0").replace("$", "").replace(",", "").strip()
                g["total_amount"] += float(charge_str) if charge_str else 0.0
        except (ValueError, TypeError):
            pass
        # pdf_id already set above; keep earliest parsed time below
        group_ids.setdefault(key, []).append(str(r.get("__id__")))
        # capture earliest parsed_at_utc for this group if present
        pat = r.get("parsed_at_utc") or r.get("ParsedAtUtc")
        if pat:
            try:
                if (g["parsed_dt"] is None) or (pat < g["parsed_dt"]):
                    # normalize to milliseconds without timezone for display
                    ts = pat.replace('Z', '+00:00')
                    try:
                        dtv = dt.datetime.fromisoformat(ts)
                        g["parsed_dt_fmt"] = dtv.strftime('%Y-%m-%d %H:%M:%S.%f')[:23]
                    except Exception:
                        g["parsed_dt_fmt"] = pat
                    g["parsed_dt"] = pat
            except Exception:
                pass

    # batch lookup statuses once
    all_ids: List[str] = [i for ids in group_ids.values() for i in ids if i]
    stmap = get_status_map(all_ids)

    def has_account_artifact(acct_val: str) -> bool:
        """Return True if an artifact exists for this account on the day in either Stage 6 (Pre-Entrata) OR Stage 7 (Post-Entrata).
        We match by sanitized account token anywhere in the basename.
        """
        acct = (acct_val or "").strip()
        if not acct:
            return False
        token_dash = "".join(ch if (ch.isalnum() or ch in "-") else "-" for ch in acct)
        token_us = "".join(ch if ch.isalnum() else "_" for ch in acct)
        prefixes = [
            f"{PRE_ENTRATA_PREFIX}yyyy={y}/mm={m}/dd={d}/",
            f"{POST_ENTRATA_PREFIX}yyyy={y}/mm={m}/dd={d}/",
        ]
        for pfx in prefixes:
            try:
                resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=pfx)
                for obj in resp.get("Contents", []) or []:
                    k = obj.get("Key", "")
                    if token_dash in k or token_us in k:
                        return True
            except Exception:
                continue
        return False

    # compute status per group consistent with dashboard rule
    for key, meta in inv.items():
        _, acct, pid = key
        ids_all = group_ids.get(key, [])
        # Exclude Deleted lines from status calculation (matches API logic)
        ids_active = [i for i in ids_all if stmap.get(i, {}).get("status") != "Deleted"]
        submitted = sum(1 for i in ids_active if stmap.get(i, {}).get("status") == "Submitted")
        # Get latest submitted_at time for this group
        submitted_times = [stmap.get(i, {}).get("submitted_at") for i in ids_active if stmap.get(i, {}).get("submitted_at")]
        if submitted_times:
            latest_submit = max(submitted_times)
            meta["submitted_at"] = latest_submit
            try:
                ts = latest_submit.replace('Z', '+00:00')
                dtv = dt.datetime.fromisoformat(ts)
                meta["submitted_at_fmt"] = dtv.strftime('%Y-%m-%d %H:%M:%S')[:19]
            except Exception:
                meta["submitted_at_fmt"] = latest_submit[:19] if latest_submit else None
        if submitted == 0:
            meta["status"] = "REVIEW"
        elif submitted == len(ids_active):
            # All non-deleted lines submitted = COMPLETE
            meta["status"] = "COMPLETE"
        else:
            meta["status"] = "PARTIAL"
        # Flag multi-account PDFs
        accounts_in_pdf = pdf_accounts.get(pid, set())
        meta["multi_account"] = len(accounts_in_pdf) > 1
        meta["account_count"] = len(accounts_in_pdf)
    invoices = list(inv.values())
    # sort: REVIEW/PARTIAL first (oldest dates first), COMPLETE at bottom
    status_rank = {"REVIEW": 0, "PARTIAL": 1, "COMPLETE": 2}
    invoices.sort(key=lambda x: (status_rank.get(x["status"], 9), x.get("parsed_dt") or x["parsed_date"], x["vendor"], x["account"], x["invoice"]))
    return templates.TemplateResponse("invoices.html", {"request": request, "date": date, "invoices": invoices, "user": user})


@app.post("/api/delete_parsed")
def api_delete_parsed(date: str = Form(...), pdf_ids: str = Form(...), user: str = Depends(require_user)):
    """Delete parsed invoices by pdf_id by removing their underlying enriched jsonl files for the given day.
    This deletes all S3 objects for the day that contain lines associated with the provided pdf_ids.
    """
    try:
        y, m, d = date.split('-')
    except ValueError:
        return JSONResponse({"error": "bad date"}, status_code=400)
    try:
        wanted = set([p for p in (pdf_ids or '').split(',') if p])
        if not wanted:
            return JSONResponse({"error": "no pdf_ids"}, status_code=400)
        rows = load_day(y, m, d)
        # find all source keys that have any row with matching pdf_id
        keys = set()
        accounts = set()
        for r in rows:
            key = r.get("__s3_key__")
            if not key:
                continue
            pid = pdf_id_from_key(key)
            if pid in wanted:
                keys.add(key)
                acct = str(r.get("Account Number", "")).strip()
                if acct:
                    accounts.add(acct)
        if not keys:
            return {"ok": True, "deleted": 0}
        # delete the objects
        deleted = 0
        for k in keys:
            s3.delete_object(Bucket=BUCKET, Key=k)
            deleted += 1
        # Also delete any Pre-Entrata files for the same day whose key includes the account token
        if accounts:
            day_prefix = f"{PRE_ENTRATA_PREFIX}yyyy={y}/mm={m}/dd={d}/"
            resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=day_prefix)
            to_delete = []
            # Build token variants for each account
            tokens = set()
            for a in accounts:
                a = a.strip()
                if not a:
                    continue
                tokens.add("".join(ch if (ch.isalnum() or ch in "-") else "-" for ch in a))
                tokens.add("".join(ch if ch.isalnum() else "_" for ch in a))
            for obj in resp.get("Contents", []) or []:
                k = obj.get("Key", "")
                if any(t in k for t in tokens):
                    to_delete.append(k)
            for k in to_delete:
                try:
                    s3.delete_object(Bucket=BUCKET, Key=k)
                    deleted += 1
                except Exception:
                    pass
        # Invalidate caches so deleted files don't show up
        invalidate_day_cache(y, m, d)
        _CACHE.pop(("day_status_counts", y, m, d), None)
        _CACHE.pop(("parse_dashboard",), None)
        return {"ok": True, "deleted": deleted}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/bulk_assign_property")
def api_bulk_assign_property(
    date: str = Form(...),
    pdf_ids: str = Form(...),
    property_id: str = Form(""),
    property_name: str = Form(""),
    user: str = Depends(require_user)
):
    """Bulk assign property to all lines in the selected invoices."""
    try:
        y, m, d = date.split('-')
    except ValueError:
        return JSONResponse({"error": "bad date"}, status_code=400)

    wanted = set([p for p in (pdf_ids or '').split(',') if p])
    if not wanted:
        return JSONResponse({"error": "no pdf_ids"}, status_code=400)
    if not property_id and not property_name:
        return JSONResponse({"error": "property_id or property_name required"}, status_code=400)

    try:
        rows = load_day(y, m, d)
        # Group rows by S3 key
        by_key: dict[str, list] = {}
        for r in rows:
            key = r.get("__s3_key__")
            if not key:
                continue
            pid = pdf_id_from_key(key)
            if pid in wanted:
                by_key.setdefault(key, []).append(r)

        if not by_key:
            return {"ok": True, "updated": 0, "message": "No matching invoices found"}

        # Update each file
        updated_count = 0
        for s3_key, key_rows in by_key.items():
            # Read original file
            try:
                txt = _read_s3_text(BUCKET, s3_key)
                lines_orig = [json.loads(l) for l in txt.strip().split('\n') if l.strip()]
            except Exception:
                continue

            # Update property fields in each line
            for rec in lines_orig:
                if property_id:
                    rec["EnrichedPropertyID"] = property_id
                if property_name:
                    rec["EnrichedPropertyName"] = property_name

            # Write back
            new_content = '\n'.join(json.dumps(rec, ensure_ascii=False) for rec in lines_orig)
            if s3_key.endswith('.gz'):
                body = gzip.compress(new_content.encode('utf-8'))
                s3.put_object(Bucket=BUCKET, Key=s3_key, Body=body, ContentType='application/json', ContentEncoding='gzip')
            else:
                s3.put_object(Bucket=BUCKET, Key=s3_key, Body=new_content.encode('utf-8'), ContentType='application/json')
            updated_count += 1

        # Update or create header drafts in DynamoDB so property override matches S3
        for pdf_id in wanted:
            try:
                pk = f"draft#{pdf_id}#__header__#{user}"
                resp = ddb.get_item(TableName=DRAFTS_TABLE, Key={"pk": {"S": pk}})
                item = resp.get("Item")
                if item:
                    # Draft exists - update it with new property
                    fields_raw = item.get("fields", {}).get("S", "{}")
                    fields = json.loads(fields_raw) if fields_raw else {}
                    if property_id:
                        fields["EnrichedPropertyID"] = property_id
                    if property_name:
                        fields["EnrichedPropertyName"] = property_name
                    ddb.update_item(
                        TableName=DRAFTS_TABLE,
                        Key={"pk": {"S": pk}},
                        UpdateExpression="SET fields = :f",
                        ExpressionAttributeValues={":f": {"S": json.dumps(fields)}}
                    )
                else:
                    # No draft exists - create one with just property info
                    fields = {}
                    if property_id:
                        fields["EnrichedPropertyID"] = property_id
                    if property_name:
                        fields["EnrichedPropertyName"] = property_name
                    ddb.put_item(
                        TableName=DRAFTS_TABLE,
                        Item={
                            "pk": {"S": pk},
                            "fields": {"S": json.dumps(fields)}
                        }
                    )
            except Exception as e:
                print(f"[bulk_assign_property] Warning: Failed to update header draft for {pdf_id}: {e}")

        # Invalidate cache
        invalidate_day_cache(y, m, d)
        return {"ok": True, "updated": updated_count}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/bulk_assign_vendor")
def api_bulk_assign_vendor(
    date: str = Form(...),
    pdf_ids: str = Form(...),
    vendor_id: str = Form(""),
    vendor_name: str = Form(""),
    user: str = Depends(require_user)
):
    """Bulk assign vendor to all lines in the selected invoices."""
    try:
        y, m, d = date.split('-')
    except ValueError:
        return JSONResponse({"error": "bad date"}, status_code=400)

    wanted = set([p for p in (pdf_ids or '').split(',') if p])
    if not wanted:
        return JSONResponse({"error": "no pdf_ids"}, status_code=400)
    if not vendor_id and not vendor_name:
        return JSONResponse({"error": "vendor_id or vendor_name required"}, status_code=400)

    try:
        rows = load_day(y, m, d)
        # Group rows by S3 key
        by_key: dict[str, list] = {}
        for r in rows:
            key = r.get("__s3_key__")
            if not key:
                continue
            pid = pdf_id_from_key(key)
            if pid in wanted:
                by_key.setdefault(key, []).append(r)

        if not by_key:
            return {"ok": True, "updated": 0, "message": "No matching invoices found"}

        # Update each file
        updated_count = 0
        for s3_key, key_rows in by_key.items():
            # Read original file
            try:
                txt = _read_s3_text(BUCKET, s3_key)
                lines_orig = [json.loads(l) for l in txt.strip().split('\n') if l.strip()]
            except Exception:
                continue

            # Update vendor fields in each line
            for rec in lines_orig:
                if vendor_id:
                    rec["EnrichedVendorID"] = vendor_id
                if vendor_name:
                    rec["EnrichedVendorName"] = vendor_name

            # Write back
            new_content = '\n'.join(json.dumps(rec, ensure_ascii=False) for rec in lines_orig)
            if s3_key.endswith('.gz'):
                body = gzip.compress(new_content.encode('utf-8'))
                s3.put_object(Bucket=BUCKET, Key=s3_key, Body=body, ContentType='application/json', ContentEncoding='gzip')
            else:
                s3.put_object(Bucket=BUCKET, Key=s3_key, Body=new_content.encode('utf-8'), ContentType='application/json')
            updated_count += 1

        # Update or create header drafts in DynamoDB so vendor override matches S3
        for pdf_id in wanted:
            try:
                pk = f"draft#{pdf_id}#__header__#{user}"
                resp = ddb.get_item(TableName=DRAFTS_TABLE, Key={"pk": {"S": pk}})
                item = resp.get("Item")
                if item:
                    # Draft exists - update it with new vendor
                    fields_raw = item.get("fields", {}).get("S", "{}")
                    try:
                        fields = json.loads(fields_raw) if isinstance(fields_raw, str) else {}
                    except Exception:
                        fields = {}
                else:
                    # No draft exists - create new one with vendor info
                    fields = {}
                if vendor_name:
                    fields["EnrichedVendorName"] = vendor_name
                if vendor_id:
                    fields["EnrichedVendorID"] = vendor_id
                # Write draft (create or update; use #f for reserved word 'fields')
                ddb.put_item(
                    TableName=DRAFTS_TABLE,
                    Item={
                        "pk": {"S": pk},
                        "fields": {"S": json.dumps(fields, ensure_ascii=False)}
                    }
                )
            except Exception as e:
                print(f"[bulk_assign_vendor] Warning: Failed to update header draft for {pdf_id}: {e}")

        # Invalidate cache
        invalidate_day_cache(y, m, d)
        return {"ok": True, "updated": updated_count}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/bulk_rework")
def api_bulk_rework(
    date: str = Form(...),
    pdf_ids: str = Form(...),
    notes: str = Form(""),
    user: str = Depends(require_user)
):
    """Bulk send selected invoices back for rework with shared notes."""
    try:
        y, m, d = date.split('-')
    except ValueError:
        return JSONResponse({"error": "bad date"}, status_code=400)

    wanted = list(set([p for p in (pdf_ids or '').split(',') if p]))
    if not wanted:
        return JSONResponse({"error": "no pdf_ids"}, status_code=400)

    try:
        rows = load_day(y, m, d)

        # Group rows by pdf_id
        by_pdf: dict[str, list] = {}
        for r in rows:
            key = r.get("__s3_key__")
            if not key:
                continue
            pid = pdf_id_from_key(key)
            if pid in wanted:
                by_pdf.setdefault(pid, []).append(r)

        sent_count = 0
        errors = []

        for pid in wanted:
            pid_rows = by_pdf.get(pid, [])
            if not pid_rows:
                errors.append(f"No data found for {pid}")
                continue

            first = pid_rows[0]

            # Find PDF link
            pdf_link = first.get("PDF_LINK", "")
            if not pdf_link:
                # Try to infer from key
                pdf_link = _infer_pdf_key_for_doc(y, m, d, pid_rows, pid)

            if not pdf_link:
                errors.append(f"No PDF link for {pid}")
                continue

            # Parse the S3 key from PDF link
            final_url = pdf_link
            if '?' in final_url:
                final_url = final_url.split('?')[0]

            parsed = None
            if final_url.startswith('s3://'):
                parts = final_url[5:].split('/', 1)
                if len(parts) == 2:
                    parsed = (parts[0], parts[1])
            elif '.s3.' in final_url or 's3.amazonaws.com' in final_url:
                try:
                    from urllib.parse import urlparse
                    pu = urlparse(final_url)
                    if '.s3.' in pu.netloc:
                        bucket_part = pu.netloc.split('.s3.')[0]
                        key_part = pu.path.lstrip('/')
                        parsed = (bucket_part, key_part)
                    elif 's3.amazonaws.com' in pu.netloc:
                        path_parts = pu.path.lstrip('/').split('/', 1)
                        if len(path_parts) == 2:
                            parsed = (path_parts[0], path_parts[1])
                except Exception:
                    pass

            if not parsed:
                errors.append(f"Cannot parse PDF location for {pid}")
                continue

            src_bucket, src_key = parsed

            # Copy to rework prefix
            ts = dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
            base = os.path.basename(src_key).replace("\\", "_").replace("/", "_")
            dest_key = f"{REWORK_PREFIX}yyyy={y}/mm={m}/dd={d}/{ts}_{pid}_{base}"

            try:
                s3.copy_object(Bucket=BUCKET, CopySource={"Bucket": src_bucket, "Key": src_key}, Key=dest_key)
            except Exception as e:
                errors.append(f"Copy failed for {pid}: {str(e)}")
                continue

            # Write sidecar rework.json with notes
            meta = {
                "notes": notes,
                "user": user,
                "rework_ts": ts,
                "original_pdf_id": pid,
                "bill_from": first.get("Bill From", ""),
                "account_number": first.get("Account Number", ""),
                "bill_date": first.get("Bill Date", ""),
            }
            meta_key = dest_key.rsplit('.', 1)[0] + ".rework.json"
            s3.put_object(Bucket=BUCKET, Key=meta_key, Body=json.dumps(meta, ensure_ascii=False).encode('utf-8'), ContentType='application/json')

            # Delete enriched artifacts for this pdf_id
            keys_to_delete = set()
            accounts = set()
            for r in pid_rows:
                k = r.get("__s3_key__")
                if k:
                    keys_to_delete.add(k)
                acct = str(r.get("Account Number", "")).strip()
                if acct:
                    accounts.add(acct)

            for k in keys_to_delete:
                try:
                    s3.delete_object(Bucket=BUCKET, Key=k)
                except Exception:
                    pass

            # Delete Pre-Entrata files for same accounts
            if accounts:
                day_prefix = f"{PRE_ENTRATA_PREFIX}yyyy={y}/mm={m}/dd={d}/"
                resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=day_prefix)
                tokens = set()
                for a in accounts:
                    a = a.strip()
                    if not a:
                        continue
                    tokens.add("".join(ch if (ch.isalnum() or ch in "-") else "-" for ch in a))
                    tokens.add("".join(ch if ch.isalnum() else "_" for ch in a))
                for obj in resp.get("Contents", []) or []:
                    k = obj.get("Key", "")
                    if any(t in k for t in tokens):
                        try:
                            s3.delete_object(Bucket=BUCKET, Key=k)
                        except Exception:
                            pass

            sent_count += 1

        # Invalidate caches
        invalidate_day_cache(y, m, d)
        _CACHE.pop(("day_status_counts", y, m, d), None)
        _CACHE.pop(("parse_dashboard",), None)

        return {"ok": True, "sent": sent_count, "errors": errors if errors else None}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/split_bill")
def api_split_bill(date: str = Form(...), pdf_id: str = Form(...), user: str = Depends(require_user)):
    """Split a multi-account PDF into separate bill files by account number.

    This takes an enriched JSONL file that contains multiple account numbers and splits it
    into separate files, one per account. Each new file gets a unique key based on
    original_key + account_number suffix.
    """
    try:
        y, m, d = date.split('-')
    except ValueError:
        return JSONResponse({"error": "bad date"}, status_code=400)

    try:
        rows = load_day(y, m, d)

        # Find all rows belonging to this pdf_id
        target_key = None
        target_rows = []
        for r in rows:
            key = r.get("__s3_key__", "")
            if not key:
                continue
            pid = pdf_id_from_key(key)
            if pid == pdf_id:
                target_key = key
                target_rows.append(r)

        if not target_rows:
            return JSONResponse({"error": "No rows found for pdf_id"}, status_code=404)

        # Group rows by account number
        from collections import defaultdict
        rows_by_account: Dict[str, List[Dict]] = defaultdict(list)
        for r in target_rows:
            acct = str(r.get("Account Number", "")) or "(unknown)"
            rows_by_account[acct].append(r)

        if len(rows_by_account) < 2:
            return JSONResponse({"error": "Only one account found - nothing to split"}, status_code=400)

        # Create separate files for each account
        created_files = []
        base_key = target_key.rsplit('.', 1)[0]  # Remove .jsonl extension

        for acct, acct_rows in rows_by_account.items():
            # Create new key with account suffix
            safe_acct = "".join(ch if ch.isalnum() else "_" for ch in acct)
            new_key = f"{base_key}_ACCT_{safe_acct}.jsonl"

            # Prepare rows for new file (remove internal fields)
            output_lines = []
            for r in acct_rows:
                # Create clean copy without internal fields
                clean_row = {k: v for k, v in r.items() if not k.startswith("__")}
                output_lines.append(json.dumps(clean_row, ensure_ascii=False))

            # Write new file
            content = "\n".join(output_lines)
            s3.put_object(
                Bucket=BUCKET,
                Key=new_key,
                Body=content.encode('utf-8'),
                ContentType='application/json'
            )
            created_files.append({"key": new_key, "account": acct, "rows": len(acct_rows)})

        # Delete original combined file
        s3.delete_object(Bucket=BUCKET, Key=target_key)

        # Also delete any header drafts for the old pdf_id (they're no longer valid)
        try:
            for suffix in ["admin", "__final__"]:
                pk = f"draft#{pdf_id}#__header__#{suffix}"
                ddb.delete_item(TableName=DRAFTS_TABLE, Key={"pk": {"S": pk}})
        except Exception:
            pass  # Ignore errors cleaning up drafts

        # Invalidate cache so the page shows the new split files
        invalidate_day_cache(y, m, d)

        return {
            "ok": True,
            "original_file": target_key,
            "split_into": created_files,
            "message": f"Split into {len(created_files)} separate bills by account"
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/rework")
def api_rework(
    date: str = Form(...),
    pdf_id: str = Form(...),
    notes: str = Form(""),
    bill_from: str = Form(""),
    account_number: str = Form(""),
    bill_date: str = Form(""),
    pdf_key: str = Form(""),
    expected_lines: str | None = Form(None),
    user: str = Depends(require_user)
):
    """Send a bill back to a REWORK pipeline and delete current parsed artifacts for this pdf_id on the given day.
    Steps:
      - Identify rows for (date,pdf_id)
      - Derive source PDF s3 key from PDF_LINK
      - Copy PDF to REWORK_PREFIX with timestamped name
      - Write a sidecar rework.json with notes and metadata
      - Delete enriched artifacts for the pdf_id and related Pre-Entrata files for same account on that day
    """
    try:
        y, m, d = date.split('-')
    except ValueError:
        return JSONResponse({"error": "bad date"}, status_code=400)
    try:
        rows = load_day(y, m, d)
        # filter rows belonging to this pdf_id
        hit = [r for r in rows if r.get("__s3_key__") and pdf_id_from_key(r.get("__s3_key__")) == pdf_id]
        if not hit:
            # Fallback: try to locate by Account Number (and Bill Date if provided)
            acc = (account_number or str(first.get("Account Number", "") if False else "")).strip()  # safeguard; "first" not defined yet
            if not acc:
                # derive from header draft if present
                try:
                    hd = get_draft(pdf_id, "__header__", user) or {"fields": {}}
                    acc = str(hd.get("fields", {}).get("Account Number", "")).strip()
                except Exception:
                    acc = ""
            bd = str(bill_date or "").strip()
            if acc:
                cand = [r for r in rows if str(r.get("Account Number", "")).strip() == acc]
                if bd:
                    exact = [r for r in cand if str(r.get("Bill Date", "")).strip() == bd]
                    hit = exact or cand
                else:
                    hit = cand
        if not hit:
            return JSONResponse({"error": "no rows for pdf_id"}, status_code=404)
        first = hit[0]
        # Allow explicit key from client to avoid parsing fragile links
        if pdf_key and isinstance(pdf_key, str):
            key_in = pdf_key.strip()
            try:
                if key_in.startswith(('http://','https://','s3://')):
                    parsed = _parse_s3_from_url(key_in)
                    if parsed:
                        src_bucket, src_key = parsed
                    else:
                        from urllib.parse import urlparse as _up
                        _p = _up(key_in)
                        src_bucket, src_key = BUCKET, (_p.path or '').lstrip('/')
                else:
                    key2 = key_in.lstrip('/')
                    if key2.startswith(f"{BUCKET}/"):
                        key2 = key2[len(BUCKET)+1:]
                    src_bucket, src_key = BUCKET, key2
                # proceed to copy
                pdf_url = key_in
                parsed = (src_bucket, src_key)
            except Exception:
                parsed = None
        else:
            parsed = None
        pdf_url = str(first.get("PDF_LINK", "") or first.get("source_input_key", "") or (pdf_key or "")) if not parsed else pdf_url
        if not pdf_url:
            # Try to infer PDF key as a fallback
            cand = _infer_pdf_key_for_doc(y, m, d, hit, pdf_id)
            if cand:
                src_bucket, src_key = BUCKET, cand
                pdf_url = cand
            else:
                return JSONResponse({"error": "missing PDF link"}, status_code=400)
        # Step 1: expand legacy short links
        pdf_url = _maybe_expand_short(pdf_url)
        # Step 2: try parse original and resolved final (unless parsed from pdf_key already)
        parsed_orig = parsed or _parse_s3_from_url(pdf_url)
        final_url = _resolve_final_url(pdf_url)
        parsed_final = _parse_s3_from_url(final_url)
        # Step 3: if looks like lambda-url and still not parsed, aggressively resolve via GET
        if not parsed_final and pdf_url:
            try:
                host = (urlparse(pdf_url).netloc or "")
                if ".lambda-url." in host:
                    r = requests.get(pdf_url, allow_redirects=True, timeout=12, stream=False)
                    final_url = r.url or final_url or pdf_url
                    parsed_final = _parse_s3_from_url(final_url)
            except Exception:
                pass
        parsed = parsed_final or parsed_orig
        # Step 4: last-ditch explicit virtual-hosted parse
        if not parsed and (final_url or pdf_url):
            cand = final_url or pdf_url
            try:
                p = urlparse(cand)
                host = p.netloc or ""; path = (p.path or "").lstrip('/')
                if ".s3.amazonaws.com" in host and path:
                    b = host.split('.s3.amazonaws.com', 1)[0]
                    if b:
                        parsed = (b, unquote(path))
            except Exception:
                pass
        # Step 5: accept bare key
        if not parsed and pdf_url and ('/' in pdf_url) and not pdf_url.startswith(('http://','https://','s3://')):
            parsed = (BUCKET, pdf_url.lstrip('/'))
        if not parsed:
            # Fallback: try to infer the PDF key like /pdf does
            try:
                key_guess = _infer_pdf_key_for_doc(y, m, d, hit, pdf_id)
                if not key_guess:
                    # global scan by pdf_id across known prefixes
                    cands = []
                    for pfx in (REWORK_PREFIX, INPUT_PREFIX, PARSED_INPUTS_PREFIX, HIST_ARCHIVE_PREFIX):
                        pag = s3.get_paginator("list_objects_v2")
                        for page in pag.paginate(Bucket=BUCKET, Prefix=pfx):
                            for obj in page.get("Contents", []) or []:
                                k = obj.get("Key", ""); base = os.path.basename(k).lower()
                                if base.endswith('.pdf') and pdf_id.lower() in base:
                                    cands.append((k, obj.get("LastModified")))
                    if cands:
                        cands.sort(key=lambda t: (t[1] or 0), reverse=True)
                        key_guess = cands[0][0]
                if key_guess:
                    src_bucket, src_key = BUCKET, key_guess.lstrip('/')
                    parsed = (src_bucket, src_key)
            except Exception:
                parsed = None
        if not parsed:
            print(f"/api/rework parse failed: pdf_url={pdf_url} final_url={final_url}")
            return JSONResponse({"error": "cannot parse s3 from PDF link"}, status_code=400)
        src_bucket, src_key = parsed
        # copy to rework prefix
        ts = dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        base = os.path.basename(src_key).replace("\\", "_").replace("/", "_")
        dest_key = f"{REWORK_PREFIX}yyyy={y}/mm={m}/dd={d}/{ts}_{pdf_id}_{base}"
        s3.copy_object(Bucket=BUCKET, CopySource={"Bucket": src_bucket, "Key": src_key}, Key=dest_key)
        # write sidecar json with notes (and optional header context like Bill From)
        # Attempt to read header draft so we can include 'Bill From' back to the parser
        header_pid = pdf_id  # header draft is keyed by pdf_id + '__header__'
        header_draft = get_draft(header_pid, "__header__", user) or {"fields": {}}
        bill_from_val = (str(bill_from).strip() if bill_from is not None else "")
        if not bill_from_val:
            bill_from_val = str(header_draft.get("fields", {}).get("Bill From", "")).strip()
        if not bill_from_val:
            # fallback to source row fields if draft not present
            r0 = first
            bill_from_val = (
                str(r0.get("Bill From", ""))
                or str(r0.get("Bill From Name", ""))
                or str(r0.get("Bill From Name First Line", ""))
                or str(r0.get("Vendor Name", ""))
                or str(r0.get("EnrichedVendorName", ""))
            )
        # Normalize expected_lines to int if provided
        exp_lines_val = None
        try:
            if expected_lines is not None and str(expected_lines).strip():
                exp_lines_val = int(str(expected_lines).strip())
        except Exception:
            exp_lines_val = None

        meta = {
            "requested_by": user,
            "requested_utc": dt.datetime.utcnow().isoformat(),
            "date": date,
            "pdf_id": pdf_id,
            "src_bucket": src_bucket,
            "src_key": src_key,
            "dest_bucket": BUCKET,
            "dest_key": dest_key,
            "notes": notes or "",
            "Bill From": bill_from_val,
        }
        if exp_lines_val is not None:
            # include multiple synonymous keys for downstream compatibility
            meta["expected_line_count"] = exp_lines_val
            meta["expectedLines"] = exp_lines_val
            meta["expected_lines"] = exp_lines_val
            meta["line_count"] = exp_lines_val
            meta["min_lines"] = exp_lines_val
        meta_key = dest_key.rsplit('.', 1)[0] + ".rework.json"
        s3.put_object(Bucket=BUCKET, Key=meta_key, Body=json.dumps(meta, ensure_ascii=False).encode('utf-8'), ContentType='application/json')

        # delete enriched artifacts for this pdf_id (reuse logic from api_delete_parsed)
        # find all enriched jsonl objects tied to this pdf_id for the day
        keys = set()
        accounts = set()
        for r in hit:
            k = r.get("__s3_key__")
            if k:
                keys.add(k)
            acct = str(r.get("Account Number", "")).strip()
            if acct:
                accounts.add(acct)
        deleted = 0
        for k in keys:
            try:
                s3.delete_object(Bucket=BUCKET, Key=k); deleted += 1
            except Exception:
                pass
        # Also delete Pre-Entrata files for same day/account
        if accounts:
            day_prefix = f"{PRE_ENTRATA_PREFIX}yyyy={y}/mm={m}/dd={d}/"
            resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=day_prefix)
            tokens = set()
            for a in accounts:
                tokens.add("".join(ch if (ch.isalnum() or ch in "-") else "-" for ch in a))
                tokens.add("".join(ch if ch.isalnum() else "_" for ch in a))
            for obj in resp.get("Contents", []) or []:
                k = obj.get("Key", "")
                if any(t in k for t in tokens):
                    try:
                        s3.delete_object(Bucket=BUCKET, Key=k); deleted += 1
                    except Exception:
                        pass
        # Invalidate cache for this day so UI reflects deletions immediately
        try:
            invalidate_day_cache(y, m, d)
        except Exception:
            pass

        return {"ok": True, "copied_key": dest_key, "meta_key": meta_key, "deleted": deleted}
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)
def get_status_map(ids: List[str]) -> Dict[str, Dict[str, str]]:
    """Returns a dict mapping id -> {"status": str, "submitted_at": str}"""
    if not ids:
        return {}
    # DynamoDB batch_get in chunks of 100
    out: Dict[str, Dict[str, str]] = {}
    it = iter(ids)
    while True:
        chunk = list(islice(it, 100))
        if not chunk:
            break
        keys = [{"pk": {"S": i}} for i in chunk]
        resp = ddb.batch_get_item(RequestItems={REVIEW_TABLE: {"Keys": keys}})
        for item in resp.get("Responses", {}).get(REVIEW_TABLE, []):
            pk = item.get("pk", {}).get("S")
            status = item.get("status", {}).get("S", "")
            submitted_at = item.get("submitted_at", {}).get("S", "")
            if pk:
                out[pk] = {"status": status, "submitted_at": submitted_at}
    return out


def day_status_counts(y: str, m: str, d: str) -> Dict[str, int]:
    """Compute per-day status PER INVOICE GROUP (vendor, account, pdf_id) for the dashboard.

    Rules (aligned with invoices list logic except artifact check):
    - Exclude lines marked 'Deleted'.
    - COMPLETE when all non-deleted lines are Submitted (no artifact check here).
    - PARTIAL if some submitted; REVIEW if none submitted.
    """
    # Cache day_status_counts to avoid expensive DynamoDB queries on every parse page load
    # Use longer TTL for past days since they change less frequently
    cache_key = ("day_status_counts", y, m, d)
    now = time.time()
    ent = _CACHE.get(cache_key)
    ttl = _get_cache_ttl(y, m, d)
    if ent and (now - ent.get("ts", 0) < ttl):
        return ent.get("data", {})

    rows = load_day(y, m, d)

    # Preload header overrides per pdf, prefer shared '__final__', then use first user found
    header_by_pdf: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = r.get("__s3_key__", "")
        if not key:
            continue
        pid = pdf_id_from_key(key)
        if pid and pid not in header_by_pdf:
            # Try to get shared final header, or any user's header
            hdr_draft = get_draft(pid, "__header__", "__final__")
            if not hdr_draft:
                # If no final header, try to get first user's header (approximate)
                # This matches invoices_status logic which uses current user
                hdr_draft = {}
            header_by_pdf[pid] = (hdr_draft or {}).get("fields", {})

    # Build mapping: (vendor, account, pdf_id) -> list of row ids
    groups: Dict[tuple, List[str]] = {}
    all_ids: List[str] = []
    for r in rows:
        key = r.get("__s3_key__")
        if not key:
            continue
        pid = pdf_id_from_key(key)
        rid = str(r.get("__id__"))
        if not (rid and pid):
            continue

        hdr = header_by_pdf.get(pid, {})
        # Use row's Account Number directly (NOT header override) to preserve multi-account grouping
        # Use Line Item Account Number as fallback when Account Number is blank (for subtotals/taxes)
        account_no = str(r.get("Account Number", "") or r.get("Line Item Account Number", "") or "") or "(unknown)"
        vendor = (
            str(hdr.get("EnrichedVendorName", ""))
            or str(r.get("EnrichedVendorName", ""))
            or str(r.get("Vendor Name", ""))
            or str(r.get("Vendor", ""))
            or str(r.get("Utility Type", ""))
            or "(unknown)"
        )

        group_key = (vendor, account_no, pid)
        groups.setdefault(group_key, []).append(rid)
        all_ids.append(rid)

    stmap = get_status_map(all_ids)

    review = partial = complete = 0
    for group_key, ids_all in groups.items():
        ids_active = [i for i in ids_all if stmap.get(i, {}).get("status") != "Deleted"]
        if not ids_active:
            # No active lines; treat as REVIEW bucket until files are fully removed
            review += 1
            continue
        submitted = sum(1 for i in ids_active if stmap.get(i, {}).get("status") == "Submitted")
        if submitted == 0:
            review += 1
        elif submitted == len(ids_active):
            complete += 1
        else:
            partial += 1

    counts = {"REVIEW": review, "PARTIAL": partial, "COMPLETE": complete}
    _CACHE[cache_key] = {"ts": now, "data": counts}
    return counts


@app.get("/review", response_class=HTMLResponse)
def review_view(request: Request, date: str, pdf_id: str, user: str = Depends(require_user)):
    try:
        y, m, d = date.split("-")
    except ValueError:
        return RedirectResponse("/", status_code=302)
    all_rows = load_day(y, m, d)
    # Filter strictly by pdf_id (unique hash from __s3_key__), not by invoice number
    rows = [r for r in all_rows if r.get("__s3_key__") and pdf_id_from_key(r.get("__s3_key__")) == pdf_id]
    if not rows:
        return templates.TemplateResponse("error.html", {"request": request, "message": "No rows found for that document."}, status_code=404)
    # build header defaults from first row (invoice-level)
    header_fields = [
        "EnrichedPropertyName","EnrichedPropertyID",
        "EnrichedVendorName","EnrichedVendorID","Account Number",
        "Bill Period Start","Bill Period End","Bill Date","Due Date",
        "Service Address","Service City","Service State","Service Zipcode",
        "Special Instructions",
        "Bill From",
    ]
    header: Dict[str, Any] = {k: (rows[0].get(k, "") if rows else "") for k in header_fields}

    # Merge saved header draft (user edits) over JSONL defaults for initial render
    # This prevents flash of empty fields while JS loads the draft
    header_draft = get_draft(pdf_id, "__header__", user)
    if header_draft and header_draft.get("fields"):
        draft_fields = header_draft["fields"]
        if isinstance(draft_fields, str):
            try:
                draft_fields = json.loads(draft_fields)
            except Exception:
                draft_fields = {}
        if isinstance(draft_fields, dict):
            for k, v in draft_fields.items():
                if k in header_fields and v:  # Only overwrite if draft has non-empty value
                    header[k] = v

    # Backfill 'Bill From' if missing using common source fields (vendor/billing name lines)
    if not (header.get("Bill From") or ""):
        r0 = rows[0] if rows else {}
        bf = (
            str(r0.get("Bill From", ""))
            or str(r0.get("Bill From Name", ""))
            or str(r0.get("Bill From Name First Line", ""))
            or str(r0.get("Vendor Name", ""))
            or str(r0.get("EnrichedVendorName", ""))
        )
        header["Bill From"] = bf
    # Build a simple vendor name -> id map from all rows for the day
    vend_map: Dict[str, str] = {}
    for r in all_rows:
        vn = str(r.get("EnrichedVendorName", "") or r.get("Vendor Name", "") or r.get("Vendor", "")).strip()
        vid = str(r.get("EnrichedVendorID", "")).strip()
        if vn:
            key = vn.upper()
            if key not in vend_map or not vend_map[key]:
                vend_map[key] = vid
    # Do not override EnrichedVendorName from Bill From in the UI. Let enrichment derive the canonical vendor.
    # We still backfill EnrichedVendorID when a vendor name is present.
    # If vendor ID is missing but we have a vendor name (from data or edits), try to backfill ID
    if (not str(header.get("EnrichedVendorID", "")).strip()) and str(header.get("EnrichedVendorName", "")).strip():
        vkey = str(header.get("EnrichedVendorName", "")).strip().upper()
        if vkey in vend_map and vend_map[vkey]:
            header["EnrichedVendorID"] = vend_map[vkey]
    header_account = header.get("Account Number", "")

    # We already filtered by the exact pdf_id; no further narrowing needed

    pdf_link_any = _infer_pdf_key_for_doc(y, m, d, rows, pdf_id)
    # build line models with sequential numbering independent of __row_idx__
    lines = []
    for i, r in enumerate(rows, start=1):
        key = r.get("__s3_key__"); idx = r.get("__row_idx__", 0)
        pid = pdf_id_from_key(key) if key else ""
        lid = line_id_from(key or "", idx)
        lines.append({
            "pdf_id": pid,
            "line_id": lid,
            "line_number": i,
            "orig_id": r.get("__id__"),
            "pdf_link": (r.get("PDF_LINK", "") or pdf_link_any),
            "original": {
                "EnrichedGLAccountNumber": r.get("EnrichedGLAccountNumber",""),
                "EnrichedGLAccountName": r.get("EnrichedGLAccountName",""),
                "GL DESC_NEW": r.get("GL DESC_NEW",""),
                "ENRICHED CONSUMPTION": r.get("ENRICHED CONSUMPTION",""),
                "ENRICHED UOM": r.get("ENRICHED UOM",""),
                "Meter Number": r.get("Meter Number",""),
                "Meter Size": r.get("Meter Size",""),
                "House Or Vacant": r.get("House Or Vacant",""),
                "Utility Type": r.get("Utility Type",""),
                "Line Item Description": r.get("Line Item Description",""),
                "Line Item Charge": r.get("Line Item Charge",""),
                "Consumption Amount": r.get("Consumption Amount",""),
                "Unit of Measure": r.get("Unit of Measure",""),
                "Previous Reading": r.get("Previous Reading",""),
                "Previous Reading Date": r.get("Previous Reading Date",""),
                "Current Reading": r.get("Current Reading",""),
                "Current Reading Date": r.get("Current Reading Date",""),
                "Rate": r.get("Rate",""),
            }
        })
    return templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "date": date,
            "invoice": str(rows[0].get("Invoice Number", "")) or "(unknown)",
            "account": header_account,
            "header": header,
            "lines": lines,
            "user": user,
        },
    )


# -------- APIs (JSON) --------
def _read_recent_exports(prefix: str, max_parts: int = 5) -> list[dict]:
    """Read up to N most-recent dt=YYYY-MM-DD/data.json.gz files under prefix."""
    _k = ("recent_exports", prefix, max_parts)
    now = time.time()
    ent = _CACHE.get(_k)
    if ent and (now - ent.get("ts", 0) < CACHE_TTL_SECONDS):
        return ent.get("data", [])
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    parts = []
    for obj in resp.get("Contents", []):
        k = obj["Key"]
        if "/dt=" in k and k.endswith("data.json.gz"):
            try:
                dt_str = k.split("/dt=")[-1].split("/")[0]
                parts.append((dt_str, k))
            except Exception:
                pass
    if not parts:
        return []
    parts.sort(key=lambda x: x[0], reverse=True)
    items: list[dict] = []
    for _, key in parts[:max_parts]:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        gz = obj["Body"].read()
        data = gzip.decompress(gz)
        text = data.decode("utf-8", errors="ignore")
        text_stripped = text.lstrip()
        if text_stripped.startswith("["):
            import json as _json
            try:
                arr = _json.loads(text)
                if isinstance(arr, list):
                    items.extend([x for x in arr if isinstance(x, dict)])
            except Exception:
                pass
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    import json as _json
                    rec = _json.loads(line)
                    if isinstance(rec, dict):
                        items.append(rec)
                except Exception:
                    continue
    _CACHE[_k] = {"ts": now, "data": items}
    return items

def _get_first_key(d: dict, candidates: list[str]) -> str:
    for k in candidates:
        if k in d:
            return k
    # try case-insensitive
    lower = {k.lower(): k for k in d.keys()}
    for k in candidates:
        if k.lower() in lower:
            return lower[k.lower()]
    return ""

# -------- URL/PDF helpers --------
def _infer_pdf_key_for_doc(y: str, m: str, d: str, rows: List[Dict[str, Any]], pdf_id: str) -> str:
    """Best-effort inference of original PDF S3 key for a document when rows lack PDF_LINK.
    Strategy:
      1) Use any non-empty PDF_LINK present in rows.
      2) Derive <timestamp>_<orig_base> from enriched key basename and search:
         - INPUT_PREFIX (flat)
         - REWORK day folder
         - Global REWORK (latest match)
         - Global INPUT (latest match)
         - Variant without trailing " (1)"
         - Any PDF in day REWORK that includes pdf_id
    Returns a key relative to the bucket, or empty string.
    """
    # 1) any direct S3 PDF link or explicit source key; ignore non-S3 short links
    for r in rows:
        val = str(r.get("PDF_LINK", "") or r.get("source_input_key", "") or "").strip()
        if val:
            lv = val.lower()
            if lv.startswith("s3://") or ".s3.amazonaws.com" in lv or ".s3." in lv:
                return val
    try:
        # 2) derive from enriched key basename
        ek = str(rows[0].get("__s3_key__", "")) if rows else ""
        tail = os.path.basename(ek)
        base_no_ext = tail.rsplit('.', 1)[0]
        parts = base_no_ext.split('_', 1)
        ts_part = parts[0] if parts else ""
        orig_base = parts[1] if len(parts) > 1 else ""
        # Try INPUT flat
        if ts_part and orig_base:
            cand1 = f"{INPUT_PREFIX}{ts_part}_{orig_base}.pdf"
            try:
                s3.head_object(Bucket=BUCKET, Key=cand1)
                return cand1
            except Exception:
                pass
        # If we have a timestamp part, search known prefixes for any PDF starting with that timestamp
        if ts_part:
            try:
                best = (None, None)
                for pfx in (INPUT_PREFIX, PARSED_INPUTS_PREFIX, REWORK_PREFIX):
                    pag = s3.get_paginator("list_objects_v2")
                    for page in pag.paginate(Bucket=BUCKET, Prefix=pfx):
                        for obj in page.get("Contents", []) or []:
                            k = obj.get("Key", ""); lm = obj.get("LastModified")
                            bn = os.path.basename(k)
                            if bn.lower().endswith('.pdf') and bn.startswith(f"{ts_part}_"):
                                # Prefer exact orig_base if present, else pick freshest
                                if orig_base and bn.lower().startswith(f"{ts_part}_{orig_base}".lower()):
                                    return k
                                if not best[1] or (lm and lm > best[1]):
                                    best = (k, lm)
                if best[0]:
                    return best[0]
            except Exception:
                pass
        # REWORK day
        if orig_base:
            day_prefix = f"{REWORK_PREFIX}yyyy={y}/mm={m}/dd={d}/"
            resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=day_prefix)
            for obj in resp.get("Contents", []) or []:
                k = obj.get("Key", "")
                if k.lower().endswith(("_" + orig_base + ".pdf").lower()):
                    return k
        # Global REWORK (exact filename match)
        if orig_base:
            best = (None, None)
            rp = s3.get_paginator("list_objects_v2")
            for page in rp.paginate(Bucket=BUCKET, Prefix=REWORK_PREFIX):
                for obj in page.get("Contents", []) or []:
                    k = obj.get("Key", ""); lm = obj.get("LastModified")
                    if k.lower().endswith(("_" + orig_base + ".pdf").lower()):
                        if not best[1] or (lm and lm > best[1]):
                            best = (k, lm)
            if best[0]:
                return best[0]
        # Global PARSED_INPUTS (exact filename match)
        if orig_base:
            best = (None, None)
            pi = s3.get_paginator("list_objects_v2")
            for page in pi.paginate(Bucket=BUCKET, Prefix=PARSED_INPUTS_PREFIX):
                for obj in page.get("Contents", []) or []:
                    k = obj.get("Key", ""); lm = obj.get("LastModified")
                    if k.lower().endswith(("_" + orig_base + ".pdf").lower()):
                        if not best[1] or (lm and lm > best[1]):
                            best = (k, lm)
            if best[0]:
                return best[0]
            # Fuzzy: any PARSED_INPUTS PDF containing orig_base in filename
            best = (None, None)
            for page in pi.paginate(Bucket=BUCKET, Prefix=PARSED_INPUTS_PREFIX):
                for obj in page.get("Contents", []) or []:
                    k = obj.get("Key", ""); lm = obj.get("LastModified")
                    base = os.path.basename(k).lower()
                    if base.endswith('.pdf') and orig_base.lower() in base:
                        if not best[1] or (lm and lm > best[1]):
                            best = (k, lm)
            if best[0]:
                return best[0]
            # Fuzzy: any REWORK PDF containing orig_base in filename
            best = (None, None)
            for page in rp.paginate(Bucket=BUCKET, Prefix=REWORK_PREFIX):
                for obj in page.get("Contents", []) or []:
                    k = obj.get("Key", ""); lm = obj.get("LastModified")
                    base = os.path.basename(k).lower()
                    if base.endswith('.pdf') and orig_base.lower() in base:
                        if not best[1] or (lm and lm > best[1]):
                            best = (k, lm)
            if best[0]:
                return best[0]
        # Global INPUT (exact filename match)
        if orig_base:
            best = (None, None)
            ip = s3.get_paginator("list_objects_v2")
            for page in ip.paginate(Bucket=BUCKET, Prefix=INPUT_PREFIX):
                for obj in page.get("Contents", []) or []:
                    k = obj.get("Key", ""); lm = obj.get("LastModified")
                    if k.lower().endswith(("_" + orig_base + ".pdf").lower()):
                        if not best[1] or (lm and lm > best[1]):
                            best = (k, lm)
            if best[0]:
                return best[0]
            # Fuzzy: any INPUT PDF containing orig_base in filename
            best = (None, None)
            for page in ip.paginate(Bucket=BUCKET, Prefix=INPUT_PREFIX):
                for obj in page.get("Contents", []) or []:
                    k = obj.get("Key", ""); lm = obj.get("LastModified")
                    base = os.path.basename(k).lower()
                    if base.endswith('.pdf') and orig_base.lower() in base:
                        if not best[1] or (lm and lm > best[1]):
                            best = (k, lm)
            if best[0]:
                return best[0]
        # Variant without (1)
        if orig_base and orig_base.endswith(" (1)"):
            ob = orig_base[:-4]
            best = (None, None)
            rp = s3.get_paginator("list_objects_v2")
            for page in rp.paginate(Bucket=BUCKET, Prefix=REWORK_PREFIX):
                for obj in page.get("Contents", []) or []:
                    k = obj.get("Key", ""); lm = obj.get("LastModified")
                    if k.lower().endswith(("_" + ob + ".pdf").lower()):
                        if not best[1] or (lm and lm > best[1]):
                            best = (k, lm)
            if best[0]:
                return best[0]
            best = (None, None)
            ip = s3.get_paginator("list_objects_v2")
            for page in ip.paginate(Bucket=BUCKET, Prefix=INPUT_PREFIX):
                for obj in page.get("Contents", []) or []:
                    k = obj.get("Key", ""); lm = obj.get("LastModified")
                    if k.lower().endswith(("_" + ob + ".pdf").lower()):
                        if not best[1] or (lm and lm > best[1]):
                            best = (k, lm)
            if best[0]:
                return best[0]
        # Day REWORK any pdf containing pdf_id
        day_prefix = f"{REWORK_PREFIX}yyyy={y}/mm={m}/dd={d}/"
        resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=day_prefix)
        pdf_keys = []
        for obj in resp.get("Contents", []) or []:
            k = obj.get("Key", "")
            kl = k.lower()
            if kl.endswith('.pdf') and (pdf_id.lower() in kl or True):
                pdf_keys.append(k)
        # Prefer pdfs containing pdf_id, else if exactly one PDF that day, use it
        for k in pdf_keys:
            if pdf_id.lower() in k.lower():
                return k
        if len(pdf_keys) == 1:
            return pdf_keys[0]
        # Global scan by pdf_id across REWORK/INPUT/PARSED_INPUTS as a last resort
        try:
            candidates = []
            for pfx in (REWORK_PREFIX, INPUT_PREFIX, PARSED_INPUTS_PREFIX):
                pag = s3.get_paginator("list_objects_v2")
                for page in pag.paginate(Bucket=BUCKET, Prefix=pfx):
                    for obj in page.get("Contents", []) or []:
                        k = obj.get("Key", ""); base = os.path.basename(k).lower()
                        if base.endswith('.pdf') and pdf_id.lower() in base:
                            candidates.append((k, obj.get("LastModified")))
            if candidates:
                candidates.sort(key=lambda t: (t[1] or 0), reverse=True)
                return candidates[0][0]
        except Exception:
            pass
    except Exception:
        pass
    return ""
def _resolve_final_url(u: str) -> str:
    """Follow redirects to resolve any short URL to a final URL.
    Falls back to the input on failure. Uses HEAD first, then GET.
    """
    if not u:
        return ""
    try:
        try:
            r = requests.head(u, allow_redirects=True, timeout=6)
            if r.url:
                return r.url
        except Exception:
            pass
        r2 = requests.get(u, allow_redirects=True, timeout=8, stream=True)
        return r2.url or u
    except Exception:
        return u

def _parse_s3_from_url(u: str):
    """Parse various S3 URL styles to (bucket, key).
    Supports:
      - s3://bucket/key
      - https://s3.amazonaws.com/bucket/key
      - https://s3.<region>.amazonaws.com/bucket/key
      - https://bucket.s3.amazonaws.com/key
      - https://bucket.s3.<region>.amazonaws.com/key
      - https://bucket.s3-<region>.amazonaws.com/key
    Returns tuple or None if unrecognized.
    """
    if not u:
        return None
    try:
        if u.startswith("s3://"):
            rest = u[5:]
            i = rest.find('/')
            if i > 0:
                return rest[:i], unquote(rest[i+1:])
            return None
        p = urlparse(u)
        host = p.netloc or ""
        path = (p.path or "").lstrip('/')
        if not host:
            return None
        # virtual-hosted-style
        # bucket.s3.amazonaws.com or bucket.s3.<region>.amazonaws.com or bucket.s3-<region>.amazonaws.com
        if host.endswith("amazonaws.com"):
            parts = host.split('.')
            if parts and parts[0] and not parts[0].startswith('s3'):
                # parts[0] is bucket
                bucket = parts[0]
                return bucket, unquote(path)
            # path-style: s3.amazonaws.com/bucket/key or s3.<region>.amazonaws.com/bucket/key
            segs = path.split('/', 1)
            if len(segs) == 2 and segs[0] and segs[1]:
                return segs[0], unquote(segs[1])
        # explicit virtual-hosted fallback: <bucket>.s3.amazonaws.com/<key>
        if ".s3.amazonaws.com" in host and path:
            try:
                bucket = host.split('.s3.amazonaws.com', 1)[0]
                if bucket:
                    return bucket, unquote(path)
            except Exception:
                pass
        # AWS Console object URL patterns
        # Examples:
        # https://s3.console.aws.amazon.com/s3/object/<bucket>?region=us-east-1&prefix=<key>
        # https://s3.console.aws.amazon.com/s3/buckets/<bucket>/object?prefix=<key>
        if host.startswith('s3.console.aws.amazon.com'):
            q = parse_qs(p.query or '')
            pref = (q.get('prefix') or q.get('key') or [''])[0]
            # try extract bucket from path segs
            segs = [s for s in path.split('/') if s]
            bucket = ''
            if 'object' in segs:
                try:
                    i = segs.index('object')
                    if i+1 < len(segs):
                        bucket = segs[i+1]
                except ValueError:
                    pass
            if not bucket and 'buckets' in segs:
                try:
                    i = segs.index('buckets')
                    if i+1 < len(segs):
                        bucket = segs[i+1]
                except ValueError:
                    pass
            if bucket and pref:
                return bucket, unquote(pref.lstrip('/'))
        # Generic query with bucket/key
        q = parse_qs(p.query or '')
        qb = (q.get('bucket') or q.get('Bucket') or [''])[0]
        qk = (q.get('key') or q.get('Key') or [''])[0]
        if qb and qk:
            return qb, unquote(qk.lstrip('/'))
        return None
    except Exception:
        return None

def _maybe_expand_short(u: str) -> str:
    """If URL is one of our legacy short links (Lambda Function URL), expand via DDB.
    Expected shape: https://<hash>.lambda-url.<region>.on.aws/<code>
    Returns the expanded original URL if found; otherwise returns input.
    """
    try:
        if not u:
            return u
        p = urlparse(u)
        host = p.netloc or ""
        path = (p.path or "/").strip("/")
        # Lambda Function URL host typically contains '.lambda-url.' and '.on.aws'
        if ".lambda-url." in host and host.endswith(".on.aws") and path and "/" not in path:
            code = path
            try:
                resp = ddb.get_item(TableName=SHORT_TABLE, Key={"code": {"S": code}})
                item = resp.get("Item")
                if item and "url" in item:
                    url = item["url"].get("S") or item["url"].get("s")
                    if url:
                        return url
            except Exception:
                return u
        return u
    except Exception:
        return u

@app.get("/api/catalogs")
def api_catalogs(user: str = Depends(require_user), response: Response = None):
    """Return full catalogs from S3 exports for properties, vendors, and GL accounts.
    Sources:
      - s3://{BUCKET}/{EXPORTS_ROOT}dim_property/dt=YYYY-MM-DD/data.json.gz
      - s3://{BUCKET}/{EXPORTS_ROOT}dim_vendor/dt=YYYY-MM-DD/data.json.gz
      - s3://{BUCKET}/{EXPORTS_ROOT}dim_gl_account/dt=YYYY-MM-DD/data.json.gz

    PERF: Caches processed result for 5 minutes to avoid repeated S3 reads and processing.
    """
    # Check endpoint-level cache first (processed result)
    cache_key = "api_catalogs_processed"
    now = time.time()
    ent = _CACHE.get(cache_key)
    if ent and (now - ent.get("ts", 0) < CACHE_TTL_SECONDS):
        try:
            if response is not None:
                response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
        except Exception:
            pass
        return ent.get("data", {})

    try:
        # PERF: Only load 1 most recent file per catalog (was 5)
        prop_items = _read_recent_exports(f"{EXPORTS_ROOT}dim_property/", max_parts=1)
        gl_items = _read_recent_exports(f"{EXPORTS_ROOT}dim_gl_account/", max_parts=1)

        props = []
        vends = []
        gls = []

        for r in prop_items:
            k_name = _get_first_key(r, ["PropertyName", "property_name", "Name", "name"])
            k_id = _get_first_key(r, ["PropertyID", "property_id", "Id", "id"])
            name = str(r.get(k_name, "")).strip()
            pid = str(r.get(k_id, "")).strip()
            if name:
                props.append({"name": name, "id": pid})

        # Load vendors from new vendor cache (has vendorCode) with fallback to dim_vendor
        try:
            print("[api_catalogs] Loading vendors from api-vendor/vendors/latest.json...")
            vend_cache_obj = s3.get_object(Bucket="api-vendor", Key="vendors/latest.json")
            vend_cache_data = json.loads(vend_cache_obj["Body"].read().decode("utf-8"))
            vendor_list = vend_cache_data.get("vendors", [])
            print(f"[api_catalogs] Loaded {len(vendor_list)} vendors from cache")
            codes_count = 0
            for v in vendor_list:
                # Use displayName for the dropdown (includes location and code)
                # Fall back to plain name if displayName not available
                display = str(v.get("displayName") or v.get("name", "")).strip()
                vid = str(v.get("vendorId", "")).strip()
                vcode = str(v.get("vendorCode", "")).strip()
                loc_id = str(v.get("locationId", "")).strip()
                if vcode:
                    codes_count += 1
                if display:
                    vends.append({"name": display, "id": vid, "code": vcode, "locationId": loc_id})
            print(f"[api_catalogs] Vendors with code: {codes_count}/{len(vendor_list)}")
        except Exception as e:
            print(f"[api_catalogs] ERROR loading vendor cache: {e}, falling back to dim_vendor")
            vend_items = _read_recent_exports(f"{EXPORTS_ROOT}dim_vendor/", max_parts=1)
            for r in vend_items:
                k_name = _get_first_key(r, ["VendorName", "vendor_name", "Name", "name"])
                k_id = _get_first_key(r, ["VendorID", "vendor_id", "Id", "id"])
                name = str(r.get(k_name, "")).strip()
                vid = str(r.get(k_id, "")).strip()
                if name:
                    vends.append({"name": name, "id": vid, "code": ""})

        for r in gl_items:
            # Fuzzy detection for GL keys if straightforward keys missing
            # Prefer formatted account number if available
            k_num = _get_first_key(r, [
                "FORMATTED_ACCOUNT_NUMBER",
                "FormattedAccountNumber",
                "formatted_account_number",
                "Formatted Account Number",
                "GLAccountNumber",
                "gl_account_number",
                "ACCOUNT_NUMBER",
                "Number",
                "number",
            ])
            k_nm = _get_first_key(r, ["NAME", "GLAccountName", "gl_account_name", "Name", "name"]) 
            if not k_num:
                for k in r.keys():
                    kl = k.lower()
                    if ("formatted" in kl and "account" in kl and ("num" in kl or "number" in kl)) or (("gl" in kl or "g/l" in kl or "general ledger" in kl) and ("acct" in kl or "account" in kl) and ("num" in kl or "number" in kl or "code" in kl)):
                        k_num = k; break
            if not k_nm:
                for k in r.keys():
                    kl = k.lower()
                    if ("gl" in kl or "general ledger" in kl) and ("name" in kl or "descr" in kl or "description" in kl):
                        k_nm = k; break
            num = str(r.get(k_num, "")).strip() if k_num else ""
            nm = str(r.get(k_nm, "")).strip() if k_nm else ""
            if num:
                gls.append({"number": num, "name": nm})

        # unique & sorted
        def _uniq(items, key):
            seen = set(); out = []
            for it in items:
                k = it.get(key, "")
                if k not in seen:
                    out.append(it); seen.add(k)
            return out

        props = sorted(_uniq(props, "name"), key=lambda x: x["name"].upper())
        # Dedupe vendors by ID (not name) - multiple vendors can have same name (e.g. City of Tacoma)
        vends = sorted(_uniq(vends, "id"), key=lambda x: x["name"].upper())
        gls = sorted(_uniq(gls, "number"), key=lambda x: x["name"].upper()) 

        # utilities: return Title Case consistently
        _base_utils = [
            "WATER","SEWER","TRASH","ELECTRICITY","GAS","STORMWATER","RECYCLE","RECYCLING","INTERNET","CABLE","TELECOM","WASTEWATER","HOA"
        ]
        utils = sorted({u.title() for u in _base_utils})
        try:
            if response is not None:
                response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
        except Exception:
            pass
        result = {"properties": props, "vendors": vends, "gl_accounts": gls, "utilities": utils}
        # Cache the processed result
        _CACHE[cache_key] = {"ts": time.time(), "data": result}
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
@app.get("/api/options")
def api_options(date: str, user: str = Depends(require_user), response: Response = None):
    """Return dropdown options for properties, vendors, GL accounts, and utilities for a given day.
    Positioned after app initialization so the route registers properly.
    """
    try:
        y, m, d = date.split('-')
    except ValueError:
        return JSONResponse({"error": "bad date"}, status_code=400)
    rows = load_day(y, m, d)
    prop = {}
    vend = {}
    gl = {}
    # Start with a Title Case base set
    utils = set(u.title() for u in [
        "WATER","SEWER","TRASH","ELECTRICITY","GAS","STORMWATER","RECYCLE","RECYCLING","INTERNET","CABLE","TELECOM","WASTEWATER","HOA"
    ])
    for r in rows:
        pn = str(r.get("EnrichedPropertyName", "") or r.get("Property Name", "")).strip()
        pid = str(r.get("EnrichedPropertyID", "")).strip()
        if pn:
            if pn not in prop:
                prop[pn] = pid
            elif not prop[pn] and pid:
                prop[pn] = pid

        vn = str(r.get("EnrichedVendorName", "") or r.get("Vendor Name", "") or r.get("Vendor", "")).strip()
        vid = str(r.get("EnrichedVendorID", "")).strip()
        if vn:
            if vn not in vend:
                vend[vn] = vid
            elif not vend[vn] and vid:
                vend[vn] = vid

        gnum = str(r.get("EnrichedGLAccountNumber", "")).strip()
        gname = str(r.get("EnrichedGLAccountName", "")).strip()
        gid = str(r.get("EnrichedGLAccountID", "")).strip()
        if gnum:
            if gnum not in gl:
                gl[gnum] = {"name": gname, "id": gid}
            elif not gl[gnum].get("name") and gname:
                gl[gnum]["name"] = gname
            elif not gl[gnum].get("id") and gid:
                gl[gnum]["id"] = gid
        ut = str(r.get("Utility Type", "")).strip()
        if ut:
            utils.add(ut.title())
    props = [{"name": k, "id": v} for k, v in sorted(prop.items())]
    vends = [{"name": k, "id": v} for k, v in sorted(vend.items())]
    gls = [{"number": k, "name": v.get("name", ""), "id": v.get("id", "")} for k, v in sorted(gl.items())]
    try:
        if response is not None:
            response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
    except Exception:
        pass
    return {"properties": props, "vendors": vends, "gl_accounts": gls, "utilities": sorted(utils)}
@app.get("/api/dates")
def api_dates(user: str = Depends(require_user)):
    return {"dates": list_dates()}


@app.get("/api/day")
def api_day(date: str, user: str = Depends(require_user), response: Response = None):
    y, m, d = date.split("-")
    rows = load_day(y, m, d)
    try:
        if response is not None:
            response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
    except Exception:
        pass
    return {"rows": rows}


@app.get("/api/invoices")
def api_invoices(date: str, user: str = Depends(require_user), response: Response = None):
    y, m, d = date.split("-")
    rows = load_day(y, m, d)
    # Build id list to fetch statuses so we can exclude Deleted lines from counts
    id_list: List[str] = [str(r.get("__id__")) for r in rows if r.get("__id__")]
    stmap = get_status_map(id_list)
    # Cache per-pdf header overrides for vendor/account, prefer shared '__final__', then current user
    header_by_pdf: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = r.get("__s3_key__", "")
        if not key: continue
        pid = pdf_id_from_key(key)
        if pid and pid not in header_by_pdf:
            d = get_draft(pid, "__header__", "__final__") or get_draft(pid, "__header__", user)
            header_by_pdf[pid] = (d or {}).get("fields", {})
    inv: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if stmap.get(str(r.get("__id__")), {}).get("status") == "Deleted":
            continue
        inv_no = str(r.get("Invoice Number", "")) or "(unknown)"
        key = r.get("__s3_key__", "")
        pid = pdf_id_from_key(key) if key else ""
        hdr = header_by_pdf.get(pid, {})
        # If header changed account/vendor, reflect in list display (grouping stays by invoice number here)
        g = inv.setdefault(inv_no, {"invoice": inv_no, "count": 0, "status": "REVIEW"})
        g["count"] += 1
    try:
        if response is not None:
            response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
    except Exception:
        pass
    return {"invoices": sorted(inv.values(), key=lambda x: x["invoice"]) }


@app.get("/api/invoices_status")
def api_invoices_status(date: str, user: str = Depends(require_user), response: Response = None):
    """Return status per (vendor, account, pdf_id) group for the given date, matching the logic used by /invoices."""
    y, m, d = date.split("-")
    rows = load_day(y, m, d)
    # Preload header overrides per pdf, prefer shared '__final__', then current user
    header_by_pdf: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = r.get("__s3_key__", "")
        if not key: continue
        pid = pdf_id_from_key(key)
        if pid and pid not in header_by_pdf:
            d = get_draft(pid, "__header__", "__final__") or get_draft(pid, "__header__", user)
            header_by_pdf[pid] = (d or {}).get("fields", {})

    inv: Dict[tuple, Dict[str, Any]] = {}
    group_ids: Dict[tuple, List[str]] = {}
    for r in rows:
        pdf_id = pdf_id_from_key(r.get("__s3_key__", "")) if r.get("__s3_key__") else "(unknown)"
        hdr = header_by_pdf.get(pdf_id, {})
        # Use row's Account Number directly (NOT header override) to preserve multi-account grouping
        # Use Line Item Account Number as fallback when Account Number is blank (for subtotals/taxes)
        account_no = str(r.get("Account Number", "") or r.get("Line Item Account Number", "") or "") or "(unknown)"
        invoice_no = str(r.get("Invoice Number", "")) or "(unknown)"
        vendor = (
            str(hdr.get("EnrichedVendorName", ""))
            or str(r.get("EnrichedVendorName", ""))
            or str(r.get("Vendor Name", ""))
            or str(r.get("Vendor", ""))
            or str(r.get("Utility Type", ""))
            or "(unknown)"
        )
        property_name = (
            str(r.get("EnrichedPropertyName", ""))
            or str(r.get("Property Name", ""))
            or str(r.get("PropertyName", ""))
            or ""
        )
        key = (vendor, account_no, pdf_id)
        g = inv.setdefault(key, {
            "vendor": vendor,
            "account": account_no,
            "pdf_id": pdf_id,
            "invoice": invoice_no,
            "count": 0,
            "status": "REVIEW",
            "property": property_name
        })
        # Update property if not set (first row may not have it)
        if not g.get("property") and property_name:
            g["property"] = property_name
        rid = str(r.get("__id__"))
        group_ids.setdefault(key, []).append(rid)
        # We will compute count after we know which ids are Deleted

    all_ids: List[str] = [i for ids in group_ids.values() for i in ids if i]
    stmap = get_status_map(all_ids)

    def has_account_artifact(acct_val: str) -> bool:
        # Consider both Pre-Entrata and Post-Entrata artifacts for completion
        acct = (acct_val or "").strip()
        if not acct:
            return False
        token_dash = "".join(ch if (ch.isalnum() or ch in "-") else "-" for ch in acct)
        token_us = "".join(ch if ch.isalnum() else "_" for ch in acct)
        prefixes = [
            f"{PRE_ENTRATA_PREFIX}yyyy={y}/mm={m}/dd={d}/",
            f"{POST_ENTRATA_PREFIX}yyyy={y}/mm={m}/dd={d}/",
        ]
        for pfx in prefixes:
            try:
                resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=pfx)
                for obj in resp.get("Contents", []) or []:
                    k = obj.get("Key", "")
                    if token_dash in k or token_us in k:
                        return True
            except Exception:
                continue
        return False

    for key, meta in inv.items():
        _, acct, _ = key
        ids_all = group_ids.get(key, [])
        # Exclude Deleted from counts and completion criteria
        ids_active = [i for i in ids_all if stmap.get(i, {}).get("status") != "Deleted"]
        meta["count"] = len(ids_active)
        submitted = sum(1 for i in ids_active if stmap.get(i, {}).get("status") == "Submitted")
        if submitted == 0:
            meta["status"] = "REVIEW"
        elif submitted == len(ids_active):
            meta["status"] = "COMPLETE"
        else:
            meta["status"] = "PARTIAL"

    out = [
        {
            "vendor": v["vendor"],
            "account": v["account"],
            "pdf_id": v["pdf_id"],
            "invoice": v["invoice"],
            "count": v["count"],
            "status": v["status"],
            "property": v.get("property", ""),
        }
        for v in inv.values()
    ]
    try:
        if response is not None:
            response.headers["Cache-Control"] = f"private, max-age={CACHE_TTL_SECONDS}"
    except Exception:
        pass
    return {"invoices": out}


@app.get("/api/drafts")
def api_get_draft(pdf_id: str, line_id: str, user: str = Depends(require_user)):
    # Choose the freshest between user and '__final__' by updated_utc; avoid stale user autosaves overriding submitted values
    final_d = get_draft(pdf_id, line_id, "__final__")
    user_d = get_draft(pdf_id, line_id, user)
    def ts(d):
        try:
            return dt.datetime.fromisoformat(d.get("updated_utc")) if d and d.get("updated_utc") else dt.datetime.min
        except Exception:
            return dt.datetime.min
    out = None
    if user_d and final_d:
        out = user_d if ts(user_d) >= ts(final_d) else final_d
    else:
        out = user_d or final_d or {"fields": {}}
    return {"draft": out or {"fields": {}} }


@app.get("/api/drafts/new-lines")
def api_get_new_line_drafts(pdf_id: str, user: str = Depends(require_user)):
    """Get all new line drafts for a pdf_id (lines added by user that don't exist in original S3 data).

    Scans drafts table for entries where line_id starts with 'new-' for this pdf_id.
    Returns list of {line_id, fields} for each new line found.
    """
    if not pdf_id:
        return {"new_lines": []}

    # Query both user's drafts and __final__ drafts for new lines
    # Use scan with filter since we can't query by partial pk
    new_lines = []
    seen_line_ids = set()

    for check_user in [user, "__final__"]:
        prefix = f"draft#{pdf_id}#new-"
        try:
            # Scan is expensive but new lines are rare and this is a targeted prefix
            paginator = ddb.get_paginator('scan')
            for page in paginator.paginate(
                TableName=DRAFTS_TABLE,
                FilterExpression="begins_with(pk, :prefix)",
                ExpressionAttributeValues={":prefix": {"S": f"draft#{pdf_id}#new-"}},
            ):
                for item in page.get("Items", []):
                    pk = item.get("pk", {}).get("S", "")
                    # Extract line_id from pk: draft#{pdf_id}#{line_id}#{user}
                    parts = pk.split("#")
                    if len(parts) >= 4:
                        line_id = parts[2]
                        item_user = parts[3]
                        if line_id.startswith("new-") and line_id not in seen_line_ids:
                            # Parse fields
                            fields_raw = item.get("fields", {}).get("S", "{}")
                            try:
                                fields = json.loads(fields_raw) if isinstance(fields_raw, str) else fields_raw
                            except:
                                fields = {}
                            # Check if deleted
                            if fields.get("__deleted__"):
                                continue
                            new_lines.append({
                                "line_id": line_id,
                                "fields": fields
                            })
                            seen_line_ids.add(line_id)
        except Exception as e:
            print(f"[NEW LINES] Error scanning for new lines: {e}")

    return {"new_lines": new_lines}


@app.post("/api/drafts/batch")
def api_get_drafts_batch(payload: Dict[str, Any] = Body(...), user: str = Depends(require_user)):
    """Batch load multiple drafts in a single request - much faster than individual calls.

    Request body: {"items": [{"pdf_id": "...", "line_id": "..."}, ...]}
    Response: {"drafts": {"pdf_id#line_id": {...draft...}, ...}}
    """
    items = payload.get("items", [])
    if not items:
        return {"drafts": {}}

    # Build list of all keys to fetch (both user and __final__ for each item)
    keys_to_fetch = []
    for item in items:
        pdf_id = item.get("pdf_id")
        line_id = item.get("line_id")
        if pdf_id and line_id:
            # Fetch both user draft and final draft to compare
            keys_to_fetch.append({"pk": {"S": f"draft#{pdf_id}#{line_id}#{user}"}})
            keys_to_fetch.append({"pk": {"S": f"draft#{pdf_id}#{line_id}#__final__"}})

    if not keys_to_fetch:
        return {"drafts": {}}

    # DynamoDB BatchGetItem has a limit of 100 keys per request
    all_items = []
    for i in range(0, len(keys_to_fetch), 100):
        batch_keys = keys_to_fetch[i:i+100]
        try:
            resp = ddb.batch_get_item(
                RequestItems={
                    DRAFTS_TABLE: {"Keys": batch_keys}
                }
            )
            all_items.extend(resp.get("Responses", {}).get(DRAFTS_TABLE, []))
            # Handle unprocessed keys (retry once)
            unprocessed = resp.get("UnprocessedKeys", {}).get(DRAFTS_TABLE, {}).get("Keys", [])
            if unprocessed:
                retry_resp = ddb.batch_get_item(RequestItems={DRAFTS_TABLE: {"Keys": unprocessed}})
                all_items.extend(retry_resp.get("Responses", {}).get(DRAFTS_TABLE, []))
        except Exception as e:
            print(f"[BATCH DRAFTS] Error fetching batch: {e}")

    # Parse DynamoDB items into a lookup dict
    drafts_by_pk = {}
    for item in all_items:
        pk = item.get("pk", {}).get("S", "")
        parsed = {k: list(v.values())[0] for k, v in item.items()}
        if "fields" in parsed:
            try:
                parsed["fields"] = json.loads(parsed["fields"]) if isinstance(parsed["fields"], str) else parsed["fields"]
            except Exception:
                parsed["fields"] = {}
        drafts_by_pk[pk] = parsed

    # Helper to get timestamp for comparison
    def ts(d):
        try:
            return dt.datetime.fromisoformat(d.get("updated_utc")) if d and d.get("updated_utc") else dt.datetime.min
        except Exception:
            return dt.datetime.min

    # Build response: pick freshest between user and final for each item
    result = {}
    for item in items:
        pdf_id = item.get("pdf_id")
        line_id = item.get("line_id")
        if not pdf_id or not line_id:
            continue

        user_pk = f"draft#{pdf_id}#{line_id}#{user}"
        final_pk = f"draft#{pdf_id}#{line_id}#__final__"
        user_d = drafts_by_pk.get(user_pk)
        final_d = drafts_by_pk.get(final_pk)

        # Pick the freshest
        if user_d and final_d:
            out = user_d if ts(user_d) >= ts(final_d) else final_d
        else:
            out = user_d or final_d or {"fields": {}}

        key = f"{pdf_id}#{line_id}"
        result[key] = out or {"fields": {}}

    return {"drafts": result}


@app.put("/api/drafts")
def api_put_draft(payload: Dict[str, Any] = Body(...), user: str = Depends(require_user)):
    pdf_id = payload.get("pdf_id"); line_id = payload.get("line_id"); fields = payload.get("fields", {})
    date = payload.get("date", ""); invoice = str(payload.get("invoice", ""))
    if not pdf_id or not line_id:
        return JSONResponse({"error":"missing pdf_id/line_id"}, status_code=400)
    put_draft(pdf_id, line_id, user, fields, date, invoice)
    return {"ok": True}


# -------- Invoice Timing Tracker APIs --------
def _get_timing(invoice_id: str, user: str) -> dict:
    """Get timing record for an invoice/user combination."""
    pk = f"timing#{invoice_id}#{user}"
    try:
        resp = ddb.get_item(TableName=DRAFTS_TABLE, Key={"pk": {"S": pk}})
        item = resp.get("Item")
        if not item:
            return {"total_seconds": 0, "sessions": []}
        return {
            "total_seconds": int(item.get("total_seconds", {}).get("N", 0)),
            "sessions": json.loads(item.get("sessions", {}).get("S", "[]")),
            "last_heartbeat": item.get("last_heartbeat", {}).get("S", ""),
            "current_session_start": item.get("current_session_start", {}).get("S", ""),
        }
    except Exception as e:
        print(f"[TIMING] Error getting timing: {e}")
        return {"total_seconds": 0, "sessions": []}


def _put_timing(invoice_id: str, user: str, timing_data: dict):
    """Save timing record for an invoice/user combination."""
    pk = f"timing#{invoice_id}#{user}"
    try:
        item = {
            "pk": {"S": pk},
            "invoice_id": {"S": invoice_id},
            "user": {"S": user},
            "total_seconds": {"N": str(int(timing_data.get("total_seconds", 0)))},
            "sessions": {"S": json.dumps(timing_data.get("sessions", []))},
            "updated_utc": {"S": dt.datetime.utcnow().isoformat()},
        }
        if timing_data.get("last_heartbeat"):
            item["last_heartbeat"] = {"S": timing_data["last_heartbeat"]}
        if timing_data.get("current_session_start"):
            item["current_session_start"] = {"S": timing_data["current_session_start"]}
        ddb.put_item(TableName=DRAFTS_TABLE, Item=item)
        return True
    except Exception as e:
        print(f"[TIMING] Error saving timing: {e}")
        return False


@app.get("/api/timing/{invoice_id}")
def api_get_timing(invoice_id: str, user: str = Depends(require_user)):
    """Get timing data for an invoice."""
    timing = _get_timing(invoice_id, user)
    return {
        "invoice_id": invoice_id,
        "user": user,
        "total_seconds": timing.get("total_seconds", 0),
        "total_minutes": round(timing.get("total_seconds", 0) / 60, 1),
        "sessions": timing.get("sessions", []),
    }


@app.post("/api/timing/{invoice_id}/start")
def api_start_timing(invoice_id: str, user: str = Depends(require_user)):
    """Start a timing session for an invoice."""
    timing = _get_timing(invoice_id, user)
    now = dt.datetime.utcnow().isoformat()
    timing["current_session_start"] = now
    timing["last_heartbeat"] = now
    _put_timing(invoice_id, user, timing)
    return {"ok": True, "started_at": now}


@app.post("/api/timing/{invoice_id}/heartbeat")
def api_timing_heartbeat(invoice_id: str, user: str = Depends(require_user)):
    """Update heartbeat - called periodically to track active time."""
    timing = _get_timing(invoice_id, user)
    now = dt.datetime.utcnow()
    now_str = now.isoformat()

    # If no active session, start one
    if not timing.get("current_session_start"):
        timing["current_session_start"] = now_str
        timing["last_heartbeat"] = now_str
        _put_timing(invoice_id, user, timing)
        return {"ok": True, "action": "started_new_session"}

    # Check if session is stale (no heartbeat for > 2 minutes = session ended)
    last_hb = timing.get("last_heartbeat", "")
    if last_hb:
        try:
            last_hb_dt = dt.datetime.fromisoformat(last_hb.replace("Z", "+00:00").replace("+00:00", ""))
            diff = (now - last_hb_dt).total_seconds()
            if diff > 120:  # Session was stale, close it and start new
                # Add elapsed time from stale session (use last heartbeat as end)
                session_start = timing.get("current_session_start", "")
                if session_start:
                    start_dt = dt.datetime.fromisoformat(session_start.replace("Z", "+00:00").replace("+00:00", ""))
                    session_seconds = (last_hb_dt - start_dt).total_seconds()
                    if session_seconds > 0:
                        timing["total_seconds"] = timing.get("total_seconds", 0) + int(session_seconds)
                        timing.setdefault("sessions", []).append({
                            "start": session_start,
                            "end": last_hb,
                            "seconds": int(session_seconds),
                        })
                # Start new session
                timing["current_session_start"] = now_str
        except Exception:
            pass

    timing["last_heartbeat"] = now_str
    _put_timing(invoice_id, user, timing)
    return {"ok": True, "total_seconds": timing.get("total_seconds", 0)}


@app.post("/api/timing/{invoice_id}/stop")
def api_stop_timing(invoice_id: str, user: str = Depends(require_user)):
    """Stop timing session for an invoice."""
    timing = _get_timing(invoice_id, user)
    now = dt.datetime.utcnow()
    now_str = now.isoformat()

    session_start = timing.get("current_session_start", "")
    session_seconds = 0

    if session_start:
        try:
            start_dt = dt.datetime.fromisoformat(session_start.replace("Z", "+00:00").replace("+00:00", ""))
            session_seconds = int((now - start_dt).total_seconds())
            if session_seconds > 0:
                timing["total_seconds"] = timing.get("total_seconds", 0) + session_seconds
                timing.setdefault("sessions", []).append({
                    "start": session_start,
                    "end": now_str,
                    "seconds": session_seconds,
                })
        except Exception:
            pass

    timing["current_session_start"] = ""
    timing["last_heartbeat"] = ""
    _put_timing(invoice_id, user, timing)

    return {
        "ok": True,
        "session_seconds": session_seconds,
        "total_seconds": timing.get("total_seconds", 0),
        "total_minutes": round(timing.get("total_seconds", 0) / 60, 1),
    }


@app.get("/api/timing/summary")
def api_get_timing_summary(date: str = "", user: str = Depends(require_user)):
    """Get timing summary for all invoices worked on by user (optionally filtered by date)."""
    try:
        # Scan for all timing records for this user
        response = ddb.scan(
            TableName=DRAFTS_TABLE,
            FilterExpression="begins_with(pk, :prefix) AND #u = :user",
            ExpressionAttributeNames={"#u": "user"},
            ExpressionAttributeValues={
                ":prefix": {"S": "timing#"},
                ":user": {"S": user},
            },
        )
        items = []
        for item in response.get("Items", []):
            invoice_id = item.get("invoice_id", {}).get("S", "")
            total_secs = int(item.get("total_seconds", {}).get("N", 0))
            updated = item.get("updated_utc", {}).get("S", "")
            # Filter by date if provided
            if date and updated and not updated.startswith(date):
                continue
            items.append({
                "invoice_id": invoice_id,
                "total_seconds": total_secs,
                "total_minutes": round(total_secs / 60, 1),
                "updated": updated,
            })
        items.sort(key=lambda x: x.get("updated", ""), reverse=True)
        total_all = sum(i.get("total_seconds", 0) for i in items)
        return {
            "user": user,
            "date_filter": date,
            "invoices": items,
            "invoice_count": len(items),
            "total_seconds": total_all,
            "total_minutes": round(total_all / 60, 1),
            "total_hours": round(total_all / 3600, 2),
        }
    except Exception as e:
        print(f"[TIMING] Error getting summary: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/overrides")
def api_overrides(date: str = Form(...), payload: str = Form(...), user: str = Depends(require_user)):
    # payload is JSON array of override items with id/source_s3_key/row_index/changes
    try:
        items = json.loads(payload)
    except Exception as e:
        return JSONResponse({"error": f"invalid payload: {e}"}, status_code=400)
    y, m, d = date.split("-")
    out_key = write_overrides(y, m, d, items)
    if not out_key:
        return JSONResponse({"error": "no overrides"}, status_code=400)
    return {"ok": True, "s3_key": out_key}


@app.post("/api/status")
def api_status(id: str = Form(...), status: str = Form(...), user: str = Form("reviewer")):
    put_status(id, status, user)
    return {"ok": True}


@app.post("/api/submit")
def api_submit(date: str = Form(...), ids: str = Form(...), extras: str = Form(""), deleted_ids: str = Form(""), user: str = Depends(require_user)):
    """Finalize an invoice: create override delta and merged outputs, update status, and optionally notify via SQS.

    Behavior:
    - Load originals for provided ids (all from the same invoice).
    - Load header draft (line_id='__header__') and each line draft.
    - Build per-line delta (only changed fields) and merged records (original + applied overrides).
    - Write two files to S3 under OVERRIDE_PREFIX: overrides_delta_*.jsonl and overrides_merged_*.jsonl.
    - Update DDB review status and optionally send SQS messages.
    """
    # parse date
    try:
        y, m, d = date.split('-')
    except ValueError:
        return JSONResponse({"error": "bad date"}, status_code=400)

    try:
        # Use ||| as delimiter to avoid breaking on filenames with commas
        id_list = [x for x in ids.split('|||') if x]
        deleted_set = set([x for x in (deleted_ids or "").split('|||') if x])
        if not id_list:
            return JSONResponse({"error": "no ids"}, status_code=400)

        # load all rows for the day, map by __id__
        # Try with cached data first (matches what page saw), then force refresh if no match
        rows = load_day(y, m, d)
        by_id = {str(r.get("__id__")): r for r in rows}
        originals: List[Dict[str, Any]] = []
        for id_ in id_list:
            if id_ in by_id:
                originals.append(by_id[id_])
        # If no matches found, try again with fresh data (cache may have expired since page load)
        if not originals:
            rows = load_day(y, m, d, force_refresh=True)
            by_id = {str(r.get("__id__")): r for r in rows}
            for id_ in id_list:
                if id_ in by_id:
                    originals.append(by_id[id_])
        if not originals:
            return JSONResponse({"error": "no matching originals", "submitted_ids": id_list, "available_ids": list(by_id.keys())[:5]}, status_code=404)

        # determine pdf_id for header draft key from first original
        first = originals[0]
        key0 = first.get("__s3_key__", "")
        pid0 = pdf_id_from_key(key0) if key0 else ""
        header_draft = get_draft(pid0, "__header__", user) or {"fields": {}}
        header_fields = header_draft.get("fields", {})

        # editable line-level fields (exclude GL DESC_NEW which is auto)
        line_edit_fields = [
            "EnrichedGLAccountNumber","EnrichedGLAccountName","ENRICHED CONSUMPTION","ENRICHED UOM",
            "Meter Number","Meter Size","House Or Vacant","Utility Type",
            "Line Item Description","Line Item Charge","Consumption Amount","Unit of Measure",
            "Previous Reading","Previous Reading Date","Current Reading","Current Reading Date","Rate",
        ]
        # invoice-level editable fields (applied to each line)
        # NOTE: Service Address, Service City, Service State, Service Zipcode are NOT header fields
        # They are per-line fields since an invoice can have multiple service addresses
        header_edit_fields = [
            "EnrichedVendorName","EnrichedVendorID","EnrichedVendorLocationID",
            "EnrichedPropertyName","EnrichedPropertyID",
            "Account Number",
            "Bill Period Start","Bill Period End","Bill Date","Due Date",
            "Special Instructions",
            "Bill From",
        ]

        deltas: List[Dict[str, Any]] = []
        merged: List[Dict[str, Any]] = []

        def _norm(val: Any) -> str:
            return (str(val or "").strip())

        def _build_gl_desc(rec: Dict[str, Any]) -> str:
            """Recompute GL DESC_NEW to mirror enrichment formatting using the latest edited values.

            For VACANT GLs (5705-0000, 5715-0000, 5720-1000, 5721-1000):
              "(M/D/YY-M/D/YY V[E/G/W/S] Street#Letter@Unit[!])"
              Example: (7/24/25-8/21/25 VE 9436N@159)

            For HOUSE GLs (standard format):
              "{Line Item Description} | {BPS}-{BPE} | {Service Address} | {Account Number} | {Line Item Account Number} | {Meter Number} | {Consumption} | {UOM}"
            """
            # Check for VACANT GL - use special format
            vacant_desc = _build_vacant_gl_desc(rec)
            if vacant_desc:
                return vacant_desc

            # HOUSE format (standard)
            addr = _norm(rec.get("Service Address")).upper()
            acct = _norm(rec.get("Account Number"))
            li_acct = _norm(rec.get("Line Item Account Number"))
            meter = _norm(rec.get("Meter Number"))
            desc = _norm(rec.get("Line Item Description")).upper()
            # Prefer enriched consumption/uom, fallback to raw
            cons = _norm(rec.get("ENRICHED CONSUMPTION") or rec.get("Consumption Amount"))
            uom = _norm(rec.get("ENRICHED UOM") or rec.get("Unit of Measure")).upper()
            bps = _norm(rec.get("Bill Period Start"))
            bpe = _norm(rec.get("Bill Period End"))
            rng = f"{bps}-{bpe}" if (bps or bpe) else ""
            # Build in requested order
            parts = [
                desc,
                rng,
                addr,
                acct,
                li_acct,
                meter,
                cons,
                uom,
            ]
            return " | ".join(parts)

        UPPER_FIELDS = [
            "Service Address", "Service City", "Service State"
        ]

        # --- House/Vacant server-side guardrails ---
        VACANT_NAMES = {"VACANT ELECTRIC","VACANT GAS","VACANT WATER","VACANT SEWER","VACANT ACTIVATION"}
        HOUSE_BACKFILL = {
            "VACANT ELECTRIC": "HOUSE ELECTRIC",
            "VACANT GAS": "GAS",
            "VACANT WATER": "WATER",
            "VACANT SEWER": "SEWER",
            "VACANT ACTIVATION": "",
        }
        import re as _re
        _unit_re = _re.compile(r"\b(?:APT|UNIT|#|STE|SUITE|APARTMENT|BLDG)\s*\w+", _re.I)
        def _ensure_hov(rec: Dict[str, Any]) -> None:
            """Default to House unless there's a clear unit/apartment indicator. Keep GL Name in sync.
            Vacant only when a clear unit/apartment indicator is present and no explicit parser choice exists.
            """
            hov = str(rec.get("House Or Vacant") or "").strip()
            gln = str(rec.get("EnrichedGLAccountName") or "").strip()
            util = str(rec.get("Utility Type") or "").strip()
            addr = str(rec.get("Service Address") or "").strip()
            has_unit = bool(_unit_re.search(addr))
            gln_upper = gln.upper()
            is_vacant_gl = ("VACANT" in gln_upper)
            # decide desired HOV; if parser already set HOV, respect it
            desired = hov if hov else ("Vacant" if has_unit else "House")
            if hov != desired:
                rec["House Or Vacant"] = desired
            # adjust GL Name if it conflicts with desired
            if desired == "Vacant":
                if not is_vacant_gl:
                    # try to map to a Vacant version for the utility
                    vac_try = f"Vacant {util}".strip()
                    if vac_try.upper() in VACANT_NAMES:
                        rec["EnrichedGLAccountName"] = vac_try
                    elif gln and not gln_upper.startswith("VACANT "):
                        rec["EnrichedGLAccountName"] = "Vacant " + gln
            else:  # House
                if is_vacant_gl:
                    mapped = HOUSE_BACKFILL.get(gln_upper, gln.replace("Vacant ", "").replace("VACANT ", "").strip())
                    if mapped is not None:
                        rec["EnrichedGLAccountName"] = mapped

        # Prepare vendor name -> id map from the full day's rows to resolve IDs on submit
        vendor_map_submit: Dict[str, str] = {}
        for r in rows:
            n = str(r.get("EnrichedVendorName", "") or r.get("Vendor Name", "") or r.get("Vendor", "")).strip()
            i = str(r.get("EnrichedVendorID", "")).strip()
            if n:
                key = n.upper()
                if key not in vendor_map_submit or not vendor_map_submit[key]:
                    vendor_map_submit[key] = i

        for orig in originals:
            key = orig.get("__s3_key__", ""); idx = orig.get("__row_idx__", 0)
            pid = pdf_id_from_key(key) if key else ""
            lid = line_id_from(key or "", idx)
            line_draft = get_draft(pid, lid, user) or {"fields": {}}
            lf = line_draft.get("fields", {})

            # If this original line is flagged deleted, skip producing merged/delta output
            if str(orig.get("__id__")) in deleted_set:
                continue

            # apply header + line fields
            new_rec = dict(orig)
            for k in header_edit_fields:
                if k in header_fields and header_fields[k] != "":
                    new_rec[k] = header_fields[k]
            for k in line_edit_fields:
                if k in lf and lf[k] != "":
                    new_rec[k] = lf[k]
            # If we have a vendor name but missing ID, attempt to map by name
            if str(new_rec.get("EnrichedVendorName", "")).strip() and not str(new_rec.get("EnrichedVendorID", "")).strip():
                vkey = str(new_rec.get("EnrichedVendorName", "")).strip().upper()
                vid = vendor_map_submit.get(vkey, "")
                if vid:
                    new_rec["EnrichedVendorID"] = vid

            # Normalize select fields to ALL CAPS for downstream analytics consistency
            for fname in UPPER_FIELDS:
                if fname in new_rec and isinstance(new_rec[fname], str):
                    new_rec[fname] = new_rec[fname].upper()

            # Server-side enforce House/Vacant rule before recomputing desc
            _ensure_hov(new_rec)

            # Recompute GL DESC_NEW using latest values
            new_rec["GL DESC_NEW"] = _build_gl_desc(new_rec)

            # build delta only for changed fields (excluding GL DESC_NEW)
            delta = {k: new_rec.get(k) for k in (set(header_edit_fields) | set(line_edit_fields)) if str(new_rec.get(k, "")) != str(orig.get(k, ""))}
            if delta:
                deltas.append({
                    "__id__": orig.get("__id__"),
                    "__s3_key__": key,
                    "__row_idx__": idx,
                    "changes": delta,
                })
            merged.append(new_rec)

        # Handle any manual extra lines (added in UI)
        extra_lines: List[Dict[str, Any]] = []
        try:
            if extras:
                parsed = json.loads(extras)
                if isinstance(parsed, list):
                    extra_lines = parsed
        except Exception:
            # ignore malformed extras to avoid blocking submits
            extra_lines = []

        for e in extra_lines:
            # build a new record using header defaults overlaid with provided fields
            new_rec = dict(first)  # start from first to retain context columns
            # apply header fields first
            for k in header_edit_fields:
                if k in header_fields and header_fields[k] != "":
                    new_rec[k] = header_fields[k]
            # For manual lines, clear line-specific fields that aren't explicitly provided
            # This prevents inheriting consumption/meter data from the first line
            for k in line_edit_fields:
                if k not in (e or {}) or (e or {}).get(k) == "":
                    new_rec[k] = ""
            # then overlay provided manual fields
            for k, v in (e or {}).items():
                if v != "":
                    new_rec[k] = v
            # Normalize select fields to ALL CAPS
            for fname in UPPER_FIELDS:
                if fname in new_rec and isinstance(new_rec[fname], str):
                    new_rec[fname] = new_rec[fname].upper()
            # Enforce House/Vacant on manual line too
            _ensure_hov(new_rec)
            # Recompute GL DESC_NEW for manual line
            new_rec["GL DESC_NEW"] = _build_gl_desc(new_rec)
            new_rec["__id__"] = None
            new_rec["__s3_key__"] = first.get("__s3_key__", "")
            new_rec["__row_idx__"] = -1
            new_rec["__manual__"] = True
            merged.append(new_rec)

            # represent the manual line as a delta of provided fields + any header-applied edits
            change_keys = set(header_edit_fields) | set(line_edit_fields)
            changes_obj = {k: new_rec.get(k) for k in change_keys if k in new_rec}
            deltas.append({
                "__id__": None,
                "__s3_key__": new_rec.get("__s3_key__", ""),
                "__row_idx__": -1,
                "changes": changes_obj,
                "__manual__": True,
            })

        # write outputs
        delta_key = _write_jsonl(OVERRIDE_PREFIX, y, m, d, "overrides_delta", deltas)
        merged_key = _write_jsonl(OVERRIDE_PREFIX, y, m, d, "overrides_merged", merged)

        # also write a per-invoice final (pre-Entrata) JSONL under Bill_Parser_6_PreEntrata_Submission
        def _safe(val: str) -> str:
            val = (val or "").strip()
            if not val:
                return "(unknown)"
            # collapse whitespace and sanitize
            val = re.sub(r"\s+", " ", val)
            # limit to filename-safe range for the basename only
            keep = []
            for ch in val:
                keep.append(ch if (ch.isalnum() or ch in " -_()&,+.#") else "-")
            return "".join(keep).strip()

        # Prefer updated header fields (entered during review) for file naming; fall back to original values
        prop_name = _safe(str(
            header_fields.get("EnrichedPropertyName")
            or first.get("EnrichedPropertyName", "")
            or first.get("Property Name", "")
        ))
        vendor_name = _safe(str(
            header_fields.get("EnrichedVendorName")
            or first.get("EnrichedVendorName", "")
            or first.get("Vendor Name", "")
            or first.get("Vendor", "")
            or first.get("Utility Type", "")
        ))
        account_name = _safe(str(
            header_fields.get("Account Number")
            or first.get("Account Number", "")
            or first.get("Account", "")
        ))
        svc_start = _safe(str(
            header_fields.get("Bill Period Start")
            or first.get("Bill Period Start", "")
        ))
        svc_end = _safe(str(
            header_fields.get("Bill Period End")
            or first.get("Bill Period End", "")
        ))
        due_date = _safe(str(
            header_fields.get("Due Date")
            or first.get("Due Date", "")
        ))
        basename = f"{prop_name}-{vendor_name}-{account_name}-{svc_start}-{svc_end}-{due_date}"

        # Inject Title, Status, and Submitter into each merged record for POST payloads
        # Title format: Account | Property | Vendor | YYYY-MM-DD
        status_label = "Not Posted"
        title_str = f"{account_name} | {prop_name} | {vendor_name} | {y}-{m}-{d}"
        submit_timestamp = dt.datetime.utcnow().isoformat()
        merged = [
            {**rec, "Title": title_str, "Status": status_label, "Submitter": user, "SubmittedAt": submit_timestamp}
            for rec in merged
        ]

        if os.getenv("PRE_ENTRATA_KEEP_ONLY_LATEST", "1") == "1":
            prefix = f"{PRE_ENTRATA_PREFIX}yyyy={y}/mm={m}/dd={d}/"
            resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
            for obj in resp.get("Contents", []):
                k = obj["Key"]
                # Only delete previous file if BOTH account AND due date match (allows multiple historical invoices)
                if account_name in k and due_date and due_date in k:
                    s3.delete_object(Bucket=BUCKET, Key=k)

        preentrata_key = _write_jsonl(PRE_ENTRATA_PREFIX, y, m, d, basename, merged)

        # --- Append extra lines to Stage 4 enriched file so line counts are accurate ---
        if extra_lines and first.get("__s3_key__"):
            try:
                s3_key = first["__s3_key__"]
                # Read current enriched file
                txt = _read_s3_text(BUCKET, s3_key)
                existing_lines = [l for l in txt.strip().split('\n') if l.strip()]

                # Build extra line records (cleaned up for Stage 4 format)
                extra_records = []
                for e in extra_lines:
                    new_rec = dict(first)
                    for k in header_edit_fields:
                        if k in header_fields and header_fields[k] != "":
                            new_rec[k] = header_fields[k]
                    for k, v in (e or {}).items():
                        if v != "":
                            new_rec[k] = v
                    for fname in UPPER_FIELDS:
                        if fname in new_rec and isinstance(new_rec[fname], str):
                            new_rec[fname] = new_rec[fname].upper()
                    _ensure_hov(new_rec)
                    new_rec["GL DESC_NEW"] = _build_gl_desc(new_rec)
                    # Remove internal fields before saving to S3
                    for internal_key in ["__id__", "__s3_key__", "__row_idx__", "__manual__"]:
                        new_rec.pop(internal_key, None)
                    extra_records.append(json.dumps(new_rec, ensure_ascii=False))

                # Append extra lines to existing file
                new_content = '\n'.join(existing_lines + extra_records)
                if s3_key.endswith('.gz'):
                    body = gzip.compress(new_content.encode('utf-8'))
                    s3.put_object(Bucket=BUCKET, Key=s3_key, Body=body, ContentType='application/json', ContentEncoding='gzip')
                else:
                    s3.put_object(Bucket=BUCKET, Key=s3_key, Body=new_content.encode('utf-8'), ContentType='application/json')

                # Mark the new extra lines as Submitted so status = COMPLETE
                # Their __id__ is s3_key#row_idx where row_idx starts at len(existing_lines)
                for i in range(len(extra_lines)):
                    new_row_idx = len(existing_lines) + i
                    new_id = f"{s3_key}#{new_row_idx}"
                    put_status(new_id, "Submitted", user)

                # Invalidate cache so the new lines are picked up
                invalidate_day_cache(y, m, d)
                print(f"[SUBMIT] Appended {len(extra_lines)} extra lines to Stage 4: {s3_key}")
            except Exception as e:
                print(f"[SUBMIT] Warning: Failed to append extra lines to Stage 4: {e}")

        # --- Update Stage 4 with submitted header values so invoices page shows correct property/vendor ---
        if first.get("__s3_key__") and header_fields:
            try:
                s3_key = first["__s3_key__"]
                txt = _read_s3_text(BUCKET, s3_key)
                lines_raw = [l for l in txt.strip().split('\n') if l.strip()]
                updated_lines = []
                for line in lines_raw:
                    try:
                        rec = json.loads(line)
                        # Apply header field overrides
                        for k in header_edit_fields:
                            if k in header_fields and header_fields[k] != "":
                                rec[k] = header_fields[k]
                        updated_lines.append(json.dumps(rec, ensure_ascii=False))
                    except:
                        updated_lines.append(line)
                new_content = '\n'.join(updated_lines)
                if s3_key.endswith('.gz'):
                    body = gzip.compress(new_content.encode('utf-8'))
                    s3.put_object(Bucket=BUCKET, Key=s3_key, Body=body, ContentType='application/json', ContentEncoding='gzip')
                else:
                    s3.put_object(Bucket=BUCKET, Key=s3_key, Body=new_content.encode('utf-8'), ContentType='application/json')
                invalidate_day_cache(y, m, d)
                print(f"[SUBMIT] Updated Stage 4 with header values: {s3_key}")
            except Exception as e:
                print(f"[SUBMIT] Warning: Failed to update Stage 4 with header values: {e}")

        # --- Persist a shared '__final__' snapshot of the submitted state ---
        try:
            # Save header snapshot for everyone to see
            put_draft(pid0, "__header__", "__final__", header_fields, date, first.get("Invoice Number", ""))
            # Save each line's fields as submitted, including deletion flag
            for orig in originals:
                key = orig.get("__s3_key__", ""); idx = orig.get("__row_idx__", 0)
                pid = pdf_id_from_key(key) if key else ""
                lid = line_id_from(key or "", idx)
                line_draft = get_draft(pid, lid, user) or {"fields": {}}
                lf = dict(line_draft.get("fields", {}) or {})
                if str(orig.get("__id__")) in deleted_set:
                    lf["__deleted__"] = "1"
                put_draft(pid, lid, "__final__", lf, date, first.get("Invoice Number", ""))
        except Exception:
            # Snapshot failure should never block a submit
            pass

        sent = 0
        deleted_only = set(deleted_set)
        for id_ in id_list:
            if str(id_) in deleted_only:
                put_status(id_, "Deleted", user)
                # do not enqueue or count as sent
                continue
            if REVIEW_QUEUE_URL:
                body = json.dumps({"id": id_, "submitted_by": user, "submitted_utc": dt.datetime.utcnow().isoformat(), "delta_key": delta_key, "merged_key": merged_key})
                sqs.send_message(QueueUrl=REVIEW_QUEUE_URL, MessageBody=body)
            put_status(id_, "Submitted", user)
            sent += 1

        # include manual lines in the sent tally for user feedback (no DDB status for manual rows)
        sent += len(extra_lines)

        # Invalidate day_status_counts cache so dashboard reflects new status immediately
        _CACHE.pop(("day_status_counts", y, m, d), None)
        _CACHE.pop(("parse_dashboard",), None)

        return {"ok": True, "sent": sent, "delta_key": delta_key, "merged_key": merged_key, "preentrata_key": preentrata_key}
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return JSONResponse({"ok": False, "error": str(e), "trace": tb}, status_code=500)


@app.get("/pdf")
def pdf_proxy(u: str = "", k: str = "", date: str = "", pdf_id: str = ""):
    """Proxy endpoint that regenerates a fresh presigned URL for a given (possibly expired) PDF link.
    Accepts query param u=<original_or_short_url> and redirects to a new presigned URL.
    """
    # If an explicit S3 key was provided, use it
    if k:
        key = k.lstrip('/')
        # Handle accidental inclusion of bucket in key
        if key.startswith(f"{BUCKET}/"):
            key = key[len(BUCKET)+1:]
        bucket = BUCKET
        try:
            print(f"/pdf proxy (k): streaming bucket={bucket} key={key}")
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj['Body']
            from starlette.responses import StreamingResponse
            base_name = os.path.basename(key) or 'document.pdf'
            headers = {
                'Content-Disposition': f'inline; filename="{base_name}"',
                'Content-Type': 'application/pdf',
                'Cache-Control': 'private, max-age=300',
            }
            return StreamingResponse(body.iter_chunks(chunk_size=8192), headers=headers, media_type='application/pdf')
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    # If 'u' looks like a bare S3 key (not a URL), treat it as key
    if u and not (u.startswith('http://') or u.startswith('https://') or u.startswith('s3://')) and ('/' in u):
        key = u.lstrip('/')
        if key.startswith(f"{BUCKET}/"):
            key = key[len(BUCKET)+1:]
        bucket = BUCKET
        try:
            print(f"/pdf proxy: bucket={bucket} key={key} (u treated as key)")
            base_name = os.path.basename(key) or 'document.pdf'
            url = s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket,
                    'Key': key,
                    'ResponseContentDisposition': f'inline; filename=\"{base_name}\"',
                    'ResponseContentType': 'application/pdf',
                },
                ExpiresIn=3600
            )
            return RedirectResponse(url)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # Expand legacy short links before anything else
    if u:
        # normalize percent-encoding once, then expand short
        try:
            from urllib.parse import unquote as _unq
            u = _unq(u)
            # some callers double-encode; a second pass is safe
            u = _unq(u)
        except Exception:
            pass
        u = _maybe_expand_short(u)
    # Try to parse original first, but also resolve and try final (prefer final if S3)
    parsed_orig = _parse_s3_from_url(u)
    final = _resolve_final_url(u)
    parsed_final = _parse_s3_from_url(final)
    # If still not parsed and looks like Lambda Function URL, aggressively resolve via GET
    if not parsed_final and u and (".lambda-url." in (urlparse(u).netloc or "")):
        try:
            r = requests.get(u, allow_redirects=True, timeout=12, stream=False)
            final = r.url or final or u
            parsed_final = _parse_s3_from_url(final)
        except Exception:
            pass
    parsed = parsed_final or parsed_orig
    print(f"/pdf debug: u={u} final={final} parsed_orig={bool(parsed_orig)} parsed_final={bool(parsed_final)}")
    # Last-ditch S3 parse for virtual-hosted links if parser failed
    if not parsed and (final or u):
        candidate = final or u
        try:
            from urllib.parse import urlparse as _up
            _p = _up(candidate)
            _host = _p.netloc or ""
            _path = (_p.path or "").lstrip('/')
            if ".s3.amazonaws.com" in _host and _path:
                _bucket = _host.split('.s3.amazonaws.com', 1)[0]
                if _bucket:
                    parsed = (_bucket, unquote(_path))
                    print(f"/pdf debug: applied last-ditch s3 host parse bucket={_bucket}")
        except Exception:
            pass
    if parsed:
        bucket, key = parsed
        try:
            print(f"/pdf proxy: streaming bucket={bucket} key={key}")
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj['Body']
            from starlette.responses import StreamingResponse
            base_name = os.path.basename(key) or 'document.pdf'
            headers = {
                'Content-Disposition': f'inline; filename="{base_name}"',
                'Content-Type': 'application/pdf',
                'Cache-Control': 'private, max-age=300',
            }
            return StreamingResponse(body.iter_chunks(chunk_size=8192), headers=headers, media_type='application/pdf')
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
    # Fallback: if caller provided date+pdf_id, infer the S3 key from enriched outputs
    try:
        if date and pdf_id:
            y, m, d = date.split("-")
            rows = load_day(y, m, d)
            # Narrow rows to this document id
            doc_rows = [r for r in rows if r.get("__s3_key__") and pdf_id_from_key(r.get("__s3_key__")) == pdf_id]
            key_guess = _infer_pdf_key_for_doc(y, m, d, doc_rows, pdf_id) if doc_rows else ""
            if not key_guess:
                # Even if no rows, attempt a global scan by pdf_id across known prefixes
                try:
                    cands = []
                    for pfx in (REWORK_PREFIX, INPUT_PREFIX, PARSED_INPUTS_PREFIX, HIST_ARCHIVE_PREFIX):
                        pag = s3.get_paginator("list_objects_v2")
                        for page in pag.paginate(Bucket=BUCKET, Prefix=pfx):
                            for obj in page.get("Contents", []) or []:
                                k = obj.get("Key", ""); base = os.path.basename(k).lower()
                                if base.endswith('.pdf') and pdf_id.lower() in base:
                                    cands.append((k, obj.get("LastModified")))
                    if cands:
                        cands.sort(key=lambda t: (t[1] or 0), reverse=True)
                        key_guess = cands[0][0]
                except Exception:
                    pass
            if key_guess:
                    # key_guess may be an absolute URL or s3:// URI; normalize to (bucket,key)
                    tgt_bucket = BUCKET
                    key2 = key_guess
                    try:
                        if key_guess.startswith('http://') or key_guess.startswith('https://') or key_guess.startswith('s3://'):
                            parsed = _parse_s3_from_url(key_guess)
                            if parsed:
                                tgt_bucket, key2 = parsed[0], parsed[1]
                            else:
                                # fall back to path portion if possible
                                from urllib.parse import urlparse as _up
                                _p = _up(key_guess)
                                key2 = (_p.path or '').lstrip('/')
                        key2 = key2.lstrip('/')
                        if key2.startswith(f"{BUCKET}/"):
                            key2 = key2[len(BUCKET)+1:]
                    except Exception:
                        key2 = key_guess.lstrip('/')
                    print(f"/pdf fallback infer: bucket={tgt_bucket} key={key2}")
                    obj = s3.get_object(Bucket=tgt_bucket, Key=key2)
                    body = obj['Body']
                    from starlette.responses import StreamingResponse
                    base_name = os.path.basename(key2) or 'document.pdf'
                    headers = {
                        'Content-Disposition': f'inline; filename="{base_name}"',
                        'Content-Type': 'application/pdf',
                        'Cache-Control': 'private, max-age=300',
                    }
                    return StreamingResponse(body.iter_chunks(chunk_size=8192), headers=headers, media_type='application/pdf')
    except Exception:
        pass
    # If we can't parse to S3 at this point, fail clearly instead of redirecting to expired links
    print(f"/pdf error: non-s3 url after resolution u={u} final={final}")
    return JSONResponse({"error": "unable to parse s3 url"}, status_code=400)
