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
    raise RuntimeError("Provide Entrata credentials via env or Secrets Manager")


def find_latest_dim_vendor_key() -> str:
    s3 = _s3_client()
    resp = s3.list_objects_v2(Bucket=SRC_BUCKET, Prefix=DIM_VENDOR_PREFIX)
    contents = resp.get("Contents") or []
    if not contents:
        raise RuntimeError(f"No objects under s3://{SRC_BUCKET}/{DIM_VENDOR_PREFIX}")
    # pick latest by LastModified and that ends with data.json or data.json.gz
    cand = [o for o in contents if o.get("Key", "").endswith(("data.json", "data.json.gz"))]
    if not cand:
        # fallback: latest overall
        latest = max(contents, key=lambda x: x.get("LastModified"))
        return latest.get("Key")
    latest = max(cand, key=lambda x: x.get("LastModified"))
    return latest.get("Key")


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
        vid = (
            r.get("VENDOR_ID")
            or r.get("vendor_id")
            or r.get("Vendor ID")
            or r.get("id")
            or r.get("vendorId")
        )
        if vid is None:
            continue
        s = str(vid).strip()
        if s:
            ids.add(s)
    return sorted(list(ids))


def call_get_vendors(creds: Dict[str, Any], vendor_ids_csv: str, vendor_codes_csv: str = "") -> Dict[str, Any]:
    url = f"{creds['base_url']}/{creds['org']}/v1/vendors"
    headers = {
        "X-API-Key": creds["api_key"],
        "X-Send-Pagination-Links": "0",
        "Content-Type": "application/json",
    }
    body = {
        "auth": {"type": "apikey"},
        "requestId": str(int(time.time() * 1000)),
        "method": {
            "name": "getVendors",
            "params": {}
        }
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
        vendors = (((resp or {}).get("response") or {}).get("result") or {}).get("vendors") or []
        for v in vendors:
            vid = str(v.get("vendorId") or v.get("id") or "").strip()
            vcode = str(v.get("vendorCode") or v.get("code") or "").strip()
            name = str(v.get("name") or v.get("vendorName") or "").strip()
            locs = []
            for loc in (v.get("locations") or []):
                lid = str(loc.get("locationId") or loc.get("id") or "").strip()
                if lid:
                    locs.append(lid)
            if vid:
                out.append({"vendorId": vid, "vendorCode": vcode, "name": name, "locations": locs})
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


def run() -> Dict[str, Any]:
    creds = load_entrata_creds()
    vendor_ids: List[str] = []
    try:
        latest_key = find_latest_dim_vendor_key()
        payload = read_s3_text(SRC_BUCKET, latest_key)
        vendor_ids = parse_vendor_ids(payload)
    except Exception:
        vendor_ids = []

    aggregated: Dict[str, Dict[str, Any]] = {}
    if vendor_ids:
        # Chunk into reasonable comma-separated lists (e.g., 100 per call)
        chunk_size = int(os.getenv("VENDOR_BATCH", "100"))
        chunks = [vendor_ids[i:i+chunk_size] for i in range(0, len(vendor_ids), chunk_size)]
        for idx, chunk in enumerate(chunks, start=1):
            csv_ids = ",".join(chunk)
            resp = call_get_vendors(creds, vendor_ids_csv=csv_ids)
            items = extract_vendors_locations(resp)
            for it in items:
                aggregated[it["vendorId"]] = it
            time.sleep(0.2)  # be polite
    else:
        # Fallback: attempt to fetch without filters; some tenants return a default page
        resp = call_get_vendors(creds, vendor_ids_csv="")
        items = extract_vendors_locations(resp)
        for it in items:
            aggregated[it["vendorId"]] = it

    # Convert aggregated to list
    final_list = list(aggregated.values())

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
