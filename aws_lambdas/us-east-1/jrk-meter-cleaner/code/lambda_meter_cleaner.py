import os
import json
import boto3
import gzip
import io
import requests
from datetime import datetime, timedelta
from typing import Optional

s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")

# Stage prefixes for scanning invoices
STAGE_PREFIXES = [
    "Bill_Parser_7_PostEntrata_Submission/",  # Posted bills
    "Bill_Parser_8_UBI_Assigned/",  # UBI assigned
]
UTILITY_TYPES = ["water", "sewer", "gas", "electricity", "electric"]

BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
METER_DATA_PREFIX = os.getenv("METER_DATA_PREFIX", "Bill_Parser_Meter_Data/")
GEMINI_SECRET_NAME = os.getenv("GEMINI_SECRET_NAME", "gemini/matcher-keys")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# Normalize utility type to canonical form
def _normalize_utility_type(ut: str) -> str:
    ut_lower = (ut or "").lower().strip()
    if ut_lower in ["electric", "electricity", "elec"]:
        return "electricity"
    if ut_lower in ["water", "h2o"]:
        return "water"
    if ut_lower in ["gas", "natural gas", "nat gas"]:
        return "gas"
    if ut_lower in ["sewer", "sewage", "wastewater"]:
        return "sewer"
    return ut_lower

# UOM normalization rules - comprehensive variants
UOM_RULES = {
    "electricity": {
        "standard": "kWh",
        "variants": ["kwh", "kw", "kilowatt hours", "kilowatt-hours", "kw-h", "kwhr", "kilowatthours", "kilowatt hour", "kwhs"],
        "conversions": {}  # kWh is base unit
    },
    "gas": {
        "standard": "Therms",
        "variants": ["therms", "therm", "thm", "th", "ccf", "mcf", "cf", "cubic feet", "100 cubic feet", "hundred cubic feet", "dtherms", "decatherms", "dth"],
        "conversions": {
            "ccf": 1.024,  # 1 CCF = 1.024 Therms
            "mcf": 10.24,  # 1 MCF = 10.24 Therms
            "cf": 0.01024,  # 1 CF = 0.01024 Therms
            "100 cubic feet": 1.024,
            "hundred cubic feet": 1.024,
            "dtherms": 10.0,
            "decatherms": 10.0,
            "dth": 10.0,
        }
    },
    "water": {
        "standard": "Gallons",
        "variants": ["gallons", "gallon", "gal", "gals", "kgal", "kgals", "1000 gallons", "thousands of gallons",
                     "thousand gallons", "hcf", "ccf", "100 gallons", "hundred gallons", "cf", "cubic feet",
                     "1000 gal", "1,000 gallons", "mgal", "million gallons"],
        "conversions": {
            "kgal": 1000.0,
            "kgals": 1000.0,
            "1000 gallons": 1000.0,
            "1000 gal": 1000.0,
            "1,000 gallons": 1000.0,
            "thousands of gallons": 1000.0,
            "thousand gallons": 1000.0,
            "hcf": 748.052,  # 1 HCF = 748.052 gallons
            "ccf": 748.052,  # 1 CCF = 748.052 gallons
            "cf": 7.48052,   # 1 CF = 7.48 gallons
            "cubic feet": 7.48052,
            "100 gallons": 100.0,
            "hundred gallons": 100.0,
            "mgal": 1000000.0,
            "million gallons": 1000000.0,
        }
    },
    "sewer": {
        "standard": "Gallons",
        "variants": ["gallons", "gallon", "gal", "gals", "kgal", "kgals", "1000 gallons", "thousands of gallons",
                     "thousand gallons", "hcf", "ccf", "100 gallons", "cf", "cubic feet"],
        "conversions": {
            "kgal": 1000.0,
            "kgals": 1000.0,
            "1000 gallons": 1000.0,
            "thousands of gallons": 1000.0,
            "thousand gallons": 1000.0,
            "hcf": 748.052,
            "ccf": 748.052,
            "cf": 7.48052,
            "cubic feet": 7.48052,
            "100 gallons": 100.0,
        }
    }
}


