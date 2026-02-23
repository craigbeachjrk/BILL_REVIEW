import os
import json
import time
import argparse
from typing import List, Dict, Any

import boto3
import requests
from botocore.exceptions import NoCredentialsError, ClientError
import gzip
import io
import gzip
import io

"""
Example: Entrata getVendors call using API key from CLI, environment, or AWS Secrets Manager.

Priority of credentials: CLI args > environment variables > Secrets Manager.

Environment variables (if using env):
- ENTRATA_ORG   (e.g., jrkpropertyholdingsentratacore)
- ENTRATA_BASE  (e.g., https://apis.entrata.com/ext/orgs)
- ENTRATA_API_KEY
- ENTRATA_TIMEOUT (optional, default 30)

Secrets Manager (if using SM):
- Secret name (env ENTRATA_CORE_SECRET_NAME, default jrk/entrata_core) JSON value:
  {
    "org": "jrkpropertyholdingsentratacore",
    "base_url": "https://apis.entrata.com/ext/orgs",
    "api_key": "REDACTED",
    "timeout_seconds": 30
  }
Requires AWS region (defaults to us-east-1) and credentials.

Usage examples:
  python entrata_get_vendors_example.py --org jrkpropertyholdingsentratacore --base https://apis.entrata.com/ext/orgs --api-key %ENTRATA_API_KEY% --vendor-ids 294342
  python entrata_get_vendors_example.py --vendor-codes ABC123,XYZ
"""


def load_entrata_secret() -> Dict[str, Any]:
    # 1) Local JSON file next to app: bill_review_app\secrets\entrata_core.json
    try:
        base_dir = os.path.dirname(os.path.dirname(__file__))
        local_path = os.path.join(base_dir, "secrets", "entrata_core.json")
        if os.path.isfile(local_path):
            with open(local_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            org = str(data.get("org") or "").strip()
            base_url = str(data.get("base_url") or "").strip().rstrip("/")
            api_key = str(data.get("api_key") or "").strip()
            timeout = int(data.get("timeout_seconds") or 30)
            if org and base_url and api_key:
                return {"org": org, "base_url": base_url, "api_key": api_key, "timeout": int(timeout)}
    except Exception:
        pass

    # 2) Local .env file fallback: GL_DETAIL_API_PULL\.env for ENTRATA_API_KEY
    try:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "GL_DETAIL_API_PULL", ".env")
        if os.path.isfile(env_path):
            api_key = ""
            with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("ENTRATA_API_KEY"):
                        # supports ENTRATA_API_KEY="value" or ENTRATA_API_KEY=value
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            api_key = parts[1].strip().strip('"')
                            break
            if api_key:
                org = "jrkpropertyholdingsentratacore"
                base_url = "https://apis.entrata.com/ext/orgs"
                timeout = 30
                return {"org": org, "base_url": base_url, "api_key": api_key, "timeout": int(timeout)}
    except Exception:
        pass

    # 3) AWS Secrets Manager (requires local AWS credentials)
    secret_name = "jrk/entrata_core"
    region = "us-east-1"
    sm = boto3.client("secretsmanager", region_name=region)
    resp = sm.get_secret_value(SecretId=secret_name)
    raw = resp.get("SecretString") or ""
    data = json.loads(raw) if raw else {}
    org = str(data.get("org") or "").strip()
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    api_key = str(data.get("api_key") or "").strip()
    timeout = int(data.get("timeout_seconds") or 30)
    if not (org and base_url and api_key):
        raise RuntimeError("Entrata secret missing one of: org, base_url, api_key")
    return {"org": org, "base_url": base_url, "api_key": api_key, "timeout": int(timeout)}


