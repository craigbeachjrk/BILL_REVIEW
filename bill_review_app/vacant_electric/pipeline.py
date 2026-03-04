"""
VEPipeline orchestrator - runs the full VE billing pipeline.
"""
import pandas as pd
import numpy as np
from typing import Optional

from .config import VEConfig, UTILITY_NAME_MAP
from .models import VEResult, VEStats, PropertySummary, UnmatchedRecord
from .property_maps import BLDG, APT, RBV
from .queries import bills_query, leases_query, total_expense_query
from .parser import parse_bill_start, parse_bill_end, parse_unit_string
from .corrections import load_corrections, apply_unit_string_corrections, apply_mapped_unit_corrections
from .matcher import (
    aggregate_gl_to_invoice,
    join_with_leases,
    filter_overlap,
    dedup_by_status,
    calculate_proration,
    apply_admin_fees,
    finalize_detail,
    aggregate_charges,
)
from .reports import generate_entrata_csv, generate_property_pdf


class VEPipeline:
    """
    Orchestrates the full Vacant Electric billing pipeline.

    Intermediate DataFrames are stored as public attributes for inspection.
    Individual step methods can be called separately for debugging.
    """

    def __init__(self, config: VEConfig):
        self.config = config

        # Intermediate state (populated by step methods)
        self.raw_bills_df: Optional[pd.DataFrame] = None
        self.raw_leases_df: Optional[pd.DataFrame] = None
        self.expense_df: Optional[pd.DataFrame] = None
        self.main_df: Optional[pd.DataFrame] = None
        self.lease_df: Optional[pd.DataFrame] = None
        self.matched_df: Optional[pd.DataFrame] = None
        self.final_df: Optional[pd.DataFrame] = None
        self.errors_df: Optional[pd.DataFrame] = None
        self.agg_df: Optional[pd.DataFrame] = None
        self.entrata_csv_df: Optional[pd.DataFrame] = None
        self.unmatched_df: Optional[pd.DataFrame] = None
        self.match_summary_df: Optional[pd.DataFrame] = None

        # Stats tracking
        self._stats = VEStats()

    def run(self, snowflake_conn) -> VEResult:
        """Execute the full pipeline and return results."""
        print(f"=== VACANT ELECTRIC - {self.config.month_name} {self.config.year} CLOSE ===")
        print(f"Query filter: postMonth = {self.config.month_abbr}")
        print(f"Output Post Date/Month: {self.config.post_date}")
        print()

        self.fetch_data(snowflake_conn)
        self.parse_memos()
        self.apply_corrections()
        self.map_properties()
        self.aggregate_gl_lines()
        self.match_and_prorate()
        return self.finalize()

    def fetch_data(self, conn) -> None:
        """Run 3 Snowflake queries and dedup GL on glDetailId."""
        def fetch(query):
            cur = conn.cursor()
            try:
                cur.execute(query)
                return cur.fetch_pandas_all()
            finally:
                cur.close()

        print("Fetching bills data from GL_TRANSACTIONS...")
        self.raw_bills_df = fetch(bills_query(self.config.month_abbr))
        self._stats.total_gl_records = len(self.raw_bills_df)
        print(f"  -> {len(self.raw_bills_df)} bill records retrieved (before dedup)")

        self.raw_bills_df = self.raw_bills_df.drop_duplicates(subset=['glDetailId'])
        self._stats.gl_after_dedup = len(self.raw_bills_df)
        print(f"  -> {len(self.raw_bills_df)} bill records after glDetailId dedup")

        print("Fetching resident/lease data from LEASE_LIVE...")
        self.raw_leases_df = fetch(leases_query())
        print(f"  -> {len(self.raw_leases_df)} lease records retrieved (before dedup)")

        self.raw_leases_df = self.raw_leases_df.drop_duplicates(
            subset=['PropertyId', 'BldgId', 'UnitId', 'ResiId']
        )
        print(f"  -> {len(self.raw_leases_df)} lease records after dedup")

        print("Fetching total expense data for billback % calculation...")
        self.expense_df = fetch(total_expense_query(self.config.month_abbr))
        self.expense_df.columns = [c.lower() for c in self.expense_df.columns]
        self.expense_df['total_debit'] = self.expense_df['total_debit'].astype(float)
        self._stats.total_expense = self.expense_df['total_debit'].sum()
        print(f"  -> {len(self.expense_df)} property expense records, ${self._stats.total_expense:,.2f} total VE expense")

    def parse_memos(self) -> None:
        """Parse dates + unit strings from GL memos, net dr/cr amounts."""
        print("\n=== DATA PROCESSING ===")
        self.main_df = self.raw_bills_df.copy()

        # Net debits and credits
        self.main_df['dramount'] = self.main_df['dramount'].fillna(0) + self.main_df['cramount'].fillna(0)
        credit_rows = (self.main_df['cramount'].fillna(0) < 0).sum()
        print(f"Netting {credit_rows} credit rows into dramount")

        # CEH exclusion
        self.main_df = self.main_df[
            ~((self.main_df['Property'] == '01CEH') &
              (self.main_df['description'].str.contains('200C@M14')))
        ]

        # Map utility names
        self.main_df['Utility'] = self.main_df['accountno'].replace(
            list(UTILITY_NAME_MAP.keys()),
            list(UTILITY_NAME_MAP.values())
        )

        # Parse bill dates and unit strings
        self.main_df['Bill Start'] = self.main_df['description'].apply(parse_bill_start)
        self.main_df['Bill End'] = self.main_df['description'].apply(parse_bill_end)
        self.main_df['Unit String'] = self.main_df['description'].apply(parse_unit_string)

        bs_failures = self.main_df['Bill Start'].isna().sum()
        be_failures = self.main_df['Bill End'].isna().sum()
        us_failures = self.main_df['Unit String'].isna().sum()
        print(f"  Bill Start parse failures: {bs_failures}")
        print(f"  Bill End parse failures: {be_failures}")
        print(f"  Unit String parse failures: {us_failures}")

    def apply_corrections(self) -> None:
        """Load + apply both stages of AI corrections."""
        if not self.config.corrections_csv_path:
            print("  No corrections CSV configured, skipping.")
            return

        us_corrections, mu_corrections = load_corrections(self.config.corrections_csv_path)
        print(f"\n  Loaded {len(us_corrections)} UNIT_STRING corrections")
        print(f"  Loaded {len(mu_corrections)} MAPPED_UNIT corrections")

        # Stage 1: unit string corrections (before mapping)
        self.main_df, count1 = apply_unit_string_corrections(self.main_df, us_corrections)
        self._stats.unit_string_corrections_applied = count1
        print(f"  Applied {count1} unit string corrections")

        # Store mapped_unit corrections for Stage 2 (after mapping)
        self._mu_corrections = mu_corrections

    def map_properties(self) -> None:
        """Run MAP_UNIT + RBV, build composite keys."""
        # Map Building IDs
        bldg_list = []
        bldg_failures = []
        for index, row in self.main_df.iterrows():
            try:
                bldg_id = BLDG(row['entityid'], row['Unit String'])
            except:
                bldg_id = None
            if bldg_id is None:
                bldg_failures.append((index, row['entityid'], row.get('Unit String', ''), row['description']))
            bldg_list.append(bldg_id)
        self.main_df['Bldg ID'] = bldg_list

        # Map Unit IDs (with RBV special handling)
        unitid_list = []
        unitid_failures = []
        for index, row in self.main_df.iterrows():
            try:
                if row['entityid'] == "01RBV":
                    unit_id = RBV(row['description'])
                else:
                    unit_id = APT(row['entityid'], row['Unit String'])
            except:
                unit_id = None
            if unit_id is None:
                unitid_failures.append((index, row['entityid'], row.get('Unit String', ''), row['description']))
            unitid_list.append(unit_id)
        self.main_df['Unit ID'] = unitid_list

        print(f"  Bldg ID mapping failures: {len(bldg_failures)}")
        print(f"  Unit ID mapping failures: {len(unitid_failures)}")

        # Stage 2: Apply MAPPED_UNIT corrections (after mapping)
        if hasattr(self, '_mu_corrections') and self._mu_corrections:
            self.main_df, count2 = apply_mapped_unit_corrections(self.main_df, self._mu_corrections)
            self._stats.mapped_unit_corrections_applied = count2
            print(f"  Applied {count2} mapped unit corrections")

        # Convert dates
        self.main_df['Bill Start'] = pd.to_datetime(self.main_df['Bill Start'], errors='coerce')
        self.main_df['Bill End'] = pd.to_datetime(self.main_df['Bill End'], errors='coerce')
        self.main_df['Created'] = pd.to_datetime(self.main_df['Created'], errors='coerce')

        # Build composite key
        self.main_df['Key'] = (
            self.main_df['entityid'].fillna('').astype(str)
            + self.main_df['Bldg ID'].fillna('').astype(str)
            + self.main_df['Unit ID'].fillna('').astype(str)
        )

        # Prepare lease df with key
        self.lease_df = self.raw_leases_df.copy()
        self.lease_df['Key'] = (
            self.lease_df['PropertyId'].fillna('').astype(str)
            + self.lease_df['BldgId'].fillna('').astype(str)
            + self.lease_df['UnitId'].fillna('').astype(str)
        )

        # Matching analysis
        self._stats.has_unit_string = self.main_df['Unit String'].notna().sum()
        self._stats.has_bldg_id = self.main_df['Bldg ID'].notna().sum()
        self._stats.has_unit_id = self.main_df['Unit ID'].notna().sum()

        valid_keys = set(self.lease_df['Key'].dropna().unique())
        self.main_df['key_matched'] = self.main_df['Key'].isin(valid_keys)
        self._stats.matched_to_lease = int(self.main_df['key_matched'].sum())
        self._stats.unmatched = len(self.main_df) - self._stats.matched_to_lease

        total = len(self.main_df)
        matched = self._stats.matched_to_lease
        self._stats.match_rate = matched / total * 100 if total > 0 else 0.0

        print(f"\n  Total GL records: {total}")
        print(f"  Matched to Lease: {matched} ({self._stats.match_rate:.1f}%)")
        print(f"  UNMATCHED: {self._stats.unmatched}")

    def aggregate_gl_lines(self) -> None:
        """Collapse GL line items to invoice level."""
        self.main_df = aggregate_gl_to_invoice(self.main_df)

    def match_and_prorate(self) -> None:
        """Join with leases, filter overlap, dedup, prorate, admin fees, aggregate."""
        # Save unmatched records before join
        self.unmatched_df = self.main_df[~self.main_df['key_matched']].copy()

        # Join
        print("\n=== Joining with rent roll... ===")
        joined = join_with_leases(self.main_df, self.lease_df)

        # Filter overlap
        overlapping = filter_overlap(joined)

        # Dedup by status
        deduped = dedup_by_status(overlapping)
        self._stats.after_status_dedup = len(deduped)

        # Prorate
        prorated = calculate_proration(deduped)

        # Admin fees
        admin_applied = apply_admin_fees(prorated, self.config.admin_fees)

        # Finalize detail (error separation, bill days, model/down filtering)
        self.final_df, self.errors_df = finalize_detail(admin_applied)

        # Aggregate charges
        self.agg_df = aggregate_charges(self.final_df)
        self._stats.final_charge_rows = len(self.agg_df)
        self._stats.total_billback_amount = float(self.agg_df['Total'].sum())
        self._stats.total_admin_fees = float(self.agg_df['Admin_Charge'].sum())

        if self._stats.total_expense > 0:
            self._stats.recovery_rate = self._stats.total_billback_amount / self._stats.total_expense * 100

    def finalize(self) -> VEResult:
        """Build VEResult with all stats, summaries, DataFrames, and PDFs."""
        self._stats.properties_count = self.agg_df['entityid'].nunique() if self.agg_df is not None else 0

        # Build Entrata CSV
        self.entrata_csv_df = generate_entrata_csv(
            self.agg_df, self.config.post_date, self.config.post_month
        )

        # Build match summary
        if self.main_df is not None:
            self.match_summary_df = self.main_df.groupby('entityid').agg(
                total_records=('dramount', 'count'),
                matched=('key_matched', 'sum'),
                total_amount=('dramount', 'sum'),
            ).reset_index()
            self.match_summary_df['unmatched'] = self.match_summary_df['total_records'] - self.match_summary_df['matched']
            self.match_summary_df['match_pct'] = (
                self.match_summary_df['matched'] / self.match_summary_df['total_records'] * 100
            ).round(1)

        # Build property summaries
        property_summaries = []
        if self.agg_df is not None:
            prop_totals = self.agg_df.groupby(['entityid', 'Property']).agg(
                total_amount=('Total', 'sum'),
                charge_count=('Total', 'count')
            ).reset_index()

            expense_by_prop = {}
            if self.expense_df is not None:
                for _, row in self.expense_df.iterrows():
                    expense_by_prop[row['entityid']] = float(row['total_debit'])

            for _, row in prop_totals.iterrows():
                eid = row['entityid']
                exp = expense_by_prop.get(eid, 0)
                bb = float(row['total_amount'])
                property_summaries.append(PropertySummary(
                    entity_id=eid,
                    property_name=row['Property'],
                    total_amount=bb,
                    charge_count=int(row['charge_count']),
                    total_expense=exp,
                    recovery_rate=bb / exp * 100 if exp > 0 else 0,
                ))

        # Build unmatched record objects
        unmatched_records = []
        if self.unmatched_df is not None:
            for _, row in self.unmatched_df.iterrows():
                us = row.get('Unit String')
                bldg = row.get('Bldg ID')
                uid = row.get('Unit ID')
                if pd.isna(us) or us is None:
                    reason = 'no_unit_string'
                elif pd.isna(bldg) or bldg is None:
                    reason = 'no_bldg_mapping'
                elif pd.isna(uid) or uid is None:
                    reason = 'no_unit_mapping'
                else:
                    reason = 'no_lease_match'
                unmatched_records.append(UnmatchedRecord(
                    entity_id=row['entityid'],
                    description=str(row.get('description', '')),
                    unit_string=str(us) if pd.notna(us) else None,
                    bldg_id=str(bldg) if pd.notna(bldg) else None,
                    unit_id=str(uid) if pd.notna(uid) else None,
                    key=str(row.get('Key', '')) if pd.notna(row.get('Key')) else None,
                    amount=float(row.get('dramount', 0)),
                    utility=str(row.get('Utility', '')),
                    failure_reason=reason,
                ))

        # Generate PDFs per property
        pdf_reports = {}
        if self.agg_df is not None:
            email_df = self.agg_df[[
                'entityid', 'Bldg ID', 'Unit ID', 'ResiId', 'Name', 'MoveInDate', 'MoveOutDate',
                'ResiStatus', 'Utility', 'Bill Start', 'Bill End', 'Bill Days',
                'Overlap Days', 'dramount', 'Prorated_Billback', 'Admin_Charge', 'Total'
            ]].copy()
            email_df = email_df.rename(columns={
                'Prorated_Billback': 'Prorated Billback',
                'Admin_Charge': 'Admin Charge'
            })

            unique_dict = dict(zip(self.agg_df['entityid'], self.agg_df['Property']))
            for eid in sorted(email_df['entityid'].unique()):
                prop_df = email_df[email_df['entityid'] == eid]
                prop_name = unique_dict.get(eid, eid)
                prop_total = float(prop_df['Total'].sum())
                try:
                    pdf_bytes = generate_property_pdf(
                        eid, prop_name, prop_df,
                        self.config.month, self.config.year, prop_total
                    )
                    pdf_reports[eid] = pdf_bytes
                except Exception as e:
                    print(f"  Warning: PDF generation failed for {eid}: {e}")

        # Print summary
        print(f"\n{'='*80}")
        print(f"=== PIPELINE COMPLETE ===")
        print(f"{'='*80}")
        print(f"  Final charge rows: {self._stats.final_charge_rows}")
        print(f"  Total billback: ${self._stats.total_billback_amount:,.2f}")
        print(f"  Total expense: ${self._stats.total_expense:,.2f}")
        print(f"  Recovery rate: {self._stats.recovery_rate:.1f}%")
        print(f"  Properties: {self._stats.properties_count}")
        print(f"  PDFs generated: {len(pdf_reports)}")

        return VEResult(
            stats=self._stats,
            property_summaries=property_summaries,
            unmatched_records=unmatched_records,
            charges_df=self.agg_df,
            entrata_csv_df=self.entrata_csv_df,
            detail_df=self.final_df,
            unmatched_df=self.unmatched_df,
            errors_df=self.errors_df,
            match_summary_df=self.match_summary_df,
            expense_df=self.expense_df,
            pdf_reports=pdf_reports,
        )
