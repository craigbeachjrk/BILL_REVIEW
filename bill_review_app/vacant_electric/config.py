"""
VE Pipeline configuration: constants, GL accounts, utility mappings, and VEConfig dataclass.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Optional
import calendar

# GL accounts that carry vacant utility charges
GL_ACCOUNTS = ('5705-0000', '5715-0000', '5720-1000', '5721-1000')

# GL account number -> human-readable utility name
UTILITY_NAME_MAP = {
    '5705-0000': 'Vacant Electric',
    '5708-0000': 'EPS is Weird',
    '5715-0000': 'Vacant Gas',
    '5720-1000': 'Vacant Water',
    '5721-1000': 'Vacant Sewer',
}

# Utility name -> Entrata charge code
CHARGE_CODE_MAP = {
    'Vacant Electric': 'ELECR - Util. Non-Comp-Elect',
    'Vacant Sewer':    'SEWRR - SEWER ADJ',
    'Vacant Water':    'WATRR - WATER',
    'Vacant Gas':      'GASRE - Util Non-Compli GAS',
}

# Entrata lease status abbreviation -> full name
LEASE_STATUS_MAP = {
    'C': 'Current',
    'P': 'Past',
    'N': 'Notice',
}

# Lease dedup priority (lower = preferred)
STATUS_PRIORITY = {'C': 0, 'N': 1, 'P': 2}

# Minimum billback threshold - rows at or below this are filtered out
MIN_BILLBACK_THRESHOLD = 5.00

# Minimum overlap days for admin fee eligibility
MIN_ADMIN_OVERLAP_DAYS = 3

# VE memo patterns for SQL filtering
VE_MEMO_PATTERNS = ('VE', 'VG', 'VW', 'VS')


@dataclass
class VEConfig:
    """Configuration for a single VE pipeline run."""
    month: int          # 1-12, the billing month (e.g., 1 for January close)
    year: int           # 4-digit year of the billing month
    admin_fees: Dict[str, float] = field(default_factory=dict)  # entityid -> admin fee amount
    corrections_csv_path: Optional[str] = None
    output_dir: Optional[str] = None

    @property
    def month_name(self) -> str:
        """Full month name, e.g. 'January'."""
        return calendar.month_name[self.month]

    @property
    def month_abbr(self) -> str:
        """Snowflake postMonth format, e.g. 'Jan,2026'."""
        return f"{calendar.month_abbr[self.month]},{self.year}"

    @property
    def post_date(self) -> str:
        """Post date string, first of the NEXT month, e.g. '02/01/2026' for Jan close."""
        if self.month == 12:
            post_m, post_y = 1, self.year + 1
        else:
            post_m, post_y = self.month + 1, self.year
        return f"{post_m:02d}/01/{post_y}"

    @property
    def post_month(self) -> str:
        """Post month string (same as post_date)."""
        return self.post_date

    @property
    def converted_post_month(self) -> str:
        """Query filter date, first of the billing month, e.g. '1/01/2026'."""
        return f"{self.month}/01/{self.year}"

    @property
    def selected_month(self) -> str:
        """Alias for month_name, backward compat with original script."""
        return self.month_name

    @property
    def selected_year(self) -> str:
        """Year as string."""
        return str(self.year)
