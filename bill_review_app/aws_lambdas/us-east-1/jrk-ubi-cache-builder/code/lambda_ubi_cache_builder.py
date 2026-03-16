"""
UBI Unassigned Cache Builder Lambda

Runs on a schedule (EventBridge, every 1-2 hours) to build the UBI unassigned
bills cache. Writes the result to S3 so the AppRunner application can read it
instantly without doing any S3 scanning itself.

Steps:
  A. Load config (GL mappings, accounts-to-track, dimension tables)
  B. Scan DynamoDB for exclusion hashes (already-assigned line hashes)
  C. Scan Stage 8 for UBI history (last assigned periods per account)
  D. Scan Stage 7 for unassigned bills (with GL mapping + suggestions)
  E. Compute filter options from scanned data
  F. Write gzipped JSON cache to S3
"""

import os
import json
import gzip
import hashlib
import re
import time
from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
POST_ENTRATA_PREFIX = os.getenv("POST_ENTRATA_PREFIX",
                                "Bill_Parser_7_PostEntrata_Submission/")
UBI_ASSIGNED_PREFIX = os.getenv("UBI_ASSIGNED_PREFIX",
                                "Bill_Parser_8_UBI_Assigned/")
CONFIG_TABLE = os.getenv("CONFIG_TABLE", "jrk-bill-config")
ASSIGNMENTS_TABLE = os.getenv("ASSIGNMENTS_TABLE",
                              "jrk-bill-ubi-assignments")
ACCOUNTS_TRACK_KEY = os.getenv("ACCOUNTS_TRACK_KEY",
                               "Bill_Parser_Config/accounts_to_track.json")
EXPORTS_ROOT = os.getenv("EXPORTS_ROOT",
                         "Bill_Parser_Enrichment/exports/")
DIM_PROPERTY_PREFIX = os.getenv("DIM_PROPERTY_PREFIX",
                                EXPORTS_ROOT + "dim_property/")
DIM_GL_PREFIX = os.getenv("DIM_GL_PREFIX",
                          EXPORTS_ROOT + "dim_gl_account/")
CACHE_OUTPUT_KEY = os.getenv("CACHE_OUTPUT_KEY",
                             "Bill_Parser_Cache/ubi_unassigned_cache.json.gz")
DAYS_BACK = int(os.getenv("DAYS_BACK", "60"))

