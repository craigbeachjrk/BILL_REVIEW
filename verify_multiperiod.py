"""
Verify multi-period format in Stage 8
"""
import boto3
import json
from collections import defaultdict

# AWS setup
session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')

BUCKET = 'jrk-analytics-billing'
STAGE_8_PREFIX = 'Bill_Parser_8_UBI_Assigned/'

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
        return []

# List Stage 8 files
paginator = s3.get_paginator('list_objects_v2')
all_keys = []
for page in paginator.paginate(Bucket=BUCKET, Prefix=STAGE_8_PREFIX):
    for obj in page.get('Contents', []):
        if obj['Key'].endswith('.jsonl'):
            all_keys.append(obj['Key'])

print(f"Found {len(all_keys)} files in Stage 8")

# Analyze multi-period stats
total_lines = 0
total_assignments = 0
by_period_count = defaultdict(int)  # How many lines have N periods
by_period = defaultdict(lambda: {'count': 0, 'amount': 0})

for key in all_keys:
    lines = read_jsonl_from_s3(key)
    for line in lines:
        total_lines += 1
        ubi_assignments = line.get('ubi_assignments', [])
        period_count = len(ubi_assignments) if ubi_assignments else 1
        by_period_count[period_count] += 1

        if ubi_assignments:
            for asn in ubi_assignments:
                total_assignments += 1
                period = asn.get('period', '')
                amount = asn.get('amount', 0)
                by_period[period]['count'] += 1
                by_period[period]['amount'] += amount
        else:
            # Legacy format
            total_assignments += 1
            period = line.get('ubi_period', '')
            amount = line.get('ubi_amount', 0)
            by_period[period]['count'] += 1
            by_period[period]['amount'] += amount

print(f"\n=== MULTI-PERIOD VERIFICATION ===")
print(f"Total unique line items: {total_lines}")
print(f"Total assignment entries: {total_assignments}")

print(f"\n=== LINES BY PERIOD COUNT ===")
for count in sorted(by_period_count.keys()):
    print(f"  {count} period(s): {by_period_count[count]} lines")

print(f"\n=== ASSIGNMENTS BY UBI PERIOD ===")
print(f"{'Period':15} | {'Count':>8} | {'Amount':>15}")
print("-" * 45)
total_count = 0
total_amount = 0
for period in sorted(by_period.keys()):
    data = by_period[period]
    print(f"{period:15} | {data['count']:8} | ${data['amount']:14,.2f}")
    total_count += data['count']
    total_amount += data['amount']
print("-" * 45)
print(f"{'TOTAL':15} | {total_count:8} | ${total_amount:14,.2f}")
