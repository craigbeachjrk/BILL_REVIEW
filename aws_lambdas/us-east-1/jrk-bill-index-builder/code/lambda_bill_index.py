"""
Bill Index Builder Lambda — builds the completion tracker bill index.

Scans S3 stages (S6, S7, S8, S9, S99), reads the first JSON record from each
JSONL file to extract propertyId/accountNumber/dates, and writes a compressed
index to S3 for the AppRunner app to consume.

Trigger: EventBridge schedule (daily) or manual invoke from app.
Typical runtime: 5-15 minutes depending on file count.
"""
import os
import json
import gzip
import time
import re
import datetime as dt
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config as BotoConfig

_boto_cfg = BotoConfig(max_pool_connections=50, retries={"max_attempts": 2, "mode": "adaptive"})
s3 = boto3.client("s3", config=_boto_cfg)

# Configuration
BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
CONFIG_BUCKET = os.getenv("CONFIG_BUCKET", "jrk-analytics-billing")
CONFIG_PREFIX = os.getenv("CONFIG_PREFIX", "Bill_Parser_Config/")
BILL_INDEX_CACHE_KEY = os.getenv("BILL_INDEX_CACHE_KEY", CONFIG_PREFIX + "bill_index_cache.json.gz")
COMPLETION_TRACKER_CACHE_KEY = os.getenv("COMPLETION_TRACKER_CACHE_KEY", CONFIG_PREFIX + "completion_tracker_cache.json.gz")

# S3 stage prefixes
STAGE6_PREFIX = os.getenv("STAGE6_PREFIX", "Bill_Parser_6_PreEntrata_Submission/")
POST_ENTRATA_PREFIX = os.getenv("POST_ENTRATA_PREFIX", "Bill_Parser_7_PostEntrata_Submission/")
UBI_ASSIGNED_PREFIX = os.getenv("UBI_ASSIGNED_PREFIX", "Bill_Parser_8_UBI_Assigned/")
FLAGGED_REVIEW_PREFIX = os.getenv("FLAGGED_REVIEW_PREFIX", "Bill_Parser_9_Flagged_Review/")
HIST_ARCHIVE_PREFIX = os.getenv("HIST_ARCHIVE_PREFIX", "Bill_Parser_99_Historical Archive/")

# DynamoDB for account config
DDB_CONFIG_TABLE = os.getenv("DDB_CONFIG_TABLE", "jrk-bill-config")
ddb = boto3.resource("dynamodb")
config_table = ddb.Table(DDB_CONFIG_TABLE)


def _parse_date_any(s: str):
    """Parse date string in various formats."""
    if not s:
        return None
    s = str(s).strip()
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s, f).date()
        except Exception:
            pass
    return None


def _normalize_account_number(acct: str) -> str:
    """Normalize account number: strip non-alphanumeric, lowercase, strip leading zeros."""
    cleaned = re.sub(r'[^A-Za-z0-9]', '', str(acct or "")).lower()
    cleaned = cleaned.lstrip('0') or '0'
    return cleaned


def _get_accounts_to_track() -> list:
    """Load tracked accounts from DynamoDB config."""
    try:
        resp = config_table.get_item(Key={"config_key": "accounts-to-track"})
        item = resp.get("Item")
        if item and "config_value" in item:
            val = item["config_value"]
            if isinstance(val, str):
                return json.loads(val)
            return val
    except Exception as e:
        print(f"[BILL INDEX] Failed to load accounts config: {e}")
    return []


def _iter_stage_keys(prefix_root: str, months: list) -> list:
    """List all JSONL S3 keys for given stage prefix across specified months."""
    seen = set()
    keys = []
    for md in months:
        y, m = md.year, md.month
        prefixes = [
            f"{prefix_root}yyyy={y}/mm={m:02d}/",
            f"{prefix_root}{y}/{m:02d}/",
        ]
        for p in prefixes:
            try:
                paginator = s3.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=BUCKET, Prefix=p):
                    for obj in page.get("Contents", []) or []:
                        k = obj.get("Key", "")
                        if k and k not in seen and k.endswith(".jsonl"):
                            seen.add(k)
                            keys.append(k)
            except Exception:
                pass
    return keys


_FIELDS_NEEDED = {
    "EnrichedPropertyID", "propertyId", "PropertyID", "Property ID",
    "Account Number", "accountNumber", "AccountNumber",
    "Bill Date", "billDate",
    "Bill Period Start", "billPeriodStart",
    "Bill Period End", "billPeriodEnd",
}


