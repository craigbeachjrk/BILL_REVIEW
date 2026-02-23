"""Test count excluding recent data to see historical baseline"""
import boto3
from datetime import datetime, timedelta

session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')

BUCKET = 'jrk-analytics-billing'
STAGE7_PREFIX = 'Bill_Parser_7_PostEntrata_Submission/'
STAGE8_PREFIX = 'Bill_Parser_8_UBI_Assigned/'

# Get Stage 8 basenames
print("Loading Stage 8 basenames...")
stage8_basenames = set()
s3_pag = s3.get_paginator('list_objects_v2')
for page in s3_pag.paginate(Bucket=BUCKET, Prefix=STAGE8_PREFIX):
    for obj in page.get('Contents', []):
        if obj['Key'].endswith('.jsonl'):
            stage8_basenames.add(obj['Key'].split('/')[-1])
print(f"  Stage 8 files: {len(stage8_basenames)}")

# Check Stage 7 BEFORE Dec 17
print("\nCounting Stage 7 files BEFORE Dec 17...")
cutoff = datetime(2025, 12, 17)
today = datetime.now()

total_before = 0
unassigned_before = 0

for i in range(90):
    d = today - timedelta(days=i)
    if d >= cutoff:
        continue  # Skip Dec 17 and later

    prefix = f"{STAGE7_PREFIX}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"
    try:
        for page in s3_pag.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                if obj['Key'].endswith('.jsonl'):
                    total_before += 1
                    basename = obj['Key'].split('/')[-1]
                    if basename not in stage8_basenames:
                        unassigned_before += 1
    except:
        pass

print(f"\n=== FILES BEFORE DEC 17 ===")
print(f"Total files: {total_before}")
print(f"Unassigned: {unassigned_before}")

# Also count AFTER Dec 17
print("\nCounting Stage 7 files AFTER Dec 17...")
total_after = 0
unassigned_after = 0

for i in range(90):
    d = today - timedelta(days=i)
    if d < cutoff:
        continue  # Skip before Dec 17

    prefix = f"{STAGE7_PREFIX}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"
    try:
        for page in s3_pag.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                if obj['Key'].endswith('.jsonl'):
                    total_after += 1
                    basename = obj['Key'].split('/')[-1]
                    if basename not in stage8_basenames:
                        unassigned_after += 1
    except:
        pass

print(f"\n=== FILES AFTER DEC 17 (inclusive) ===")
print(f"Total files: {total_after}")
print(f"Unassigned: {unassigned_after}")

print(f"\n=== COMBINED ===")
print(f"Total: {total_before + total_after}")
print(f"Unassigned: {unassigned_before + unassigned_after}")
