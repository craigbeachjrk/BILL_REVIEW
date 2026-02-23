"""Count actual line items to understand the data"""
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

# Load exclusion hashes
print("Loading exclusion hashes from DynamoDB...")
exclusion_hashes = set()
paginator = ddb.get_paginator('scan')
for page in paginator.paginate(TableName='jrk-bill-ubi-assignments', ProjectionExpression='line_hash'):
    for item in page.get('Items', []):
        if 'line_hash' in item and 'S' in item['line_hash']:
            exclusion_hashes.add(item['line_hash']['S'])
print(f"  Exclusion hashes: {len(exclusion_hashes)}")

# Collect all Stage 7 keys
print("\nCollecting Stage 7 files...")
all_keys = []
s3_pag = s3.get_paginator('list_objects_v2')
today = datetime.now()
for i in range(90):
    d = today - timedelta(days=i)
    prefix = f"{STAGE7_PREFIX}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"
    try:
        for page in s3_pag.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                if obj['Key'].endswith('.jsonl'):
                    all_keys.append(obj['Key'])
    except:
        pass
print(f"  Stage 7 files: {len(all_keys)}")

# Count lines
print("\nCounting lines...")
total_lines = 0
assigned_lines = 0
unassigned_lines = 0
files_with_unassigned = 0

def process_file(key):
    global total_lines, assigned_lines, unassigned_lines
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        txt = obj['Body'].read().decode('utf-8', errors='ignore')
        lines = [json.loads(l) for l in txt.strip().split('\n') if l.strip()]

        file_total = 0
        file_assigned = 0
        file_unassigned = 0

        for line in lines:
            h = compute_hash(line)
            file_total += 1
            if h in exclusion_hashes:
                file_assigned += 1
            else:
                file_unassigned += 1

        return (file_total, file_assigned, file_unassigned, file_unassigned > 0)
    except:
        return (0, 0, 0, False)

results = []
with ThreadPoolExecutor(max_workers=50) as executor:
    futures = [executor.submit(process_file, k) for k in all_keys]
    for i, future in enumerate(as_completed(futures)):
        results.append(future.result())
        if (i+1) % 500 == 0:
            print(f"  Processed {i+1}/{len(all_keys)}...")

for tot, asn, unasn, has_unasn in results:
    total_lines += tot
    assigned_lines += asn
    unassigned_lines += unasn
    if has_unasn:
        files_with_unassigned += 1

print(f"\n=== LINE-LEVEL COUNTS ===")
print(f"Total Stage 7 files: {len(all_keys)}")
print(f"Total line items: {total_lines}")
print(f"Assigned (in DynamoDB): {assigned_lines}")
print(f"Unassigned lines: {unassigned_lines}")
print(f"Files with ANY unassigned lines: {files_with_unassigned}")
