import os
import json
import boto3
import base64
import gzip
import io
import requests
from urllib.parse import unquote_plus
import difflib
import re
from datetime import datetime

s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")

BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
INPUT_PREFIX = os.getenv("INPUT_PREFIX", "Bill_Parser_3_Parsed_Outputs/")
OUTPUT_PREFIX = os.getenv("OUTPUT_PREFIX", "Bill_Parser_4_Enriched_Outputs/")
ENRICH_PREFIX = os.getenv("ENRICH_PREFIX", "Bill_Parser_Enrichment/exports/")
DIM_VENDOR_PREFIX = os.getenv("DIM_VENDOR_PREFIX", ENRICH_PREFIX + "dim_vendor/")
DIM_PROPERTY_PREFIX = os.getenv("DIM_PROPERTY_PREFIX", ENRICH_PREFIX + "dim_property/")
DIM_GL_PREFIX = os.getenv("DIM_GL_PREFIX", ENRICH_PREFIX + "dim_gl_account/")
MATCHER_SECRET_NAME = os.getenv("MATCHER_SECRET_NAME", "gemini/matcher-keys")
ENRICH_MODEL = os.getenv("ENRICH_MODEL", "gemini-1.5-flash")
PARSED_INPUTS_PREFIX = os.getenv("PARSED_INPUTS_PREFIX", "Bill_Parser_2_Parsed_Inputs/")
SHORTENER_URL = os.getenv("SHORTENER_URL", "")  # e.g., https://abc123.execute-api.us-east-1.amazonaws.com

_VENDOR_CANDIDATES = None
_PROPERTY_CANDIDATES = None
_VENDOR_NAME_INDEX = None  # normalized name -> candidate
_GL_CANDIDATES = None


def _list_latest_object(bucket: str, prefix: str):
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    contents = resp.get("Contents") or []
    if not contents:
        return None
    latest = max(contents, key=lambda x: x.get("LastModified"))
    return latest.get("Key")


def _load_jsonl_from_s3(bucket: str, key: str) -> list:
    """Load candidate records from S3 supporting JSONL and gzipped JSON/JSONL.
    - If key ends with .gz or ContentEncoding=gzip, we decompress first
    - If full body parses as a JSON array, return that array
    - Else treat as JSON Lines
    """
    obj = s3.get_object(Bucket=bucket, Key=key)
    raw = obj["Body"].read()
    # Detect gzip by extension or header
    is_gz = key.lower().endswith('.gz') or obj.get('ContentEncoding') == 'gzip'
    if is_gz:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                body = gz.read().decode('utf-8', errors='ignore')
        except Exception:
            body = raw.decode('utf-8', errors='ignore')
    else:
        body = raw.decode('utf-8', errors='ignore')

    # Try full JSON array first
    try:
        parsed = json.loads(body)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass

    # Fallback to JSON Lines
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


def _norm_name(s: str) -> str:
    return " ".join((s or "").lower().replace("&", "and").replace(",", " ").replace(".", " ").split())


