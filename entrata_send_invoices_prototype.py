import os
import sys
import json
import argparse
import datetime as dt
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import boto3
import botocore
import requests

# --- Config via env ---
BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
PRE_ENTRATA_PREFIX = os.getenv("PRE_ENTRATA_PREFIX", "Bill_Parser_6_PreEntrata_Submission/")
# Default to api-vendor if not provided so web POST works without extra config
VENDOR_CACHE_S3 = os.getenv("VENDOR_CACHE_S3", "s3://api-vendor/vendors/latest.json")
VENDOR_CACHE_LOCAL = os.getenv("VENDOR_CACHE_LOCAL", "vendor_cache.json")  # optional
REGION = os.getenv("AWS_REGION", "us-east-1")
# Hardcoded Entrata config (per request)
ENTRATA_BASE_URL = "https://apis.entrata.com/ext/orgs"
ENTRATA_ORG = "jrkpropertyholdingsentratacore"
ENTRATA_API_KEY_ENV = "288f3174-2ec2-48e5-8f31-742ec278e53b"
ENTRATA_API_SECRET_NAME = ""  # unused when hardcoded

# Hardcoded local test file path (Windows)
DEFAULT_TEST_JSONL = r"H:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV\bill_review_app\test\909 West-City of Tempe-Customer Service-2012521789-07-08-2025-08-06-2025_20251022T000402Z.jsonl"

# Hardcoded vendor cache path provided by user
DEFAULT_VENDOR_CACHE = r"H:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV\bill_review_app\test\latest.json"

s3 = boto3.client("s3", region_name=REGION)
secrets = boto3.client("secretsmanager", region_name=REGION)


def parse_date_any(s: str) -> Optional[dt.date]:
    if not s:
        return None
    s = str(s).strip()
    fmts = ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y"]
    for f in fmts:
        try:
            return datetime.strptime(s, f).date()
        except Exception:
            pass
    return None


def list_pre_entrata_objects(limit: int = 50) -> List[str]:
    keys: List[str] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=BUCKET, Prefix=PRE_ENTRATA_PREFIX)
        for page in pages:
            for obj in page.get("Contents", []) or []:
                k = obj.get("Key", "")
                if k.lower().endswith(".jsonl"):
                    keys.append(k)
        return keys[-limit:]
    except botocore.exceptions.NoCredentialsError:
        return []


