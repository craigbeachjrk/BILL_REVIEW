"""
Test the NEW master bills generation logic (S3 Stage 8 based)
This simulates what main.py's /api/master-bills/generate endpoint will do
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

def safe_parse_charge(val):
    """Parse a charge value safely"""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace('$', '').replace(',', '').strip()
    try:
        return float(s)
    except:
        return 0.0

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
    print("=== TESTING NEW MASTER BILLS GENERATION (S3 Stage 8) ===\n")

    # List all files in Stage 8
    paginator = s3.get_paginator('list_objects_v2')
    all_keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=STAGE_8_PREFIX):
        for obj in page.get('Contents', []):
            if obj['Key'].endswith('.jsonl'):
                all_keys.append(obj['Key'])

    print(f"Found {len(all_keys)} JSONL files in Stage 8")

    # Group line items by ubi_period, property_id, account_number
    # This simulates the master bills aggregation logic
    all_line_items = []

    def process_file(s3_key):
        lines = read_jsonl_from_s3(s3_key)
        results = []
        for line in lines:
            ubi_period = line.get('ubi_period', '')
            if not ubi_period:
                continue  # Skip lines without ubi_period

            property_id = line.get('EnrichedPropertyID', line.get('Property ID', ''))
            property_name = line.get('EnrichedPropertyName', line.get('Property Name', ''))
            account_number = line.get('Account Number', '')
            vendor_name = line.get('EnrichedVendorName', line.get('Vendor Name', ''))
            charge_code = line.get('Charge Code', '')
            is_excluded = line.get('Is Excluded From UBI', 0) or line.get('is_excluded_from_ubi', 0)

            # Get the amount - prefer ubi_amount, fall back to Line Item Charge
            amount = line.get('ubi_amount', 0.0)
            if amount == 0.0:
                amount = safe_parse_charge(line.get('Line Item Charge', 0))

            results.append({
                'ubi_period': ubi_period,
                'property_id': property_id,
                'property_name': property_name,
                'account_number': account_number,
                'vendor_name': vendor_name,
                'charge_code': charge_code,
                'amount': amount,
                'is_excluded': is_excluded,
                's3_key': s3_key,
            })
        return results

    # Read all files in parallel
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(process_file, key) for key in all_keys]
        for future in as_completed(futures):
            all_line_items.extend(future.result())

    print(f"Total line items: {len(all_line_items)}")

    # Group by property for master bills (simulating what the endpoint does)
    by_property = defaultdict(lambda: defaultdict(lambda: {
        'accounts': defaultdict(list),
        'total_amount': 0,
        'line_count': 0
    }))

    for item in all_line_items:
        prop_id = item['property_id']
        ubi_period = item['ubi_period']

        by_property[prop_id][ubi_period]['accounts'][item['account_number']].append(item)
        by_property[prop_id][ubi_period]['total_amount'] += item['amount']
        by_property[prop_id][ubi_period]['line_count'] += 1

    # Generate master bill summaries by period
    print("\n=== MASTER BILLS BY PERIOD ===")
    period_summary = defaultdict(lambda: {'properties': 0, 'lines': 0, 'amount': 0})

    for prop_id, periods in by_property.items():
        for ubi_period, data in periods.items():
            period_summary[ubi_period]['properties'] += 1
            period_summary[ubi_period]['lines'] += data['line_count']
            period_summary[ubi_period]['amount'] += data['total_amount']

    print(f"{'Period':15} | {'Properties':>10} | {'Lines':>10} | {'Amount':>15}")
    print("-" * 60)

    total_properties = 0
    total_lines = 0
    total_amount = 0

    for period in sorted(period_summary.keys()):
        data = period_summary[period]
        print(f"{period:15} | {data['properties']:10} | {data['lines']:10} | ${data['amount']:14,.2f}")
        total_properties += data['properties']
        total_lines += data['lines']
        total_amount += data['amount']

    print("-" * 60)
    print(f"{'TOTAL':15} | {total_properties:10} | {total_lines:10} | ${total_amount:14,.2f}")

    # Compare with old summary
    print("\n=== COMPARISON WITH OLD (DynamoDB) ===")
    with open('C:/temp/old_summary.json', 'r') as f:
        old_summary = json.load(f)

    print(f"OLD DynamoDB: {old_summary['total_count']} lines, ${old_summary['grand_total']:,.2f}")
    print(f"NEW S3 Stage8: {total_lines} lines, ${total_amount:,.2f}")
    print(f"Migration Coverage: {100*total_lines/old_summary['total_count']:.1f}%")

if __name__ == '__main__':
    main()
