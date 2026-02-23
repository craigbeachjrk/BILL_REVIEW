"""
Reconcile the migration counts
"""
import boto3
import json
import hashlib
from collections import defaultdict

# AWS setup
session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')

BUCKET = 'jrk-analytics-billing'
STAGE_7_PREFIX = 'Bill_Parser_7_PostEntrata_Submission/'
STAGE_8_PREFIX = 'Bill_Parser_8_UBI_Assigned/'

# Volatile fields excluded from hash (same as main.py)
_VOLATILE_LINE_FIELDS = {
    "Charge Code",
    "Charge Code Source",
    "Charge Code Overridden",
    "Charge Code Override Reason",
    "Mapped Utility Name",
    "Current Amount",
    "Amount Overridden",
    "Amount Override Reason",
    "Is Excluded From UBI",
    "Exclusion Reason",
    "is_excluded_from_ubi",
    "exclusion_reason",
}

def _compute_stable_line_hash(rec):
    stable_rec = {k: v for k, v in rec.items() if k not in _VOLATILE_LINE_FIELDS}
    line_data = json.dumps(stable_rec, sort_keys=True)
    return hashlib.sha256(line_data.encode()).hexdigest()

def read_jsonl_from_s3(s3_key):
    try:
        response = s3.get_object(Bucket=BUCKET, Key=s3_key)
        content = response['Body'].read().decode('utf-8')
        lines = []
        for line in content.strip().split('\n'):
            if line.strip():
                lines.append(json.loads(line))
        return lines
    except:
        return None

# Load DynamoDB assignments
with open('C:/temp/dynamo_assignments.json', 'r') as f:
    assignments = json.load(f)

print(f"DynamoDB assignments: {len(assignments)}")

# Check for duplicate line_hash entries
all_hashes = [a.get('line_hash') for a in assignments]
unique_hashes = set(all_hashes)
print(f"Unique line hashes: {len(unique_hashes)}")
print(f"Duplicate assignments: {len(all_hashes) - len(unique_hashes)}")

# Group by (s3_key, line_hash) to find true duplicates
by_key_hash = defaultdict(list)
for a in assignments:
    key = (a.get('s3_key'), a.get('line_hash'))
    by_key_hash[key].append(a)

duplicates = {k: v for k, v in by_key_hash.items() if len(v) > 1}
print(f"\nDuplicate (s3_key, line_hash) pairs: {len(duplicates)}")

if duplicates:
    print("\nSample duplicates:")
    for (s3_key, line_hash), dups in list(duplicates.items())[:5]:
        print(f"  {s3_key[-60:]}...")
        for d in dups:
            print(f"    -> Period: {d.get('ubi_period')}, Date: {d.get('assigned_date')[:10]}, Amount: ${d.get('amount')}")

# Count unique combinations
unique_combinations = len(by_key_hash)
print(f"\nUnique (s3_key, line_hash) combinations: {unique_combinations}")

# Check Stage 8 - count actual lines written
print("\n=== COUNTING STAGE 8 ===")
paginator = s3.get_paginator('list_objects_v2')
stage8_keys = []
for page in paginator.paginate(Bucket=BUCKET, Prefix=STAGE_8_PREFIX):
    for obj in page.get('Contents', []):
        if obj['Key'].endswith('.jsonl'):
            stage8_keys.append(obj['Key'])

print(f"Stage 8 files: {len(stage8_keys)}")

# Count total lines in Stage 8
total_stage8_lines = 0
for key in stage8_keys[:100]:  # Sample first 100
    lines = read_jsonl_from_s3(key)
    if lines:
        total_stage8_lines += len(lines)

avg_lines_per_file = total_stage8_lines / 100 if stage8_keys else 0
estimated_total = avg_lines_per_file * len(stage8_keys)
print(f"Sample (100 files): {total_stage8_lines} lines")
print(f"Estimated total: ~{int(estimated_total)} lines")

# Exact count
print("\nCounting all Stage 8 lines...")
total = 0
for key in stage8_keys:
    lines = read_jsonl_from_s3(key)
    if lines:
        total += len(lines)
print(f"Actual total Stage 8 lines: {total}")
