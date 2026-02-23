"""Debug why DynamoDB hashes don't match Stage 7 hashes"""
import boto3
import json
import hashlib

session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')
ddb = session.client('dynamodb')

BUCKET = 'jrk-analytics-billing'

# Volatile fields - should match main.py
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

# Get sample DynamoDB entries with s3_key
print("Getting sample DynamoDB entries with s3_key...")
response = ddb.scan(
    TableName='jrk-bill-ubi-assignments',
    Limit=50,
    ProjectionExpression='line_hash, s3_key'
)

checked = 0
matched = 0
mismatched = 0

for item in response['Items']:
    db_hash = item['line_hash']['S']
    s3_key = item.get('s3_key', {}).get('S', '')

    if not s3_key or 'Bill_Parser_7' not in s3_key:
        continue

    checked += 1

    # Read the S3 file
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=s3_key)
        txt = obj['Body'].read().decode('utf-8', errors='ignore')
        lines = [json.loads(l) for l in txt.strip().split('\n') if l.strip()]

        # Compute hash for each line
        found = False
        for line in lines:
            h = compute_hash(line)
            if h == db_hash:
                found = True
                break

        if found:
            matched += 1
        else:
            mismatched += 1
            if mismatched <= 3:  # Show details for first 3 mismatches
                print(f"\n=== MISMATCH {mismatched} ===")
                print(f"S3 key: {s3_key}")
                print(f"DB hash: {db_hash}")
                print(f"File has {len(lines)} lines")
                print("Computed hashes:")
                for i, line in enumerate(lines[:3]):
                    print(f"  Line {i}: {compute_hash(line)}")

    except Exception as e:
        print(f"Error reading {s3_key}: {e}")

print(f"\n=== SUMMARY ===")
print(f"Checked: {checked}")
print(f"Matched: {matched}")
print(f"Mismatched: {mismatched}")
print(f"Match rate: {100*matched/checked:.1f}%")