def _get_gemini_key() -> Optional[str]:
    """Retrieve Gemini API key from Secrets Manager."""
    print(f"[METER CLEANER] Attempting to get secret: {GEMINI_SECRET_NAME}")
    try:
        resp = secrets.get_secret_value(SecretId=GEMINI_SECRET_NAME)
        secret_data = json.loads(resp["SecretString"])
        # Handle different secret structures:
        # 1. {"keys": ["key1", "key2"]} - array of keys (use first)
        # 2. {"api_key": "..."} or {"GEMINI_API_KEY": "..."}
        if "keys" in secret_data and isinstance(secret_data["keys"], list) and secret_data["keys"]:
            key = secret_data["keys"][0]
        else:
            key = secret_data.get("api_key") or secret_data.get("GEMINI_API_KEY")
        if key:
            print(f"[METER CLEANER] Successfully retrieved API key (length: {len(key)})")
        else:
            print(f"[METER CLEANER] Secret found but couldn't extract key. Structure: {list(secret_data.keys())}")
        return key
    except Exception as e:
        import traceback
        print(f"[METER CLEANER] Failed to get Gemini key: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


def _load_meter_data() -> dict:
    """Load meter data from S3."""
    key = METER_DATA_PREFIX + "meter_master.json.gz"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        raw = obj["Body"].read()
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            return json.loads(gz.read().decode('utf-8'))
    except s3.exceptions.NoSuchKey:
        return {"meters": {}, "readings": [], "scan_metadata": {}}
    except Exception as e:
        print(f"[METER CLEANER] Error loading meter data: {e}")
        return {"meters": {}, "readings": [], "scan_metadata": {}}


def _save_meter_data(data: dict):
    """Save meter data to S3."""
    key = METER_DATA_PREFIX + "meter_master.json.gz"
    body = json.dumps(data, default=str).encode('utf-8')
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode='wb') as gz:
        gz.write(body)
    compressed.seek(0)
    s3.put_object(Bucket=BUCKET, Key=key, Body=compressed.read(), ContentType='application/json', ContentEncoding='gzip')