def _ensure_candidates_loaded():
    global _VENDOR_CANDIDATES, _PROPERTY_CANDIDATES
    global _VENDOR_NAME_INDEX
    global _GL_CANDIDATES
    if _VENDOR_CANDIDATES is None:
        vk = _list_latest_object(BUCKET, DIM_VENDOR_PREFIX)
        _VENDOR_CANDIDATES = []
        if vk:
            records = _load_jsonl_from_s3(BUCKET, vk)
            print(json.dumps({"message": "Loaded vendor candidates", "key": vk, "count": len(records)}))
            _VENDOR_NAME_INDEX = {}
            for r in records:
                # Prefer exported VENDOR_NAME explicitly for matching
                name = (
                    r.get("VENDOR_NAME")
                    or r.get("vendor_name")
                    or r.get("Vendor Name")
                    or r.get("name")
                    or ""
                ).strip()
                if name:
                    vid = (
                        r.get("VENDOR_ID")
                        or r.get("vendor_id")
                        or name
                    )
                    cand = {"id": str(vid), "name": name}
                    _VENDOR_CANDIDATES.append(cand)
                    _VENDOR_NAME_INDEX[_norm_name(name)] = cand
    if _PROPERTY_CANDIDATES is None:
        pk = _list_latest_object(BUCKET, DIM_PROPERTY_PREFIX)
        _PROPERTY_CANDIDATES = []
        if pk:
            records = _load_jsonl_from_s3(BUCKET, pk)
            print(json.dumps({"message": "Loaded property candidates", "key": pk, "count": len(records)}))
            for r in records:
                name = (
                    r.get("property_name")
                    or r.get("Property Name")
                    or r.get("PROPERTY_NAME")
                    or r.get("name")
                    or ""
                ).strip()
                if name:
                    pid = (
                        r.get("property_id")
                        or r.get("PROPERTY_ID")
                        or name
                    )
                    state = (
                        r.get("GEO_STATE")
                        or r.get("STATE")
                        or r.get("state")
                        or ""
                    )
                    pcode = (
                        r.get("LOOKUP_CODE")
                        or r.get("PROPERTY_CODE")
                        or r.get("code")
                        or r.get("CODE")
                        or ""
                    )
                    _PROPERTY_CANDIDATES.append({"id": str(pid), "name": name, "state": str(state).strip(), "lookup_code": str(pcode).strip()})

    if _GL_CANDIDATES is None:
        gk = _list_latest_object(BUCKET, DIM_GL_PREFIX)
        _GL_CANDIDATES = []
        if gk:
            records = _load_jsonl_from_s3(BUCKET, gk)
            print(json.dumps({"message": "Loaded GL candidates", "key": gk, "count": len(records)}))
            for r in records:
                name = (r.get("NAME") or r.get("name") or "").strip()
                if not name:
                    continue
                gl_id = r.get("GL_ACCOUNT_ID") or r.get("id") or name
                acc_num = (
                    r.get("FORMATTED_GL_ACCOUNT_NUMBER")
                    or r.get("FORMATTED_ACCOUNT_NUMBER")
                    or r.get("GL_ACCOUNT_NUMBER")
                    or r.get("ACCOUNT_NUMBER")
                    or r.get("formattedGlAccountNumber")
                    or r.get("glAccountNumber")
                    or r.get("ACCOUNT_NO")
                    or r.get("GL_NUMBER")
                    or r.get("number")
                    or ""
                )
                _GL_CANDIDATES.append({
                    "id": str(gl_id),
                    "name": name,
                    "number": str(acc_num)
                })

def _find_gl_by_name_contains(words: list[str]) -> dict | None:
    if not _GL_CANDIDATES:
        return None
    for c in _GL_CANDIDATES:
        n = _norm_name(c.get("name", ""))
        if all(w in n for w in words):
            return c
    return None

def _fmt_period_mmddyyyy(bs: str, be: str) -> str:
    def norm_one(s: str) -> str:
        if not s:
            return ""
        s = str(s).strip()
        fmts = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"]
        for f in fmts:
            try:
                return datetime.strptime(s, f).strftime("%m/%d/%Y")
            except Exception:
                pass
        # digits fallback
        ds = re.sub(r"\D", "", s)
        if len(ds) == 8:
            try:
                return datetime.strptime(ds, "%Y%m%d").strftime("%m/%d/%Y")
            except Exception:
                try:
                    return datetime.strptime(ds, "%m%d%Y").strftime("%m/%d/%Y")
                except Exception:
                    pass
        return s
    bs2 = norm_one(bs)
    be2 = norm_one(be)
    if bs2 or be2:
        return f"{bs2}-{be2}".strip('-')
    return ""

# --- House/Vacant normalization (default House; Vacant only with clear unit/apt indicator) ---
_UNIT_HOV_RE = re.compile(r"\b(?:APT|UNIT|#|STE|SUITE|APARTMENT|BLDG)\s*\w+", re.I)
_VACANT_NAMES = {"VACANT ELECTRIC","VACANT GAS","VACANT WATER","VACANT SEWER","VACANT ACTIVATION"}
_HOUSE_BACKFILL = {
    "VACANT ELECTRIC": "HOUSE ELECTRIC",
    "VACANT GAS": "GAS",
    "VACANT WATER": "WATER",
    "VACANT SEWER": "SEWER",
    "VACANT ACTIVATION": "",
}

