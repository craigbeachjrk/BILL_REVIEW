"""
Lease matching, proration, admin fees, and final aggregation.

Critical business rules:
1. GL aggregation to invoice level happens BEFORE lease matching
2. Composite key: entityid + bldg_id + unit_id
3. Lease dedup priority: Current > Notice > Past
4. Admin fee: once per (Name, Key, invoicedoc) tuple, NOT per GL line
5. Minimum $5 billback threshold; Past residents removed from final output
"""
import pandas as pd
import numpy as np
from datetime import timedelta
from typing import Dict, Tuple

from .config import STATUS_PRIORITY, MIN_BILLBACK_THRESHOLD, MIN_ADMIN_OVERLAP_DAYS


def aggregate_gl_to_invoice(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse multi-line GL entries to invoice level BEFORE matching.
    GL_TRANSACTIONS has multiple line items per invoice (delivery, supply, taxes, fees).
    Collapse to one row per (entityid, unit, invoicedoc, utility) and sum dramount.
    """
    pre_count = len(df)
    agg = df.groupby(
        ['entityid', 'Property', 'Bldg ID', 'Unit ID', 'Key', 'invoicedoc', 'Utility', 'accountno'],
        dropna=False
    ).agg(
        dramount=('dramount', 'sum'),
        Bill_Start=('Bill Start', 'min'),
        Bill_End=('Bill End', 'max'),
        n_gl_lines=('dramount', 'count'),
        description=('description', 'first'),
        Created=('Created', 'first'),
        glDetailId=('glDetailId', 'first'),
        ApprovedYN=('ApprovedYN', 'first'),
        cramount=('cramount', 'sum'),
        Unit_String=('Unit String', 'first'),
        key_matched=('key_matched', 'first')
    ).reset_index()
    agg.rename(columns={
        'Bill_Start': 'Bill Start',
        'Bill_End': 'Bill End',
        'Unit_String': 'Unit String'
    }, inplace=True)
    # Snowflake TRY_TO_NUMBER returns Decimal; cast to float for arithmetic
    agg['dramount'] = agg['dramount'].astype(float)
    agg['cramount'] = agg['cramount'].astype(float)
    print(f"  Aggregated GL line items by invoice: {pre_count} -> {len(agg)} records")
    print(f"  ({pre_count - len(agg)} GL line items collapsed into invoice-level rows)")
    return agg


def join_with_leases(gl_df: pd.DataFrame, lease_df: pd.DataFrame) -> pd.DataFrame:
    """Left join GL records with rent roll on composite key."""
    return pd.merge(
        gl_df,
        lease_df[['Key', 'ResiStatus', 'ResiId', 'ResiFirstName', 'ResiLastName', 'MoveInDate', 'MoveOutDate']],
        on='Key',
        how='left'
    )


def filter_overlap(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only leases that overlap with the bill period."""
    df['MoveInDate'] = pd.to_datetime(df['MoveInDate'], errors='coerce')
    df['MoveOutDate'] = pd.to_datetime(df['MoveOutDate'], errors='coerce')
    return df[
        (df['MoveInDate'] <= df['Bill End']) &
        ((df['MoveOutDate'] >= df['Bill Start']) | pd.isna(df['MoveOutDate']))
    ]


def dedup_by_status(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep one resident per GL record, preferring Current > Notice > Past.
    Uses glDetailId as the dedup key.
    """
    df = df.copy()
    df['_prio'] = df['ResiStatus'].map(STATUS_PRIORITY).fillna(3)
    df = df.sort_values(['glDetailId', '_prio']).drop_duplicates(subset=['glDetailId'], keep='first')
    df = df.drop(columns=['_prio'])
    print(f"  After resident dedup: {len(df)} records (1 resident per GL record)")
    return df


def calculate_proration(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate overlap days and prorated billback amount.
    Overlap = intersection of (MoveIn, MoveOut) and (Bill Start, Bill End).
    Prorated = (overlap_days / bill_days) * dramount.
    """
    df = df.copy()
    df['Overlap Start'] = df[['MoveInDate', 'Bill Start']].max(axis=1)
    df['Overlap End'] = df[['MoveOutDate', 'Bill End']].min(axis=1)
    df['Overlap Days'] = (df['Overlap End'] - df['Overlap Start']).dt.days + 1

    prorated_list = []
    for idx, row in df.iterrows():
        try:
            overlap_days = row['Overlap Days']
            bill_start = pd.to_datetime(row['Bill Start'], errors='coerce')
            bill_end = pd.to_datetime(row['Bill End'], errors='coerce')
            dramount = float(row['dramount'])
            if pd.isna(overlap_days) or pd.isna(bill_start) or pd.isna(bill_end) or pd.isna(dramount):
                prorated_list.append(0)
                continue
            total_days = (bill_end - bill_start + timedelta(days=1)).days
            prorated = (overlap_days / total_days) * dramount
            prorated_list.append(prorated)
        except Exception:
            prorated_list.append(0)
    df['Prorated Billback'] = prorated_list
    return df


def apply_admin_fees(df: pd.DataFrame, admin_dict: Dict[str, float]) -> pd.DataFrame:
    """
    Apply admin fees: ONE fee per unique invoice per resident per unit.
    Tracked by (Name, Key, invoicedoc) set.

    Rules:
    - Only if dramount > 0 (actual charge, not credit)
    - Only if Overlap Days > MIN_ADMIN_OVERLAP_DAYS (3)
    - Only once per (Name, Key, invoicedoc) tuple
    """
    df = df.copy()
    df = df.sort_values(by=['entityid', 'Bldg ID', 'Unit ID'])
    df['Name'] = df['ResiFirstName'].astype(str) + " " + df['ResiLastName'].astype(str)
    df.drop(columns=['ResiFirstName', 'ResiLastName'], inplace=True)

    admin_charges = []
    seen_invoices = set()
    for index, row in df.iterrows():
        try:
            invoice_key = (row['Name'], row['Key'], row['invoicedoc'])
            if (row['dramount'] > 0
                    and row['Overlap Days'] > MIN_ADMIN_OVERLAP_DAYS
                    and invoice_key not in seen_invoices):
                charge = admin_dict.get(row['entityid'], 0)
                admin_charges.append(charge)
                seen_invoices.add(invoice_key)
            else:
                admin_charges.append(0)
        except:
            admin_charges.append(0)

    df['Admin Charge'] = admin_charges
    df['Total'] = df['Admin Charge'] + df['Prorated Billback']
    return df


def finalize_detail(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Apply final filters to detail DataFrame:
    - Map charge codes and lease status names
    - Separate error rows (! in description)
    - Calculate Bill Days
    - Remove Model/Down units

    Returns:
        (final_df, errors_df)
    """
    from .config import CHARGE_CODE_MAP

    df = df.copy()
    df['Code'] = df['Utility'].map(CHARGE_CODE_MAP)

    # Separate errors
    df['error'] = df['description'].apply(lambda d: "!" in str(d))
    errors_df = df[df['error'] == True].copy()
    df = df[df['error'] == False].copy()

    # Bill Days
    temp_diff = []
    for idx, row in df.iterrows():
        try:
            diff = (row['Bill End'] - row['Bill Start']).days + 1
            temp_diff.append(diff)
        except:
            temp_diff.append(np.nan)
    df['Bill Days'] = temp_diff
    df = df[df['Bill Days'].notna()]

    df['Total'] = df['Total'].round(decimals=2)

    # Filter model/down units
    df = df[df["Name"] != "Model Model"]
    df = df[df["Name"] != "Down Down"]

    return df, errors_df


def aggregate_charges(df: pd.DataFrame) -> pd.DataFrame:
    """
    Final aggregation: one row per property+building+unit+utility+service_period.
    Apply $5 threshold and remove Past residents.
    """
    # Build memo column before aggregation
    df = df.copy()
    df['memo'] = (
        df['Utility'].astype(str) + ' '
        + df['Overlap Start'].astype(str) + '-'
        + df['Overlap End'].astype(str)
    )

    agg = df.groupby(
        ['Property', 'entityid', 'Bldg ID', 'Unit ID', 'Code', 'memo',
         'ResiId', 'Name', 'MoveInDate', 'MoveOutDate', 'ResiStatus',
         'Utility', 'Bill Start', 'Bill End', 'Bill Days', 'Overlap Start', 'Overlap End', 'Overlap Days'],
        dropna=False
    ).agg(
        Total=('Total', 'sum'),
        dramount=('dramount', 'sum'),
        Prorated_Billback=('Prorated Billback', 'sum'),
        Admin_Charge=('Admin Charge', 'sum'),
        source_invoices=('invoicedoc', lambda x: ' | '.join(sorted(set(str(v) for v in x if pd.notna(v)))))
    ).reset_index()

    agg['Total'] = agg['Total'].round(2)

    pre_filter = len(agg)
    net_negative = (agg['Total'] <= 0).sum()
    net_small = ((agg['Total'] > 0) & (agg['Total'] <= MIN_BILLBACK_THRESHOLD)).sum()

    agg = agg[agg['Total'] > MIN_BILLBACK_THRESHOLD]

    past_removed = (agg['ResiStatus'] == 'Past').sum()
    agg = agg[agg['ResiStatus'] != 'Past']

    print(f"\n  Aggregated: {len(df)} detail rows -> {pre_filter} charge lines")
    print(f"  Removed: {net_negative} net-zero/negative, {net_small} under ${MIN_BILLBACK_THRESHOLD}, {past_removed} past residents -> {len(agg)} final rows")

    return agg
