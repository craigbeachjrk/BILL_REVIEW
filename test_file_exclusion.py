"""Test FILE-level exclusion (if file exists in Stage 8, skip entirely)"""
import boto3
from datetime import datetime, timedelta

session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')

BUCKET = 'jrk-analytics-billing'
STAGE7_PREFIX = 'Bill_Parser_7_PostEntrata_Submission/'
STAGE8_PREFIX = 'Bill_Parser_8_UBI_Assigned/'
STAGE99_PREFIX = 'Bill_Parser_99_Historical Archive/'

print("Step 1: Collecting Stage 8 + Stage 99 file basenames...")
assigned_files = set()

def get_file_basenames(prefix):
    """Get just the filename part (without prefix)"""
    basenames = set()
    paginator = s3.get_paginator('list_objects_v2')
    today = datetime.now()
    for i in range(90):
        d = today - timedelta(days=i)
        date_prefix = f"{prefix}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"
        try:
            for page in paginator.paginate(Bucket=BUCKET, Prefix=date_prefix):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if key.endswith('.jsonl'):
                        # Extract just the filename
                        basename = key.split('/')[-1]
                        basenames.add(basename)
        except:
            pass
    return basenames

stage8_files = get_file_basenames(STAGE8_PREFIX)
stage99_files = get_file_basenames(STAGE99_PREFIX)
assigned_files = stage8_files | stage99_files
print(f"  Stage 8 files: {len(stage8_files)}")
print(f"  Stage 99 files: {len(stage99_files)}")
print(f"  Total unique: {len(assigned_files)}")

print("\nStep 2: Collecting Stage 7 files...")
stage7_files = []
paginator = s3.get_paginator('list_objects_v2')
today = datetime.now()
for i in range(90):
    d = today - timedelta(days=i)
    prefix = f"{STAGE7_PREFIX}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"
    try:
        for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.endswith('.jsonl'):
                    basename = key.split('/')[-1]
                    stage7_files.append((key, basename))
    except:
        pass
print(f"  Stage 7 files: {len(stage7_files)}")

print("\nStep 3: Counting unassigned files (FILE-level exclusion)...")
unassigned = 0
for key, basename in stage7_files:
    if basename not in assigned_files:
        unassigned += 1

print(f"\n=== RESULTS (FILE-LEVEL EXCLUSION) ===")
print(f"Total Stage 7 files: {len(stage7_files)}")
print(f"Files in Stage 8/99: {len(assigned_files)}")
print(f"Unassigned files: {unassigned}")
print(f"Expected: ~800-900")