def _ensure_hov(rec: dict) -> None:
    hov = str(rec.get("House Or Vacant") or "").strip()
    gln = str(rec.get("EnrichedGLAccountName") or "").strip()
    util = str(rec.get("Utility Type") or "").strip()
    addr = str(rec.get("Service Address") or "").strip()
    has_unit = bool(_UNIT_HOV_RE.search(addr))
    gln_upper = gln.upper()
    is_vacant_gl = ("VACANT" in gln_upper)
    # Respect explicit parser choice when provided; otherwise default by unit pattern
    desired = hov if hov else ("Vacant" if has_unit else "House")
    if hov != desired:
        rec["House Or Vacant"] = desired
    if desired == "Vacant":
        if not is_vacant_gl:
            vac_try = f"Vacant {util}".strip()
            if vac_try.upper() in _VACANT_NAMES:
                rec["EnrichedGLAccountName"] = vac_try
            elif gln and not gln_upper.startswith("VACANT "):
                rec["EnrichedGLAccountName"] = "Vacant " + gln
    else:
        if is_vacant_gl:
            mapped = _HOUSE_BACKFILL.get(gln_upper, gln.replace("Vacant ", "").replace("VACANT ", "").strip())
            if mapped is not None:
                rec["EnrichedGLAccountName"] = mapped

def _street_num_and_letter(service_addr: str) -> tuple[str, str]:
    """Extract street number and first letter of street name from Service Address.
    Returns (num, letter) or ("", "").
    """
    if not service_addr:
        return "", ""
    m = re.search(r"(\d+)\s+([A-Za-z]+)", service_addr)
    if not m:
        return "", ""
    num = m.group(1)
    letter = m.group(2)[0].upper() if m.group(2) else ""
    return num, letter

