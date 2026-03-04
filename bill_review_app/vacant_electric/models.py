"""
Data contracts for VE pipeline inputs and outputs.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict
import pandas as pd


@dataclass
class VEStats:
    """Aggregate statistics for a pipeline run."""
    total_gl_records: int = 0
    gl_after_dedup: int = 0
    gl_after_aggregation: int = 0
    has_unit_string: int = 0
    has_bldg_id: int = 0
    has_unit_id: int = 0
    matched_to_lease: int = 0
    unmatched: int = 0
    match_rate: float = 0.0
    unit_string_corrections_applied: int = 0
    mapped_unit_corrections_applied: int = 0
    after_overlap_filter: int = 0
    after_status_dedup: int = 0
    final_charge_rows: int = 0
    net_negative_removed: int = 0
    under_threshold_removed: int = 0
    past_residents_removed: int = 0
    total_billback_amount: float = 0.0
    total_admin_fees: float = 0.0
    total_expense: float = 0.0
    recovery_rate: float = 0.0
    properties_count: int = 0


@dataclass
class PropertySummary:
    """Per-property summary of VE charges."""
    entity_id: str
    property_name: str
    total_amount: float = 0.0
    charge_count: int = 0
    total_expense: float = 0.0
    recovery_rate: float = 0.0
    matched: int = 0
    unmatched: int = 0
    match_rate: float = 0.0


@dataclass
class UnmatchedRecord:
    """Detail for a GL record that couldn't be matched to a lease."""
    entity_id: str
    description: str
    unit_string: Optional[str]
    bldg_id: Optional[str]
    unit_id: Optional[str]
    key: Optional[str]
    amount: float
    utility: str
    failure_reason: str  # 'no_unit_string', 'no_bldg_mapping', 'no_unit_mapping', 'no_lease_match'


@dataclass
class VEResult:
    """Complete output from a VE pipeline run."""
    stats: VEStats = field(default_factory=VEStats)
    property_summaries: List[PropertySummary] = field(default_factory=list)
    unmatched_records: List[UnmatchedRecord] = field(default_factory=list)

    # DataFrames for downstream consumers
    charges_df: Optional[pd.DataFrame] = None       # Final aggregated charges (agg_df)
    entrata_csv_df: Optional[pd.DataFrame] = None    # 19-column Entrata upload format
    detail_df: Optional[pd.DataFrame] = None         # Pre-aggregation detail (final_df)
    unmatched_df: Optional[pd.DataFrame] = None      # Unmatched GL records
    errors_df: Optional[pd.DataFrame] = None         # Error rows (! in description)
    match_summary_df: Optional[pd.DataFrame] = None  # Per-property match rates
    expense_df: Optional[pd.DataFrame] = None        # Per-property total expense

    # PDF bytes keyed by entity_id
    pdf_reports: Dict[str, bytes] = field(default_factory=dict)