# Clients (reused across invocations via Lambda warm start)
s3 = boto3.client("s3", region_name=AWS_REGION)
ddb = boto3.client("dynamodb", region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# Helper functions (copied from main.py, pure functions with no app state)
# ---------------------------------------------------------------------------

def pdf_id_from_key(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


_VOLATILE_LINE_FIELDS = {
    "Charge Code", "Charge Code Source", "Charge Code Overridden",
    "Charge Code Override Reason",
    "Mapped Utility Name", "Current Amount", "Amount Overridden",
    "Amount Override Reason",
    "Is Excluded From UBI", "Exclusion Reason",
    "is_excluded_from_ubi", "exclusion_reason",
    "ubi_period", "ubi_amount", "ubi_months_total", "ubi_assigned_by",
    "ubi_assigned_date",
    "ubi_assignments", "ubi_period_count", "ubi_notes",
    "__stage8_key__", "__s3_key__", "__row_idx__", "__id__", "__manual__",
    "ubi_auto_suggested",
    "PostedBy", "PostedAt", "Status",
}


def _compute_stable_line_hash(rec: dict) -> str:
    stable_rec = {k: v for k, v in rec.items()
                  if k not in _VOLATILE_LINE_FIELDS}
    line_data = json.dumps(stable_rec, sort_keys=True)
    return hashlib.sha256(line_data.encode()).hexdigest()


ESSENTIAL_FIELDS = {
    'EnrichedPropertyName', 'EnrichedPropertyID', 'Property Name',
    'EnrichedVendorName', 'EnrichedVendorID', 'Vendor Name',
    'Account Number', 'Bill Period Start', 'Bill Period End',
    'EnrichedGLAccountNumber', 'EnrichedGLAccountName', 'GL Account Number',
    'Line Item Description', 'Line Item Charge', 'Charge Code',
    'source_input_key', 'PDF_LINK', 'Invoice Number',
    'Service Address', 'Meter Number', 'Consumption Amount',
    'Unit of Measure',
    'Current Amount', 'Amount Overridden', 'Charge Code Source',
    'Submitter', 'SubmittedBy',
}


def _lookup_charge_code(property_id, gl_account_id, gl_code, mappings):
    if not property_id or (not gl_account_id and not gl_code):
        return None
    pid = str(property_id).strip()
    gaid = str(gl_account_id or "").strip()
    gc = str(gl_code or "").strip()
    # 1. Property-specific by GL Code
    if gc:
        for m in mappings:
            if (str(m.get("property_id", "")).strip() == pid
                    and str(m.get("gl_code", "")).strip() == gc):
                return m
    # 2. Property-specific by GL Account ID
    if gaid:
        for m in mappings:
            if (str(m.get("property_id", "")).strip() == pid
                    and str(m.get("gl_account_id", "")).strip() == gaid):
                return m
    # 3. Wildcard by GL Code
    if gc:
        for m in mappings:
            if (str(m.get("property_id", "")).strip() == "*"
                    and str(m.get("gl_code", "")).strip() == gc):
                return m
    # 4. Wildcard by GL Account ID
    if gaid:
        for m in mappings:
            if (str(m.get("property_id", "")).strip() == "*"
                    and str(m.get("gl_account_id", "")).strip() == gaid):
                return m
    return None


def _parse_service_period_to_month(date_str):
    if not date_str:
        return None
    try:
        if "/" in str(date_str):
            parts = str(date_str).split("/")
            return (int(parts[2]), int(parts[0]))
        elif "-" in str(date_str):
            parts = str(date_str).split("-")
            return (int(parts[0]), int(parts[1]))
    except Exception:
        pass
    return None


def _get_next_ubi_period(current_period):
    try:
        parts = current_period.split("/")
        month = int(parts[0])
        year = int(parts[1])
        if month == 12:
            return f"01/{year + 1}"
        else:
            return f"{month + 1:02d}/{year}"
    except Exception:
        return None


def _get_prev_ubi_period(current_period):
    try:
        parts = current_period.split("/")
        month = int(parts[0])
        year = int(parts[1])
        if month == 1:
            return f"12/{year - 1}"
        else:
            return f"{month - 1:02d}/{year}"
    except Exception:
        return None


def _parse_date_any(s):
    if not s:
        return None
    s = str(s).strip()
    fmts = ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m/%d/%y"]
    for f in fmts:
        try:
            return datetime.strptime(s, f).date()
        except Exception:
            pass
    return None


def safe_parse_charge(charge_val):
    if charge_val is None:
        return 0.0
    charge_str = str(charge_val).replace("$", "").replace(",", "").strip()
    if not charge_str:
        return 0.0
    if "/" in charge_str or "-" in charge_str:
        if re.match(r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$', charge_str):
            return 0.0
    try:
        return float(charge_str)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Step A: Load configuration data
# ---------------------------------------------------------------------------

def load_gl_mappings():
    """Load GL charge-code mappings from DynamoDB config table."""
    try:
        resp = ddb.get_item(
            TableName=CONFIG_TABLE,
            Key={"PK": {"S": "CONFIG#gl-charge-code-mapping"},
                 "SK": {"S": "v1"}}
        )
        if "Item" not in resp:
            return []
        data_str = resp["Item"].get("Data", {}).get("S", "")
        if not data_str:
            return []
        parsed = json.loads(data_str)
        return parsed if isinstance(parsed, list) else []
    except Exception as e:
        print(f"[CONFIG] Error loading GL mappings: {e}")
        return []


def load_accounts_to_track():
    """Load accounts-to-track from S3."""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=ACCOUNTS_TRACK_KEY)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[CONFIG] Error loading accounts-to-track: {e}")
        return []


def build_ubi_account_keys(accounts):
    """Build set of UBI account lookup keys."""
    keys = set()
    for acct in accounts:
        if acct.get("is_ubi") is True and acct.get("is_tracked", True):
            prop_id = str(acct.get("propertyId", "")).strip()
            vendor_id = str(acct.get("vendorId", "")).strip()
            acct_num = str(acct.get("accountNumber", "")).strip()
            if prop_id and acct_num:
                keys.add(f"{prop_id}|{vendor_id}|{acct_num}")
                keys.add(f"{prop_id}||{acct_num}")
    return keys


def _read_s3_text(bucket, key):
    """Read S3 object as text, handling .gz files."""
    obj = s3.get_object(Bucket=bucket, Key=key)
    raw = obj["Body"].read()
    if key.lower().endswith(".gz"):
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            return gz.read().decode("utf-8", errors="ignore")
    return raw.decode("utf-8", errors="ignore")


def load_dim_records(prefix):
    """Load dimension table records from S3."""
    standard_key = f"{prefix}latest.json.gz"
    try:
        txt = _read_s3_text(BUCKET, standard_key)
    except Exception:
        # Fallback: find latest data file by listing
        try:
            paginator = s3.get_paginator("list_objects_v2")
            best_key, best_ts = None, None
            for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
                for obj in page.get("Contents", []):
                    k = obj["Key"]
                    if "data.json" in k:
                        lm = obj.get("LastModified")
                        if best_ts is None or (lm and lm > best_ts):
                            best_ts = lm
                            best_key = k
            if not best_key:
                return []
            txt = _read_s3_text(BUCKET, best_key)
        except Exception:
            return []
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
    # JSONL fallback
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
    return out


# ---------------------------------------------------------------------------
# Step B: Scan DynamoDB for exclusion hashes
# ---------------------------------------------------------------------------

def load_exclusion_hashes():
    """Full scan of jrk-bill-ubi-assignments for line_hash values."""
    print("[EXCLUSION] Scanning assignments table...")
    t0 = time.time()
    hashes = set()
    try:
        paginator = ddb.get_paginator("scan")
        for page in paginator.paginate(
            TableName=ASSIGNMENTS_TABLE,
            ProjectionExpression="line_hash"
        ):
            for item in page.get("Items", []):
                lh = item.get("line_hash", {}).get("S")
                if lh:
                    hashes.add(lh)
    except Exception as e:
        print(f"[EXCLUSION] Error: {e}")
    elapsed = time.time() - t0
    print(f"[EXCLUSION] Loaded {len(hashes)} hashes in {elapsed:.1f}s")
    return hashes


# ---------------------------------------------------------------------------
# Step C: Scan Stage 8 for UBI history
# ---------------------------------------------------------------------------

def scan_stage8_history():
    """Scan all Stage 8 files to find last assigned period per account."""
    print("[STAGE8] Scanning for UBI history...")
    t0 = time.time()

    # List all Stage 8 keys
    all_keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=UBI_ASSIGNED_PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".jsonl"):
                all_keys.append(obj["Key"])
    print(f"[STAGE8] Found {len(all_keys)} files to scan")

    account_data = {}  # account_key -> list of entries

    def process_stage8_file(key):
        local_accounts = {}
        try:
            file_timestamp = ""
            ts_match = re.search(r'_(\d{8}T\d{6}Z)_', key)
            if ts_match:
                file_timestamp = ts_match.group(1)

            obj_data = s3.get_object(Bucket=BUCKET, Key=key)
            txt = obj_data["Body"].read().decode("utf-8", errors="ignore")
            for line in txt.splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    ubi_period = rec.get("ubi_period")
                    if not ubi_period:
                        continue
                    service_start = rec.get("Bill Period Start", "")
                    service_end = rec.get("Bill Period End", "")
                    service_month = _parse_service_period_to_month(
                        service_start)
                    if not service_month:
                        continue
                    prop_id = rec.get("EnrichedPropertyID", "")
                    vendor_id = rec.get("EnrichedVendorID", "")
                    acct_num = str(
                        rec.get("Account Number", "")).strip()
                    account_key = f"{prop_id}|{vendor_id}|{acct_num}"
                    if account_key not in local_accounts:
                        local_accounts[account_key] = []
                    local_accounts[account_key].append({
                        "service_month": service_month,
                        "service_start": service_start,
                        "service_end": service_end,
                        "ubi_period": ubi_period,
                        "file_timestamp": file_timestamp,
                    })
                except Exception:
                    continue
        except Exception:
            pass
        return local_accounts

    with ThreadPoolExecutor(max_workers=50) as executor:
        results = list(executor.map(process_stage8_file, all_keys))

    # Merge results
    for local_accounts in results:
        for account_key, entries in local_accounts.items():
            if account_key not in account_data:
                account_data[account_key] = []
            account_data[account_key].extend(entries)

    # Find latest per account
    result = {}
    for account_key, entries in account_data.items():
        if not entries:
            continue
        sorted_entries = sorted(
            entries,
            key=lambda x: (x["service_month"],
                           x.get("file_timestamp", "")),
            reverse=True
        )
        latest = sorted_entries[0]
        # Deduplicate assignments
        seen_combos = set()
        all_assignments = []
        for e in sorted_entries:
            combo = (e["service_month"], e["ubi_period"])
            if combo not in seen_combos:
                seen_combos.add(combo)
                all_assignments.append({
                    "service_start": e.get("service_start", ""),
                    "service_end": e.get("service_end", ""),
                    "service_month": e["service_month"],
                    "ubi_period": e["ubi_period"],
                })
        result[account_key] = {
            "last_service_month": latest["service_month"],
            "last_ubi_period": latest["ubi_period"],
            "last_service_start": latest.get("service_start", ""),
            "last_service_end": latest.get("service_end", ""),
            "all_assignments": all_assignments,
        }

    elapsed = time.time() - t0
    print(f"[STAGE8] Scanned in {elapsed:.1f}s, "
          f"found {len(result)} accounts with history")
    return result


# ---------------------------------------------------------------------------
# Step D: Scan Stage 7 for unassigned bills
# ---------------------------------------------------------------------------

def scan_unassigned_bills(ubi_account_keys, excluded_hashes,
                          last_ubi_periods, gl_mappings, days_back):
    """Scan Stage 7 for unassigned bills. Core cache computation."""
    print(f"[STAGE7] Scanning last {days_back} days...")
    t0 = time.time()

    gl_lines_patched = 0

    # Build date-partitioned prefixes
    today = datetime.now()
    prefixes = []
    for i in range(days_back):
        d = today - timedelta(days=i)
        prefixes.append(
            f"{POST_ENTRATA_PREFIX}"
            f"yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/")

    # Collect all S3 keys
    all_keys = []
    for prefix in prefixes:
        try:
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
                for obj in page.get("Contents", []):
                    if obj["Key"].endswith(".jsonl"):
                        all_keys.append(obj["Key"])
        except Exception:
            continue
    print(f"[STAGE7] Found {len(all_keys)} JSONL files")

    def process_file(key):
        nonlocal gl_lines_patched
        try:
            obj_data = s3.get_object(Bucket=BUCKET, Key=key)
            txt = obj_data["Body"].read().decode("utf-8", errors="ignore")
            lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
            if not lines:
                return None

            # Apply GL mappings in memory (no S3 rewrite)
            if gl_mappings:
                patched_lines = []
                for raw_line in lines:
                    try:
                        rec = json.loads(raw_line)
                    except json.JSONDecodeError:
                        patched_lines.append(raw_line)
                        continue
                    is_overridden = rec.get("Charge Code Overridden") in (
                        True, "true", "True")
                    if not is_overridden:
                        prop_id = rec.get("EnrichedPropertyID", "")
                        gl_aid = (rec.get("EnrichedGLAccountID", "")
                                  or rec.get("GL Account ID", ""))
                        gl_c = (rec.get("EnrichedGLAccountNumber", "")
                                or rec.get("GL Account Number", ""))
                        mapping = _lookup_charge_code(
                            prop_id, gl_aid, gl_c, gl_mappings)
                        if mapping and mapping.get("charge_code"):
                            old_cc = rec.get("Charge Code", "")
                            new_cc = mapping["charge_code"]
                            if old_cc != new_cc:
                                rec["Charge Code"] = new_cc
                                rec["Charge Code Source"] = "mapping"
                                if mapping.get("utility_name"):
                                    rec["Mapped Utility Name"] = \
                                        mapping["utility_name"]
                                gl_lines_patched += 1
                    patched_lines.append(
                        json.dumps(rec, ensure_ascii=False)
                        if isinstance(rec, dict) else rec)
                lines = patched_lines

            try:
                first_rec = (json.loads(lines[0]) if isinstance(lines[0], str)
                             else lines[0])
            except json.JSONDecodeError:
                return None

            computed_pdf_id = pdf_id_from_key(key)
            date_match = re.search(
                r'yyyy=(\d{4})/mm=(\d{2})/dd=(\d{2})', key)
            review_date = (
                f"{date_match.group(1)}-{date_match.group(2)}"
                f"-{date_match.group(3)}"
                if date_match else "")

            posted_at_str = (first_rec.get("PostedAt", "")
                             or first_rec.get("SubmittedAt", ""))
            posted_at_ts = 0
            submitter = (first_rec.get("Submitter", "")
                         or first_rec.get("SubmittedBy", ""))
            if posted_at_str:
                try:
                    posted_at_dt = datetime.fromisoformat(
                        posted_at_str.replace('Z', '+00:00'))
                    posted_at_ts = posted_at_dt.timestamp()
                except Exception:
                    pass
            if not posted_at_str:
                s3_last_mod = obj_data.get("LastModified")
                if s3_last_mod:
                    posted_at_str = s3_last_mod.strftime(
                        "%Y-%m-%dT%H:%M:%S")
                    posted_at_ts = s3_last_mod.timestamp()

            property_id = first_rec.get("EnrichedPropertyID", "")
            vendor_id = first_rec.get("EnrichedVendorID", "")
            account_number = str(
                first_rec.get("Account Number", "")).strip()
            account_key = f"{property_id}|{vendor_id}|{account_number}"
            account_key_nv = f"{property_id}||{account_number}"

            is_ubi = (account_key in ubi_account_keys
                      or account_key_nv in ubi_account_keys)

            # --- Suggestion logic ---
            history = (last_ubi_periods.get(account_key)
                       if is_ubi else None)
            suggested_period = None
            last_ubi_period = None
            last_service_month_str = None
            last_service_dates = None

            if history and is_ubi:
                last_service_month = history.get("last_service_month")
                last_ubi_period = history.get("last_ubi_period")
                last_service_start = history.get("last_service_start", "")
                last_service_end = history.get("last_service_end", "")

                if last_service_month:
                    last_service_month_str = (
                        f"{last_service_month[1]:02d}"
                        f"/{last_service_month[0]}")
                    if last_service_start and last_service_end:
                        last_service_dates = (
                            f"{last_service_start} - {last_service_end}")
                    elif last_service_start:
                        last_service_dates = last_service_start

                    bill_service_start = first_rec.get(
                        "Bill Period Start", "")
                    bill_service_month = _parse_service_period_to_month(
                        bill_service_start)

                    if bill_service_month and last_ubi_period:
                        last_year, last_month = last_service_month
                        bill_year, bill_month = bill_service_month
                        if last_month == 12:
                            exp_year, exp_month = last_year + 1, 1
                        else:
                            exp_year, exp_month = last_year, last_month + 1
                        if (bill_year == exp_year
                                and bill_month == exp_month):
                            suggested_period = _get_next_ubi_period(
                                last_ubi_period)
                        elif (bill_year, bill_month) > (
                                last_year, last_month):
                            suggested_period = _get_next_ubi_period(
                                last_ubi_period)
                    elif last_ubi_period:
                        suggested_period = _get_next_ubi_period(
                            last_ubi_period)

            # Duplicate detection + prior period suggestion
            duplicate_warning = None
            prior_period_suggestion = None
            bill_svc_start_raw = first_rec.get("Bill Period Start", "")
            bill_svc_end_raw = first_rec.get("Bill Period End", "")

            if history and is_ubi and bill_svc_start_raw:
                all_assignments = history.get("all_assignments", [])
                bill_start_dt = _parse_date_any(bill_svc_start_raw)
                bill_end_dt = _parse_date_any(bill_svc_end_raw)

                if bill_start_dt and all_assignments:
                    for asgn in all_assignments:
                        asgn_start = _parse_date_any(
                            asgn.get("service_start", ""))
                        asgn_end = _parse_date_any(
                            asgn.get("service_end", ""))
                        if asgn_start:
                            start_diff = abs(
                                (bill_start_dt - asgn_start).days)
                            if bill_end_dt and asgn_end:
                                end_diff = abs(
                                    (bill_end_dt - asgn_end).days)
                            else:
                                end_diff = 999
                            if start_diff <= 5 and end_diff <= 5:
                                duplicate_warning = asgn.get(
                                    "ubi_period", "")
                                break

                    if not suggested_period and not duplicate_warning:
                        bill_svc_month = _parse_service_period_to_month(
                            bill_svc_start_raw)
                        if bill_svc_month:
                            bill_y, bill_m = bill_svc_month
                            for asgn in all_assignments:
                                asgn_month = asgn.get("service_month")
                                if not asgn_month:
                                    continue
                                asgn_y, asgn_m = asgn_month
                                if asgn_m == 1:
                                    prev_y, prev_m = asgn_y - 1, 12
                                else:
                                    prev_y, prev_m = asgn_y, asgn_m - 1
                                if bill_y == prev_y and bill_m == prev_m:
                                    prior_ubi = _get_prev_ubi_period(
                                        asgn.get("ubi_period", ""))
                                    if prior_ubi:
                                        prior_period_suggestion = prior_ubi
                                        suggested_period = prior_ubi
                                        break

            bill_info = {
                "s3_key": key,
                "vendor": (first_rec.get("EnrichedVendorName", "")
                           or first_rec.get("Vendor Name", "")),
                "account": first_rec.get("Account Number", ""),
                "account_key": account_key,
                "property_name": first_rec.get("EnrichedPropertyName", ""),
                "pdf_id": computed_pdf_id,
                "review_date": review_date,
                "invoice_no": first_rec.get("Invoice Number", ""),
                "total_amount": 0.0,
                "line_count": 0,
                "unassigned_lines": [],
                "last_modified": posted_at_str,
                "last_modified_ts": posted_at_ts,
                "submitter": submitter,
                "suggested_period": suggested_period,
                "last_assigned_period": last_ubi_period,
                "last_assigned_service": (last_service_dates
                                          or last_service_month_str),
                "is_ubi_account": is_ubi,
                "duplicate_warning": duplicate_warning,
                "prior_period_suggestion": prior_period_suggestion,
            }

            for line in lines:
                try:
                    rec = json.loads(line)
                    line_hash = _compute_stable_line_hash(rec)
                    if line_hash in excluded_hashes:
                        continue
                    charge = safe_parse_charge(
                        rec.get("Line Item Charge", "0"))
                    sanitized_rec = {}
                    for k, v in rec.items():
                        if k not in ESSENTIAL_FIELDS:
                            continue
                        if isinstance(v, str):
                            sanitized_rec[k] = v.replace(
                                '\x00', '').replace('\ufffd', '')
                        else:
                            sanitized_rec[k] = v
                    bill_info["unassigned_lines"].append({
                        "line_hash": line_hash,
                        "line_data": sanitized_rec,
                        "charge": charge,
                    })
                    bill_info["total_amount"] += charge
                    bill_info["line_count"] += 1
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue

            if bill_info["unassigned_lines"]:
                return bill_info
            return None
        except Exception as e:
            print(f"[STAGE7] Error processing {key}: {e}")
            return None

    # Process concurrently
    unassigned_bills = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(process_file, k): k for k in all_keys}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    unassigned_bills.append(result)
            except Exception as e:
                print(f"[STAGE7] Future error: {e}")

    elapsed = time.time() - t0
    print(f"[STAGE7] Computed {len(unassigned_bills)} bills in {elapsed:.1f}s"
          f" (GL: {gl_lines_patched} lines patched)")
    return unassigned_bills