def _save_cleaning_report(report: dict):
    """Save cleaning report to S3."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    key = f"{METER_DATA_PREFIX}cleaning_reports/{timestamp}_report.json"
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(report, indent=2, default=str).encode('utf-8'), ContentType='application/json')
    return key


def _normalize_uom(raw_uom: str, utility_type: str) -> tuple:
    """Normalize UOM and return (canonical_uom, conversion_factor)."""
    if not raw_uom:
        return UOM_RULES.get(utility_type, {}).get("standard", "Unknown"), 1.0

    raw_lower = raw_uom.lower().strip()
    rules = UOM_RULES.get(utility_type, {})

    # Check if it's already standard
    if raw_lower == rules.get("standard", "").lower():
        return rules.get("standard", raw_uom), 1.0

    # Check variants
    if raw_lower in [v.lower() for v in rules.get("variants", [])]:
        conversions = rules.get("conversions", {})
        factor = conversions.get(raw_lower, 1.0)
        return rules.get("standard", raw_uom), factor

    return "Unknown", 1.0


def _list_s3_files_modified_after(prefix: str, after_date: datetime) -> list:
    """List S3 files modified after a given date."""
    files = []
    paginator = s3.get_paginator('list_objects_v2')

    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get('Contents', []):
            if obj['LastModified'].replace(tzinfo=None) > after_date:
                files.append(obj['Key'])

    return files


def _scan_invoice_file(s3_key: str) -> list:
    """Scan a single invoice JSONL file and extract meter readings."""
    readings = []
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=s3_key)
        content = obj['Body'].read()

        # Decompress if gzipped
        if s3_key.endswith('.gz'):
            content = gzip.decompress(content)

        lines = content.decode('utf-8').strip().split('\n')

        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                raw_utility = record.get('Utility Type') or record.get('utility_type') or ''
                utility_type = _normalize_utility_type(raw_utility)

                if utility_type not in ["water", "sewer", "gas", "electricity"]:
                    continue

                meter_number = record.get('Meter Number') or record.get('meter_number') or ''
                if not meter_number:
                    continue

                # Get UOM - field is "Unit of Measure" in the actual data
                raw_uom = record.get('Unit of Measure') or record.get('UOM') or record.get('uom') or ''
                canonical_uom, conversion = _normalize_uom(raw_uom, utility_type)

                # Get consumption - field is "Consumption Amount" in the actual data
                consumption_str = record.get('Consumption Amount') or record.get('Consumption') or record.get('consumption') or '0'
                try:
                    raw_consumption = float(str(consumption_str).replace(',', ''))
                except:
                    raw_consumption = 0.0

                # Get property info from enriched fields
                property_id = record.get('EnrichedPropertyID') or record.get('property_id') or 'unknown'
                property_name = record.get('EnrichedPropertyName') or record.get('property_name') or 'unknown'

                # Get dates - fields are "Bill Period Start/End" in the actual data
                service_start = record.get('Bill Period Start') or record.get('Service Start') or ''
                service_end = record.get('Bill Period End') or record.get('Service End') or ''

                # Get vendor
                vendor_name = record.get('EnrichedVendorName') or record.get('Vendor Name') or record.get('vendor_name') or ''

                # Get line item amount
                amount_str = record.get('Line Item Charge') or record.get('Amount') or record.get('amount') or '0'
                try:
                    amount = float(str(amount_str).replace(',', ''))
                except:
                    amount = 0.0

                canonical_meter_id = f"{property_id}|{utility_type}|{meter_number}"
                readings.append({
                    "source_s3_key": s3_key,
                    "line_index": idx,
                    "canonical_meter_id": canonical_meter_id,
                    "meter_id": canonical_meter_id,  # Alias for compatibility
                    "meter_number": meter_number,
                    "normalized_meter_number": meter_number,  # UI expects this
                    "property_id": property_id,
                    "property_name": property_name,
                    "utility_type": utility_type,
                    "reading_date": service_end,  # UI uses this for chart
                    "service_start": service_start,
                    "service_end": service_end,
                    "raw_consumption": raw_consumption,
                    "raw_uom": raw_uom,
                    "canonical_uom": canonical_uom,
                    "normalized_uom": canonical_uom,
                    "conversion_factor": conversion,
                    "enriched_consumption": raw_consumption * conversion,
                    "normalized_consumption": raw_consumption * conversion,  # Alias
                    "amount": amount,
                    "vendor_name": vendor_name,
                    "scanned_date": datetime.utcnow().isoformat()
                })
            except (json.JSONDecodeError, ValueError) as e:
                print(f"[METER CLEANER] Error parsing line {idx} in {s3_key}: {e}")
                continue

    except Exception as e:
        print(f"[METER CLEANER] Error scanning {s3_key}: {e}")

    return readings


def _scan_new_files(days_back: int = 1) -> dict:
    """Scan S3 for new invoice files and extract meter readings."""
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    print(f"[METER CLEANER] Scanning for files modified after {cutoff.isoformat()}")

    all_readings = []
    files_scanned = 0

    for prefix in STAGE_PREFIXES:
        files = _list_s3_files_modified_after(prefix, cutoff)
        print(f"[METER CLEANER] Found {len(files)} new files in {prefix}")

        for s3_key in files:
            if s3_key.endswith('.jsonl') or s3_key.endswith('.jsonl.gz'):
                readings = _scan_invoice_file(s3_key)
                all_readings.extend(readings)
                files_scanned += 1

    # Group readings into meters
    meters = {}
    for r in all_readings:
        meter_id = r["canonical_meter_id"]
        if meter_id not in meters:
            meters[meter_id] = {
                "meter_id": meter_id,
                "canonical_meter_id": meter_id,  # UI expects this
                "display_name": r["meter_number"],
                "normalized_meter_number": r["meter_number"],  # UI expects this
                "property_id": r["property_id"],
                "property_name": r["property_name"],
                "utility_type": r["utility_type"],
                "canonical_uom": r["canonical_uom"],  # UI expects this
                "reading_count": 0,
                "first_seen": r["scanned_date"],
                "last_seen": r["scanned_date"]
            }
        meters[meter_id]["reading_count"] += 1
        meters[meter_id]["last_seen"] = r["scanned_date"]

    # Add sparkline data to each meter
    meters = _add_sparklines_to_meters(meters, all_readings)

    return {
        "meters": meters,
        "readings": all_readings,
        "scan_metadata": {
            "scan_date": datetime.utcnow().isoformat(),
            "days_back": days_back,
            "files_scanned": files_scanned,
            "readings_found": len(all_readings),
            "meters_found": len(meters)
        }
    }


def _merge_meter_data(existing: dict, new_data: dict) -> dict:
    """Merge new scan results into existing meter data."""
    existing_meters = existing.get("meters", {})
    existing_readings = existing.get("readings", [])

    new_meters = new_data.get("meters", {})
    new_readings = new_data.get("readings", [])

    # Create a set of existing reading keys for deduplication
    existing_keys = set()
    for r in existing_readings:
        key = f"{r.get('source_s3_key')}|{r.get('line_index')}"
        existing_keys.add(key)

    # Add new readings that don't already exist
    added_readings = 0
    for r in new_readings:
        key = f"{r.get('source_s3_key')}|{r.get('line_index')}"
        if key not in existing_keys:
            existing_readings.append(r)
            added_readings += 1

    # Merge meter metadata
    for meter_id, meter in new_meters.items():
        if meter_id in existing_meters:
            existing_meters[meter_id]["reading_count"] += meter["reading_count"]
            existing_meters[meter_id]["last_seen"] = meter["last_seen"]
        else:
            existing_meters[meter_id] = meter

    print(f"[METER CLEANER] Merged {added_readings} new readings, now have {len(existing_readings)} total")

    return {
        "meters": existing_meters,
        "readings": existing_readings,
        "scan_metadata": new_data.get("scan_metadata", {}),
        "pending_merges": existing.get("pending_merges", [])
    }


def _get_rescan_progress() -> dict:
    """Load rescan progress from S3."""
    key = METER_DATA_PREFIX + "rescan_progress.json"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        return json.loads(obj["Body"].read().decode('utf-8'))
    except:
        return {"processed_files": [], "status": "not_started"}


def _save_rescan_progress(progress: dict):
    """Save rescan progress to S3."""
    key = METER_DATA_PREFIX + "rescan_progress.json"
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(progress).encode('utf-8'), ContentType='application/json')


def _clear_rescan_progress():
    """Clear rescan progress after completion."""
    key = METER_DATA_PREFIX + "rescan_progress.json"
    try:
        s3.delete_object(Bucket=BUCKET, Key=key)
    except:
        pass


def _get_invoice_base_name(s3_key: str) -> str:
    """Extract base invoice name from S3 key (strip timestamps and path)."""
    # Key format: prefix/yyyy=.../PropertyName-Vendor-Account-StartDate-EndDate_Timestamp1_Timestamp2.jsonl
    filename = s3_key.split('/')[-1]
    # Remove .jsonl or .jsonl.gz
    if filename.endswith('.jsonl.gz'):
        filename = filename[:-9]
    elif filename.endswith('.jsonl'):
        filename = filename[:-6]
    # Split by underscore - base name is before the first timestamp (format: YYYYMMDDTHHMMSSZ)
    parts = filename.split('_')
    # Find where timestamps start (they look like 20251023T220200Z)
    base_parts = []
    for part in parts:
        if len(part) == 16 and part[8] == 'T' and part.endswith('Z'):
            break  # This is a timestamp, stop
        base_parts.append(part)
    return '_'.join(base_parts) if base_parts else filename


def _get_invoice_timestamp(s3_key: str) -> str:
    """Extract the latest timestamp from S3 key for version sorting."""
    filename = s3_key.split('/')[-1]
    parts = filename.replace('.jsonl.gz', '').replace('.jsonl', '').split('_')
    timestamps = [p for p in parts if len(p) == 16 and p[8:9] == 'T' and p.endswith('Z')]
    return max(timestamps) if timestamps else ""


def _parse_date(date_str: str):
    """Parse date string in various formats, return datetime or None."""
    if not date_str:
        return None
    for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except:
            continue
    return None


def _calculate_daily_rate(consumption: float, start_date: str, end_date: str) -> float:
    """Calculate daily consumption rate for a billing period."""
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if not start or not end:
        return 0.0
    days = (end - start).days
    if days <= 0:
        days = 1  # Avoid division by zero
    return consumption / days


def _add_sparklines_to_meters(meters: dict, all_readings: list) -> dict:
    """Add sparkline data to each meter based on readings - using daily consumption rate."""

    # Step 1: Dedupe readings by invoice base name - keep only latest version of each invoice
    invoice_versions = {}  # {base_name: {s3_key, timestamp, readings[]}}
    for r in all_readings:
        s3_key = r.get("source_s3_key", "")
        base_name = _get_invoice_base_name(s3_key)
        timestamp = _get_invoice_timestamp(s3_key)

        if base_name not in invoice_versions:
            invoice_versions[base_name] = {"timestamp": timestamp, "s3_key": s3_key, "readings": []}

        # Keep readings from the latest version
        if timestamp >= invoice_versions[base_name]["timestamp"]:
            if timestamp > invoice_versions[base_name]["timestamp"]:
                # Newer version found - replace
                invoice_versions[base_name] = {"timestamp": timestamp, "s3_key": s3_key, "readings": []}
            invoice_versions[base_name]["readings"].append(r)

    # Flatten to just the latest readings
    latest_readings = []
    for inv in invoice_versions.values():
        latest_readings.extend(inv["readings"])

    # Step 2: Group by meter
    meter_readings = {}
    for r in latest_readings:
        meter_id = r.get("canonical_meter_id") or r.get("meter_id")
        if not meter_id:
            continue
        if meter_id not in meter_readings:
            meter_readings[meter_id] = []

        consumption = r.get("enriched_consumption") or r.get("normalized_consumption") or 0
        service_start = r.get("service_start") or ""
        service_end = r.get("service_end") or r.get("reading_date") or ""
        daily_rate = _calculate_daily_rate(consumption, service_start, service_end)

        end_dt = _parse_date(service_end)
        sort_key = end_dt.strftime("%Y-%m-%d") if end_dt else ""

        meter_readings[meter_id].append({
            "date": service_end,
            "sort_key": sort_key,
            "daily_rate": daily_rate,
            "total_consumption": consumption,
            "source_s3_key": r.get("source_s3_key"),
            "line_index": r.get("line_index")
        })

    # Step 3: For each meter, check for discrepancies (different values for same date)
    for meter_id, readings_list in meter_readings.items():
        if meter_id not in meters:
            continue

        # Group by service date
        date_groups = {}
        for r in readings_list:
            key = r["sort_key"]
            if not key:
                continue
            if key not in date_groups:
                date_groups[key] = []
            date_groups[key].append(r)

        # Check for discrepancies - different consumption values on same date
        discrepancies = []
        for date, items in date_groups.items():
            if len(items) > 1:
                # Get unique non-zero consumption values
                values = list(set(r["total_consumption"] for r in items if r["total_consumption"] > 0))
                if len(values) > 1:  # Different non-zero values = discrepancy
                    discrepancies.append({
                        "date": date,
                        "values": sorted(values),
                        "min": min(values),
                        "max": max(values),
                        "count": len(items)
                    })

        if discrepancies:
            meters[meter_id]["has_discrepancies"] = True
            meters[meter_id]["discrepancies"] = discrepancies
            meters[meter_id]["discrepancy_count"] = len(discrepancies)

        # For sparkline, sum consumption per date (for meters with multiple line items)
        date_totals = {}
        for date, items in date_groups.items():
            # Use max value if discrepancy, otherwise sum (for multi-line items on same meter)
            total = sum(r["total_consumption"] for r in items)
            daily_rate = sum(r["daily_rate"] for r in items)
            date_totals[date] = {"total": total, "daily_rate": daily_rate, "date": items[0]["date"]}

        # Sort by date and take last 12 periods
        sorted_dates = sorted(date_totals.keys())[-12:]
        meters[meter_id]["sparkline"] = [round(date_totals[d]["daily_rate"], 2) for d in sorted_dates]
        meters[meter_id]["sparkline_dates"] = [date_totals[d]["date"] for d in sorted_dates]

        # For discrepancies, also store min/max for box plot
        if discrepancies:
            meters[meter_id]["sparkline_ranges"] = []
            for d in sorted_dates:
                disc = next((x for x in discrepancies if x["date"] == d), None)
                if disc:
                    meters[meter_id]["sparkline_ranges"].append({"min": disc["min"], "max": disc["max"]})
                else:
                    val = date_totals[d]["daily_rate"]
                    meters[meter_id]["sparkline_ranges"].append({"min": val, "max": val})

        if sorted_dates:
            last_date = sorted_dates[-1]
            meters[meter_id]["latest_daily_rate"] = round(date_totals[last_date]["daily_rate"], 2)
            meters[meter_id]["latest_consumption"] = date_totals[last_date]["total"]
            meters[meter_id]["latest_date"] = date_totals[last_date]["date"]

    return meters


def _full_rescan(context=None, batch_size: int = 50, force_fresh: bool = False) -> dict:
    """Do a complete rescan of all Stage 7 and 8 files in batches."""
    print("[METER CLEANER] Starting full rescan of all stages")

    # If force_fresh, clear any existing progress to start over
    if force_fresh:
        print("[METER CLEANER] Force fresh rescan - clearing progress file")
        _clear_rescan_progress()

    # Load existing progress
    progress = _get_rescan_progress()
    processed_files = set(progress.get("processed_files", []))
    is_resuming = len(processed_files) > 0 and not force_fresh

    if is_resuming:
        print(f"[METER CLEANER] Resuming rescan, {len(processed_files)} files already processed")
        # Load existing meter data to append to
        existing_data = _load_meter_data()
        all_readings = existing_data.get("readings", [])
        meters = existing_data.get("meters", {})
    else:
        print("[METER CLEANER] Starting fresh rescan")
        all_readings = []
        meters = {}

    # Collect all files to process
    all_files = []
    for prefix in STAGE_PREFIXES:
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                s3_key = obj['Key']
                if (s3_key.endswith('.jsonl') or s3_key.endswith('.jsonl.gz')) and s3_key not in processed_files:
                    all_files.append(s3_key)

    total_files = len(all_files) + len(processed_files)
    print(f"[METER CLEANER] Found {len(all_files)} files to process ({len(processed_files)} already done, {total_files} total)")

    files_scanned_this_batch = 0
    batch_start = datetime.utcnow()

    for s3_key in all_files:
        # Check if we're running low on time (keep 30 seconds buffer)
        if context and hasattr(context, 'get_remaining_time_in_millis'):
            remaining_ms = context.get_remaining_time_in_millis()
            if remaining_ms < 30000:  # Less than 30 seconds left
                print(f"[METER CLEANER] Low on time ({remaining_ms}ms), saving progress...")
                progress["processed_files"] = list(processed_files)
                progress["status"] = "in_progress"
                progress["last_save"] = datetime.utcnow().isoformat()
                _save_rescan_progress(progress)
                _save_meter_data({
                    "meters": meters,
                    "readings": all_readings,
                    "scan_metadata": {
                        "scan_date": datetime.utcnow().isoformat(),
                        "scan_type": "full_partial",
                        "files_scanned": len(processed_files),
                        "files_remaining": len(all_files) - files_scanned_this_batch,
                        "readings_found": len(all_readings),
                        "meters_found": len(meters)
                    },
                    "pending_merges": []
                })
                return {
                    "meters": meters,
                    "readings": all_readings,
                    "scan_metadata": {
                        "scan_date": datetime.utcnow().isoformat(),
                        "scan_type": "full_partial",
                        "status": "in_progress",
                        "files_scanned": len(processed_files),
                        "files_remaining": len(all_files) - files_scanned_this_batch,
                        "readings_found": len(all_readings),
                        "meters_found": len(meters)
                    },
                    "pending_merges": []
                }

        # Process the file
        readings = _scan_invoice_file(s3_key)
        for r in readings:
            all_readings.append(r)
            meter_id = r["canonical_meter_id"]
            if meter_id not in meters:
                meters[meter_id] = {
                    "meter_id": meter_id,
                    "canonical_meter_id": meter_id,  # UI expects this
                    "display_name": r["meter_number"],
                    "normalized_meter_number": r["meter_number"],  # UI expects this
                    "property_id": r["property_id"],
                    "property_name": r["property_name"],
                    "utility_type": r["utility_type"],
                    "canonical_uom": r["canonical_uom"],  # UI expects this
                    "reading_count": 0,
                    "first_seen": r["scanned_date"],
                    "last_seen": r["scanned_date"]
                }
            meters[meter_id]["reading_count"] += 1
            if r["scanned_date"] < meters[meter_id]["first_seen"]:
                meters[meter_id]["first_seen"] = r["scanned_date"]
            if r["scanned_date"] > meters[meter_id]["last_seen"]:
                meters[meter_id]["last_seen"] = r["scanned_date"]

        processed_files.add(s3_key)
        files_scanned_this_batch += 1

        # Save progress every batch_size files
        if files_scanned_this_batch % batch_size == 0:
            elapsed = (datetime.utcnow() - batch_start).total_seconds()
            print(f"[METER CLEANER] Batch checkpoint: {len(processed_files)} files, {len(all_readings)} readings, {elapsed:.1f}s elapsed")
            progress["processed_files"] = list(processed_files)
            progress["status"] = "in_progress"
            progress["last_save"] = datetime.utcnow().isoformat()
            _save_rescan_progress(progress)

    # All done - clear progress and save final data
    _clear_rescan_progress()

    # Add sparkline data to meters
    meters = _add_sparklines_to_meters(meters, all_readings)

    print(f"[METER CLEANER] Full rescan complete: {len(processed_files)} files, {len(all_readings)} readings, {len(meters)} meters")

    return {
        "meters": meters,
        "readings": all_readings,
        "scan_metadata": {
            "scan_date": datetime.utcnow().isoformat(),
            "scan_type": "full",
            "status": "complete",
            "files_scanned": len(processed_files),
            "readings_found": len(all_readings),
            "meters_found": len(meters)
        },
        "pending_merges": []
    }


def _find_duplicate_candidates(meters: dict) -> list:
    """Find meters that might be duplicates based on similar names."""
    def normalize_for_comparison(s):
        if not s:
            return ""
        s = s.upper().replace("-", "").replace(" ", "").replace("_", "")
        s = s.lstrip("0") or "0"
        return s

    # Group by property + utility type
    groups = {}
    for meter_id, meter in meters.items():
        key = f"{meter.get('property_id', 'unknown')}|{meter.get('utility_type', 'unknown')}"
        if key not in groups:
            groups[key] = []
        groups[key].append((meter_id, meter))

    candidates = []
    for group_key, group_meters in groups.items():
        if len(group_meters) < 2:
            continue

        for i, (id1, m1) in enumerate(group_meters):
            for id2, m2 in group_meters[i+1:]:
                norm1 = normalize_for_comparison(m1.get("display_name", ""))
                norm2 = normalize_for_comparison(m2.get("display_name", ""))

                if norm1 == norm2 or norm1.startswith(norm2) or norm2.startswith(norm1):
                    candidates.append({
                        "meter_1_id": id1,
                        "meter_1_name": m1.get("display_name"),
                        "meter_1_readings": m1.get("reading_count", 0),
                        "meter_2_id": id2,
                        "meter_2_name": m2.get("display_name"),
                        "meter_2_readings": m2.get("reading_count", 0),
                        "property_name": m1.get("property_name"),
                        "utility_type": m1.get("utility_type"),
                        "reason": "similar_names"
                    })

    return candidates


def _find_uom_issues(readings: list) -> list:
    """Find readings with UOM issues that need correction."""
    issues = []

    for r in readings:
        utility = r.get("utility_type", "").lower()
        raw_uom = r.get("raw_uom", "")
        normalized_uom = r.get("normalized_uom", "")

        if not raw_uom:
            issues.append({
                "source_s3_key": r.get("source_s3_key"),
                "line_index": r.get("line_index"),
                "meter_id": r.get("meter_id"),
                "raw_uom": raw_uom,
                "normalized_uom": normalized_uom,
                "utility_type": utility,
                "issue": "missing_uom",
                "suggestion": UOM_RULES.get(utility, {}).get("standard", "Unknown")
            })
        elif normalized_uom == "Unknown":
            issues.append({
                "source_s3_key": r.get("source_s3_key"),
                "line_index": r.get("line_index"),
                "meter_id": r.get("meter_id"),
                "raw_uom": raw_uom,
                "normalized_uom": normalized_uom,
                "utility_type": utility,
                "issue": "unrecognized_uom",
                "suggestion": None  # Will be filled by Gemini
            })

    return issues


def _analyze_with_gemini(uom_issues: list, duplicate_candidates: list) -> dict:
    """Use Gemini to analyze and suggest fixes for meter data issues via HTTP API."""
    api_key = _get_gemini_key()
    if not api_key:
        return {"error": "Could not retrieve Gemini API key"}

    # Build prompt for UOM analysis
    uom_prompt_items = []
    for issue in uom_issues[:50]:  # Limit to 50 to avoid token limits
        if issue.get("issue") == "unrecognized_uom":
            uom_prompt_items.append(f"- Utility: {issue['utility_type']}, Raw UOM: '{issue['raw_uom']}'")

    prompt = f"""You are a utility billing expert. Analyze these unit of measure (UOM) values and suggest corrections.