def _find_unit(service_addr: str) -> str:
    if not service_addr:
        return ""
    # #123 or APT 123 or UNIT X12 or STE 3B or SUITE 200
    m = re.search(r"#\s*([A-Za-z0-9-]+)", service_addr, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\b(APT|APARTMENT|UNIT|STE|SUITE)\s*([A-Za-z0-9-]+)", service_addr, re.IGNORECASE)
    if m:
        return m.group(2)
    return ""

def _find_building(service_addr: str) -> str:
    if not service_addr:
        return ""
    m = re.search(r"\bBLD?G?\s*([A-Za-z0-9-]+)|\bBL\s*([A-Za-z0-9-]+)", service_addr, re.IGNORECASE)
    if m:
        return (m.group(1) or m.group(2) or "").upper()
    return ""

def _addr_num_and_street(service_addr: str) -> tuple[str, str]:
    """Extract street number and primary street name token from Service Address.
    Example: "333 FREMONT ST" -> ("333", "fremont")
    """
    if not service_addr:
        return "", ""
    m = re.search(r"(\d+)\s+([A-Za-z]+)", service_addr)
    if not m:
        return "", ""
    num = m.group(1)
    street = (m.group(2) or "").lower()
    return num, street

def _build_gl_desc(gl_number: str, rec: dict) -> str:
    bs = (rec.get("Bill Period Start") or rec.get("Bill Period Start ") or "").strip()
    be = (rec.get("Bill Period End") or rec.get("Bill Period End ") or "").strip()
    period = _fmt_period_mmddyyyy(bs, be)
    util = (rec.get("Utility Type") or "").strip().title()
    desc = (rec.get("Line Item Description") or "").strip()
    hov = (rec.get("House Or Vacant") or "").strip().title()
    svc = (rec.get("Service Address") or "").strip()
    usage = str(rec.get("Consumption Amount") or "").strip()
    num, letter = _street_num_and_letter(svc)
    unit = _find_unit(svc)
    bldg = _find_building(svc)

    def with_usage():
        return f"{period} {usage}".strip()

    # Map by GL account number
    g = (gl_number or "").strip()
    if g == "5706-0000":  # HOUSE ELEC
        tail = f"{num}{letter}"
        extra = f" BL {bldg}" if bldg else ""
        return f"{period} Hse Elec {tail}{extra}".strip()
    if g == "5710-0000":  # HOUSE GAS
        tail = f"{num}{letter}"
        extra = f" BL {bldg}" if bldg else ""
        return f"{period} Hse Gas {tail}{extra}".strip()
    if g == "5705-0000":  # VACANT ELEC
        tail = f"{num}{letter}"
        at = f"@{unit}" if unit else ""
        return f"{period} VE {tail}{at}".strip()
    if g == "5715-0000":  # VACANT GAS
        tail = f"{num}{letter}"
        at = f"@{unit}" if unit else ""
        return f"{period} VG {tail}{at}".strip()
    if g == "5708-1000":  # BUNDLED RESIDENT ELECTRIC - EPS (same as VE)
        tail = f"{num}{letter}"
        at = f"@{unit}" if unit else ""
        return f"{period} VE {tail}{at}".strip()
    if g == "5720-0000":  # HOUSE WATER
        return with_usage() if usage else period
    if g == "5730-0000":  # IRRIGATION
        return with_usage() if usage else period
    if g == "5727-0000":  # FIRELINE
        return period
    if g == "5721-0000":  # HOUSE SEWER (+Stormwater)
        return f"{period} Stormwater" if util == "Stormwater" else period
    if g == "5720-1000":  # VACANT WATER
        tail = f"{num}{letter}"
        at = f"@{unit}" if unit else ""
        return f"{period} VW {tail}{at}".strip()
    if g == "5721-1000":  # VACANT SEWER
        tail = f"{num}{letter}"
        at = f"@{unit}" if unit else ""
        return f"{period} VS {tail}{at}".strip()
    if g == "5731-0000":  # CITY FEE – UTILITY
        return period
    if g == "5550-0000":  # TRASH REMOVAL
        return f"{period} Trash Service".strip()
    if g == "5555-2000":  # BULK TRASH PICKUP
        return f"{period} Bulk Trash Service".strip()
    # default
    return period

def _to_gallons(amount_raw: str | float | int, uom_raw: str) -> float | None:
    """Convert a consumption value to gallons. Returns None if cannot parse.
    Supported UOMs: gallon/gal, kgal/thousand gallons, mgal (million gallons), ccf, cf/cubic feet.
    """
    try:
        amt = float(str(amount_raw).replace(",", "").strip())
    except Exception:
        return None
    u = (uom_raw or "").strip().lower()
    if not u:
        return amt  # assume already in gallons if no UOM
    # normalize
    if "ccf" in u:
        return amt * 748.0
    if u in ("cf",) or "cubic foot" in u or "cubic feet" in u or u == "ft3":
        return amt * 7.48052
    if u in ("kgal", "kgals") or "thousand" in u or "1,000" in u:
        return amt * 1000.0
    if u in ("mgal", "mgals") or "million" in u:
        return amt * 1000000.0
    if u in ("gallon", "gallons", "gal"):
        return amt

def _build_gl_desc_new(rec: dict) -> str:
    """Invoice Number| Service Address |Account Number|Line Item Account Number|Meter Number|Line Item Description|Meter Size|<Gallons> Gallons|Service Period Start - Service Period End"""
    inv = (rec.get("Invoice Number") or "").strip()
    svc = (rec.get("Service Address") or "").strip()
    acc = (rec.get("Account Number") or "").strip()
    line_acc = (rec.get("Line Item Account Number") or "").strip()
    meter = (rec.get("Meter Number") or "").strip()
    desc = (rec.get("Line Item Description") or "").strip()
    msize = (rec.get("Meter Size") or "").strip()
    gallons = _to_gallons(rec.get("Consumption Amount"), rec.get("Unit of Measure"))
    gallons_str = (f"{gallons:,.0f}" if isinstance(gallons, (int, float)) else "").strip()
    period = _fmt_period_mmddyyyy(rec.get("Bill Period Start") or "", rec.get("Bill Period End") or "")
    return " | ".join([
        inv, svc, acc, line_acc, meter, desc, msize, f"{gallons_str} Gallons".strip(), period
    ])

def _shorten_internal(long_url: str) -> str:
    """Call an internal shortener service if configured. Fallback to long_url."""
    if not long_url:
        return long_url
    base = (SHORTENER_URL or "").rstrip('/')
    if not base:
        return long_url
    try:
        payload = {"url": long_url, "ttl_seconds": 7*24*3600}
        r = requests.post(base + "/shorten", headers={"Content-Type": "application/json"}, data=json.dumps(payload), timeout=5)
        if r.status_code == 200:
            data = r.json()
            su = data.get("short_url") or data.get("url")
            if su:
                return su
    except Exception:
        pass
    return long_url

def _choose_gl_deterministic(rec: dict) -> dict | None:
    """Deterministic GL selection before Gemini, per business rules.
    - If Utility Type == Water and House Or Vacant == House:
        - If description mentions irrigation keywords -> Water Irrigation
        - Else if description mentions fire keywords -> Water Fire Line
        - Else -> default Water (non-irrigation/non-fire) account
    Returns candidate dict or None.
    """
    util = (rec.get("Utility Type") or "").strip().lower()
    # Business rule: Stormwater should be treated as Sewer for GL purposes
    if util == "stormwater":
        util = "sewer"
    hov = (rec.get("House Or Vacant") or "").strip().lower()
    desc = _norm_name(rec.get("Line Item Description") or "")
    if util == "water":
        # Explicit keywords first
        if any(k in desc for k in ["irrig", "sprinkler", "landscap", "lawn"]):
            cand = _find_gl_by_name_contains(["water", "irrigation"]) or _find_gl_by_name_contains(["irrigation"]) 
            if cand: return cand
        if any(k in desc for k in ["fire", "standpipe"]):
            cand = _find_gl_by_name_contains(["water", "fire"]) or _find_gl_by_name_contains(["fire"]) 
            if cand: return cand
        # Default for House Water: general Water
        if hov == "house" or not desc:
            # Prefer a GL whose name includes 'water' but not irrigation/fire
            water_candidates = [
                c for c in (_GL_CANDIDATES or [])
                if "water" in _norm_name(c.get("name", ""))
                and not any(b in _norm_name(c.get("name", "")) for b in ["irrigation", "fire", "vacant"])
            ]
            if water_candidates:
                return water_candidates[0]
        # Fallback still a water-related account if available
        cand = _find_gl_by_name_contains(["water"]) 
        if cand: return cand
    if util == "sewer":
        # Stormwater maps here too (handled above). Prefer sewer-related accounts.
        sewer_candidates = [
            c for c in (_GL_CANDIDATES or [])
            if "sewer" in _norm_name(c.get("name", ""))
            and not (hov != "vacant" and "vacant" in _norm_name(c.get("name", "")))
        ]
        if sewer_candidates:
            return sewer_candidates[0]
    if util == "gas":
        # Prefer gas-related accounts, non-vacant unless hov=vacant
        hov = (rec.get("House Or Vacant") or "").strip().lower()
        gas_candidates = [
            c for c in (_GL_CANDIDATES or [])
            if "gas" in _norm_name(c.get("name", ""))
            and not (hov != "vacant" and "vacant" in _norm_name(c.get("name", "")))
        ]
        if gas_candidates:
            return gas_candidates[0]
    return None


def _get_matcher_keys() -> list:
    resp = secrets.get_secret_value(SecretId=MATCHER_SECRET_NAME)
    raw = (resp.get("SecretString") or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("keys"), list):
            return [str(x).strip() for x in parsed["keys"] if str(x).strip()][:3]
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()][:3]
    except Exception:
        pass
    parts = [p.strip() for p in (raw.split(',') if ',' in raw else raw.splitlines())]
    return [p for p in parts if p][:3]


