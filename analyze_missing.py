"""
Analyze the 1,564 missing/unmatched assignments from the migration
"""
import boto3
import json
import hashlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# AWS setup
session = boto3.Session(profile_name='jrk-analytics-admin', region_name='us-east-1')
s3 = session.client('s3')

BUCKET = 'jrk-analytics-billing'
STAGE_7_PREFIX = 'Bill_Parser_7_PostEntrata_Submission/'

# Volatile fields excluded from hash (same as main.py)
_VOLATILE_LINE_FIELDS = {
    "Charge Code",
    "Charge Code Source",
    "Charge Code Overridden",
    "Charge Code Override Reason",
    "Mapped Utility Name",
    "Current Amount",
    "Amount Overridden",
    "Amount Override Reason",
    "Is Excluded From UBI",
    "Exclusion Reason",
    "is_excluded_from_ubi",
    "exclusion_reason",
}

def _compute_stable_line_hash(rec):
    """Compute a stable hash for a line item, excluding volatile fields."""
    stable_rec = {k: v for k, v in rec.items() if k not in _VOLATILE_LINE_FIELDS}
    line_data = json.dumps(stable_rec, sort_keys=True)
    return hashlib.sha256(line_data.encode()).hexdigest()

def check_s3_file_exists(s3_key):
    """Check if an S3 file exists"""
    try:
        s3.head_object(Bucket=BUCKET, Key=s3_key)
        return True
    except:
        return False

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
        return None

def main():
    print("=== ANALYZING MISSING ASSIGNMENTS ===\n")

    # Load all assignments from DynamoDB export
    with open('C:/temp/dynamo_assignments.json', 'r') as f:
        all_assignments = json.load(f)

    print(f"Total assignments in DynamoDB: {len(all_assignments)}")

    # Group by s3_key
    by_s3_key = defaultdict(list)
    for a in all_assignments:
        s3_key = a.get('s3_key', '')
        if s3_key:
            by_s3_key[s3_key].append(a)

    print(f"Unique S3 files referenced: {len(by_s3_key)}")

    # Analyze each file
    missing_analysis = {
        'file_not_found': [],      # S3 file doesn't exist
        'hash_mismatch': [],       # File exists but hash doesn't match
        'file_empty': [],          # File exists but is empty
    }

    matched_count = 0
    processed = 0

    for s3_key, key_assignments in by_s3_key.items():
        processed += 1
        if processed % 200 == 0:
            print(f"  Processed {processed}/{len(by_s3_key)} files...")

        # Check if file exists
        lines = read_jsonl_from_s3(s3_key)

        if lines is None:
            # File doesn't exist or error reading
            for a in key_assignments:
                missing_analysis['file_not_found'].append({
                    's3_key': s3_key,
                    'line_hash': a.get('line_hash'),
                    'ubi_period': a.get('ubi_period'),
                    'amount': float(a.get('amount', 0)),
                    'assigned_date': a.get('assigned_date'),
                })
            continue

        if not lines:
            # File is empty
            for a in key_assignments:
                missing_analysis['file_empty'].append({
                    's3_key': s3_key,
                    'line_hash': a.get('line_hash'),
                    'ubi_period': a.get('ubi_period'),
                    'amount': float(a.get('amount', 0)),
                })
            continue

        # Compute hashes for all lines in the file
        file_hashes = set()
        for line in lines:
            computed_hash = _compute_stable_line_hash(line)
            file_hashes.add(computed_hash)

        # Check each assignment
        for a in key_assignments:
            db_hash = a.get('line_hash')
            if db_hash in file_hashes:
                matched_count += 1
            else:
                missing_analysis['hash_mismatch'].append({
                    's3_key': s3_key,
                    'line_hash': db_hash,
                    'ubi_period': a.get('ubi_period'),
                    'amount': float(a.get('amount', 0)),
                    'assigned_date': a.get('assigned_date'),
                    'file_line_count': len(lines),
                })

    # Summary
    print("\n" + "=" * 60)
    print("MISSING ASSIGNMENTS ANALYSIS")
    print("=" * 60)

    total_missing = (len(missing_analysis['file_not_found']) +
                     len(missing_analysis['hash_mismatch']) +
                     len(missing_analysis['file_empty']))

    print(f"\nMatched successfully: {matched_count}")
    print(f"Total missing: {total_missing}")

    print(f"\n1. FILE NOT FOUND: {len(missing_analysis['file_not_found'])} assignments")
    print("   (The S3 file referenced in DynamoDB no longer exists)")

    print(f"\n2. HASH MISMATCH: {len(missing_analysis['hash_mismatch'])} assignments")
    print("   (The S3 file exists but the line item hash doesn't match any line)")

    print(f"\n3. FILE EMPTY: {len(missing_analysis['file_empty'])} assignments")
    print("   (The S3 file exists but is empty)")

    # Detailed breakdown of file not found
    if missing_analysis['file_not_found']:
        print("\n--- FILES NOT FOUND ---")
        not_found_files = set(m['s3_key'] for m in missing_analysis['file_not_found'])
        print(f"Unique files missing: {len(not_found_files)}")

        # Group by period
        by_period = defaultdict(lambda: {'count': 0, 'amount': 0})
        for m in missing_analysis['file_not_found']:
            period = m['ubi_period']
            by_period[period]['count'] += 1
            by_period[period]['amount'] += m['amount']

        print("\nBy UBI Period:")
        for period in sorted(by_period.keys()):
            data = by_period[period]
            print(f"  {period}: {data['count']} items, ${data['amount']:,.2f}")

        print("\nSample missing files:")
        for f in list(not_found_files)[:10]:
            print(f"  {f}")

    # Detailed breakdown of hash mismatches
    if missing_analysis['hash_mismatch']:
        print("\n--- HASH MISMATCHES ---")
        mismatch_files = set(m['s3_key'] for m in missing_analysis['hash_mismatch'])
        print(f"Files with mismatched hashes: {len(mismatch_files)}")

        # Group by period
        by_period = defaultdict(lambda: {'count': 0, 'amount': 0})
        for m in missing_analysis['hash_mismatch']:
            period = m['ubi_period']
            by_period[period]['count'] += 1
            by_period[period]['amount'] += m['amount']

        print("\nBy UBI Period:")
        for period in sorted(by_period.keys()):
            data = by_period[period]
            print(f"  {period}: {data['count']} items, ${data['amount']:,.2f}")

        print("\nSample files with mismatches:")
        for f in list(mismatch_files)[:10]:
            # Count mismatches in this file
            file_mismatches = [m for m in missing_analysis['hash_mismatch'] if m['s3_key'] == f]
            print(f"  {f}")
            print(f"    -> {len(file_mismatches)} mismatched, file has {file_mismatches[0]['file_line_count']} lines")

    # Save detailed results
    with open('C:/temp/missing_analysis.json', 'w') as f:
        json.dump({
            'summary': {
                'matched': matched_count,
                'file_not_found': len(missing_analysis['file_not_found']),
                'hash_mismatch': len(missing_analysis['hash_mismatch']),
                'file_empty': len(missing_analysis['file_empty']),
            },
            'file_not_found': missing_analysis['file_not_found'][:100],
            'hash_mismatch': missing_analysis['hash_mismatch'][:100],
        }, f, indent=2)
    print("\nDetailed results saved to C:/temp/missing_analysis.json")

if __name__ == '__main__':
    main()
