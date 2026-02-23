"""
Migration and Comparison Script for UBI Assignments
Moves data from DynamoDB to S3 Stage 8 and compares master bills generation
"""
import boto3
import json
import hashlib
from collections import defaultdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# AWS setup
session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
dynamodb = session.resource('dynamodb')
s3 = session.client('s3')

BUCKET = 'jrk-analytics-billing'
STAGE_7_PREFIX = 'Bill_Parser_7_PostEntrata_Submission/'
STAGE_8_PREFIX = 'Bill_Parser_8_UBI_Assigned/'
STAGE_99_PREFIX = 'Bill_Parser_99_Historical Archive/'

def scan_all_dynamodb_assignments():
    """Scan all items from jrk-bill-ubi-assignments table"""
    table = dynamodb.Table('jrk-bill-ubi-assignments')
    items = []
    response = table.scan()
    items.extend(response['Items'])

    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response['Items'])
        print(f"  Scanned {len(items)} items so far...")

    return items

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
        print(f"  Error reading {s3_key}: {e}")
        return []

def compute_line_hash(line):
    """Compute hash for a line item"""
    hash_fields = [
        str(line.get('Property', '')),
        str(line.get('Vendor', '')),
        str(line.get('AccountNumber', '')),
        str(line.get('ServiceStartDate', '')),
        str(line.get('ServiceEndDate', '')),
        str(line.get('ChargeDescription', '')),
        str(line.get('ChargeAmount', '')),
    ]
    hash_str = '|'.join(hash_fields)
    return hashlib.sha256(hash_str.encode()).hexdigest()

def generate_master_bills_from_dynamodb(assignments):
    """Generate master bills the OLD way (DynamoDB-based)"""
    print("\n=== Generating Master Bills from DynamoDB ===")

    # Group by ubi_period
    by_period = defaultdict(list)
    for a in assignments:
        period = a.get('ubi_period', 'Unknown')
        by_period[period].append({
            'line_hash': a.get('line_hash'),
            's3_key': a.get('s3_key'),
            'amount': float(a.get('amount', 0)),
            'months_total': int(a.get('months_total', 1)),
            'assigned_by': a.get('assigned_by'),
            'assigned_date': a.get('assigned_date'),
        })

    # Now for each period, we need to get the actual line items from S3
    master_bills = {}
    for period, period_assignments in by_period.items():
        print(f"  Processing period {period} with {len(period_assignments)} assignments...")

        # Group by s3_key to batch reads
        by_s3_key = defaultdict(list)
        for a in period_assignments:
            by_s3_key[a['s3_key']].append(a)

        period_items = []
        for s3_key, key_assignments in by_s3_key.items():
            lines = read_jsonl_from_s3(s3_key)
            hash_to_assignment = {a['line_hash']: a for a in key_assignments}

            for line in lines:
                line_hash = compute_line_hash(line)
                if line_hash in hash_to_assignment:
                    a = hash_to_assignment[line_hash]
                    line['ubi_period'] = period
                    line['ubi_amount'] = a['amount']
                    line['ubi_months'] = a['months_total']
                    period_items.append(line)

        master_bills[period] = period_items
        print(f"    Found {len(period_items)} line items for period {period}")

    return master_bills

def migrate_to_stage_8(assignments):
    """Migrate DynamoDB assignments to S3 Stage 8"""
    print("\n=== Migrating Data to Stage 8 ===")

    # Group assignments by s3_key
    by_s3_key = defaultdict(list)
    for a in assignments:
        by_s3_key[a.get('s3_key', '')].append(a)

    print(f"  Processing {len(by_s3_key)} unique S3 files...")

    migrated_count = 0
    for s3_key, key_assignments in by_s3_key.items():
        if not s3_key:
            continue

        # Read the original file from Stage 7
        lines = read_jsonl_from_s3(s3_key)
        if not lines:
            continue

        # Build hash -> assignment map
        hash_to_assignment = {a.get('line_hash'): a for a in key_assignments}

        # Find matching lines and add UBI fields
        assigned_lines = []
        for line in lines:
            line_hash = compute_line_hash(line)
            if line_hash in hash_to_assignment:
                a = hash_to_assignment[line_hash]
                line['ubi_period'] = a.get('ubi_period')
                line['ubi_amount'] = float(a.get('amount', 0))
                line['ubi_months'] = int(a.get('months_total', 1))
                line['ubi_assigned_by'] = a.get('assigned_by')
                line['ubi_assigned_date'] = a.get('assigned_date')
                assigned_lines.append(line)

        if assigned_lines:
            # Generate Stage 8 key from original Stage 7 key
            # Bill_Parser_7_PostEntrata_Submission/yyyy=2025/mm=12/dd=16/file.jsonl
            # -> Bill_Parser_8_UBI_Assigned/yyyy=2025/mm=12/dd=16/file.jsonl
            stage_8_key = s3_key.replace(STAGE_7_PREFIX, STAGE_8_PREFIX)

            # Write to Stage 8
            content = '\n'.join(json.dumps(line) for line in assigned_lines)
            s3.put_object(Bucket=BUCKET, Key=stage_8_key, Body=content.encode('utf-8'))
            migrated_count += len(assigned_lines)

            # Also write to Stage 99 (archive)
            stage_99_key = s3_key.replace(STAGE_7_PREFIX, STAGE_99_PREFIX)
            s3.put_object(Bucket=BUCKET, Key=stage_99_key, Body=content.encode('utf-8'))

    print(f"  Migrated {migrated_count} line items to Stage 8 and Stage 99")
    return migrated_count

