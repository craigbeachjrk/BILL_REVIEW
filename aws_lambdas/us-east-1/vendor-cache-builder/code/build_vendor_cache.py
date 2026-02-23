import os
import json
import io
import gzip
import time
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

import boto3
from botocore.exceptions import NoCredentialsError, ClientError
import requests

# Configuration
AWS_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
# Source enrichment bucket/prefix (where dim_vendor lives)
SRC_BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
EXPORTS_ROOT = os.getenv("ENRICH_PREFIX", "Bill_Parser_Enrichment/exports/")
DIM_VENDOR_PREFIX = os.getenv("DIM_VENDOR_PREFIX", EXPORTS_ROOT + "dim_vendor/")
# Target cache bucket
DEST_BUCKET = os.getenv("DEST_BUCKET", "api_vendor")
DEST_PREFIX = os.getenv("DEST_PREFIX", "vendors/")
# Entrata creds: prefer env, else Secrets Manager secret name
ENTRATA_SECRET_NAME = os.getenv("ENTRATA_CORE_SECRET_NAME", "jrk/entrata_core")
DEBUG_CAPTURE = (os.getenv("DEBUG_CAPTURE") or "").lower() in ("1", "true", "yes")
DIAG_BATCHES = int(os.getenv("DIAG_BATCHES") or 0)
# Optional externally provided run prefix, else will be generated in run()
DEBUG_PREFIX = os.getenv("DEBUG_PREFIX") or ""


def _s3_client():
    return boto3.client("s3", region_name=AWS_REGION)


def _secrets_client():
    return boto3.client("secretsmanager", region_name=AWS_REGION)


def load_entrata_creds() -> Dict[str, Any]:
    org = (os.getenv("ENTRATA_ORG") or "").strip()
    base = (os.getenv("ENTRATA_BASE") or "").strip().rstrip('/')
    api_key = (os.getenv("ENTRATA_API_KEY") or "").strip()
    timeout = int(os.getenv("ENTRATA_TIMEOUT") or 30)
    if org and base and api_key:
        return {"org": org, "base_url": base, "api_key": api_key, "timeout": timeout}
    try:
        sm = _secrets_client()
        resp = sm.get_secret_value(SecretId=ENTRATA_SECRET_NAME)
        raw = resp.get("SecretString") or ""
        data = json.loads(raw) if raw else {}
        org = str(data.get("org") or "").strip()
        base = str(data.get("base_url") or "").strip().rstrip('/')
        api_key = str(data.get("api_key") or "").strip()
        timeout = int(data.get("timeout_seconds") or timeout)
        if not (org and base and api_key):
            raise RuntimeError("Entrata secret missing org/base_url/api_key")
        return {"org": org, "base_url": base, "api_key": api_key, "timeout": timeout}
    except (NoCredentialsError, ClientError):
        pass


def log_resp_shape(label: str, resp: Any) -> None:
    try:
        if isinstance(resp, dict):
            response = (resp or {}).get("response")
            result = (response or {}).get("result") if isinstance(response, dict) else None
            print(json.dumps({
                "diag": label,
                "type": "json",
                "has_response": isinstance(response, dict),
                "has_result": isinstance(result, (dict, list)),
                "response_keys": list(response.keys()) if isinstance(response, dict) else None,
                "result_type": type(result).__name__ if result is not None else None,
                "result_keys": list(result.keys()) if isinstance(result, dict) else None
            }))
            # Short preview
            preview = json.dumps(resp)[:1000]
            print(json.dumps({"diag": f"{label}_preview", "body": preview}))
        else:
            txt = str(resp)
            print(json.dumps({"diag": label, "type": "text", "len": len(txt), "preview": txt[:1000]}))
    except Exception as e:
        print(json.dumps({"diag": label, "error": str(e)}))


def find_latest_dim_vendor_key() -> str:
    s3 = _s3_client()
    # List only under dim_vendor/ and collect candidate data files
    token = None
    best_key = None
    best_ts = None
    while True:
        kwargs = {"Bucket": SRC_BUCKET, "Prefix": DIM_VENDOR_PREFIX}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        contents = resp.get("Contents") or []
        for o in contents:
            k = o.get("Key", "")
            if not (k.endswith("data.json") or k.endswith("data.json.gz")):
                continue
            lm = o.get("LastModified")
            if best_ts is None or (lm and lm > best_ts):
                best_ts = lm
                best_key = k
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    if not best_key:
        raise RuntimeError(f"No data.json(.gz) under s3://{SRC_BUCKET}/{DIM_VENDOR_PREFIX}")
    print(json.dumps({"msg": "selected_dim_vendor_key", "key": best_key}))
    return best_key