# ---------------------------------------------------------------------------
# Step E: Compute filter options
# ---------------------------------------------------------------------------

def compute_filter_options(bills):
    """Extract unique properties/vendors/GL codes from scanned bills."""
    properties = set()
    vendors = set()
    gl_codes = set()

    for bill in bills:
        for line in bill.get("unassigned_lines", []):
            ld = line.get("line_data", {})
            prop = (ld.get("EnrichedPropertyName")
                    or ld.get("Property Name") or "")
            if prop:
                properties.add(prop)
            vendor = (ld.get("EnrichedVendorName")
                      or ld.get("Vendor Name") or "")
            if vendor:
                vendors.add(vendor)
            gl = (ld.get("EnrichedGLAccountNumber")
                  or ld.get("GL Account Number") or "")
            if gl:
                gl_codes.add(gl)

    # Also add from property name at bill level
    for bill in bills:
        if bill.get("property_name"):
            properties.add(bill["property_name"])
        if bill.get("vendor"):
            vendors.add(bill["vendor"])

    # Merge dimension table values
    try:
        dim_props = load_dim_records(DIM_PROPERTY_PREFIX)
        for r in dim_props:
            name = (r.get("name") or r.get("NAME")
                    or r.get("propertyName") or r.get("Property Name")
                    or r.get("PROPERTY_NAME") or r.get("Property")
                    or r.get("PROPERTY"))
            if name and str(name).strip():
                properties.add(str(name).strip())
    except Exception as e:
        print(f"[FILTER] Error loading dim properties: {e}")

    try:
        dim_gls = load_dim_records(DIM_GL_PREFIX)
        for r in dim_gls:
            gl_num = (r.get("FORMATTED_ACCOUNT_NUMBER")
                      or r.get("formattedAccountNumber")
                      or r.get("glAccountNumber")
                      or r.get("GL_ACCOUNT_NUMBER")
                      or r.get("accountNumber")
                      or r.get("ACCOUNT_NUMBER")
                      or r.get("glNumber") or r.get("GL_NUMBER") or "")
            if gl_num and str(gl_num).strip():
                gl_codes.add(str(gl_num).strip())
    except Exception as e:
        print(f"[FILTER] Error loading dim GL codes: {e}")

    return {
        "properties": sorted(list(properties)),
        "vendors": sorted(list(vendors)),
        "gl_codes": sorted(list(gl_codes)),
    }