def generate_master_bills_from_s3_stage8():
    """Generate master bills the NEW way (S3 Stage 8)"""
    print("\n=== Generating Master Bills from S3 Stage 8 ===")

    # List all files in Stage 8
    paginator = s3.get_paginator('list_objects_v2')
    all_keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=STAGE_8_PREFIX):
        for obj in page.get('Contents', []):
            if obj['Key'].endswith('.jsonl'):
                all_keys.append(obj['Key'])

    print(f"  Found {len(all_keys)} JSONL files in Stage 8")

    # Read all files and group by ubi_period
    by_period = defaultdict(list)

    def process_file(s3_key):
        lines = read_jsonl_from_s3(s3_key)
        return lines

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_key = {executor.submit(process_file, key): key for key in all_keys}
        for future in as_completed(future_to_key):
            lines = future.result()
            for line in lines:
                period = line.get('ubi_period', 'Unknown')
                by_period[period].append(line)

    master_bills = dict(by_period)
    for period, items in master_bills.items():
        print(f"    Period {period}: {len(items)} line items")

    return master_bills

def compare_master_bills(old_bills, new_bills):
    """Compare old and new master bills"""
    print("\n=== COMPARISON RESULTS ===")

    all_periods = set(old_bills.keys()) | set(new_bills.keys())

    match = True
    for period in sorted(all_periods):
        old_count = len(old_bills.get(period, []))
        new_count = len(new_bills.get(period, []))
        old_total = sum(float(item.get('ubi_amount', item.get('ChargeAmount', 0))) for item in old_bills.get(period, []))
        new_total = sum(float(item.get('ubi_amount', item.get('ChargeAmount', 0))) for item in new_bills.get(period, []))

        status = "✓ MATCH" if old_count == new_count and abs(old_total - new_total) < 0.01 else "✗ MISMATCH"
        if status == "✗ MISMATCH":
            match = False

        print(f"  {period}:")
        print(f"    OLD (DynamoDB): {old_count} items, ${old_total:,.2f}")
        print(f"    NEW (S3 Stage8): {new_count} items, ${new_total:,.2f}")
        print(f"    Status: {status}")

    print("\n" + "="*50)
    if match:
        print("✓ ALL PERIODS MATCH - Safe to deploy!")
    else:
        print("✗ MISMATCHES FOUND - Review before deploying!")

    return match

def main():
    print("="*60)
    print("UBI MIGRATION AND COMPARISON TOOL")
    print("="*60)

    # Step 1: Scan all DynamoDB assignments
    print("\n[1/4] Scanning DynamoDB for existing assignments...")
    assignments = scan_all_dynamodb_assignments()
    print(f"  Found {len(assignments)} total assignments")

    # Step 2: Generate master bills with OLD process
    print("\n[2/4] Generating master bills with OLD process (DynamoDB)...")
    old_master_bills = generate_master_bills_from_dynamodb(assignments)

    # Step 3: Migrate to Stage 8
    print("\n[3/4] Migrating data to S3 Stage 8...")
    migrated = migrate_to_stage_8(assignments)

    # Step 4: Generate master bills with NEW process
    print("\n[4/4] Generating master bills with NEW process (S3 Stage 8)...")
    new_master_bills = generate_master_bills_from_s3_stage8()

    # Compare results
    compare_master_bills(old_master_bills, new_master_bills)

    # Save detailed results
    with open('C:/temp/migration_results.json', 'w') as f:
        json.dump({
            'old_summary': {period: len(items) for period, items in old_master_bills.items()},
            'new_summary': {period: len(items) for period, items in new_master_bills.items()},
            'total_assignments': len(assignments),
            'migrated_count': migrated,
        }, f, indent=2)
    print("\nDetailed results saved to C:/temp/migration_results.json")

if __name__ == '__main__':
    main()