def _gemini_match(api_key: str, target: str, candidates: list, threshold: float = 0.0, max_alternates: int = 2, context: dict | None = None) -> dict:
    if not target or not candidates:
        return {}
    capped = candidates[:1000]
    payload_obj = {
        "task": "fuzzy_match",
        "threshold": threshold,
        "max_alternates": max_alternates,
        "target": target,
        "candidates": capped,
        "context": context or {},
        "instructions": (
            "You are an entity matcher. Use semantics, normalization, and geographic hints to select the closest candidate from the provided candidates list. "
            "Return ONLY valid JSON with keys: best {id: string, name: string, score: float} and alternates (array up to max_alternates). "
            "The 'best' MUST be one of the provided candidates (use the candidate's exact id and name). Do NOT return the target string as the best if it is not in candidates. "
            "Use context.city/state/zip/utility_type when provided to disambiguate. Always include a best guess."
        ),
    }
    prompt = json.dumps(payload_obj, ensure_ascii=False)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{ENRICH_MODEL}:generateContent?key={api_key}"
    req = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    r = requests.post(url, headers={"Content-Type": "application/json"}, data=json.dumps(req), timeout=60)
    if r.status_code != 200:
        return {}
    data = r.json()
    cands = (data.get("candidates") or [])
    if not cands:
        return {}
    parts = (((cands[0] or {}).get("content") or {}).get("parts") or [])
    text = "".join([p.get("text", "") for p in parts if isinstance(p, dict)]).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("best"):
            return obj
    except Exception:
        pass
    # If model didn't return parseable JSON with a best, return empty to allow deterministic fallback
    return {}


