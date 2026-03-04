"""
Background pipeline execution with DynamoDB persistence.

Runs VEPipeline in a background thread, classifies all lines (including past residents
and sub-threshold rows for reviewer visibility), enriches with bill PDFs and lease
clauses, and writes everything to DynamoDB.
"""
import json
import logging
import re
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Callable

import pandas as pd

from .config import VEConfig, CHARGE_CODE_MAP
from .pipeline import VEPipeline
from .classifier import classify_detail_df, classify_unmatched_df, get_status_summary, get_suggested_action
from .s3_bills import BillPDFLocator
from .lease_clauses import LeaseClauseFinder
from .web_models import (
    VEBatch, VELineReview, VEBatchStore,
    BATCH_RUNNING, BATCH_READY, BATCH_FAILED,
    ACTION_PENDING,
)

logger = logging.getLogger(__name__)

# Shared thread pool for background pipeline runs
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ve-batch')


def run_batch(
    month: int,
    year: int,
    user: str,
    snowflake_conn,
    store: VEBatchStore,
    admin_fees: Optional[Dict[str, float]] = None,
    corrections_csv_path: Optional[str] = None,
    bill_locator: Optional[BillPDFLocator] = None,
    clause_finder: Optional[LeaseClauseFinder] = None,
    on_progress: Optional[Callable] = None,
) -> str:
    """
    Launch a pipeline batch run in a background thread.

    Args:
        month: Billing month (1-12)
        year: Billing year
        user: Username who triggered the run
        snowflake_conn: Active Snowflake connection
        store: VEBatchStore instance
        admin_fees: Entity ID -> admin fee amount mapping
        corrections_csv_path: Path to corrections CSV
        bill_locator: Optional BillPDFLocator for bill PDF enrichment
        clause_finder: Optional LeaseClauseFinder for lease clause enrichment
        on_progress: Optional callback(batch_id, message) for progress updates

    Returns:
        batch_id string (pipeline runs asynchronously)
    """
    batch = VEBatch(
        month=month,
        year=year,
        status=BATCH_RUNNING,
        created_by=user,
    )
    store.put_batch(batch)
    batch_id = batch.batch_id

    logger.info(f"Starting batch {batch_id}: {month}/{year} by {user}")

    _executor.submit(
        _run_batch_worker,
        batch_id, month, year, snowflake_conn, store,
        admin_fees, corrections_csv_path,
        bill_locator, clause_finder, on_progress,
    )

    return batch_id