def read_s3_text(bucket: str, key: str) -> str:
    s3 = _s3_client()
    obj = s3.get_object(Bucket=bucket, Key=key)
    raw = obj["Body"].read()
    if key.lower().endswith(".gz"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                return gz.read().decode("utf-8", errors="ignore")
        except Exception:
            return raw.decode("utf-8", errors="ignore")
    return raw.decode("utf-8", errors="ignore")


def parse_vendor_ids(payload: str) -> List[str]:
    # supports JSON array, object, or JSONL
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            items = parsed
        else:
            # object: may be { records: [...] } or similar
            items = parsed.get("records") if isinstance(parsed, dict) else []
            if not isinstance(items, list):
                items = []
    except Exception:
        # JSONL
        items = []
        for ln in payload.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                items.append(json.loads(ln))
            except Exception:
                continue
    ids = set()
    for r in items:
        cand = (
            r.get("VENDOR_ID")
            or r.get("vendor_id")
            or r.get("Vendor ID")
            or r.get("id")
            or r.get("vendorId")
        )
        if cand is None:
            # heuristic: any key that contains vendor and id
            for k, v in r.items():
                lk = str(k).lower()
                if "vendor" in lk and "id" in lk:
                    cand = v
                    break
        if cand is None:
            continue
        s = str(cand).strip()
        if s:
            ids.add(s)
    print(json.dumps({"msg": "vendor_id_count", "count": len(ids)}))
    return sorted(list(ids))


def parse_vendor_codes(payload: str) -> List[str]:
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            items = parsed
        else:
            items = parsed.get("records") if isinstance(parsed, dict) else []
            if not isinstance(items, list):
                items = []
    except Exception:
        items = []
        for ln in payload.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                items.append(json.loads(ln))
            except Exception:
                continue
    codes = set()
    for r in items:
        vc = (
            r.get("VENDOR_CODE")
            or r.get("vendor_code")
            or r.get("Vendor Code")
            or r.get("code")
            or r.get("vendorCode")
        )
        if vc is None:
            # heuristic: any key that contains vendor and code
            for k, v in r.items():
                lk = str(k).lower()
                if "vendor" in lk and "code" in lk:
                    vc = v
                    break
        if vc is None:
            continue
        s = str(vc).strip()
        if s:
            codes.add(s)
    print(json.dumps({"msg": "vendor_code_count", "count": len(codes)}))
    return sorted(list(codes))


def parse_vendor_records(payload: str) -> List[Dict[str, Any]]:
    # Returns list of {id: str, code: str}
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            items = parsed
        else:
            items = parsed.get("records") if isinstance(parsed, dict) else []
            if not isinstance(items, list):
                items = []
    except Exception:
        items = []
        for ln in payload.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                items.append(json.loads(ln))
            except Exception:
                continue
    out: List[Dict[str, Any]] = []
    for r in items:
        vid = (
            r.get("VENDOR_ID") or r.get("vendor_id") or r.get("Vendor ID") or r.get("id") or r.get("vendorId")
        )
        vcode = (
            r.get("VENDOR_CODE") or r.get("vendor_code") or r.get("Vendor Code") or r.get("code") or r.get("vendorCode")
        )
        rec = {"id": str(vid).strip() if vid is not None else "", "code": str(vcode).strip() if vcode is not None else ""}
        if rec["id"] or rec["code"]:
            out.append(rec)
    return out


def _vendor_array_length_from_resp(resp: Any) -> int:
    try:
        if not isinstance(resp, dict):
            return 0
        response = resp.get("response") or {}
        result = response.get("result") or {}
        vendors_obj = None
        if isinstance(result, dict):
            vendors_obj = result.get("vendors") or result.get("vendorList") or (result.get("data") or {}).get("vendors") or result.get("items")
        if isinstance(vendors_obj, list):
            return len(vendors_obj)
        if isinstance(vendors_obj, dict):
            arr = vendors_obj.get("vendor") or vendors_obj.get("vendors") or []
            return len(arr) if isinstance(arr, list) else 0
        return 0
    except Exception:
        return 0

def _post_json(url: str, headers: Dict[str, str], body: Dict[str, Any], timeout: int) -> requests.Response:
    h = dict(headers)
    h["Content-Type"] = "application/json"
    return requests.post(url, headers=h, json=body, timeout=timeout)


def _post_form(url: str, headers: Dict[str, str], body: Dict[str, Any], timeout: int, field_name: str) -> requests.Response:
    # field_name: 'requestBody' or 'request'
    form = {field_name: json.dumps(body), "requestContentType": "APPLICATION/JSON; CHARSET=UTF-8"}
    return requests.post(url, headers=headers, data=form, timeout=timeout)


def call_get_vendors_all(creds: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{creds['base_url']}/{creds['org']}/v1/vendors"
    base_headers = {
        "X-API-Key": creds["api_key"],
        "X-Send-Pagination-Links": "1",
    }
    # Candidate bodies (no filters)
    base_body = {
        "auth": {"type": "apikey"},
        "requestId": str(int(time.time() * 1000)),
        "method": {"name": "getVendors", "params": {}}
    }
    bodies: List[Dict[str, Any]] = []
    bodies.append(base_body)
    b2 = json.loads(json.dumps(base_body)); b2["method"]["version"] = "1"; bodies.append(b2)
    b3 = json.loads(json.dumps(base_body)); b3["method"]["version"] = "r1"; bodies.append(b3)
    b4 = {"auth": {"type": "apikey"}, "requestId": base_body["requestId"], "method": "getVendors", "params": {}}; bodies.append(b4)

    attempts: List[Tuple[str, Dict[str, Any], str]] = []
    for b in bodies:
        attempts.append(("json", b, ""))
        attempts.append(("form", b, "requestBody"))
        attempts.append(("form", b, "request"))

    last_err = None
    for mode, body, field in attempts:
        try:
            if mode == "json":
                r = _post_json(url, base_headers, body, creds["timeout"])
            else:
                r = _post_form(url, base_headers, body, creds["timeout"], field)
            if r.status_code == 404 and "provided version" in r.text.lower():
                last_err = RuntimeError(r.text)
                continue
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"raw": r.text}
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"getVendors(all) failed after {len(attempts)} attempts: {last_err}")


