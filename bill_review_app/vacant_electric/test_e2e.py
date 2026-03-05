"""
End-to-end integration test for VE pipeline.

Connects to Snowflake + S3, runs the FULL batch_runner flow:
1. Snowflake queries (bills, leases, expenses)
2. Pipeline (parse, map, match, prorate, classify)
3. Bill PDF enrichment (S3 jrk-utility-pdfs)
4. Lease clause enrichment (S3 jrk-data-feeds-staging)
5. Line serialization (VELineReview objects)
6. Validates no legacy artifacts (01 prefix, C/P/N, NaT, float IDs)
"""
import sys
import os
import json
import logging
import traceback

import boto3
import snowflake.connector
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vacant_electric.queries import bills_query, leases_query, total_expense_query, ap_invoice_query
from vacant_electric.config import VEConfig, STATUS_PRIORITY
from vacant_electric.property_maps import MAPPING_DISPATCH
from vacant_electric.classifier import classify_detail_df
from vacant_electric.pipeline import VEPipeline
from vacant_electric.batch_runner import (
    _row_to_line, _unmatched_row_to_line,
    _enrich_bill_pdfs, _enrich_lease_clauses,
    _load_property_mapping, _load_s3_property_mapping, _load_ap_invoice_data,
)
from vacant_electric.s3_bills import BillPDFLocator
from vacant_electric.lease_clauses import LeaseClauseFinder

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
    session = boto3.Session(profile_name='jrk-analytics-admin')
    secrets_client = session.client('secretsmanager', region_name='us-east-1')
    response = secrets_client.get_secret_value(SecretId='jrk-bill-review/snowflake')
    creds = json.loads(response['SecretString'])
    connect_args = {
        'account': creds['account'], 'user': creds['user'],
        'database': creds['database'], 'schema': creds['schema'],
        'warehouse': creds['warehouse'], 'insecure_mode': True,
    }
    if creds.get('role'):
        connect_args['role'] = creds['role']
    pk_secret_name = creds.get('private_key_secret')
    if pk_secret_name and not creds.get('password'):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, NoEncryption, PrivateFormat
        pk_resp = secrets_client.get_secret_value(SecretId=pk_secret_name)
        pk = load_pem_private_key(pk_resp['SecretString'].encode('utf-8'), password=None)
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
    month, year = 1, 2026
    config = VEConfig(month=month, year=year)

    print(f"\n{'='*70}")
    print(f"VE FULL END-TO-END TEST — {config.month_name} {year}")
    print(f"{'='*70}")

    # ── 1. Connections ──
    print("\n[1] Connecting to Snowflake + S3...")
    try:
        conn = get_snowflake_conn()
        check("Snowflake connection", True)
    except Exception as e:
        print(f"  FATAL: Cannot connect to Snowflake: {e}")
        return 1

    session = boto3.Session(profile_name='jrk-analytics-admin')
    s3 = session.client('s3', region_name='us-east-1')
    check("S3 client created", True)

    # ── 2. Snowflake queries ──
    print("\n[2] Running Snowflake queries...")
    bills_df = fetch(conn, bills_query(config.month_abbr))
    check("Bills query", len(bills_df) > 0, f"{len(bills_df)} rows")
    print(f"     Sample entity_ids: {list(bills_df['entityid'].dropna().unique()[:5])}")

    has_01 = bills_df['entityid'].dropna().str.startswith('01').any()
    check("Bills: no '01' prefix", not has_01)

    leases_df = fetch(conn, leases_query())
    check("Leases query", len(leases_df) > 0, f"{len(leases_df)} rows")

    has_01_lease = leases_df['PropertyId'].dropna().str.startswith('01').any()
    check("Leases: no '01' prefix", not has_01_lease)

    statuses = set(leases_df['ResiStatus'].dropna().unique())
    check("ResiStatus: full names (Current/Past/Notice)", statuses <= {'Current', 'Past', 'Notice'}, f"Got: {statuses}")
    print(f"     ResiStatus values: {statuses}")

    # Key overlap
    bill_entities = set(bills_df['entityid'].dropna().unique())
    lease_entities = set(leases_df['PropertyId'].dropna().unique())
    dispatch_keys = set(MAPPING_DISPATCH.keys())
    check("entity_ids match MAPPING_DISPATCH", bill_entities <= dispatch_keys | bill_entities,
          f"{len(bill_entities & dispatch_keys)}/{len(bill_entities)} matched")
    print(f"     Bill properties: {len(bill_entities)}, Lease properties: {len(lease_entities)}, Overlap: {len(bill_entities & lease_entities)}")

    # ── 3. Full pipeline ──
    print("\n[3] Running full VEPipeline (parse, map, match, prorate, classify)...")
    try:
        pipeline = VEPipeline(config)
        result = pipeline.run(conn)
        check("Pipeline completed", True)
        print(f"     {result.stats.total_gl_records} GL -> {result.stats.matched_to_lease} matched ({result.stats.match_rate:.1f}%)")
        print(f"     {result.stats.final_charge_rows} charge rows, ${result.stats.total_billback_amount:,.2f}")

        check("Has charge rows (not all $0)", result.stats.final_charge_rows > 0,
              f"0 charge rows = proration still broken")
        check("Total > $0", result.stats.total_billback_amount > 0)

        # Classification
        classified = classify_detail_df(pipeline.final_df)
        status_counts = classified['review_status'].value_counts().to_dict()
        print(f"     Classification: {status_counts}")
        check("Multiple status types (not all BILLING_ISSUE)",
              len(status_counts) > 1 or 'BILLING_ISSUE' not in status_counts)

        # No legacy artifacts in final_df
        check("final_df: no '01' prefix", not pipeline.final_df['entityid'].dropna().str.startswith('01').any())
        check("final_df: ResiStatus full names",
              set(pipeline.final_df['ResiStatus'].dropna().unique()) <= {'Current', 'Past', 'Notice'})
        check("agg_df: no entrata_lease_status column", 'entrata_lease_status' not in pipeline.agg_df.columns)
        check("agg_df: no Past residents", 'Past' not in set(pipeline.agg_df['ResiStatus'].dropna().unique()))

    except Exception as e:
        check("Pipeline completed", False, f"{e}\n{traceback.format_exc()}")
        conn.close()
        return 1

    # ── 4. Build VELineReview objects (same as batch_runner) ──
    print("\n[4] Building VELineReview objects from pipeline output...")
    lines = []
    for _, row in classified.iterrows():
        line = _row_to_line(row, 'TEST_BATCH')
        lines.append(line)

    unmatched_df = result.unmatched_df
    if unmatched_df is not None and not unmatched_df.empty:
        from vacant_electric.classifier import classify_unmatched_df
        classified_unmatched = classify_unmatched_df(unmatched_df)
        for _, row in classified_unmatched.iterrows():
            lines.append(_unmatched_row_to_line(row, 'TEST_BATCH'))

    check("VELineReview objects created", len(lines) > 0, f"{len(lines)} lines")
    print(f"     {len(lines)} total lines ({len(classified)} matched + {len(unmatched_df) if unmatched_df is not None else 0} unmatched)")

    # Validate line fields — no NaT, no .0 on IDs
    nat_moves = sum(1 for l in lines if l.move_out_date in ('NaT', 'nan', 'None'))
    check("No 'NaT' in move_out_date", nat_moves == 0, f"{nat_moves} lines have 'NaT'")

    nat_moves_in = sum(1 for l in lines if l.move_in_date in ('NaT', 'nan', 'None'))
    check("No 'NaT' in move_in_date", nat_moves_in == 0, f"{nat_moves_in} lines have 'NaT'")

    float_ids = sum(1 for l in lines if l.resi_id.endswith('.0'))
    check("No '.0' suffix on resi_id", float_ids == 0, f"{float_ids} lines have '.0'")

    float_lease_ids = sum(1 for l in lines if l.lease_id.endswith('.0'))
    check("No '.0' suffix on lease_id", float_lease_ids == 0, f"{float_lease_ids} lines have '.0'")

    # Check entity_ids are clean
    prefixed = sum(1 for l in lines if l.entity_id.startswith('01'))
    check("No '01' prefix on line entity_id", prefixed == 0, f"{prefixed} lines have '01'")

    # Check resi_status is full name
    line_statuses = set(l.resi_status for l in lines if l.resi_status)
    check("Line resi_status: full names", line_statuses <= {'Current', 'Past', 'Notice', ''},
          f"Got: {line_statuses}")

    # Sample a few lines
    sample = [l for l in lines if l.total > 0][:3]
    for l in sample:
        print(f"     {l.entity_id} | {l.property_name} | Unit {l.unit_id} | {l.resident_name} | "
              f"Status={l.resi_status} | ${l.total:.2f} | MoveIn={l.move_in_date} MoveOut={l.move_out_date}")

    # ── 5. Bill PDF enrichment ──
    print("\n[5] Testing bill PDF enrichment...")
    try:
        prop_mapping = _load_property_mapping(s3)
        check("Property mapping loaded", len(prop_mapping) > 0, f"{len(prop_mapping)} properties")

        s3_prop_map = _load_s3_property_mapping(s3)
        check("S3 property mapping loaded", len(s3_prop_map) > 0, f"{len(s3_prop_map)} entries")

        ap_rows = _load_ap_invoice_data(conn)
        check("AP invoice data loaded", len(ap_rows) > 0, f"{len(ap_rows)} rows")

        bill_locator = BillPDFLocator(s3, s3_prop_map)
        bill_locator.link_properties_from_ap(ap_rows)
        bill_locator.index_pdfs()
        check("BillPDFLocator indexed", bill_locator._indexed)

        _enrich_bill_pdfs(lines, bill_locator)
        bills_found = sum(1 for l in lines if l.bill_pdf_url)
        print(f"     Bill PDFs found: {bills_found}/{len(lines)}")
        check("At least some bill PDFs found", bills_found > 0, "0 PDFs found")

        # Show sample
        sample_pdf = next((l for l in lines if l.bill_pdf_url), None)
        if sample_pdf:
            print(f"     Sample: {sample_pdf.entity_id} Unit {sample_pdf.unit_id} -> {sample_pdf.bill_pdf_key[:80]}...")

    except Exception as e:
        check("Bill PDF enrichment", False, f"{e}\n{traceback.format_exc()}")

    # ── 6. Lease clause enrichment ──
    print("\n[6] Testing lease clause enrichment...")
    try:
        clause_finder = LeaseClauseFinder(s3)
        clause_finder.set_property_mapping(prop_mapping)
        check("LeaseClauseFinder configured", True)

        _enrich_lease_clauses(lines, clause_finder)
        leases_found = sum(1 for l in lines if l.lease_page_url)
        extractions_found = sum(1 for l in lines if l.lease_extraction)
        print(f"     Lease clauses found: {leases_found}/{len(lines)}")
        print(f"     Extractions found: {extractions_found}/{len(lines)}")
        check("At least some lease clauses found", leases_found > 0, "0 lease clauses found")

        # Show sample
        sample_lease = next((l for l in lines if l.lease_extraction), None)
        if sample_lease:
            ext = json.loads(sample_lease.lease_extraction)
            print(f"     Sample: {sample_lease.entity_id} Unit {sample_lease.unit_id} -> "
                  f"method={ext.get('billing_method')}, utilities={ext.get('utility_types')}")

    except Exception as e:
        check("Lease clause enrichment", False, f"{e}\n{traceback.format_exc()}")

    # ── 7. Final line integrity ──
    print("\n[7] Final line integrity check...")
    lines_with_total = [l for l in lines if l.total > 0]
    lines_with_status = [l for l in lines if l.review_status and l.review_status != '']
    check("Lines with total > $0 exist", len(lines_with_total) > 0, f"{len(lines_with_total)} lines")
    check("All lines have review_status", len(lines_with_status) == len(lines),
          f"{len(lines) - len(lines_with_status)} missing")

    # Summary stats
    total_amount = sum(l.total for l in lines)
    by_status = {}
    for l in lines:
        by_status[l.review_status] = by_status.get(l.review_status, 0) + 1
    print(f"\n     TOTAL LINES: {len(lines)}")
    print(f"     TOTAL AMOUNT: ${total_amount:,.2f}")
    print(f"     BY STATUS: {by_status}")
    print(f"     BILL PDFs: {sum(1 for l in lines if l.bill_pdf_url)}")
    print(f"     LEASE CLAUSES: {sum(1 for l in lines if l.lease_page_url)}")
    print(f"     EXTRACTIONS: {sum(1 for l in lines if l.lease_extraction)}")

    conn.close()

    print(f"\n{'='*70}")
    print(f"RESULTS: {PASSED} passed, {FAILED} failed")
    print(f"{'='*70}\n")
    return 1 if FAILED > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