def _deterministic_best(target: str, candidates: list) -> dict:
    """Pick a best candidate deterministically using normalized fuzzy ratio."""
    def norm(s: str) -> str:
        return " ".join((s or "").lower().replace("&", "and").replace(",", " ").split())
    t = norm(target)
    best = None
    best_score = -1.0
    for c in candidates[:2000]:
        name = str(c.get("name", ""))
        score = difflib.SequenceMatcher(None, t, norm(name)).ratio()
        if score > best_score:
            best_score = score
            best = c
    if not best:
        best = candidates[0]
        best_score = 0.0
    return {
        "id": str(best.get("id")),
        "name": str(best.get("name")),
        "score": float(best_score)
    }


def _resolve_best_from_model(model_obj: dict, candidates: list, target: str) -> dict:
    if not isinstance(model_obj, dict):
        return _deterministic_best(target, candidates)
    best = model_obj.get("best") or {}
    bid = str(best.get("id", ""))
    bname = str(best.get("name", ""))
    # try match by id first, else by exact name
    for c in candidates:
        if bid and str(c.get("id")) == bid:
            return {"id": str(c.get("id")), "name": str(c.get("name")), "score": float(best.get("score", 0.0))}
    for c in candidates:
        if bname and str(c.get("name")) == bname:
            return {"id": str(c.get("id")), "name": str(c.get("name")), "score": float(best.get("score", 0.0))}
    # if model best is not in candidates, fallback deterministically
    return _deterministic_best(target, candidates)


