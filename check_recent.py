"""Check if unassigned files are mostly recent (new data)"""
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

# Check Stage 7 by date
print("\nStage 7 files by date (last 30 days):")
today = datetime.now()
for i in range(30):
    d = today - timedelta(days=i)
    prefix = f"{STAGE7_PREFIX}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"

    total = 0
    unassigned = 0
    try:
        for page in s3_pag.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                if obj['Key'].endswith('.jsonl'):
                    total += 1
                    basename = obj['Key'].split('/')[-1]
                    if basename not in stage8_basenames:
                        unassigned += 1
    except:
        pass

    if total > 0:
        print(f"  {d.strftime('%Y-%m-%d')}: {total:3d} files, {unassigned:3d} unassigned ({100*unassigned/total:.0f}%)")