def _read_first_record(key: str):
    """Read first JSON line from a JSONL, extract only the fields we need.
    Uses Range requests, escalating: 32KB -> 512KB -> full read.
    Returns a small dict (no base64 PDFs or other bulk data)."""
    for range_end in (32767, 524287, None):
        try:
            kwargs = {"Bucket": BUCKET, "Key": key}
            if range_end is not None:
                kwargs["Range"] = f"bytes=0-{range_end}"
            obj = s3.get_object(**kwargs)
            chunk = obj["Body"].read(524288 if range_end is None else range_end + 1)
            obj["Body"].close()
            first_line = chunk.decode("utf-8", errors="ignore").split("\n")[0].strip()
            if not first_line:
                return None
            rec = json.loads(first_line)
            # Extract only needed fields to save memory
            slim = {k: rec[k] for k in _FIELDS_NEEDED if k in rec}
            slim["__s3_key__"] = key
            return slim
        except json.JSONDecodeError:
            if range_end is None:
                return None
            continue
        except Exception:
            return None
    return None


def _read_first_records_batch(keys: list) -> list:
    """Read first record from each JSONL, batched with threading."""
    if not keys:
        return []
    BATCH_SIZE = 500
    WORKERS = 40
    out = []
    for batch_start in range(0, len(keys), BATCH_SIZE):
        batch_keys = keys[batch_start:batch_start + BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(_read_first_record, k): k for k in batch_keys}
            for future in as_completed(futures, timeout=120):
                try:
                    result = future.result(timeout=10)
                    if result:
                        out.append(result)
                except Exception:
                    pass
        done = batch_start + len(batch_keys)
        if done % 1000 < BATCH_SIZE or done >= len(keys):
            print(f"[BILL INDEX] Read {done}/{len(keys)} ({len(out)} records)")
    return out


