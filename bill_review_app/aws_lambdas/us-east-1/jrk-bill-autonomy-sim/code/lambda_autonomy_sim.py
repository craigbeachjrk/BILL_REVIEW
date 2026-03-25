"""
Autonomy Simulation Lambda — batch-analyzes recent bills with deterministic AI checks.

Compares what the AI would have done (auto-pass, garbage detection) to what
humans actually did. Writes results to S3 as gzipped JSON for the dashboard.

Triggered by:
  - CloudWatch Events daily at 2 AM UTC
  - On-demand via app's POST /api/autonomy/sim/run (InvocationType=Event)

Input payload:
  {"days_back": 14}  (optional, default 14)
"""

import os
import json
import re
import gzip
import time
import boto3
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")

BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
STAGE4_PREFIX = os.getenv("STAGE4_PREFIX", "Bill_Parser_4_Enriched_Outputs/")
AI_SUGGESTIONS_TABLE = os.getenv("AI_SUGGESTIONS_TABLE", "jrk-bill-ai-suggestions")
OUTPUT_KEY = os.getenv("OUTPUT_KEY", "Bill_Parser_Config/autonomy_sim_results.json.gz")

# Same garbage patterns as main.py — kept in sync
GARBAGE_LINE_PATTERNS = [
    (r"balance\s*forward", "balance_forward"),
    (r"previous\s*balance", "previous_balance"),
    (r"payment\s*(received|thank)", "payment_received"),
    (r"amount\s*paid", "payment_received"),
    (r"(credit|debit)\s*adjustment", "adjustment"),
    (r"late\s*(fee|charge|payment)", "late_fee"),
    (r"returned\s*(check|payment)", "returned_payment"),
    (r"deposit", "deposit"),
    (r"refund", "refund"),
    (r"credit\s*balance", "credit_balance"),
    (r"balance\s*transfer", "balance_transfer"),
    (r"ca\s*climate\s*credit", "climate_credit"),
    (r"california\s*climate", "climate_credit"),
    (r"total\s*(due|amount|charges)", "total_line"),
    (r"amount\s*due", "total_line"),
    (r"please\s*pay", "total_line"),
]

HIGH_CONFIDENCE_REASONS = {"balance_forward", "payment_received", "previous_balance", "total_line"}


def _detect_garbage(lines):
    """Detect garbage lines using hardcoded patterns (no DDB dependency)."""
    results = []
    for idx, line in enumerate(lines):
        desc = (line.get("Line Item Description") or "").lower()
        desc_original = line.get("Line Item Description") or ""
        charge_raw = line.get("Line Item Charge", 0)
        try:
            charge = float(str(charge_raw).replace("$", "").replace(",", "").strip() or 0)
        except (ValueError, TypeError):
            charge = 0.0

        for pattern, reason in GARBAGE_LINE_PATTERNS:
            if re.search(pattern, desc, re.IGNORECASE):
                confidence = 0.9 if reason in HIGH_CONFIDENCE_REASONS else 0.7
                results.append({
                    "line_index": idx,
                    "description": desc_original,
                    "charge": charge,
                    "reason": reason,
                    "confidence": confidence,
                })
                break
    return results


def _get_account_history(vendor_id, property_id, account_number):
    """Get recent bill history from DDB."""
    if not vendor_id or not property_id:
        return []
    try:
        account_key = f"{vendor_id}#{property_id}#{account_number}".lower().replace(" ", "")
        resp = ddb.query(
            TableName=AI_SUGGESTIONS_TABLE,
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeValues={
                ":pk": {"S": f"HISTORY#{account_key}"},
                ":prefix": {"S": "BILL#"},
            },
            ScanIndexForward=False,
            Limit=10,
        )
        return [
            {
                "bill_date": it.get("bill_date", {}).get("S", ""),
                "total_amount": float(it.get("total_amount", {}).get("N", "0")),
                "line_count": int(it.get("line_count", {}).get("N", "0")),
            }
            for it in resp.get("Items", [])
        ]
    except Exception as e:
        print(f"[SIM] History lookup failed: {e}")
        return []