def _run_batch_worker(
    batch_id: str,
    month: int,
    year: int,
    snowflake_conn,
    store: VEBatchStore,
    admin_fees: Optional[Dict[str, float]],
    corrections_csv_path: Optional[str],
    bill_locator: Optional[BillPDFLocator],
    clause_finder: Optional[LeaseClauseFinder],
    on_progress: Optional[Callable],
):
    """Background worker that runs the full pipeline and persists results."""
    try:
        _progress(on_progress, batch_id, "Running VE pipeline...")

        config = VEConfig(
            month=month,
            year=year,
            admin_fees=admin_fees or {},
            corrections_csv_path=corrections_csv_path,
        )
        pipeline = VEPipeline(config)
        result = pipeline.run(snowflake_conn)

        _progress(on_progress, batch_id, "Pipeline complete. Classifying lines...")

        # Get the detail DataFrame BEFORE the pipeline's final filtering
        # pipeline.final_df has model/down removed but still has past residents
        # We want to use pipeline's matched_df which is pre-finalization for full visibility
        detail_df = pipeline.final_df if pipeline.final_df is not None else result.detail_df

        # Build lines from detail + unmatched
        lines = []

        # Process matched/detail lines
        if detail_df is not None and not detail_df.empty:
            classified_df = classify_detail_df(detail_df)
            _progress(on_progress, batch_id, f"Classified {len(classified_df)} matched lines")

            for _, row in classified_df.iterrows():
                line = _row_to_line(row, batch_id)
                lines.append(line)

        # Process unmatched lines
        unmatched_df = result.unmatched_df
        if unmatched_df is not None and not unmatched_df.empty:
            classified_unmatched = classify_unmatched_df(unmatched_df)
            _progress(on_progress, batch_id, f"Classified {len(classified_unmatched)} unmatched lines")

            for _, row in classified_unmatched.iterrows():
                line = _unmatched_row_to_line(row, batch_id)
                lines.append(line)

        # Enrich with bill PDFs
        if bill_locator and bill_locator._indexed:
            _progress(on_progress, batch_id, "Finding bill PDFs...")
            _enrich_bill_pdfs(lines, bill_locator)

        # Enrich with lease clauses
        if clause_finder:
            _progress(on_progress, batch_id, "Finding lease utility clauses...")
            _enrich_lease_clauses(lines, clause_finder)

        # Set suggested actions
        for line in lines:
            if line.reviewer_action == ACTION_PENDING:
                line.reviewer_action = ACTION_PENDING  # keep as pending for reviewer

        # Write lines to DynamoDB
        _progress(on_progress, batch_id, f"Writing {len(lines)} lines to DynamoDB...")
        store.put_lines_batch(lines)

        # Calculate summary stats
        total_amount = sum(l.total for l in lines)
        action_counts = {}
        status_counts = {}
        properties = set()
        for l in lines:
            action_counts[l.reviewer_action] = action_counts.get(l.reviewer_action, 0) + 1
            status_counts[l.review_status] = status_counts.get(l.review_status, 0) + 1
            if l.entity_id:
                properties.add(l.entity_id)

        # Update batch to READY
        store.update_batch_status(
            batch_id, BATCH_READY,
            total_lines=len(lines),
            total_amount=round(total_amount, 2),
            total_properties=len(properties),
            match_rate=round(result.stats.match_rate, 2),
            lines_pending=len(lines),
        )

        _progress(on_progress, batch_id, f"Batch ready: {len(lines)} lines, ${total_amount:,.2f}")
        logger.info(f"Batch {batch_id} complete: {len(lines)} lines, {len(properties)} properties")

    except Exception as e:
        logger.error(f"Batch {batch_id} failed: {e}\n{traceback.format_exc()}")
        try:
            store.update_batch_status(batch_id, BATCH_FAILED, error_message=str(e))
        except Exception:
            pass
        _progress(on_progress, batch_id, f"FAILED: {e}")


def _row_to_line(row: pd.Series, batch_id: str) -> VELineReview:
    """Convert a classified detail DataFrame row to a VELineReview."""
    def _safe_str(val, default=''):
        if pd.isna(val) if isinstance(val, (float, type(None))) else False:
            return default
        return str(val) if val is not None else default

    def _safe_date(val):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return ''
        try:
            return pd.to_datetime(val).strftime('%m/%d/%Y')
        except Exception:
            return _safe_str(val)

    def _safe_float(val, default=0.0):
        try:
            v = float(val)
            return v if not pd.isna(v) else default
        except (ValueError, TypeError):
            return default

    def _safe_int(val, default=0):
        try:
            v = int(float(val))
            return v if not pd.isna(float(val)) else default
        except (ValueError, TypeError):
            return default

    return VELineReview(
        batch_id=batch_id,
        entity_id=_safe_str(row.get('entityid')),
        property_name=_safe_str(row.get('Property')),
        bldg_id=_safe_str(row.get('Bldg ID')),
        unit_id=_safe_str(row.get('Unit ID')),
        utility=_safe_str(row.get('Utility')),
        charge_code=_safe_str(row.get('Code')),
        dramount=_safe_float(row.get('dramount')),
        prorated_billback=_safe_float(row.get('Prorated Billback')),
        admin_charge=_safe_float(row.get('Admin Charge')),
        total=_safe_float(row.get('Total')),
        resident_name=_safe_str(row.get('Name')),
        resi_id=_safe_str(row.get('ResiId')),
        resi_status=_safe_str(row.get('ResiStatus')),
        lease_id=_safe_str(row.get('ResiId')),  # ResiId is the lease identifier
        move_in_date=_safe_date(row.get('MoveInDate')),
        move_out_date=_safe_date(row.get('MoveOutDate')),
        bill_start=_safe_date(row.get('Bill Start')),
        bill_end=_safe_date(row.get('Bill End')),
        bill_days=_safe_int(row.get('Bill Days')),
        overlap_start=_safe_date(row.get('Overlap Start')),
        overlap_end=_safe_date(row.get('Overlap End')),
        overlap_days=_safe_int(row.get('Overlap Days')),
        invoicedoc=_safe_str(row.get('invoicedoc')),
        source_invoices=_safe_str(row.get('source_invoices')),
        gl_detail_id=_safe_str(row.get('glDetailId')),
        memo=_safe_str(row.get('memo')),
        review_status=_safe_str(row.get('review_status')),
    )


