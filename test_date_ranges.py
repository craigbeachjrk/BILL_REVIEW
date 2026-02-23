"""Test different date ranges to find when it was ~900"""
import boto3
from datetime import datetime, timedelta

session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')
ddb = session.client('dynamodb')

BUCKET = 'jrk-analytics-billing'
STAGE7_PREFIX = 'Bill_Parser_7_PostEntrata_Submission/'
STAGE8_PREFIX = 'Bill_Parser_8_UBI_Assigned/'

# Load exclusion hashes from DynamoDB
print("Loading exclusion hashes from DynamoDB...")
exclusion_hashes = set()
paginator = ddb.get_paginator('scan')
for page in paginator.paginate(TableName='jrk-bill-ubi-assignments', ProjectionExpression='line_hash'):
    for item in page.get('Items', []):
        if 'line_hash' in item and 'S' in item['line_hash']:
            exclusion_hashes.add(item['line_hash']['S'])
print(f"  Loaded {len(exclusion_hashes)} hashes\n")

# Get Stage 8/99 basenames for file-level check
stage8_basenames = set()
s3_paginator = s3.get_paginator('list_objects_v2')
for page in s3_paginator.paginate(Bucket=BUCKET, Prefix=STAGE8_PREFIX):
    for obj in page.get('Contents', []):
        if obj['Key'].endswith('.jsonl'):
            stage8_basenames.add(obj['Key'].split('/')[-1])
print(f"Stage 8 basenames: {len(stage8_basenames)}\n")

# Test different date ranges
for days_back in [30, 45, 60, 90]:
    stage7_count = 0
    unassigned_files = 0

    today = datetime.now()
    for i in range(days_back):
        d = today - timedelta(days=i)
        prefix = f"{STAGE7_PREFIX}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"
        try:
            for page in s3_paginator.paginate(Bucket=BUCKET, Prefix=prefix):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if key.endswith('.jsonl'):
                        stage7_count += 1
                        basename = key.split('/')[-1]
                        if basename not in stage8_basenames:
                            unassigned_files += 1
        except:
            pass

    print(f"Days back: {days_back:2d} | Stage 7: {stage7_count:4d} | Unassigned (file-level): {unassigned_files:4d}")
