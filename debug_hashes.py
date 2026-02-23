"""Debug hash matching between Stage 7 and Stage 8"""
import boto3
import json
import hashlib

session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')
BUCKET = 'jrk-analytics-billing'

# Volatile fields (should match main.py)
VOLATILE = {
    "Charge Code", "Charge Code Source", "Charge Code Overridden", "Charge Code Override Reason",
    "Mapped Utility Name", "Current Amount", "Amount Overridden", "Amount Override Reason",
    "Is Excluded From UBI", "Exclusion Reason", "is_excluded_from_ubi", "exclusion_reason",
    "ubi_period", "ubi_amount", "ubi_months_total", "ubi_assigned_by", "ubi_assigned_date",
    "ubi_assignments", "ubi_period_count",
}

def compute_hash(rec):
    stable = {k: v for k, v in rec.items() if k not in VOLATILE}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()

# Get one Stage 8 file
stage8_key = 'Bill_Parser_8_UBI_Assigned/yyyy=2025/mm=12/dd=01/Arbor Oaks Apartments-FPL-1409478003-10-10-2025-11-10-2025_20251201T214149Z_20251201T215722Z.jsonl'
stage7_key = stage8_key.replace('Bill_Parser_8_UBI_Assigned/', 'Bill_Parser_7_PostEntrata_Submission/')

print(f"Stage 8: {stage8_key}")
print(f"Stage 7: {stage7_key}")

# Read Stage 8
try:
    s8_data = s3.get_object(Bucket=BUCKET, Key=stage8_key)['Body'].read().decode('utf-8')
    s8_lines = [json.loads(l) for l in s8_data.strip().split('\n') if l.strip()]
    print(f"\nStage 8 has {len(s8_lines)} lines")
except Exception as e:
    print(f"Error reading Stage 8: {e}")
    s8_lines = []

# Read Stage 7
try:
    s7_data = s3.get_object(Bucket=BUCKET, Key=stage7_key)['Body'].read().decode('utf-8')
    s7_lines = [json.loads(l) for l in s7_data.strip().split('\n') if l.strip()]
    print(f"Stage 7 has {len(s7_lines)} lines")
except Exception as e:
    print(f"Error reading Stage 7: {e}")
    s7_lines = []

if s8_lines and s7_lines:
    # Compute hashes
    s8_hashes = {compute_hash(l) for l in s8_lines}
    s7_hashes = {compute_hash(l) for l in s7_lines}

    print(f"\nStage 8 unique hashes: {len(s8_hashes)}")
    print(f"Stage 7 unique hashes: {len(s7_hashes)}")
    print(f"Matching hashes: {len(s8_hashes & s7_hashes)}")
    print(f"Stage 8 not in Stage 7: {len(s8_hashes - s7_hashes)}")
    print(f"Stage 7 not in Stage 8: {len(s7_hashes - s8_hashes)}")

    # Show field differences for first non-matching
    if s8_hashes - s7_hashes:
        print("\n--- Fields that differ (first non-matching line) ---")
        for s8_line in s8_lines:
            h = compute_hash(s8_line)
            if h in (s8_hashes - s7_hashes):
                # Find stable fields
                s8_stable = {k: v for k, v in s8_line.items() if k not in VOLATILE}
                # Compare to first s7 line
                s7_stable = {k: v for k, v in s7_lines[0].items() if k not in VOLATILE}

                s8_keys = set(s8_stable.keys())
                s7_keys = set(s7_stable.keys())
                print(f"Keys only in Stage 8: {s8_keys - s7_keys}")
                print(f"Keys only in Stage 7: {s7_keys - s8_keys}")
                break
