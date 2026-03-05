"""
Line status classification for VE review workflow.

Post-processing on VEResult DataFrames. Adds a `review_status` column that classifies
each line for the reviewer, without modifying the core pipeline logic.

Statuses:
    CURRENT_RESIDENT  - ResiStatus=C, full overlap with bill period
    NEW_RESIDENT      - ResiStatus=C, MoveIn after BillStart (partial overlap, proration applies)
    NOTICE_RESIDENT   - ResiStatus=N
    PAST_RESIDENT     - ResiStatus=P (pipeline normally drops these — we flag instead)
    TRUE_VACANT       - No lease overlap at all during bill period
    BILLING_ISSUE     - Parse failure, mapping failure, no match, error flag, net negative, under threshold
"""
import pandas as pd
import numpy as np
import logging
from typing import Optional

from .config import MIN_BILLBACK_THRESHOLD

logger = logging.getLogger(__name__)

# Classification constants
STATUS_CURRENT = 'CURRENT_RESIDENT'
STATUS_NEW = 'NEW_RESIDENT'
STATUS_NOTICE = 'NOTICE_RESIDENT'
STATUS_PAST = 'PAST_RESIDENT'
STATUS_VACANT = 'TRUE_VACANT'
STATUS_ISSUE = 'BILLING_ISSUE'

ALL_STATUSES = [STATUS_CURRENT, STATUS_NEW, STATUS_NOTICE, STATUS_PAST, STATUS_VACANT, STATUS_ISSUE]

# Badge colors for the web UI
STATUS_COLORS = {
    STATUS_CURRENT: {'bg': '#dbeafe', 'text': '#1e40af'},   # blue
    STATUS_NEW:     {'bg': '#d1fae5', 'text': '#065f46'},   # green
    STATUS_NOTICE:  {'bg': '#fef3c7', 'text': '#92400e'},   # amber
    STATUS_PAST:    {'bg': '#fce7f3', 'text': '#9d174d'},   # pink
    STATUS_VACANT:  {'bg': '#e5e7eb', 'text': '#374151'},   # gray
    STATUS_ISSUE:   {'bg': '#fee2e2', 'text': '#991b1b'},   # red
}


def classify_line(row: pd.Series) -> str:
    """
    Classify a single line from the detail DataFrame.

    Expects columns: ResiStatus, MoveInDate, MoveOutDate, Bill Start, Bill End,
                     dramount, Total, Overlap Days, description
    """
    # Check for billing issues first
    desc = str(row.get('description', ''))
    if '!' in desc:
        return STATUS_ISSUE

    resi_status = row.get('ResiStatus')
    total = row.get('Total', 0)
    dramount = row.get('dramount', 0)
    overlap_days = row.get('Overlap Days', 0)

    # No resident match at all
    if pd.isna(resi_status) or resi_status == '':
        return STATUS_VACANT

    # Net negative or zero
    if pd.notna(total) and total <= 0:
        return STATUS_ISSUE

    # Under threshold (but still show it)
    if pd.notna(total) and 0 < total <= MIN_BILLBACK_THRESHOLD:
        return STATUS_ISSUE

    # No overlap
    if pd.isna(overlap_days) or overlap_days <= 0:
        return STATUS_ISSUE

    # Past resident
    if resi_status == 'Past':
        return STATUS_PAST

    # Notice resident
    if resi_status == 'Notice':
        return STATUS_NOTICE

    # Current resident — check if new (partial overlap from move-in)
    if resi_status == 'Current':
        move_in = row.get('MoveInDate')
        bill_start = row.get('Bill Start')
        if pd.notna(move_in) and pd.notna(bill_start):
            try:
                move_in_dt = pd.to_datetime(move_in)
                bill_start_dt = pd.to_datetime(bill_start)
                if move_in_dt > bill_start_dt:
                    return STATUS_NEW
            except Exception:
                pass
        return STATUS_CURRENT

    return STATUS_ISSUE


def classify_detail_df(detail_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add review_status column to a detail DataFrame.

    This is the main entry point. Takes the pipeline's detail_df (pre-aggregation)
    and adds classification without filtering any rows.

    Args:
        detail_df: DataFrame from VEPipeline (final_df or matched_df)

    Returns:
        DataFrame with added 'review_status' column.
    """
    if detail_df is None or detail_df.empty:
        logger.warning("Empty DataFrame passed to classifier")
        return detail_df

    df = detail_df.copy()
    df['review_status'] = df.apply(classify_line, axis=1)

    # Log summary
    counts = df['review_status'].value_counts()
    logger.info(f"Classification results: {counts.to_dict()}")

    return df


def classify_unmatched_df(unmatched_df: pd.DataFrame) -> pd.DataFrame:
    """
    Classify unmatched records. All get TRUE_VACANT or BILLING_ISSUE.

    Args:
        unmatched_df: DataFrame of unmatched GL records from pipeline

    Returns:
        DataFrame with added 'review_status' column.
    """
    if unmatched_df is None or unmatched_df.empty:
        return unmatched_df

    df = unmatched_df.copy()

    def classify_unmatched_row(row):
        reason = row.get('failure_reason', '')
        if reason in ('no_unit_string', 'no_bldg_mapping', 'no_unit_mapping'):
            return STATUS_ISSUE
        return STATUS_VACANT  # no_lease_match = truly vacant unit

    if 'failure_reason' in df.columns:
        df['review_status'] = df.apply(classify_unmatched_row, axis=1)
    else:
        df['review_status'] = STATUS_VACANT

    return df


def classify_agg_df(agg_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add review_status to the aggregated charges DataFrame.
    Uses the same logic but on aggregated rows (post-aggregation).
    """
    if agg_df is None or agg_df.empty:
        return agg_df

    df = agg_df.copy()
    df['review_status'] = df.apply(classify_line, axis=1)
    return df


def get_status_summary(df: pd.DataFrame) -> dict:
    """
    Get summary counts and amounts by review_status.

    Returns dict like:
        {'CURRENT_RESIDENT': {'count': 150, 'amount': 12345.67}, ...}
    """
    if df is None or df.empty or 'review_status' not in df.columns:
        return {}

    amount_col = 'Total' if 'Total' in df.columns else 'dramount'
    summary = {}
    for status in ALL_STATUSES:
        mask = df['review_status'] == status
        summary[status] = {
            'count': int(mask.sum()),
            'amount': round(float(df.loc[mask, amount_col].sum()), 2) if amount_col in df.columns else 0,
        }
    return summary


def get_suggested_action(review_status: str) -> str:
    """
    Suggest a default reviewer action based on classification.

    Returns: 'APPROVE', 'FLAG', or 'EXCLUDE'
    """
    if review_status in (STATUS_CURRENT, STATUS_NEW):
        return 'APPROVE'
    if review_status == STATUS_NOTICE:
        return 'APPROVE'
    if review_status == STATUS_PAST:
        return 'EXCLUDE'
    if review_status == STATUS_VACANT:
        return 'EXCLUDE'
    if review_status == STATUS_ISSUE:
        return 'FLAG'
    return 'FLAG'
