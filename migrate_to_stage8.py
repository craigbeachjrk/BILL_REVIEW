"""
Migrate DynamoDB UBI assignments to S3 Stage 8
Supports MULTIPLE UBI periods per line item
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
STAGE_8_PREFIX = 'Bill_Parser_8_UBI_Assigned/'
STAGE_99_PREFIX = 'Bill_Parser_99_Historical Archive/'

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
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as e:
        print(f"  Error reading {s3_key}: {e}")
        return None

def migrate_file(s3_key, key_assignments):
    """Migrate a single S3 file's assignments to Stage 8 with multi-period support"""
    if not s3_key or STAGE_7_PREFIX not in s3_key:
        return 0, 0, [f"Invalid s3_key: {s3_key}"]

    # Read the original file from Stage 7
    lines = read_jsonl_from_s3(s3_key)
    if lines is None:
        return 0, 0, [f"File not found: {s3_key}"]
    if not lines:
        return 0, 0, [f"Empty file: {s3_key}"]

    # Group assignments by line_hash, collecting ALL periods for each line
    # Structure: {line_hash: [list of assignment records]}
    hash_to_assignments = defaultdict(list)
    for a in key_assignments:
        hash_to_assignments[a.get('line_hash')].append(a)

    # Compute hashes for all lines in the file
    line_by_hash = {}
    for line in lines:
        computed_hash = _compute_stable_line_hash(line)
        line_by_hash[computed_hash] = line

    # Find matching lines and add ALL UBI periods
    assigned_lines = []
    matched_hashes = set()
    total_assignments_matched = 0

    for db_hash, assignment_list in hash_to_assignments.items():
        if db_hash in line_by_hash:
            line = dict(line_by_hash[db_hash])  # Make a copy

            # Collect all UBI periods for this line
            ubi_assignments = []
            for a in assignment_list:
                ubi_assignments.append({
                    "period": a.get('ubi_period'),
                    "amount": float(a.get('amount', 0)),
                    "months": int(a.get('months_total', 1)),
                    "assigned_by": a.get('assigned_by'),
                    "assigned_date": a.get('assigned_date'),
                })
                total_assignments_matched += 1

            # Sort by period for consistency
            ubi_assignments.sort(key=lambda x: x['period'])

            # Store as array of assignments
            line['ubi_assignments'] = ubi_assignments

            # Also keep legacy single-period fields for backward compatibility
            # Use the first (earliest) period
            first = ubi_assignments[0]
            line['ubi_period'] = first['period']
            line['ubi_amount'] = first['amount']
            line['ubi_months_total'] = first['months']
            line['ubi_assigned_by'] = first['assigned_by']
            line['ubi_assigned_date'] = first['assigned_date']

            # Add count of periods for easy filtering
            line['ubi_period_count'] = len(ubi_assignments)

            assigned_lines.append(line)
            matched_hashes.add(db_hash)

    warnings = []
    # Check for unmatched assignments
    unmatched_count = 0
    for db_hash, assignment_list in hash_to_assignments.items():
        if db_hash not in matched_hashes:
            unmatched_count += len(assignment_list)

    if unmatched_count > 0:
        warnings.append(f"{s3_key}: {unmatched_count} assignments did not match")

    if assigned_lines:
        # Generate Stage 8 key from original Stage 7 key
        stage_8_key = s3_key.replace(STAGE_7_PREFIX, STAGE_8_PREFIX)
        stage_99_key = s3_key.replace(STAGE_7_PREFIX, STAGE_99_PREFIX)

        # Write to Stage 8 and Stage 99
        content = '\n'.join(json.dumps(line) for line in assigned_lines)
        s3.put_object(Bucket=BUCKET, Key=stage_8_key, Body=content.encode('utf-8'))
        s3.put_object(Bucket=BUCKET, Key=stage_99_key, Body=content.encode('utf-8'))

        return len(assigned_lines), total_assignments_matched, warnings

    return 0, 0, warnings

def main():
    print("=== MIGRATING DATA TO STAGE 8 (MULTI-PERIOD FORMAT) ===")

    # Load assignments
    with open('C:/temp/dynamo_assignments.json', 'r') as f:
        assignments = json.load(f)

    print(f"Loaded {len(assignments)} assignments from DynamoDB export")

    # Group assignments by s3_key
    by_s3_key = defaultdict(list)
    for a in assignments:
        s3_key = a.get('s3_key', '')
        if s3_key:
            by_s3_key[s3_key].append(a)

    print(f"Processing {len(by_s3_key)} unique S3 files...")

    # Process files with thread pool
    total_lines = 0
    total_assignments = 0
    all_warnings = []
    processed = 0

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(migrate_file, s3_key, key_assignments): s3_key
                   for s3_key, key_assignments in by_s3_key.items()}

        for future in as_completed(futures):
            s3_key = futures[future]
            try:
                lines_count, assignments_count, warnings = future.result()
                total_lines += lines_count
                total_assignments += assignments_count
                all_warnings.extend(warnings)
                processed += 1
                if processed % 100 == 0:
                    print(f"  Processed {processed}/{len(by_s3_key)} files...")
            except Exception as e:
                all_warnings.append(f"Error processing {s3_key}: {e}")

    print(f"\n=== MIGRATION COMPLETE ===")
    print(f"Total files processed: {processed}")
    print(f"Total unique line items: {total_lines}")
    print(f"Total assignments migrated: {total_assignments}")
    print(f"Original DynamoDB assignments: {len(assignments)}")
    print(f"Migration coverage: {100*total_assignments/len(assignments):.1f}%")

    if all_warnings:
        print(f"\nWarnings ({len(all_warnings)}):")
        for w in all_warnings[:10]:
            print(f"  {w}")
        if len(all_warnings) > 10:
            print(f"  ... and {len(all_warnings) - 10} more")

    # Save migration results
    with open('C:/temp/migration_results.json', 'w') as f:
        json.dump({
            'total_lines': total_lines,
            'total_assignments': total_assignments,
            'files_processed': processed,
            'original_assignments': len(assignments),
            'warnings': all_warnings[:100]
        }, f, indent=2)
    print("\nMigration results saved to C:/temp/migration_results.json")

if __name__ == '__main__':
    main()
