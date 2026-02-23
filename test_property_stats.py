"""Test the property stats endpoint logic locally"""
import boto3
import json
import hashlib
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')
ddb = session.client('dynamodb')

BUCKET = 'jrk-analytics-billing'
POST_ENTRATA_PREFIX = 'Bill_Parser_7_PostEntrata_Submission/'

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

# Load exclusion hashes from BOTH DynamoDB tables
print("Loading exclusion hashes from BOTH DynamoDB tables...")
exclusion_hashes = set()

paginator = ddb.get_paginator('scan')
for table_name in ['jrk-bill-ubi-assignments', 'jrk-bill-ubi-archived']:
    for page in paginator.paginate(TableName=table_name, ProjectionExpression='line_hash'):
        for item in page.get('Items', []):
            if 'line_hash' in item and 'S' in item['line_hash']:
                exclusion_hashes.add(item['line_hash']['S'])
    print(f"  After {table_name}: {len(exclusion_hashes)} unique hashes")

# Collect Stage 7 files (last 90 days)
print("\nCollecting Stage 7 files (last 90 days)...")
all_keys = []
s3_pag = s3.get_paginator('list_objects_v2')
today = datetime.now()
for i in range(90):
    d = today - timedelta(days=i)
    prefix = f"{POST_ENTRATA_PREFIX}yyyy={d.year}/mm={d.month:02d}/dd={d.day:02d}/"
    try:
        for page in s3_pag.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                if obj['Key'].endswith('.jsonl'):
                    all_keys.append(obj['Key'])
    except:
        pass
print(f"  Found {len(all_keys)} files")

# Process files - get property name if invoice has unassigned lines
def get_property_if_unassigned(key):
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        txt = obj['Body'].read().decode('utf-8', errors='ignore')
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]

        if not lines:
            return None

        try:
            first_rec = json.loads(lines[0])
        except json.JSONDecodeError:
            return None

        property_name = (
            first_rec.get("EnrichedPropertyName") or
            first_rec.get("Property Name") or
            "Unknown Property"
        ).strip()

        # Check if any line is unassigned
        for line in lines:
            try:
                rec = json.loads(line)
                h = compute_hash(rec)
                if h not in exclusion_hashes:
                    return property_name
            except:
                continue

        return None
    except Exception as e:
        return None

print("\nCounting unassigned invoices by property...")
property_counts = defaultdict(int)

with ThreadPoolExecutor(max_workers=50) as executor:
    futures = [executor.submit(get_property_if_unassigned, k) for k in all_keys]
    for i, future in enumerate(as_completed(futures)):
        result = future.result()
        if result:
            property_counts[result] += 1
        if (i+1) % 500 == 0:
            print(f"  Processed {i+1}/{len(all_keys)}...")

# Sort by count descending
sorted_stats = sorted(
    [(prop, count) for prop, count in property_counts.items()],
    key=lambda x: x[1],
    reverse=True
)

print(f"\n=== RESULTS ===")
print(f"Total properties with unassigned invoices: {len(sorted_stats)}")
print(f"Total unassigned invoices: {sum(c for _, c in sorted_stats)}")
print(f"\nTop 20 properties by invoice count:")
print("-" * 60)
for i, (prop, count) in enumerate(sorted_stats[:20], 1):
    print(f"{i:2}. {prop[:45]:<45} {count:>5}")