# ---------------------------------------------------------------------------
# Step F: Write output
# ---------------------------------------------------------------------------

def write_cache_to_s3(bills, filter_options):
    """Write gzipped cache to S3."""
    payload = {
        "data": bills,
        "ts": time.time(),
        "filter_options": filter_options,
        "built_by": "lambda",
        "built_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw)
    s3.put_object(
        Bucket=BUCKET,
        Key=CACHE_OUTPUT_KEY,
        Body=compressed,
        ContentType="application/gzip",
    )
    print(f"[OUTPUT] Wrote {len(bills)} bills to S3 "
          f"({len(raw) // 1024}KB raw, {len(compressed) // 1024}KB gzip)")


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """Main entry point. Builds UBI cache and writes to S3."""
    overall_start = time.time()
    print(f"[START] UBI cache build triggered at "
          f"{datetime.utcnow().isoformat()}Z")

    # Step A: Configuration
    gl_mappings = load_gl_mappings()
    print(f"[CONFIG] Loaded {len(gl_mappings)} GL mapping rules")

    accounts = load_accounts_to_track()
    ubi_account_keys = build_ubi_account_keys(accounts)
    print(f"[CONFIG] Found {len(ubi_account_keys)} UBI account keys")

    # Step B: Exclusion hashes
    excluded_hashes = load_exclusion_hashes()

    # Step C: Stage 8 history
    last_ubi_periods = scan_stage8_history()

    # Step D: Unassigned bills
    bills = scan_unassigned_bills(
        ubi_account_keys, excluded_hashes,
        last_ubi_periods, gl_mappings, DAYS_BACK)

    # Step E: Filter options
    filter_options = compute_filter_options(bills)

    # Step F: Write to S3
    write_cache_to_s3(bills, filter_options)

    elapsed = time.time() - overall_start
    print(f"[DONE] Built cache with {len(bills)} bills in {elapsed:.1f}s")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "ok": True,
            "bills": len(bills),
            "accounts_with_history": len(last_ubi_periods),
            "exclusion_hashes": len(excluded_hashes),
            "elapsed_seconds": round(elapsed, 1),
        })
    }


if __name__ == "__main__":
    # For local testing
    result = lambda_handler({}, None)
    print(json.dumps(json.loads(result["body"]), indent=2))
