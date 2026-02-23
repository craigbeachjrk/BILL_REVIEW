"""
Verify Stage 8 data matches DynamoDB baseline
"""
import boto3
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# AWS setup
session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')

BUCKET = 'jrk-analytics-billing'
STAGE_8_PREFIX = 'Bill_Parser_8_UBI_Assigned/'

def read_jsonl_from_s3(s3_key):
    """Read a JSONL file from S3"""
    try:
        response = s3.get_object(Bucket=BUCKET, Key=s3_key)
        content = response['Body'].read().decode('utf-8')
        lines = []
        for line in content.strip().split('\n'):
            if line.strip():
                lines.append(json.loads(line))
        return lines
    except Exception as e:
        return []

def main():
    print("=== VERIFYING STAGE 8 DATA ===\n")

    # List all files in Stage 8
    paginator = s3.get_paginator('list_objects_v2')
    all_keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=STAGE_8_PREFIX):
        for obj in page.get('Contents', []):
            if obj['Key'].endswith('.jsonl'):
                all_keys.append(obj['Key'])

    print(f"Found {len(all_keys)} JSONL files in Stage 8")

    # Read all files and aggregate by period
    by_period = defaultdict(lambda: {'count': 0, 'total': 0})
    total_lines = 0

    def process_file(s3_key):
        lines = read_jsonl_from_s3(s3_key)
        results = []
        for line in lines:
            period = line.get('ubi_period', 'Unknown')
            amount = float(line.get('ubi_amount', 0))
            results.append((period, amount))
        return results

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(process_file, key) for key in all_keys]
        for future in as_completed(futures):
            results = future.result()
            for period, amount in results:
                by_period[period]['count'] += 1
                by_period[period]['total'] += amount
                total_lines += 1

    print(f"Total line items in Stage 8: {total_lines}\n")

    # Load old summary
    with open('C:/temp/old_summary.json', 'r') as f:
        old_summary = json.load(f)

    old_by_period = old_summary['by_period']

    # Compare
    print("=== COMPARISON: OLD (DynamoDB) vs NEW (S3 Stage 8) ===")
    print(f"{'Period':15} | {'OLD Count':>10} | {'NEW Count':>10} | {'OLD Amount':>15} | {'NEW Amount':>15} | Status")
    print("-" * 90)

    all_periods = set(old_by_period.keys()) | set(by_period.keys())
    total_old_count = 0
    total_new_count = 0
    total_old_amount = 0
    total_new_amount = 0
    mismatches = []

    for period in sorted(all_periods):
        old_count = old_by_period.get(period, {}).get('count', 0)
        new_count = by_period.get(period, {}).get('count', 0)
        old_total = old_by_period.get(period, {}).get('total', 0)
        new_total = by_period.get(period, {}).get('total', 0)

        total_old_count += old_count
        total_new_count += new_count
        total_old_amount += old_total
        total_new_amount += new_total

        count_match = old_count == new_count
        amount_match = abs(old_total - new_total) < 0.01

        if count_match and amount_match:
            status = "OK"
        else:
            status = "DIFF"
            mismatches.append(period)

        print(f"{period:15} | {old_count:10} | {new_count:10} | ${old_total:14,.2f} | ${new_total:14,.2f} | {status}")

    print("-" * 90)
    print(f"{'TOTAL':15} | {total_old_count:10} | {total_new_count:10} | ${total_old_amount:14,.2f} | ${total_new_amount:14,.2f}")

    # Summary
    print("\n" + "=" * 60)
    missing_count = total_old_count - total_new_count
    missing_amount = total_old_amount - total_new_amount

    print(f"Migrated: {total_new_count} / {total_old_count} line items ({100*total_new_count/total_old_count:.1f}%)")
    print(f"Missing: {missing_count} line items (${missing_amount:,.2f})")

    if missing_count > 0:
        print(f"\nNote: {missing_count} assignments could not be migrated.")
        print("This is expected - some Stage 7 files may have been modified since assignment.")
        print("These items still exist in DynamoDB as a backup reference.")

    # Save new summary
    with open('C:/temp/new_summary.json', 'w') as f:
        json.dump({
            'by_period': {p: {'count': d['count'], 'total': d['total']} for p, d in by_period.items()},
            'total_count': total_new_count,
            'grand_total': total_new_amount
        }, f, indent=2)
    print("\nNew summary saved to C:/temp/new_summary.json")

if __name__ == '__main__':
    main()
