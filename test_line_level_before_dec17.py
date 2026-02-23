"""Test line-level exclusion for data before Dec 17"""
import boto3
import json
import hashlib
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')
ddb = session.client('dynamodb')

BUCKET = 'jrk-analytics-billing'
STAGE7_PREFIX = 'Bill_Parser_7_PostEntrata_Submission/'

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

# Load exclusion hashes from DynamoDB
print("Loading exclusion hashes from DynamoDB...")
exclusion_hashes = set()
paginator = ddb.get_paginator('scan')
for page in paginator.paginate(TableName='jrk-bill-ubi-assignments', ProjectionExpression='line_hash'):
    for item in page.get('Items', []):
        if 'line_hash' in item and 'S' in item['line_hash']:
            exclusion_hashes.add(item['line_hash']['S'])
print(f"  Loaded {len(exclusion_hashes)} exclusion hashes")

# Collect Stage 7 files BEFORE Dec 17
print("\nCollecting Stage 7 files before Dec 17...")
cutoff = datetime(2025, 12, 17)
today = datetime.now()
all_keys = []

s3_pag = s3.get_paginator('list_objects_v2')
for i in range(90):
    d = today - timedelta(days=i)
    if d >= cutoff:
        continue
    prefix = f"{STAGE7_PREFIX}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"
    try:
        for page in s3_pag.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                if obj['Key'].endswith('.jsonl'):
                    all_keys.append(obj['Key'])
    except:
        pass
print(f"  Found {len(all_keys)} files")

# Process files
print("\nCounting files with unassigned lines...")
def has_unassigned(key):
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        txt = obj['Body'].read().decode('utf-8', errors='ignore')
        for line in txt.strip().split('\n'):
            if line.strip():
                rec = json.loads(line)
                h = compute_hash(rec)
                if h not in exclusion_hashes:
                    return True
        return False
    except:
        return False

unassigned = 0
with ThreadPoolExecutor(max_workers=50) as executor:
    futures = [executor.submit(has_unassigned, k) for k in all_keys]
    for i, future in enumerate(as_completed(futures)):
        if future.result():
            unassigned += 1
        if (i+1) % 500 == 0:
            print(f"  Processed {i+1}/{len(all_keys)}, {unassigned} unassigned...")

print(f"\n=== BEFORE DEC 17 (LINE-LEVEL EXCLUSION) ===")
print(f"Total files: {len(all_keys)}")
print(f"Files with unassigned lines: {unassigned}")
print(f"Expected: ~800-900")