def _unmatched_row_to_line(row: pd.Series, batch_id: str) -> VELineReview:
    """Convert an unmatched DataFrame row to a VELineReview."""
    def _safe_str(val, default=''):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return default
        return str(val)

    def _safe_float(val, default=0.0):
        try:
            v = float(val)
            return v if not pd.isna(v) else default
        except (ValueError, TypeError):
            return default

    return VELineReview(
        batch_id=batch_id,
        entity_id=_safe_str(row.get('entityid', row.get('entity_id'))),
        property_name=_safe_str(row.get('Property', '')),
        bldg_id=_safe_str(row.get('Bldg ID', row.get('bldg_id'))),
        unit_id=_safe_str(row.get('Unit ID', row.get('unit_id'))),
        utility=_safe_str(row.get('Utility', row.get('utility'))),
        dramount=_safe_float(row.get('dramount', row.get('amount'))),
        total=_safe_float(row.get('dramount', row.get('amount'))),
        review_status=_safe_str(row.get('review_status', 'TRUE_VACANT')),
        invoicedoc=_safe_str(row.get('invoicedoc')),
        gl_detail_id=_safe_str(row.get('glDetailId')),
    )


def _enrich_bill_pdfs(lines: list, locator: BillPDFLocator):
    """Add bill PDF URLs to lines."""
    found = 0
    for line in lines:
        if not line.entity_id or not line.invoicedoc:
            continue
        prop_code = BillPDFLocator.extract_property_code(line.entity_id)
        s3_key = locator.find_bill_pdf(prop_code, line.source_invoices or line.invoicedoc)
        if s3_key:
            line.bill_pdf_key = s3_key
            url = locator.get_presigned_url(s3_key)
            if url:
                line.bill_pdf_url = url
                found += 1
    logger.info(f"Bill PDFs found: {found}/{len(lines)}")


def _enrich_lease_clauses(lines: list, finder: LeaseClauseFinder):
    """Add lease utility clause info to lines."""
    found = 0
    seen = {}  # cache by (entity_id, lease_id) to avoid duplicate S3 calls
    for line in lines:
        if not line.entity_id or not line.resi_id:
            continue
        cache_key = (line.entity_id, line.resi_id)
        if cache_key in seen:
            info = seen[cache_key]
        else:
            info = finder.get_lease_utility_info(line.entity_id, line.resi_id)
            seen[cache_key] = info

        if info.get('found'):
            found += 1
            if info['page_urls']:
                line.lease_page_url = info['page_urls'][0] or ''
            if info['pages']:
                line.lease_page_key = info['pages'][0].pdf_s3_key or ''
            if info['extractions']:
                ext = info['extractions'][0]
                line.lease_extraction = json.dumps({
                    'billing_method': ext.billing_method,
                    'rubs_type': ext.rubs_type,
                    'utility_types': ext.utility_types,
                    'monthly_cap': ext.monthly_cap,
                    'admin_fee': ext.admin_fee,
                    'billing_company': ext.billing_company,
                }, default=str)

    logger.info(f"Lease clauses found: {found}/{len(lines)}")


def _progress(callback, batch_id, message):
    """Call progress callback if provided."""
    if callback:
        try:
            callback(batch_id, message)
        except Exception:
            pass
    logger.info(f"[{batch_id}] {message}")