def _enrich_lines(lines: list) -> list:
    _ensure_candidates_loaded()
    mkeys = _get_matcher_keys()
    out = []
    for ln in lines:
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        vendor = (rec.get("Vendor Name") or "").strip()
        prop = (rec.get("Bill To Name First Line") or "").strip()
        # address context for property matching
        ctx = {
            "city": (rec.get("Service City") or "").strip(),
            "state": (rec.get("Service State") or "").strip(),
            "zip": (rec.get("Service Zipcode") or "").strip(),
            "utility_type": (rec.get("Utility Type") or "").strip(),
        }
        api_key = mkeys[(hash(rec.get("Invoice Number", "")) or 0) % len(mkeys)] if mkeys else None
        if api_key:
            if vendor and _VENDOR_CANDIDATES:
                # 1) Prefer exact normalized match to export vendor name
                exact = _VENDOR_NAME_INDEX.get(_norm_name(vendor)) if _VENDOR_NAME_INDEX else None
                if exact:
                    best = {"id": exact.get("id"), "name": exact.get("name"), "score": 1.0}
                else:
                    vm = _gemini_match(api_key, vendor, _VENDOR_CANDIDATES, context={"utility_type": ctx.get("utility_type")})
                    best = _resolve_best_from_model(vm, _VENDOR_CANDIDATES, vendor)
                # Only emit VENDOR fields as requested
                rec["EnrichedVendorName"] = best.get("name")
                rec["EnrichedVendorID"] = best.get("id")
            if prop and _PROPERTY_CANDIDATES:
                # Filter property candidates by state first (if present)
                cand_list = _PROPERTY_CANDIDATES
                st = ctx.get("state", "").strip().upper()
                if st:
                    subset = [c for c in cand_list if str(c.get("state", "")).strip().upper() == st]
                    if subset:
                        cand_list = subset
                # Deterministic boost: if Service Address contains number+street, prefer candidates containing both
                try:
                    num, street = _addr_num_and_street(rec.get("Service Address"))
                    if num and street:
                        nn = num.strip()
                        ss = street.strip().lower()
                        narrowed = []
                        for c in cand_list:
                            nm = _norm_name(str(c.get("name", "")))
                            if nn in nm and ss in nm:
                                narrowed.append(c)
                        if narrowed:
                            # Choose deterministically among narrowed using our simple scorer
                            best = _deterministic_best(f"{nn} {ss}", narrowed)
                            rec["EnrichedProperty"] = best
                            rec["EnrichedPropertyName"] = best.get("name")
                            rec["EnrichedPropertyID"] = best.get("id")
                            # Skip model when high-confidence deterministic match found
                            pass
                        else:
                            pm = _gemini_match(api_key, prop, cand_list, context={**ctx, "addr_hint": f"{nn} {ss}"})
                            best = _resolve_best_from_model(pm, cand_list, prop)
                            rec["EnrichedProperty"] = best
                            rec["EnrichedPropertyName"] = best.get("name")
                            rec["EnrichedPropertyID"] = best.get("id")
                    else:
                        pm = _gemini_match(api_key, prop, cand_list, context=ctx)
                        best = _resolve_best_from_model(pm, cand_list, prop)
                        rec["EnrichedProperty"] = best
                        rec["EnrichedPropertyName"] = best.get("name")
                        rec["EnrichedPropertyID"] = best.get("id")
                except Exception:
                    pm = _gemini_match(api_key, prop, cand_list, context=ctx)
                    best = _resolve_best_from_model(pm, cand_list, prop)
                    rec["EnrichedProperty"] = best
                    rec["EnrichedPropertyName"] = best.get("name")
                    rec["EnrichedPropertyID"] = best.get("id")
                rec["EnrichedProperty"] = best
                rec["EnrichedPropertyName"] = best.get("name")
                rec["EnrichedPropertyID"] = best.get("id")

            # House/Vacant normalization before GL selection
            try:
                _ensure_hov(rec)
            except Exception:
                pass

            # GL assignment (required): deterministic business rules first, then model fallback; always emit number from same record
            if _GL_CANDIDATES:
                gbest = _choose_gl_deterministic(rec)
                if not gbest:
                    target = " | ".join([
                        (rec.get("House Or Vacant") or "").strip(),
                        (rec.get("Utility Type") or "").strip(),
                        (rec.get("Line Item Description") or "").strip(),
                    ]).strip()
                    # Build candidate set respecting Vacant rule AND utility affinity
                    hov_val = (rec.get("House Or Vacant") or "").strip().lower()
                    util_aff = (rec.get("Utility Type") or "").strip().lower()
                    base = _GL_CANDIDATES or []
                    if util_aff == "water":
                        base = [c for c in base if "water" in _norm_name(c.get("name", ""))] or base
                    elif util_aff == "sewer" or util_aff == "stormwater":
                        base = [c for c in base if "sewer" in _norm_name(c.get("name", "")) or "storm" in _norm_name(c.get("name", ""))] or base
                    elif util_aff == "gas":
                        base = [c for c in base if "gas" in _norm_name(c.get("name", ""))] or base
                    if hov_val == "vacant":
                        cands = [c for c in base if "vacant" in _norm_name(c.get("name", ""))] or base
                    else:
                        cands = [c for c in base if "vacant" not in _norm_name(c.get("name", ""))] or base
                    gm = _gemini_match(api_key, target, cands, context={
                        "house_or_vacant": rec.get("House Or Vacant"),
                        "utility_type": rec.get("Utility Type"),
                        "line_desc": rec.get("Line Item Description"),
                    })
                    gbest = _resolve_best_from_model(gm, cands, target)
                # Final guard: never map to Vacant GL unless House Or Vacant == Vacant
                if (rec.get("House Or Vacant") or "").strip().lower() != "vacant" and "vacant" in _norm_name(gbest.get("name", "")):
                    util = (rec.get("Utility Type") or "").strip().lower()
                    if util == "stormwater":
                        util = "sewer"
                    # Try to find non-vacant by utility preferred lists
                    replacement = None
                    if util == "water":
                        replacement = _choose_gl_deterministic({**rec, "House Or Vacant": "House", "Utility Type": "Water", "Line Item Description": rec.get("Line Item Description")})
                    elif util == "sewer":
                        replacement = _choose_gl_deterministic({**rec, "House Or Vacant": "House", "Utility Type": "Sewer", "Line Item Description": rec.get("Line Item Description")})
                    elif util == "gas":
                        replacement = _choose_gl_deterministic({**rec, "House Or Vacant": "House", "Utility Type": "Gas", "Line Item Description": rec.get("Line Item Description")})
                    if replacement:
                        gbest = replacement
                # Additional guard: if util=gas but chosen name looks electric, try gas replacement
                try:
                    util_now = (rec.get("Utility Type") or "").strip().lower()
                    nm = _norm_name(gbest.get("name", ""))
                    if util_now == "gas" and ("electric" in nm or "elec" in nm) and "gas" not in nm:
                        repl = _choose_gl_deterministic({**rec, "House Or Vacant": (rec.get("House Or Vacant") or "House"), "Utility Type": "Gas"})
                        if repl:
                            gbest = repl
                except Exception:
                    pass
                rec["EnrichedGLAccountID"] = gbest.get("id")
                rec["EnrichedGLAccountName"] = gbest.get("name")
                rec["EnrichedGLAccountNumber"] = gbest.get("number")
                # Build GL line description per rules
                try:
                    rec["GL_LINE_DESC"] = _build_gl_desc(rec.get("EnrichedGLAccountNumber"), rec)
                except Exception:
                    # fallback to period only
                    rec["GL_LINE_DESC"] = _fmt_period_mmddyyyy(
                        rec.get("Bill Period Start") or "",
                        rec.get("Bill Period End") or "",
                    )
        # Enriched consumption and UOM (Gallons only for Water)
        try:
            util_type = (rec.get("Utility Type") or "").strip().lower()
            if util_type == "water":
                gallons = _to_gallons(rec.get("Consumption Amount"), rec.get("Unit of Measure"))
                if gallons is not None:
                    rec["ENRICHED CONSUMPTION"] = round(float(gallons), 2)
                    rec["ENRICHED UOM"] = "Gallons"
            else:
                # For non-water, keep original numeric consumption and UOM
                amt_raw = str(rec.get("Consumption Amount") or "").replace(",", "").strip()
                try:
                    rec["ENRICHED CONSUMPTION"] = float(amt_raw) if amt_raw else None
                except Exception:
                    pass
                rec["ENRICHED UOM"] = (rec.get("Unit of Measure") or "").strip()
        except Exception:
            pass

        # New GL description format
        try:
            rec["GL DESC_NEW"] = _build_gl_desc_new(rec)
        except Exception:
            pass

        # PDF link (7 days) based on original parsed input key
        try:
            # Prefer full key provided by parser for accuracy
            full_key = (rec.get("source_input_key") or "").lstrip('/')
            if full_key:
                pdf_key = full_key
            else:
                src = (rec.get("source_file_page") or rec.get("source") or "").lstrip('/')
                pdf_key = PARSED_INPUTS_PREFIX + src if src else ""
            if pdf_key:
                url = s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': BUCKET, 'Key': pdf_key},
                    ExpiresIn=7*24*3600
                )
                # shorten if service configured; fallback to long url
                rec["PDF_LINK"] = _shorten_internal(url)
        except Exception:
            pass
        out.append(json.dumps(rec, ensure_ascii=False))
    return out


def lambda_handler(event, context):
    # For each NDJSON created in stage 3, read, enrich, write to stage 4
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            continue
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        if not key.startswith(INPUT_PREFIX):
            continue
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read().decode("utf-8", errors="ignore")
        lines = [ln for ln in body.splitlines() if ln.strip()]
        enriched_lines = _enrich_lines(lines)
        # Write to stage 4 with same partitioning and file stem
        stem = key.split("/", 1)[-1]  # drop prefix
        out_key = f"{OUTPUT_PREFIX}{stem}"
        s3.put_object(Bucket=BUCKET, Key=out_key, Body=("\n".join(enriched_lines) + "\n").encode('utf-8'), ContentType='application/x-ndjson')
        print(json.dumps({"message": "Enriched file written", "out_key": out_key, "lines": len(enriched_lines)}))
    return {"statusCode": 200, "body": json.dumps({"ok": True})}