def _build_bill_index():
    """Full bill index build: scan all stages, read file contents, write cache to S3."""
    start_time = time.time()
    today = dt.date.today()

    # Load existing cache for incremental update (skip if clear_cache requested)
    cached_index = None
    cached_keys_seen = set()
    if _CLEAR_CACHE:
        print("[BILL INDEX] clear_cache=true — full rescan from scratch")
    else:
        try:
            obj = s3.get_object(Bucket=CONFIG_BUCKET, Key=BILL_INDEX_CACHE_KEY)
            payload = json.loads(gzip.decompress(obj["Body"].read()))
            cached_index = {}
            for k_str, months_data in payload.get("index", {}).items():
                parts = k_str.split("|", 1)
                if len(parts) == 2:
                    cached_index[tuple(parts)] = {}
                    for m, info in months_data.items():
                        bd = _parse_date_any(info["bill_date"]) if info.get("bill_date") else None
                        cached_index[tuple(parts)][m] = {
                            "stage": info.get("stage"),
                            "bill_date": bd,
                            "service_days": info.get("service_days", 0),
                        }
            cached_keys_seen = set(payload.get("keys_seen", []))
            print(f"[BILL INDEX] Loaded cache: {len(cached_index)} accounts, {len(cached_keys_seen)} keys")
        except Exception as e:
            if "NoSuchKey" not in str(e):
                print(f"[BILL INDEX] Cache load failed: {e}")
            else:
                print("[BILL INDEX] No existing cache, full scan")

    # Build month list: 6 months back + 3 extra for prior-bill lookup + current
    months_back = 6
    scan_months = []
    ref = dt.date(today.year, today.month, 1)
    for i in range(months_back + 3, 0, -1):
        total_m = ref.year * 12 + ref.month - 1 - i
        scan_months.append(dt.date(total_m // 12, total_m % 12 + 1, 1))
    scan_months.append(ref)

    # Collect all keys by stage
    stages = [
        (STAGE6_PREFIX, "S6"),
        (POST_ENTRATA_PREFIX, "S7"),
        (UBI_ASSIGNED_PREFIX, "S8"),
        (FLAGGED_REVIEW_PREFIX, "S9"),
        (HIST_ARCHIVE_PREFIX, "S99"),
    ]
    all_keys_by_stage = []
    all_keys = set()
    for prefix, label in stages:
        keys = _iter_stage_keys(prefix, scan_months)
        all_keys_by_stage.append((keys, label))
        all_keys.update(keys)

    total_keys = len(all_keys)
    new_keys = all_keys - cached_keys_seen if cached_index is not None else all_keys
    print(f"[BILL INDEX] Total keys: {total_keys}, new: {len(new_keys)}")

    bills_found = cached_index if cached_index is not None else {}

    if new_keys:
        def _index_record(pid, acct, bill_date, ps, pe, stage_label):
            if not pid or not acct:
                return
            norm_acct = _normalize_account_number(acct)
            service_days = (pe - ps).days if ps and pe and pe > ps else 0

            # Map bill to ONE month using: bill_date -> service_end -> service_start
            ref_date = bill_date or pe or ps
            if not ref_date:
                return  # no usable date at all
            bill_month = f"{ref_date.month:02d}/{ref_date.year}"

            key = (pid, norm_acct)
            if key not in bills_found:
                bills_found[key] = {}
            if bill_month not in bills_found[key]:
                bills_found[key][bill_month] = {
                    "stage": stage_label, "bill_date": bill_date,
                    "service_days": service_days,
                    "service_start": ps, "service_end": pe,
                }

        for stage_keys_all, stage_label in all_keys_by_stage:
            stage_new = [k for k in stage_keys_all if k in new_keys]
            if not stage_new:
                continue
            print(f"[BILL INDEX] Reading {len(stage_new)} new {stage_label} files...")
            records = _read_first_records_batch(stage_new)
            print(f"[BILL INDEX] Read {len(records)}/{len(stage_new)} {stage_label} records")
            for rec in records:
                pid = str(rec.get("EnrichedPropertyID") or rec.get("propertyId")
                          or rec.get("PropertyID") or rec.get("Property ID") or "").strip()
                acct = str(rec.get("Account Number") or rec.get("accountNumber")
                           or rec.get("AccountNumber") or "").strip()
                bd_str = rec.get("Bill Date") or rec.get("billDate") or ""
                bill_date = _parse_date_any(str(bd_str))
                ps = _parse_date_any(str(rec.get("Bill Period Start") or rec.get("billPeriodStart") or ""))
                pe = _parse_date_any(str(rec.get("Bill Period End") or rec.get("billPeriodEnd") or ""))
                _index_record(pid, acct, bill_date, ps, pe, stage_label)

    # Persist to S3
    serializable_index = {}
    for (pid, norm_acct), months_data in bills_found.items():
        k_str = f"{pid}|{norm_acct}"
        serializable_index[k_str] = {}
        for m, info in months_data.items():
            serializable_index[k_str][m] = {
                "stage": info.get("stage"),
                "bill_date": info["bill_date"].isoformat() if info.get("bill_date") else None,
                "service_days": info.get("service_days", 0),
                "service_start": info["service_start"].isoformat() if info.get("service_start") else None,
                "service_end": info["service_end"].isoformat() if info.get("service_end") else None,
            }
    payload = json.dumps({
        "index": serializable_index,
        "keys_seen": list(all_keys),
        "built_at": datetime.utcnow().isoformat() + "Z",
        "total_keys": total_keys,
        "new_keys": len(new_keys),
        "accounts": len(bills_found),
    }).encode("utf-8")
    compressed = gzip.compress(payload)
    s3.put_object(Bucket=CONFIG_BUCKET, Key=BILL_INDEX_CACHE_KEY, Body=compressed, ContentType="application/gzip")

    elapsed = time.time() - start_time
    summary = f"{len(bills_found)} accounts, {total_keys} keys ({len(new_keys)} new), {elapsed:.1f}s"
    print(f"[BILL INDEX] Done: {summary}")
    return {
        "accounts": len(bills_found),
        "total_keys": total_keys,
        "new_keys": len(new_keys),
        "elapsed_seconds": round(elapsed, 1),
        "cache_bytes": len(compressed),
    }


_CLEAR_CACHE = False

def handler(event, context):
    """Lambda handler — invoked by EventBridge schedule or manual trigger."""
    global _CLEAR_CACHE
    _CLEAR_CACHE = bool(event.get("clear_cache", False))
    print(f"[BILL INDEX] Lambda invoked: {json.dumps(event)}")
    try:
        result = _build_bill_index()
        print(f"[BILL INDEX] Success: {json.dumps(result)}")
        return {"statusCode": 200, "body": result}
    except Exception as e:
        print(f"[BILL INDEX] Failed: {e}")
        import traceback
        traceback.print_exc()
        return {"statusCode": 500, "body": {"error": str(e)}}