def _parse_jsonl_text(txt: str, source_tag: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
            if isinstance(rec, dict):
                rec["__s3_key__"] = source_tag
                out.append(rec)
        except Exception:
            continue
    return out

def read_json_records(key_or_path: str) -> List[Dict[str, Any]]:
    # Local path support
    if os.path.exists(key_or_path):
        with open(key_or_path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
        return _parse_jsonl_text(txt, key_or_path)
    # S3 support
    key = key_or_path
    if key.startswith("s3://"):
        _, rest = key.split("s3://", 1)
        b, k2 = rest.split("/", 1)
        bucket = b; key = k2
    else:
        bucket = BUCKET
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read().decode("utf-8", errors="ignore")
    return _parse_jsonl_text(body, key_or_path)


def _normalize_location(loc_item) -> Dict[str, str]:
    """Convert a location item to {id, name, vendorCode, isPrimary} dict. Handles both old (string) and new (object) formats."""
    if isinstance(loc_item, dict):
        lid = str(loc_item.get("id") or loc_item.get("locationId") or "").strip()
        # Prefer locationName (the actual location), fall back to displayName, then name
        lname = str(loc_item.get("locationName") or loc_item.get("displayName") or loc_item.get("name") or "").strip()
        lcode = str(loc_item.get("vendorCode") or "").strip()
        is_primary = loc_item.get("isPrimary", False)
        if isinstance(is_primary, str):
            is_primary = is_primary.lower() in ("t", "true", "1", "yes")
        return {"id": lid, "name": lname, "vendorCode": lcode, "isPrimary": bool(is_primary)}
    else:
        # Old format: just a string ID
        return {"id": str(loc_item).strip(), "name": "", "vendorCode": "", "isPrimary": False}


def _parse_vendor_data(data) -> Dict[str, List[Dict[str, str]]]:
    """Parse vendor cache data into {vendorId: [{id, name}, ...]} format."""
    out: Dict[str, List[Dict[str, str]]] = {}

    if isinstance(data, dict):
        # Wrapper shape: { "vendors": [ {vendorId, locations|vendorLocationId} ... ] }
        if isinstance(data.get("vendors"), list):
            for it in data["vendors"]:
                if not isinstance(it, dict):
                    continue
                vid = str(it.get("vendorId") or it.get("VendorId") or it.get("vendorID") or it.get("vendor_id") or "").strip()
                if not vid:
                    continue
                # Single location - pass full dict so locationName/displayName are preserved
                loc = it.get("vendorLocationId") or it.get("VendorLocationId") or it.get("locationId")
                if loc:
                    out.setdefault(vid, []).append(_normalize_location(it))
                # Multiple locations
                locs = it.get("locations") if isinstance(it.get("locations"), list) else None
                if locs:
                    for loc_item in locs:
                        out.setdefault(vid, []).append(_normalize_location(loc_item))
            if out:
                # Dedupe by id
                for vid in out:
                    seen = {}
                    deduped = []
                    for loc in out[vid]:
                        if loc["id"] and loc["id"] not in seen:
                            seen[loc["id"]] = True
                            deduped.append(loc)
                    out[vid] = sorted(deduped, key=lambda x: x["id"])
                return out
        # Simple mapping dict {vid: [locs]}
        for k, v in data.items():
            if isinstance(v, list):
                out[str(k)] = [_normalize_location(x) for x in v]
            elif v is not None:
                out[str(k)] = [_normalize_location(v)]
        return out

    if isinstance(data, list):
        for it in data:
            if not isinstance(it, dict):
                continue
            vid = str(it.get("vendorId") or it.get("VendorId") or it.get("vendorID") or it.get("vendor_id") or "").strip()
            if not vid:
                continue
            loc = it.get("vendorLocationId") or it.get("VendorLocationId") or it.get("locationId")
            if loc:
                out.setdefault(vid, []).append(_normalize_location(it))
            locs = it.get("locations") if isinstance(it.get("locations"), list) else None
            if locs:
                for loc_item in locs:
                    out.setdefault(vid, []).append(_normalize_location(loc_item))
        if out:
            # Dedupe by id
            for vid in out:
                seen = {}
                deduped = []
                for loc in out[vid]:
                    if loc["id"] and loc["id"] not in seen:
                        seen[loc["id"]] = True
                        deduped.append(loc)
                out[vid] = sorted(deduped, key=lambda x: x["id"])
        return out

    return {}


def load_vendor_cache() -> Dict[str, List[Dict[str, str]]]:
    """Return {vendorId: [{id, name}, ...]}.
    Tries S3 json first, then local file. If none, returns {} and we will prompt.
    Each location is a dict with 'id' and 'name' keys.
    """
    # Prefer hardcoded vendor cache path if present
    if os.path.exists(DEFAULT_VENDOR_CACHE):
        try:
            with open(DEFAULT_VENDOR_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = _parse_vendor_data(data)
            if result:
                return result
        except Exception:
            pass
    # S3
    if VENDOR_CACHE_S3.startswith("s3://"):
        try:
            _, rest = VENDOR_CACHE_S3.split("s3://", 1)
            bkt, key = rest.split("/", 1)
            obj = s3.get_object(Bucket=bkt, Key=key)
            data = json.loads(obj["Body"].read().decode("utf-8", errors="ignore"))
            result = _parse_vendor_data(data)
            if result:
                return result
        except Exception:
            pass
    # Local (fallback)
    if os.path.exists(VENDOR_CACHE_LOCAL):
        try:
            with open(VENDOR_CACHE_LOCAL, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = _parse_vendor_data(data)
            if result:
                return result
        except Exception:
            pass
    return {}


def choose_vendor_location(vendor_id: str, cache: Dict[str, List[str]]) -> str:
    locs = cache.get(str(vendor_id), [])
    if locs:
        print(f"Found {len(locs)} location(s) for VendorId {vendor_id}: {', '.join(locs)}")
    if len(locs) == 1:
        print(f"Using cached VendorLocationId {locs[0]} for VendorId {vendor_id}")
        return locs[0]
    if len(locs) > 1:
        print(f"Multiple VendorLocationIds found for VendorId {vendor_id}:")
        for i, lid in enumerate(locs, start=1):
            print(f"  {i}. {lid}")
        while True:
            pick = input("Pick a number for the desired VendorLocationId: ").strip()
            if pick.isdigit() and 1 <= int(pick) <= len(locs):
                return locs[int(pick) - 1]
    # none found
    while True:
        manual = input(f"Enter VendorLocationId for VendorId {vendor_id}: ").strip()
        if manual:
            return manual


def group_rows_into_headers(rows: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    if not rows:
        return []
    # Simple heuristic: group by Title field value, else all rows as one batch
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        t = str(r.get("Title") or "").strip()
        key = t or "__single__"
        groups.setdefault(key, []).append(r)
    return list(groups.values())


def build_send_invoices_payload(rows: List[Dict[str, Any]], vendor_loc_resolver, post_month_date: Optional[str] = None, invoice_suffix: str = "") -> Dict[str, Any]:
    headers: List[Dict[str, Any]] = []
    groups = group_rows_into_headers(rows)
    for g in groups:
        hdr = next((x for x in g if isinstance(x, dict)), {})
        vendor_id = str(hdr.get("EnrichedVendorID") or hdr.get("Vendor ID") or hdr.get("VendorID") or "").strip()
        if not vendor_id:
            raise ValueError("Missing EnrichedVendorID in data")
        vendor_loc_id = vendor_loc_resolver(vendor_id)

        # InvoiceNumber: "<Account Number> MM/DD/YYYY" based on Bill Date
        acct = str(hdr.get("Account Number") or hdr.get("AccountNumber") or "").strip()
        bill_raw_for_inv = str(hdr.get("Bill Date") or hdr.get("Invoice Date") or hdr.get("InvoiceDate") or "").strip()
        bill_dt_for_inv = parse_date_any(bill_raw_for_inv) or datetime.utcnow().date()
        bill_str_for_inv = bill_dt_for_inv.strftime("%m/%d/%Y")
        invoice_number = f"{acct} {bill_str_for_inv}" if acct else bill_str_for_inv
        if invoice_suffix:
            invoice_number = f"{invoice_number}{invoice_suffix}"
        bill_date = str(hdr.get("Bill Date") or hdr.get("Invoice Date") or hdr.get("InvoiceDate") or "").strip()
        inv_dt = parse_date_any(bill_date)
        # PostMonth must be MM/YYYY (current month unless overridden)
        post_month = datetime.utcnow().strftime("%m/%Y")
        if post_month_date:
            # Accept YYYY-MM-DD or MM-DD-YYYY; fall back to current month if parsing fails
            dt_override = parse_date_any(post_month_date)
            if dt_override:
                post_month = dt_override.strftime("%m/%Y")
        # Always ensure properly formatted date - never pass raw string to Entrata
        invoice_date_fmt = inv_dt.strftime("%m/%d/%Y") if inv_dt else datetime.utcnow().strftime("%m/%d/%Y")

        # Parse Due Date if available, otherwise default to invoice date
        due_date_raw = str(hdr.get("Due Date") or hdr.get("DueDate") or "").strip()
        due_dt = parse_date_any(due_date_raw)
        due_date_fmt = due_dt.strftime("%m/%d/%Y") if due_dt else invoice_date_fmt

        details: List[Dict[str, Any]] = []
        total = 0.0
        for r in g:
            prop_id = str(r.get("EnrichedPropertyID") or r.get("Property Id") or r.get("PropertyID") or r.get("PropertyId") or "").strip()
            # Use only EnrichedGLAccountID; raise if missing
            gl_id = str(r.get("EnrichedGLAccountID") or "").strip()
            if not gl_id:
                raise ValueError("Missing EnrichedGLAccountID in input row")
            desc = str(r.get("GL DESC_NEW") or r.get("EnrichedGLAccountName") or r.get("Utility Type") or "").strip()
            amt_raw = str(r.get("Line Item Charge") or r.get("Amount")).strip()
            try:
                amt_val = float(amt_raw.replace(",", "").replace("$", "")) if amt_raw else 0.0
            except Exception:
                amt_val = 0.0
            total += amt_val
            details.append({
                "PropertyId": prop_id,
                "GlAccountId": gl_id,
                "Description": desc,
                "Rate": f"{amt_val:.2f}",
                "Quantity": "1",
            })
        if not details:
            continue
        # Optional PDF attachment (if injected by caller)
        pdf_b64 = str(g[0].get("__pdf_b64__") or "").strip() if g and isinstance(g[0], dict) else ""
        pdf_name = (g[0].get("__pdf_filename__") or "invoice.pdf").split("/")[-1] if g and isinstance(g[0], dict) else "invoice.pdf"
        pdf_url = str(g[0].get("__pdf_url__") or "").strip() if g and isinstance(g[0], dict) else ""
        header_obj = {
            "ApDetails": {"ApDetail": details},
            "ApPayeeId": vendor_id,
            "ApPayeeLocationId": vendor_loc_id,
            "InvoiceTotal": f"{total:.2f}",
            "InvoiceNumber": invoice_number,
            "PostMonth": post_month,
            "InvoiceDate": invoice_date_fmt,
            "InvoiceDueDate": due_date_fmt,
        }
        if pdf_b64:
            header_obj["Files"] = {"File": {"FileData": pdf_b64, "@attributes": {"FileName": pdf_name}}}
            if pdf_url:
                header_obj["InvoiceImageUrl"] = pdf_url
        headers.append(header_obj)

    payload = {
        "auth": {"type": "apikey"},
        "requestId": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
        "method": {
            "name": "sendInvoices",
            "version": "r1",
            "params": {"ApBatch": {"IsPaused": "0", "IsPosted": "0", "ApHeaders": {"ApHeader": headers}}}
        }
    }
    return payload


def _get_api_key() -> str:
    # Hardcoded token
    return ENTRATA_API_KEY_ENV


def do_post(payload: Dict[str, Any], dry_run: bool = False) -> Tuple[bool, str]:
    if dry_run or not ENTRATA_BASE_URL or not ENTRATA_ORG:
        return True, json.dumps(payload, ensure_ascii=False)[:2000]
    url = f"{ENTRATA_BASE_URL.rstrip('/')}/{ENTRATA_ORG}/v1/vendors"
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": _get_api_key() or ""
    }
    # Remove empty header if key missing
    if not headers["X-Api-Key"]:
        headers.pop("X-Api-Key", None)

    # Retry only on ConnectionError (TCP failed to connect — Entrata never received the request).
    # Do NOT retry on Timeout — the request was sent and Entrata may have processed it;
    # retrying risks creating duplicate invoices.
    max_retries = 2
    base_timeout = 30  # 30s — batch of 3 × ~32s = ~96s, under AppRunner's 120s limit

    last_error = None
    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=(10, base_timeout))
            return r.ok, r.text
        except requests.exceptions.Timeout as e:
            # Do NOT retry — Entrata may have received and processed the request
            return False, f"Timeout after {base_timeout}s — verify in Entrata before retrying"
        except requests.exceptions.ConnectionError as e:
            last_error = f"Connection error: {e} (attempt {attempt + 1}/{max_retries})"
            if attempt < max_retries - 1:
                import time
                time.sleep(2 ** attempt)
                continue
        except Exception as e:
            # Non-retryable error
            return False, str(e)

    # All retries exhausted
    return False, last_error or "Unknown error after retries"


def main():
    parser = argparse.ArgumentParser(description="Prototype: Build and POST Entrata sendInvoices payload from Pre-Entrata JSONL")
    parser.add_argument("--key", help="S3 key under bucket OR local path to pre-Entrata .jsonl")
    parser.add_argument("--dry-run", action="store_true", help="Don't POST; print payload preview")
    parser.add_argument("--show-locations", nargs="?", const="auto", help="Print vendor location ids for provided vendor id or 'auto' from file and exit")
    args = parser.parse_args()

    key = args.key
    if not key:
        # Prefer hardcoded local test file if present
        if os.path.exists(DEFAULT_TEST_JSONL):
            print(f"Using default local test file: {DEFAULT_TEST_JSONL}")
            key = DEFAULT_TEST_JSONL
        else:
            keys: List[str] = list_pre_entrata_objects(limit=50)
            if keys:
                print("Select a file:")
                for i, k in enumerate(keys, start=1):
                    print(f"  {i}. s3://{BUCKET}/{k}")
                while True:
                    pick = input("Pick number: ").strip()
                    if pick.isdigit() and 1 <= int(pick) <= len(keys):
                        key = keys[int(pick) - 1]
                        break
            else:
                print("AWS credentials not found or no S3 files listed. You can pass a local .jsonl path with --key.")
                path = input("Enter local path to pre-Entrata .jsonl (or leave blank to exit): ").strip()
                if not path:
                    print("Aborting.")
                    sys.exit(1)
                key = path
    else:
        if key.startswith("s3://"):
            # normalize to key only
            _, rest = key.split("s3://", 1)
            b, k2 = rest.split("/", 1)
            if b != BUCKET:
                print(f"Warning: overriding bucket to {b}")
                globals()["BUCKET"] = b
            key = k2

    rows = read_json_records(key)
    if not rows:
        print("No records found in file.")
        sys.exit(1)

    cache = load_vendor_cache()
    # Optional: show locations and exit
    if args.show_locations is not None:
        if args.show_locations == "auto":
            vid = str(next((r.get("EnrichedVendorID") for r in rows if r.get("EnrichedVendorID")), "")).strip()
        else:
            vid = str(args.show_locations).strip()
        locs = cache.get(vid, [])
        print(f"VendorId {vid} locations: {locs}")
        return
    def resolver(vendor_id: str) -> str:
        return choose_vendor_location(vendor_id, cache)

    payload = build_send_invoices_payload(rows, resolver)

    ok, text = do_post(payload, dry_run=args.dry_run)
    head = "DRY RUN" if (args.dry_run or not ENTRATA_BASE_URL or not ENTRATA_ORG) else "LIVE POST"
    print(f"\n=== {head} RESULT ===")
    print(text)
    if not ok:
        sys.exit(2)


if __name__ == "__main__":
    main()
