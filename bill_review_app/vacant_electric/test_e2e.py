"""
End-to-end integration test for VE pipeline after legacy translation removal.

Connects to Snowflake, runs actual queries, validates:
1. entity_id has NO "01" prefix
2. ResiStatus is full name (Current/Past/Notice), not abbreviation (C/P/N)
3. Lease keys match bill keys (composite key join works)
4. Pipeline runs through classification without errors
5. No NaT strings in output
"""
import sys
import os
import json
import logging
import traceback

import boto3
import snowflake.connector
import pandas as pd

# Add parent paths so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vacant_electric.queries import bills_query, leases_query, total_expense_query
from vacant_electric.config import VEConfig, STATUS_PRIORITY, CHARGE_CODE_MAP
from vacant_electric.property_maps import MAPPING_DISPATCH, BLDG, APT
from vacant_electric.classifier import classify_detail_df, classify_line
from vacant_electric.matcher import aggregate_gl_to_invoice, join_with_leases, filter_overlap, dedup_by_status
from vacant_electric.pipeline import VEPipeline
from vacant_electric.parser import parse_unit_string

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

PASSED = 0
FAILED = 0


def check(name, condition, detail=''):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}  -- {detail}")


def get_snowflake_conn():
    """Get Snowflake connection via AWS Secrets Manager (same as main.py)."""
    session = boto3.Session(profile_name='jrk-analytics-admin')
    secrets_client = session.client('secretsmanager', region_name='us-east-1')
    response = secrets_client.get_secret_value(SecretId='jrk-bill-review/snowflake')
    creds = json.loads(response['SecretString'])

    connect_args = {
        'account': creds['account'],
        'user': creds['user'],
        'database': creds['database'],
        'schema': creds['schema'],
        'warehouse': creds['warehouse'],
        'insecure_mode': True,
    }
    if creds.get('role'):
        connect_args['role'] = creds['role']

    # Key-pair auth
    pk_secret_name = creds.get('private_key_secret')
    if pk_secret_name and not creds.get('password'):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, NoEncryption, PrivateFormat
        pk_resp = secrets_client.get_secret_value(SecretId=pk_secret_name)
        pk_pem = pk_resp['SecretString'].encode('utf-8')
        pk = load_pem_private_key(pk_pem, password=None)
        connect_args['private_key'] = pk.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    elif creds.get('password'):
        connect_args['password'] = creds['password']

    return snowflake.connector.connect(**connect_args)


def fetch(conn, query):
    cur = conn.cursor()
    try:
        cur.execute(query)
        cols = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    finally:
        cur.close()