def _pdf_id_from_key(key):
    """Compute pdf_id (SHA1 hash) from S3 key — same as main.py."""
    import hashlib
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _load_day_bills(y, m, d):
    """Load all Stage 4 JSONL files for one day. Returns list of (s3_key, lines)."""
    prefix = f"{STAGE4_PREFIX}yyyy={y}/mm={m}/dd={d}/"
    bills = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if k.endswith(".jsonl"):
                    keys.append(k)

        def _read(key):
            try:
                resp = s3.get_object(Bucket=BUCKET, Key=key)
                body = resp["Body"].read().decode("utf-8", errors="ignore")
                lines = []
                for ln in body.splitlines():
                    ln = ln.strip()
                    if ln:
                        try:
                            lines.append(json.loads(ln))
                        except json.JSONDecodeError:
                            pass
                return (key, lines)
            except Exception as e:
                print(f"[SIM] Failed to read {key}: {e}")
                return (key, [])

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(_read, k): k for k in keys}
            for f in as_completed(futures):
                key, lines = f.result()
                if lines:
                    bills.append((key, lines))
    except Exception as e:
        print(f"[SIM] Failed to list {prefix}: {e}")
    return bills


def lambda_handler(event, context):
    """Main handler — analyze recent bills and write simulation results to S3."""
    t0 = time.time()
    days_back = int(event.get("days_back", 14))

    print(f"[SIM] Starting autonomy simulation for last {days_back} days")

    results = []
    vendor_stats = defaultdict(lambda: {
        "vendor_name": "", "bills": 0, "would_auto_pass": 0, "ai_correct": 0
    })

    today = datetime.now(timezone.utc).date()
    total_files = 0

    for day_offset in range(days_back):
        d = today - timedelta(days=day_offset)
        y, m, dd = str(d.year), f"{d.month:02d}", f"{d.day:02d}"
        day_bills = _load_day_bills(y, m, dd)
        total_files += len(day_bills)

        # Group lines by pdf_id (one JSONL file may have multiple invoices)
        by_pdf = {}
        for s3_key, lines in day_bills:
            pid = _pdf_id_from_key(s3_key)
            by_pdf.setdefault(pid, {"key": s3_key, "lines": []})
            by_pdf[pid]["lines"].extend(lines)

        for pid, info in by_pdf.items():
            lines = info["lines"]
            s3_key = info["key"]
            if not lines:
                continue

            first = lines[0]
            vendor_id = str(first.get("EnrichedVendorID", "") or "")
            vendor_name = str(first.get("EnrichedVendorName", "") or first.get("Vendor Name", "") or "")
            property_name = str(first.get("EnrichedPropertyName", "") or "")
            account_no = str(first.get("Account Number", "") or "")
            property_id = str(first.get("EnrichedPropertyID", "") or "")

            # Garbage detection
            garbage = _detect_garbage(lines)

            # Historical comparison
            history = _get_account_history(vendor_id, property_id, account_no)
            historical_flags = []
            if history:
                total = sum(
                    float(str(r.get("Line Item Charge", 0)).replace("$", "").replace(",", "").strip() or 0)
                    for r in lines
                    if not any(g.get("line_index") == i for i, g in enumerate(garbage))
                )
                avg_hist = sum(h.get("total_amount", 0) for h in history) / max(len(history), 1)
                if avg_hist > 0 and total > avg_hist * 3:
                    historical_flags.append("amount_spike")

            # Confidence + auto-pass
            confidence = 80
            if not history:
                confidence -= 20
            if garbage:
                confidence -= 5
            if historical_flags:
                confidence -= 15

            would_auto_pass = (
                len(garbage) == 0
                and len(historical_flags) == 0
                and confidence >= 85
            )

            # Check for existing AI suggestion (to compare with human action)
            human_action = "pending_review"
            ai_was_correct = None
            try:
                resp = ddb.query(
                    TableName=AI_SUGGESTIONS_TABLE,
                    KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
                    ExpressionAttributeValues={
                        ":pk": {"S": f"SUGGESTION#{pid}"},
                        ":prefix": {"S": "SUGGESTION#"},
                    },
                    ScanIndexForward=False,
                    Limit=1,
                )
                items = resp.get("Items", [])
                if items:
                    suggestion = items[0]
                    human_changed = suggestion.get("human_changed", {}).get("BOOL")
                    if human_changed is not None:
                        human_action = "submitted_with_changes" if human_changed else "submitted_unchanged"
                        ai_was_correct = (
                            (would_auto_pass and not human_changed)
                            or (not would_auto_pass and human_changed)
                        )
            except Exception:
                pass

            bill_result = {
                "pdf_id": pid,
                "date": f"{y}-{m}-{dd}",
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "property_name": property_name,
                "account_number": account_no,
                "line_count": len(lines),
                "ai_decision": "auto_pass" if would_auto_pass else "needs_review",
                "confidence": confidence,
                "garbage_detected": len(garbage),
                "garbage_details": [
                    {"desc": g.get("description", ""), "charge": g.get("charge", 0), "reason": g.get("reason", "")}
                    for g in garbage[:10]
                ],
                "historical_flags": historical_flags,
                "human_action": human_action,
                "ai_was_correct": ai_was_correct,
            }
            results.append(bill_result)

            # Vendor aggregates
            vs = vendor_stats[vendor_id]
            vs["vendor_name"] = vendor_name
            vs["bills"] += 1
            if would_auto_pass:
                vs["would_auto_pass"] += 1
            if ai_was_correct is True:
                vs["ai_correct"] += 1

    # Compute aggregate stats
    total_bills = len(results)
    auto_pass_count = sum(1 for r in results if r["ai_decision"] == "auto_pass")
    correct_count = sum(1 for r in results if r["ai_was_correct"] is True)
    evaluated_count = sum(1 for r in results if r["ai_was_correct"] is not None)

    sim_data = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "compute_seconds": round(time.time() - t0, 1),
        "date_range": {
            "start": (today - timedelta(days=days_back - 1)).isoformat(),
            "end": today.isoformat(),
        },
        "bills_analyzed": total_bills,
        "files_scanned": total_files,
        "aggregate": {
            "would_auto_pass_pct": round(auto_pass_count / max(total_bills, 1) * 100, 1),
            "auto_pass_accuracy": round(correct_count / max(evaluated_count, 1) * 100, 1) if evaluated_count else None,
            "evaluated_bills": evaluated_count,
        },
        "by_vendor": sorted(
            [
                {
                    "vendor_id": vid,
                    "vendor_name": vs["vendor_name"],
                    "bills": vs["bills"],
                    "would_auto_pass": vs["would_auto_pass"],
                    "auto_pass_pct": round(vs["would_auto_pass"] / max(vs["bills"], 1) * 100, 1),
                    "accuracy": round(vs["ai_correct"] / max(vs["bills"], 1) * 100, 1),
                    "eligible_for_assisted": vs["bills"] >= 10 and vs["ai_correct"] / max(vs["bills"], 1) >= 0.8,
                    "eligible_for_autonomous": vs["bills"] >= 20 and vs["ai_correct"] / max(vs["bills"], 1) >= 0.95,
                }
                for vid, vs in vendor_stats.items()
                if vs["bills"] > 0
            ],
            key=lambda x: x["bills"],
            reverse=True,
        ),
        "recent_bills": sorted(results, key=lambda x: x["date"], reverse=True)[:500],
    }

    # Write to S3
    compressed = gzip.compress(json.dumps(sim_data).encode())
    s3.put_object(
        Bucket=BUCKET,
        Key=OUTPUT_KEY,
        Body=compressed,
        ContentType="application/json",
        ContentEncoding="gzip",
    )

    elapsed = round(time.time() - t0, 1)
    print(f"[SIM] Complete: {total_bills} bills analyzed, {total_files} files scanned in {elapsed}s")
    print(f"[SIM] Auto-pass rate: {sim_data['aggregate']['would_auto_pass_pct']}%")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "bills_analyzed": total_bills,
            "auto_pass_pct": sim_data["aggregate"]["would_auto_pass_pct"],
            "compute_seconds": elapsed,
        }),
    }
