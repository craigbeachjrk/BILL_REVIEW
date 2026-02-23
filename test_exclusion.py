"""Test exclusion logic locally before deploying"""
import boto3
import json
import hashlib
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')
ddb = session.client('dynamodb')

BUCKET = 'jrk-analytics-billing'
POST_ENTRATA_PREFIX = 'Bill_Parser_7_PostEntrata_Submission/'

# Volatile fields - MUST match main.py exactly
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

print("Step 1: Loading exclusion hashes from DynamoDB...")
exclusion_hashes = set()
paginator = ddb.get_paginator('scan')
for page in paginator.paginate(TableName='jrk-bill-ubi-assignments', ProjectionExpression='line_hash'):
    for item in page.get('Items', []):
        if 'line_hash' in item and 'S' in item['line_hash']:
            exclusion_hashes.add(item['line_hash']['S'])
print(f"  Loaded {len(exclusion_hashes)} unique exclusion hashes")

print("\nStep 2: Collecting Stage 7 files (last 90 days)...")
all_keys = []
today = datetime.now()
for i in range(90):
    d = today - timedelta(days=i)
    prefix = f"{POST_ENTRATA_PREFIX}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"
    try:
        s3_paginator = s3.get_paginator('list_objects_v2')
        for page in s3_paginator.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                if obj['Key'].endswith('.jsonl'):
                    all_keys.append(obj['Key'])
    except:
        pass
print(f"  Found {len(all_keys)} Stage 7 files")

print("\nStep 3: Processing files to count unassigned bills...")
def process_file(key):
    """Returns True if file has at least one unassigned line"""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        txt = obj['Body'].read().decode('utf-8', errors='ignore')
        for line in txt.splitlines():
            if line.strip():
                try:
                    rec = json.loads(line)
                    h = compute_hash(rec)
                    if h not in exclusion_hashes:
                        return True  # Has at least one unassigned line
                except:
                    pass
        return False  # All lines are assigned
    except:
        return False

unassigned_count = 0
total_processed = 0
with ThreadPoolExecutor(max_workers=50) as executor:
    futures = {executor.submit(process_file, key): key for key in all_keys}
    for future in as_completed(futures):
        total_processed += 1
        if future.result():
            unassigned_count += 1
        if total_processed % 500 == 0:
            print(f"  Processed {total_processed}/{len(all_keys)} files, {unassigned_count} unassigned so far...")

print(f"\n=== RESULTS ===")
print(f"Total Stage 7 files: {len(all_keys)}")
print(f"Exclusion hashes: {len(exclusion_hashes)}")
print(f"Unassigned bills: {unassigned_count}")
print(f"Expected: ~800-900")