def build_request_body(vendor_ids: List[str], vendor_codes: List[str]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if vendor_ids:
        # Entrata example expects a comma-separated string
        params["vendorIds"] = ",".join([str(v).strip() for v in vendor_ids if str(v).strip()])
    if vendor_codes:
        params["vendorCodes"] = ",".join([str(v).strip() for v in vendor_codes if str(v).strip()])
    body = {
        "auth": {"type": "apikey"},
        "requestId": str(int(time.time() * 1000)),
        "method": {
            "name": "getVendors",
            "params": params,
        },
    }
    return body


def s3_find_latest_dim_vendor_key() -> str:
    s3 = boto3.client("s3", region_name="us-east-1")
    bucket = "jrk-analytics-billing"
    prefix = "Bill_Parser_Enrichment/exports/dim_vendor/"
    token = None
    best_key = None
    best_ts = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
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
        raise RuntimeError("No dim_vendor data.json(.gz) found in S3")
    return best_key


def s3_read_text(bucket: str, key: str) -> str:
    s3 = boto3.client("s3", region_name="us-east-1")
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
    ids: List[str] = []
    for r in items:
        cand = (
            r.get("VENDOR_ID")
            or r.get("vendor_id")
            or r.get("Vendor ID")
            or r.get("id")
            or r.get("vendorId")
        )
        if cand is None:
            for k, v in r.items():
                lk = str(k).lower()
                if "vendor" in lk and "id" in lk:
                    cand = v
                    break
        if cand is None:
            continue
        s = str(cand).strip()
        if s and s not in ids:
            ids.append(s)
    return ids


def local_read_vendor_list() -> List[str]:
    try:
        base_dir = os.path.dirname(os.path.dirname(__file__))
        txt_path = os.path.join(base_dir, "secrets", "entrata_vendors.txt")
        if os.path.isfile(txt_path):
            raw = open(txt_path, "r", encoding="utf-8", errors="ignore").read()
            parts = [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]
            return parts
    except Exception:
        pass
    return []


def local_read_dim_vendor_snapshot() -> List[str]:
    try:
        base_dir = os.path.dirname(os.path.dirname(__file__))
        snap_gz = os.path.join(base_dir, "secrets", "dim_vendor_snapshot.json.gz")
        snap_json = os.path.join(base_dir, "secrets", "dim_vendor_snapshot.json")
        if os.path.isfile(snap_gz):
            with open(snap_gz, "rb") as f:
                raw = f.read()
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                    text = gz.read().decode("utf-8", errors="ignore")
            except Exception:
                text = raw.decode("utf-8", errors="ignore")
            return parse_vendor_ids(text)
        if os.path.isfile(snap_json):
            text = open(snap_json, "r", encoding="utf-8", errors="ignore").read()
            return parse_vendor_ids(text)
    except Exception:
        pass
    return []

def s3_find_latest_dim_vendor_key() -> str:
    s3 = boto3.client("s3", region_name="us-east-1")
    bucket = "jrk-analytics-billing"
    prefix = "Bill_Parser_Enrichment/exports/dim_vendor/"
    token = None
    best_key = None
    best_ts = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
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
        raise RuntimeError("No dim_vendor data.json(.gz) found in S3")
    return best_key


def s3_read_text(bucket: str, key: str) -> str:
    s3 = boto3.client("s3", region_name="us-east-1")
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
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            items = parsed
        else:
            items = parsed.get("records") if isinstance(parsed, dict) else []
            if not isinstance(items, list):
                items = []
    except Exception:
        # JSONL fallback
        items = []
        for ln in payload.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                items.append(json.loads(ln))
            except Exception:
                continue
    ids = []
    for r in items:
        cand = (
            r.get("VENDOR_ID")
            or r.get("vendor_id")
            or r.get("Vendor ID")
            or r.get("id")
            or r.get("vendorId")
        )
        if cand is None:
            # heuristic
            for k, v in r.items():
                lk = str(k).lower()
                if "vendor" in lk and "id" in lk:
                    cand = v
                    break
        if cand is None:
            continue
        s = str(cand).strip()
        if s and s not in ids:
            ids.append(s)
    return ids


def call_get_vendors(secret: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{secret['base_url']}/{secret['org']}/v1/vendors"
    headers = {
        "X-API-Key": secret["api_key"],
        "X-Send-Pagination-Links": "0",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=body, timeout=secret["timeout"])
    try:
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Entrata getVendors HTTP {r.status_code}: {r.text}") from e
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def extract_vendor_locations(resp: Dict[str, Any]) -> Dict[str, List[str]]:
    """Best-effort extraction of vendor -> location IDs from Entrata response.
    The exact shape varies by tenant/version; we attempt common patterns.
    Returns mapping: vendorId -> [locationIds]
    """
    out: Dict[str, List[str]] = {}
    # Common response structure: { response: { result: { vendors: [ { vendorId, locations: [ { locationId } ] } ] } } }
    try:
        vendors = (
            ((resp or {}).get("response") or {}).get("result") or {}
        ).get("vendors") or []
        for v in vendors:
            vid = str(v.get("vendorId") or v.get("id") or "").strip()
            locs = []
            for loc in (v.get("locations") or []):
                lid = str(loc.get("locationId") or loc.get("id") or "").strip()
                if lid:
                    locs.append(lid)
            if vid:
                out[vid] = locs
    except Exception:
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description="Call Entrata getVendors and print vendor location IDs")
    ap.add_argument("--vendor-ids", type=str, default="", help="Comma-separated vendor IDs")
    ap.add_argument("--vendor-codes", type=str, default="", help="Comma-separated vendor codes")
    args = ap.parse_args()

    vendor_ids = [s.strip() for s in args.vendor_ids.split(",") if s.strip()] if args.vendor_ids else []
    vendor_codes = [s.strip() for s in args.vendor_codes.split(",") if s.strip()] if args.vendor_codes else []

    secret = load_entrata_secret()
    # If no filters provided, auto-populate vendorIds from latest dim_vendor in S3
    if not vendor_ids and not vendor_codes:
        # 0) Local: explicit vendor list file
        vendor_ids = local_read_vendor_list()
        if vendor_ids:
            sample_n = int(os.getenv("ENTRATA_SAMPLE_N") or 500)
            vendor_ids = vendor_ids[:sample_n]
    if not vendor_ids and not vendor_codes:
        # 1) Local: snapshot file
        vendor_ids = local_read_dim_vendor_snapshot()
        if vendor_ids:
            sample_n = int(os.getenv("ENTRATA_SAMPLE_N") or 500)
            vendor_ids = vendor_ids[:sample_n]
    if not vendor_ids and not vendor_codes:
        # 2) S3: latest dim_vendor
        try:
            key = s3_find_latest_dim_vendor_key()
            payload = s3_read_text("jrk-analytics-billing", key)
            all_ids = parse_vendor_ids(payload)
            sample_n = int(os.getenv("ENTRATA_SAMPLE_N") or 500)
            vendor_ids = all_ids[:sample_n]
        except Exception as e:
            print(json.dumps({"note": "unable_to_fetch_ids_from_s3", "error": str(e)}))
    # If no filters provided, pull a small sample of vendorIds from S3 dim_vendor
    if not vendor_ids and not vendor_codes:
        try:
            key = s3_find_latest_dim_vendor_key()
            payload = s3_read_text("jrk-analytics-billing", key)
            all_ids = parse_vendor_ids(payload)
            sample_n = int(os.getenv("ENTRATA_SAMPLE_N") or 5)
            vendor_ids = all_ids[:sample_n]
        except Exception as e:
            print(json.dumps({"note": "unable_to_fetch_ids_from_s3", "error": str(e)}))

    body = build_request_body(vendor_ids, vendor_codes)
    resp = call_get_vendors(secret, body)

    print("=== Request ===")
    print(json.dumps(body, indent=2))
    print("\n=== Response (truncated preview) ===")
    print(json.dumps(resp, indent=2)[:10000])

    locs = extract_vendor_locations(resp)
    if locs:
        print("\n=== Vendor -> Location IDs ===")
        print(json.dumps(locs, indent=2))
    else:
        print("\n(No vendor location IDs extracted; inspect full response above.)")


if __name__ == "__main__":
    main()