def call_get_vendors_filtered(creds: Dict[str, Any], vendor_ids_csv: str = "", vendor_codes_csv: str = "") -> Dict[str, Any]:
    url = f"{creds['base_url']}/{creds['org']}/v1/vendors"
    headers = {
        "X-API-Key": creds["api_key"],
        "X-Send-Pagination-Links": "0",
        "Content-Type": "application/json",
    }
    body: Dict[str, Any] = {
        "auth": {"type": "apikey"},
        "requestId": str(int(time.time() * 1000)),
        "method": {"name": "getVendors", "params": {}}
    }
    if vendor_ids_csv:
        body["method"]["params"]["vendorIds"] = vendor_ids_csv
    if vendor_codes_csv:
        body["method"]["params"]["vendorCodes"] = vendor_codes_csv
    r = requests.post(url, headers=headers, json=body, timeout=creds["timeout"])
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def extract_vendors_locations(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        base = (resp or {})
        response = base.get("response") or {}
        result = response.get("result")
        vendors = []
        # Common shapes
        if isinstance(result, dict):
            vendors_obj = result.get("vendors") or result.get("vendorList") or (result.get("data") or {}).get("vendors") or result.get("items")
            if isinstance(vendors_obj, list):
                vendors = vendors_obj
            elif isinstance(vendors_obj, dict):
                # Entrata variant: { "vendors": { "vendor": [ ... ] } }
                vendors = vendors_obj.get("vendor") or vendors_obj.get("vendors") or []
            else:
                vendors = []
        elif isinstance(result, list):
            vendors = result
        # Fallback: sometimes vendor data may be directly under response
        if not vendors:
            vendors = response.get("vendors") or []
        for v in vendors:
            vid = str(v.get("vendorId") or v.get("id") or "").strip()
            vcode = str(v.get("vendorCode") or v.get("code") or "").strip()
            name = str(v.get("name") or v.get("vendorName") or "").strip()
            # Extract additional fields
            vendor_type_id = str(v.get("vendorTypeId") or "").strip()
            vendor_type = str(v.get("vendorType") or "").strip()
            status_type_id = str(v.get("statusTypeId") or "").strip()
            status = str(v.get("status") or "").strip()
            term_id = str(v.get("termId") or "").strip()
            term = str(v.get("term") or "").strip()
            external_id = str(v.get("externalId") or "").strip()
            # isConsolidated can be string "true"/"false" or boolean
            is_consolidated_raw = v.get("isConsolidated")
            if isinstance(is_consolidated_raw, bool):
                is_consolidated = is_consolidated_raw
            else:
                is_consolidated = str(is_consolidated_raw or "").lower() in ("true", "1", "t", "yes")

            # Filter: only include vendorTypeId == "1" (Standard) and statusTypeId == "1" (Active)
            if vendor_type_id != "1" or status_type_id != "1":
                continue

            # Extract location objects with all fields
            locs = []
            loc_container = v.get("locations")
            if isinstance(loc_container, list):
                loc_list = loc_container
            elif isinstance(loc_container, dict):
                # Entrata variant: { "locations": { "location": [ { "id": ... } ] } }
                loc_list = loc_container.get("location") or loc_container.get("locations") or []
            else:
                loc_list = []
            for loc in (loc_list or []):
                lid = str((loc or {}).get("locationId") or (loc or {}).get("id") or "").strip()
                lname = str((loc or {}).get("name") or "").strip()
                lcode = str((loc or {}).get("vendorCode") or "").strip()
                # isPrimary can be "t"/"f" or boolean
                is_primary_raw = (loc or {}).get("isPrimary")
                if isinstance(is_primary_raw, bool):
                    is_primary = is_primary_raw
                else:
                    is_primary = str(is_primary_raw or "").lower() in ("true", "1", "t", "yes")
                if lid:
                    locs.append({"id": lid, "name": lname, "vendorCode": lcode, "isPrimary": is_primary})
            if vid:
                # Get vendorCode from primary location (or first location) since vendor-level is often empty
                primary_loc = next((loc for loc in locs if loc.get("isPrimary")), None)
                loc_vendor_code = (primary_loc or locs[0] if locs else {}).get("vendorCode", "") or vcode
                out.append({
                    "vendorId": vid,
                    "vendorCode": loc_vendor_code,
                    "name": name,
                    "vendorTypeId": vendor_type_id,
                    "vendorType": vendor_type,
                    "term": term,
                    "termId": term_id,
                    "status": status,
                    "statusTypeId": status_type_id,
                    "isConsolidated": is_consolidated,
                    "externalId": external_id,
                    "locations": locs
                })
    except Exception:
        pass
    return out


def write_cache_to_s3(items: List[Dict[str, Any]], creds: Dict[str, Any]) -> Tuple[str, str]:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    hour_part = now.strftime("%Y-%m-%d-%H")
    doc = {"generated_at": ts, "org": creds.get("org"), "vendors": items}
    body = (json.dumps(doc, ensure_ascii=False) + "\n").encode("utf-8")
    key_dt = f"{DEST_PREFIX}dt={hour_part}/data.json"
    key_latest = f"{DEST_PREFIX}latest.json"
    s3 = _s3_client()
    s3.put_object(Bucket=DEST_BUCKET, Key=key_dt, Body=body, ContentType="application/json")
    s3.put_object(Bucket=DEST_BUCKET, Key=key_latest, Body=body, ContentType="application/json")
    return key_dt, key_latest


def dump_debug_to_s3(name: str, obj: Any) -> None:
    if not DEBUG_CAPTURE:
        return
    try:
        prefix = os.getenv("DEBUG_PREFIX") or datetime.now(timezone.utc).strftime("diag-%Y%m%dT%H%M%SZ")
        key = f"debug/{prefix}/{name}"
        body = (json.dumps(obj, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        _s3_client().put_object(Bucket=DEST_BUCKET, Key=key, Body=body, ContentType="application/json")
    except Exception:
        pass


def run() -> Dict[str, Any]:
    creds = load_entrata_creds()
    # Generate a stable debug prefix per run, if requested
    if DEBUG_CAPTURE and not os.getenv("DEBUG_PREFIX"):
        os.environ["DEBUG_PREFIX"] = datetime.now(timezone.utc).strftime("diag-%Y%m%dT%H%M%SZ")
    aggregated: Dict[str, Dict[str, Any]] = {}

    # Track success/failure for API outage protection
    batch_success_count = 0
    batch_error_count = 0
    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 5  # Abort if 5+ consecutive failures
    MAX_ERROR_RATE = 0.5  # Abort if >50% of batches fail

    # Gather vendor IDs (preferred) or codes from dim_vendor
    vendor_ids: List[str] = []
    vendor_codes: List[str] = []
    try:
        latest_key = find_latest_dim_vendor_key()
        payload = read_s3_text(SRC_BUCKET, latest_key)
        vendor_ids = parse_vendor_ids(payload)
        if not vendor_ids:
            vendor_codes = parse_vendor_codes(payload)
        records = parse_vendor_records(payload)
    except Exception:
        vendor_ids = []
        vendor_codes = []
        records = []

    print(json.dumps({"msg": "mode", "value": "filtered", "ids": len(vendor_ids), "codes": len(vendor_codes)}))

    if vendor_ids:
        # Chunk into reasonable comma-separated lists (e.g., 100 per call)
        chunk_size = int(os.getenv("VENDOR_BATCH", "100"))
        id_chunks = [vendor_ids[i:i+chunk_size] for i in range(0, len(vendor_ids), chunk_size)]
        for idx, chunk in enumerate(id_chunks, start=1):
            try:
                csv_ids = ",".join(chunk)
                print(json.dumps({"batch": idx, "mode": "ids", "count": len(chunk)}))
                # Capture request
                if DEBUG_CAPTURE and (idx == 1):
                    dump_debug_to_s3("batch-001-request.json", {
                        "url": f"{creds['base_url']}/{creds['org']}/v1/vendors",
                        "body": {
                            "auth": {"type": "apikey"},
                            "requestId": str(int(time.time() * 1000)),
                            "method": {"name": "getVendors", "params": {"vendorIds": csv_ids}}
                        }
                    })
                resp = call_get_vendors_filtered(creds, vendor_ids_csv=csv_ids)
                if DEBUG_CAPTURE and (idx == 1):
                    dump_debug_to_s3("batch-001-response.json", resp)
                    # also dump a plain text backup if present
                    try:
                        if isinstance(resp, dict) and "raw" in resp:
                            _ = dump_debug_to_s3("batch-001-response.txt", resp.get("raw") or "")
                    except Exception:
                        pass
                if idx == 1:
                    log_resp_shape("batch-001-response-shape", resp)
                    print(json.dumps({"batch": idx, "vendor_array_len": _vendor_array_length_from_resp(resp)}))
                items = extract_vendors_locations(resp)
                if idx == 1:
                    # sample first parsed vendor if available
                    sample = items[0] if items else None
                    print(json.dumps({"batch": idx, "items_len": len(items), "sample": sample}))
                if not items and records:
                    # fallback: try codes for the matching slice if available
                    slice_codes = []
                    # build a set for quick lookup
                    want = set(chunk)
                    for rec in records:
                        if rec.get("id") in want and rec.get("code"):
                            slice_codes.append(rec["code"])
                    if slice_codes:
                        csv_codes = ",".join(slice_codes)
                        print(json.dumps({"batch": idx, "mode": "codes_fallback", "count": len(slice_codes)}))
                        try:
                            resp2 = call_get_vendors_filtered(creds, vendor_ids_csv="", vendor_codes_csv=csv_codes)
                            if DEBUG_CAPTURE and (idx == 1):
                                dump_debug_to_s3("batch-001-codes-response.json", resp2)
                                try:
                                    if isinstance(resp2, dict) and "raw" in resp2:
                                        _ = dump_debug_to_s3("batch-001-codes-response.txt", resp2.get("raw") or "")
                                except Exception:
                                    pass
                            if idx == 1:
                                log_resp_shape("batch-001-codes-response-shape", resp2)
                            items = extract_vendors_locations(resp2)
                            if idx == 1:
                                print(json.dumps({"batch": idx, "items_len_after_codes": len(items)}))
                        except Exception as ex:
                            print(json.dumps({"batch": idx, "codes_fallback_error": str(ex)}))
                print(json.dumps({"batch": idx, "extracted": len(items)}))
                for it in items:
                    aggregated[it["vendorId"]] = it
                batch_success_count += 1
                consecutive_errors = 0  # Reset on success
                time.sleep(0.2)  # be polite
                if DIAG_BATCHES and idx >= DIAG_BATCHES:
                    print(json.dumps({"diag": "single_batch_exit", "batches_processed": idx, "aggregated": len(aggregated)}))
                    # Write partial cache for visibility and return
                    try:
                        key_dt, key_latest = write_cache_to_s3(list(aggregated.values()), creds)
                        return {"ok": True, "message": "Diagnostic single-batch exit", "bucket": DEST_BUCKET, "keys": [key_dt, key_latest], "vendors": len(aggregated)}
                    except Exception as e:
                        return {"ok": True, "message": "Diagnostic exit (no S3 write)", "vendors": len(aggregated), "error": str(e)}
            except Exception as ex:
                print(json.dumps({"batch": idx, "error": str(ex)}))
                batch_error_count += 1
                consecutive_errors += 1

                # Check for API outage conditions
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(json.dumps({
                        "msg": "ABORTING - API outage detected",
                        "reason": f"{consecutive_errors} consecutive errors",
                        "success": batch_success_count,
                        "errors": batch_error_count
                    }))
                    return {
                        "ok": False,
                        "message": "Aborted due to API outage - existing cache preserved",
                        "consecutive_errors": consecutive_errors,
                        "batch_success": batch_success_count,
                        "batch_errors": batch_error_count
                    }
    elif vendor_codes:
        chunk_size = int(os.getenv("VENDOR_BATCH", "100"))
        chunks = [vendor_codes[i:i+chunk_size] for i in range(0, len(vendor_codes), chunk_size)]
        for chunk in chunks:
            csv_codes = ",".join(chunk)
            resp = call_get_vendors_filtered(creds, vendor_codes_csv=csv_codes)
            items = extract_vendors_locations(resp)
            print(json.dumps({"batch": 1, "extracted": len(items)}))
            for it in items:
                aggregated[it["vendorId"]] = it
            time.sleep(0.2)
    else:
        print(json.dumps({"msg": "no_vendor_ids_or_codes_found"}))

    # Convert aggregated to list
    final_list = list(aggregated.values())

    # Final safety check: don't overwrite cache if too many errors occurred
    total_batches = batch_success_count + batch_error_count
    if total_batches > 0:
        error_rate = batch_error_count / total_batches
        if error_rate > MAX_ERROR_RATE:
            print(json.dumps({
                "msg": "ABORTING - High error rate",
                "error_rate": f"{error_rate:.1%}",
                "success": batch_success_count,
                "errors": batch_error_count,
                "vendors_fetched": len(final_list)
            }))
            return {
                "ok": False,
                "message": "Aborted due to high error rate - existing cache preserved",
                "error_rate": error_rate,
                "batch_success": batch_success_count,
                "batch_errors": batch_error_count,
                "vendors_fetched": len(final_list)
            }

    # Additional safety: don't write if we got zero vendors (complete failure)
    if len(final_list) == 0 and total_batches > 0:
        print(json.dumps({"msg": "ABORTING - No vendors fetched", "batches_attempted": total_batches}))
        return {
            "ok": False,
            "message": "Aborted - no vendors fetched, existing cache preserved",
            "batches_attempted": total_batches
        }

    # Try writing to S3; if not available, write to local file
    try:
        key_dt, key_latest = write_cache_to_s3(final_list, creds)
        return {
            "ok": True,
            "message": "Vendor cache written to S3",
            "bucket": DEST_BUCKET,
            "keys": [key_dt, key_latest],
            "vendors": len(final_list)
        }
    except (NoCredentialsError, ClientError):
        out_path = os.path.join(os.getcwd(), "vendor_cache.latest.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "org": creds.get("org"),
                "vendors": final_list
            }, f, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "message": "Wrote vendor cache locally (no S3 creds)",
            "path": out_path,
            "vendors": len(final_list)
        }


def main():
    res = run()
    print(json.dumps(res, indent=2))


def lambda_handler(event, context):
    try:
        res = run()
        return {"statusCode": 200, "body": json.dumps(res)}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"ok": False, "error": str(e)})}


if __name__ == "__main__":
    main()