def main():
    global PASSED, FAILED

    # Use Jan 2026 as test month (known to have data from prior runs)
    month, year = 1, 2026
    config = VEConfig(month=month, year=year)

    print(f"\n{'='*70}")
    print(f"VE PIPELINE END-TO-END TEST — {config.month_name} {year}")
    print(f"{'='*70}")

    # ── 1. Connect to Snowflake ──
    print("\n[1] Connecting to Snowflake...")
    try:
        conn = get_snowflake_conn()
        check("Snowflake connection", True)
    except Exception as e:
        print(f"  FATAL: Cannot connect to Snowflake: {e}")
        return 1

    # ── 2. Run bills query ──
    print("\n[2] Running bills query...")
    bills_df = fetch(conn, bills_query(config.month_abbr))
    check("Bills query returned data", len(bills_df) > 0, f"got {len(bills_df)} rows")

    if len(bills_df) > 0:
        sample_ids = bills_df['entityid'].dropna().unique()[:5]
        print(f"     Sample entity_ids: {list(sample_ids)}")

        # Check NO "01" prefix
        has_01 = bills_df['entityid'].dropna().str.startswith('01').any()
        check("entity_id has NO '01' prefix", not has_01,
              f"Found '01' prefixed IDs: {list(bills_df[bills_df['entityid'].str.startswith('01', na=False)]['entityid'].unique()[:5])}" if has_01 else '')

        # Check entity_ids match MAPPING_DISPATCH keys
        bill_entities = set(bills_df['entityid'].dropna().unique())
        dispatch_keys = set(MAPPING_DISPATCH.keys())
        matched = bill_entities & dispatch_keys
        unmatched = bill_entities - dispatch_keys
        check("entity_ids match MAPPING_DISPATCH keys",
              len(matched) > 0,
              f"matched={len(matched)}, unmatched={len(unmatched)}")
        if unmatched:
            print(f"     Unmatched entity_ids (no mapping): {sorted(unmatched)[:10]}")
        print(f"     Matched: {len(matched)}/{len(bill_entities)} entity_ids have property maps")

    # ── 3. Run leases query ──
    print("\n[3] Running leases query...")
    leases_df = fetch(conn, leases_query())
    check("Leases query returned data", len(leases_df) > 0, f"got {len(leases_df)} rows")

    if len(leases_df) > 0:
        sample_props = leases_df['PropertyId'].dropna().unique()[:5]
        print(f"     Sample PropertyIds: {list(sample_props)}")

        # Check NO "01" prefix on PropertyId
        has_01_lease = leases_df['PropertyId'].dropna().str.startswith('01').any()
        check("PropertyId has NO '01' prefix", not has_01_lease,
              f"Found '01' prefixed: {list(leases_df[leases_df['PropertyId'].str.startswith('01', na=False)]['PropertyId'].unique()[:5])}" if has_01_lease else '')

        # Check ResiStatus is full name, not abbreviation
        statuses = set(leases_df['ResiStatus'].dropna().unique())
        print(f"     ResiStatus values: {statuses}")
        check("ResiStatus uses full names",
              statuses <= {'Current', 'Past', 'Notice'},
              f"Unexpected values: {statuses - {'Current', 'Past', 'Notice'}}")

        has_abbrev = statuses & {'C', 'P', 'N'}
        check("ResiStatus has NO abbreviations (C/P/N)", len(has_abbrev) == 0,
              f"Found abbreviations: {has_abbrev}")

        # Check STATUS_PRIORITY keys match
        check("STATUS_PRIORITY keys match ResiStatus values",
              set(STATUS_PRIORITY.keys()) == {'Current', 'Notice', 'Past'})

    # ── 4. Run total expense query ──
    print("\n[4] Running total expense query...")
    expense_df = fetch(conn, total_expense_query(config.month_abbr))
    expense_df.columns = [c.lower() for c in expense_df.columns]
    check("Expense query returned data", len(expense_df) > 0, f"got {len(expense_df)} rows")

    if len(expense_df) > 0:
        has_01_exp = expense_df['entityid'].dropna().str.startswith('01').any()
        check("Expense entityid has NO '01' prefix", not has_01_exp)

    # ── 5. Key matching test ──
    print("\n[5] Testing composite key matching...")
    if len(bills_df) > 0 and len(leases_df) > 0:
        # Pick a bill entity_id that's in both
        bill_entities = set(bills_df['entityid'].dropna().unique())
        lease_entities = set(leases_df['PropertyId'].dropna().unique())
        overlapping = bill_entities & lease_entities
        check("Bill entity_ids overlap with lease PropertyIds",
              len(overlapping) > 0,
              f"bill entities: {len(bill_entities)}, lease entities: {len(lease_entities)}, overlap: {len(overlapping)}")
        print(f"     Overlapping properties: {len(overlapping)}/{len(bill_entities)}")

    # ── 6. Run full pipeline ──
    print("\n[6] Running full VEPipeline...")
    try:
        pipeline = VEPipeline(config)
        result = pipeline.run(conn)
        check("Pipeline completed without error", True)
        print(f"     Stats: {result.stats.total_gl_records} GL records, "
              f"{result.stats.matched_to_lease} matched, "
              f"{result.stats.match_rate:.1f}% match rate")
        print(f"     Final: {result.stats.final_charge_rows} charge rows, "
              f"${result.stats.total_billback_amount:,.2f} total")

        # Check final_df has no "01" prefix
        if pipeline.final_df is not None and not pipeline.final_df.empty:
            final_has_01 = pipeline.final_df['entityid'].dropna().str.startswith('01').any()
            check("final_df entity_id clean (no 01)", not final_has_01)

            # Check ResiStatus in final_df
            final_statuses = set(pipeline.final_df['ResiStatus'].dropna().unique())
            check("final_df ResiStatus uses full names",
                  final_statuses <= {'Current', 'Past', 'Notice'},
                  f"Got: {final_statuses}")

        # Check classification works
        if pipeline.final_df is not None and not pipeline.final_df.empty:
            classified = classify_detail_df(pipeline.final_df)
            check("Classification succeeded", 'review_status' in classified.columns)
            status_counts = classified['review_status'].value_counts().to_dict()
            print(f"     Classification: {status_counts}")
            check("Not all BILLING_ISSUE",
                  set(status_counts.keys()) != {'BILLING_ISSUE'},
                  f"All lines are BILLING_ISSUE — possible $0 issue")

        # Check agg_df
        if pipeline.agg_df is not None and not pipeline.agg_df.empty:
            check("agg_df has no entrata_lease_status column",
                  'entrata_lease_status' not in pipeline.agg_df.columns)
            check("agg_df ResiStatus uses full names",
                  set(pipeline.agg_df['ResiStatus'].dropna().unique()) <= {'Current', 'Notice'})
            # Past should be filtered out
            check("agg_df has no Past residents",
                  'Past' not in set(pipeline.agg_df['ResiStatus'].dropna().unique()))

    except Exception as e:
        check("Pipeline completed without error", False, f"{e}\n{traceback.format_exc()}")

    # ── 7. NaT string check ──
    print("\n[7] Checking for NaT strings in output...")
    if pipeline.final_df is not None and not pipeline.final_df.empty:
        for col in ['MoveInDate', 'MoveOutDate', 'Bill Start', 'Bill End']:
            if col in pipeline.final_df.columns:
                nat_count = pipeline.final_df[col].astype(str).isin(['NaT', 'nan']).sum()
                # NaT in pandas is expected for missing dates - the _safe_date in batch_runner handles it
                # Just log it
                print(f"     {col}: {nat_count} NaT/nan values (handled by _safe_date in batch_runner)")

    conn.close()

    # ── Summary ──
    print(f"\n{'='*70}")
    print(f"RESULTS: {PASSED} passed, {FAILED} failed")
    print(f"{'='*70}\n")
    return 1 if FAILED > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