For each UOM, provide the standardized form:
- Electricity should use: kWh
- Gas should use: Therms (with conversion factors for CCF, MCF)
- Water should use: Gallons (with conversion factors for kGal, HCF)

Unrecognized UOMs to analyze:
{chr(10).join(uom_prompt_items) if uom_prompt_items else "None"}

Also analyze these potential duplicate meters (same property, similar meter numbers):
{json.dumps(duplicate_candidates[:20], indent=2) if duplicate_candidates else "None"}

Respond in JSON format:
{{
    "uom_corrections": [
        {{"raw_uom": "original", "corrected_uom": "standardized", "conversion_factor": 1.0, "utility_type": "electricity"}}
    ],
    "duplicate_recommendations": [
        {{"meter_1_id": "id1", "meter_2_id": "id2", "action": "merge", "target": "meter_1", "confidence": 0.9, "reason": "explanation"}}
    ],
    "general_observations": "Any overall data quality notes"
}}
"""

    try:
        # Use direct HTTP request to Gemini API (like jrk-bill-enricher)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        req_body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}

        r = requests.post(url, headers={"Content-Type": "application/json"}, data=json.dumps(req_body), timeout=60)

        if r.status_code != 200:
            print(f"[METER CLEANER] Gemini API error: {r.status_code} - {r.text[:200]}")
            return {"error": f"Gemini API returned {r.status_code}"}

        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return {"error": "No response from Gemini"}

        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or [])
        text = "".join([p.get("text", "") for p in parts if isinstance(p, dict)]).strip()

        # Extract JSON from response
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        return json.loads(text)
    except Exception as e:
        print(f"[METER CLEANER] Gemini analysis error: {e}")
        return {"error": str(e)}


def _apply_auto_fixes(meter_data: dict, gemini_suggestions: dict, auto_apply: bool = False) -> dict:
    """Apply or queue suggested fixes to meter data."""
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "auto_applied": auto_apply,
        "uom_fixes_applied": 0,
        "uom_fixes_queued": 0,
        "merges_applied": 0,
        "merges_queued": 0,
        "errors": [],
        "suggestions": gemini_suggestions
    }

    if "error" in gemini_suggestions:
        report["errors"].append(gemini_suggestions["error"])
        return report

    readings = meter_data.get("readings", [])
    meters = meter_data.get("meters", {})

    # Apply UOM corrections
    uom_corrections = {c["raw_uom"].lower(): c for c in gemini_suggestions.get("uom_corrections", [])}

    for r in readings:
        raw_uom = r.get("raw_uom", "").lower()
        if raw_uom in uom_corrections:
            correction = uom_corrections[raw_uom]
            if auto_apply:
                r["normalized_uom"] = correction["corrected_uom"]
                r["conversion_factor"] = correction.get("conversion_factor", 1.0)
                r["ai_corrected"] = True
                r["ai_correction_date"] = datetime.utcnow().isoformat()
                report["uom_fixes_applied"] += 1
            else:
                r["suggested_uom"] = correction["corrected_uom"]
                r["suggested_conversion"] = correction.get("conversion_factor", 1.0)
                report["uom_fixes_queued"] += 1

    # Queue merge recommendations (never auto-apply merges)
    merge_recs = gemini_suggestions.get("duplicate_recommendations", [])
    if "pending_merges" not in meter_data:
        meter_data["pending_merges"] = []

    for rec in merge_recs:
        if rec.get("action") == "merge" and rec.get("confidence", 0) >= 0.7:
            meter_data["pending_merges"].append({
                "meter_1_id": rec.get("meter_1_id"),
                "meter_2_id": rec.get("meter_2_id"),
                "target": rec.get("target"),
                "confidence": rec.get("confidence"),
                "reason": rec.get("reason"),
                "suggested_date": datetime.utcnow().isoformat(),
                "status": "pending"
            })
            report["merges_queued"] += 1

    return report


def lambda_handler(event, context):
    """
    Lambda handler for meter data cleaning.

    Event options:
    - action: "analyze" (default) - Find issues and get AI suggestions
    - action: "apply" - Apply pending fixes
    - action: "rescan" - Full rescan of all Stage 7/8 files (bulk cleanup)
    - action: "scan" - Incremental scan of new files
    - mode: "incremental" or "full" - For analyze action
    - days_back: Number of days to look back for incremental scan (default 1)
    - auto_apply_uom: True/False - Auto-apply UOM corrections (default False)
    """
    action = event.get("action", "analyze")
    mode = event.get("mode", "incremental")
    days_back = int(event.get("days_back", 1))
    auto_apply_uom = event.get("auto_apply_uom", False)

    print(f"[METER CLEANER] Starting action={action}, mode={mode}, days_back={days_back}")

    # Handle full rescan action (bulk cleanup)
    if action == "rescan":
        batch_size = int(event.get("batch_size", 50))
        force_fresh = event.get("force_fresh", True)  # Default to fresh rescan
        meter_data = _full_rescan(context=context, batch_size=batch_size, force_fresh=force_fresh)
        _save_meter_data(meter_data)

        scan_meta = meter_data.get("scan_metadata", {})
        status = scan_meta.get("status", "complete")
        files_remaining = scan_meta.get("files_remaining", 0)

        message = "Full rescan complete" if status == "complete" else f"Rescan in progress ({files_remaining} files remaining)"

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": message,
                "status": status,
                "meters": len(meter_data.get("meters", {})),
                "readings": len(meter_data.get("readings", [])),
                "files_scanned": scan_meta.get("files_scanned", 0),
                "files_remaining": files_remaining
            })
        }

    # Handle incremental scan action
    if action == "scan":
        existing_data = _load_meter_data()
        new_data = _scan_new_files(days_back=days_back)
        meter_data = _merge_meter_data(existing_data, new_data)
        _save_meter_data(meter_data)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Incremental scan complete",
                "new_readings": new_data.get("scan_metadata", {}).get("readings_found", 0),
                "total_meters": len(meter_data.get("meters", {})),
                "total_readings": len(meter_data.get("readings", []))
            })
        }

    # Load current meter data for analyze/apply actions
    meter_data = _load_meter_data()
    meters = meter_data.get("meters", {})
    readings = meter_data.get("readings", [])

    print(f"[METER CLEANER] Loaded {len(meters)} meters, {len(readings)} readings")

    # If mode is incremental for analyze, do a scan first
    if action == "analyze" and mode == "incremental":
        new_data = _scan_new_files(days_back=days_back)
        if new_data.get("readings"):
            meter_data = _merge_meter_data(meter_data, new_data)
            meters = meter_data.get("meters", {})
            readings = meter_data.get("readings", [])
            print(f"[METER CLEANER] After incremental scan: {len(meters)} meters, {len(readings)} readings")

    if not meters:
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "No meter data to clean", "meters": 0, "readings": 0})
        }

    if action == "analyze":
        # Find issues
        duplicate_candidates = _find_duplicate_candidates(meters)
        uom_issues = _find_uom_issues(readings)

        print(f"[METER CLEANER] Found {len(duplicate_candidates)} duplicate candidates, {len(uom_issues)} UOM issues")

        # Get AI suggestions
        gemini_result = _analyze_with_gemini(uom_issues, duplicate_candidates)

        # Apply or queue fixes
        report = _apply_auto_fixes(meter_data, gemini_result, auto_apply=auto_apply_uom)

        # Save updated data and report
        _save_meter_data(meter_data)
        report_key = _save_cleaning_report(report)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Analysis complete",
                "duplicate_candidates": len(duplicate_candidates),
                "uom_issues": len(uom_issues),
                "uom_fixes_applied": report.get("uom_fixes_applied", 0),
                "uom_fixes_queued": report.get("uom_fixes_queued", 0),
                "merges_queued": report.get("merges_queued", 0),
                "report_key": report_key,
                "observations": gemini_result.get("general_observations", "")
            })
        }

    elif action == "apply":
        # Apply pending merges that have been approved
        pending = meter_data.get("pending_merges", [])
        applied = 0

        for merge in pending:
            if merge.get("status") == "approved":
                target_id = merge.get("meter_1_id") if merge.get("target") == "meter_1" else merge.get("meter_2_id")
                source_id = merge.get("meter_2_id") if merge.get("target") == "meter_1" else merge.get("meter_1_id")

                # Move readings from source to target
                for r in readings:
                    if r.get("meter_id") == source_id:
                        r["meter_id"] = target_id
                        r["merged_from"] = source_id

                # Remove source meter
                if source_id in meters:
                    del meters[source_id]

                merge["status"] = "applied"
                merge["applied_date"] = datetime.utcnow().isoformat()
                applied += 1

        # Update reading counts
        for meter_id in meters:
            meters[meter_id]["reading_count"] = sum(1 for r in readings if r.get("meter_id") == meter_id)

        _save_meter_data(meter_data)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Fixes applied",
                "merges_applied": applied
            })
        }

    return {
        "statusCode": 400,
        "body": json.dumps({"error": f"Unknown action: {action}"})
    }
