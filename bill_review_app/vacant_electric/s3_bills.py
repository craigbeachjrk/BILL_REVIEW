"""
S3 utility bill PDF retrieval and presigned URL generation.

Ported from the original monolith script (01 - VE Script Feb 2026 Run.py, lines 1573-1850).

Bucket: jrk-utility-pdfs
Structure: {uuid}/bills/{vendor}/{account}/{file}.pdf
"""
import re
import json
import logging
from datetime import datetime
from functools import lru_cache
from typing import Optional, List, Dict, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

S3_BUCKET = 'jrk-utility-pdfs'
PRESIGNED_URL_EXPIRY = 604800  # 7 days

MONTH_ABBRS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

# Vendor name aliases: AP vendor name -> S3 folder name
VENDOR_ALIASES = {
    'Central Maine Power': 'cmp', 'Unitil': 'unitil',
    'Eversource': 'eversource', 'Eversource MA': 'eversource',
    'FPL': 'fpl', 'FPL Northwest Fl': 'fpl', 'NextEra Energy/FPL Energy': 'fpl',
    'Duke Energy Payment Processing': 'duke_energy',
    'ComEd': 'comed', 'Comed': 'comed',
    'Nicor Gas': 'nicor_gas',
    'PECO': 'peco', 'PECO- Payment Processing': 'peco',
    'Pepco': 'pepco', 'WSSC': 'wssc', 'SRP': 'srp', 'APS': 'aps',
    'SoCalGas': 'socalgas', 'SCE': 'sce', 'PG&E': 'pge',
    'Xcel': 'xcel', 'Xcel Enegy': 'xcel',
    'Spire': 'spire', 'Ameren': 'ameren',
    'Snohomish County PUD': 'snohomish_pud',
    'Puget Sound Energy': 'pse_gas',
    'TECO': 'teco', 'TXU': 'txu', 'TXU Energy': 'txu',
    'Constellation New Energy Inc': 'constellation',
    'Republic Services': 'republic_services',
    'Republic Services #690': 'republic_services',
    'Waste Management': 'waste_management',
    'Silverdale Water District': 'silverdale',
    'City of Plano': 'plano', 'City of Plano Utilities': 'plano',
    'Aqua PA': 'aqua_pa',
    'City of Charlotte-Mecklenburg County': 'city_of_charlotte',
    'Tiger Inc': 'tiger_natural_gas',
    'Portland Water District': 'portland_water',
    'Comcast': 'comcast', 'AT&T': 'att', 'Cox Business': 'cox',
    'Spectrotel': 'spectrotel', 'Chelco': 'chelco',
    'Gas South': 'gas_south', 'Evergy': 'evergy',
    'NW Natural': 'nw_natural',
    'Colorado Springs Utilities': 'colorado_springs',
    'Tacoma': 'tacoma',
}


def extract_date_from_filename(fname: str) -> Optional[str]:
    """
    Extract a YYYYMMDD date string from a PDF filename.
    Supports multiple date formats in filenames.
    """
    # Pattern: _YYYYMMDD in filename
    m = re.search(r'_(\d{8})(?:\.|$|_)', fname)
    if m:
        d = m.group(1)
        if 2020 <= int(d[:4]) <= 2030:
            return d

    base = fname.replace('.pdf', '').replace('.PDF', '')

    # MM_DD_YYYY.pdf
    m = re.match(r'(\d{2})_(\d{2})_(\d{4})\.pdf$', fname, re.I)
    if m:
        return m.group(3) + m.group(1) + m.group(2)

    # YYYY-MM-DD.pdf
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})\.pdf$', fname, re.I)
    if m:
        return m.group(1) + m.group(2) + m.group(3)

    # MM-DD-YYYY.pdf
    m = re.match(r'(\d{2})-(\d{2})-(\d{4})\.pdf$', fname, re.I)
    if m:
        return m.group(3) + m.group(1) + m.group(2)

    # YYYYMMDD_ prefix
    m = re.match(r'(\d{8})_', fname)
    if m:
        d = m.group(1)
        if 2020 <= int(d[:4]) <= 2030:
            return d

    # MonDD_YYYY or Mon-DD-YYYY
    m = re.match(r'([A-Za-z]{3})\D*(\d{1,2})\D*(\d{4})', base)
    if m:
        ms = m.group(1).lower()
        if ms in MONTH_ABBRS:
            try:
                dt = datetime(int(m.group(3)), MONTH_ABBRS[ms], int(m.group(2)))
                return dt.strftime('%Y%m%d')
            except ValueError:
                pass

    # MM-DD-YY_ (2-digit year)
    m = re.search(r'(\d{2})-(\d{2})-(\d{2})_', base)
    if m:
        try:
            dt = datetime(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
            return dt.strftime('%Y%m%d')
        except ValueError:
            pass

    return None


def parse_invoicedoc_dates(ref_str) -> List[str]:
    """
    Parse date strings from invoicedoc references (pipe-separated).
    Returns list of YYYYMMDD strings or MONTH_YYYYMM for month-only refs.
    """
    if not ref_str or (isinstance(ref_str, float) and pd.isna(ref_str)):
        return []

    dates = []
    for ref in str(ref_str).split(' | '):
        ref = ref.strip()
        # MM/DD/YYYY
        m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', ref)
        if m:
            try:
                dt = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
                dates.append(dt.strftime('%Y%m%d'))
            except ValueError:
                pass
            continue

        # MM/DD/YY
        m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2})$', ref)
        if m:
            try:
                dt = datetime(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
                dates.append(dt.strftime('%Y%m%d'))
            except ValueError:
                pass
            continue

        # MM/YY (month only)
        m = re.search(r'(\d{1,2})/(\d{2})$', ref)
        if m:
            dates.append(f"MONTH_{2000 + int(m.group(2)):04d}{int(m.group(1)):02d}")

    return dates


class BillPDFLocator:
    """
    Locates utility bill PDFs in S3 and generates presigned URLs.

    Usage:
        locator = BillPDFLocator(s3_client, s3_property_mapping, prop_to_uuids)
        locator.index_pdfs()  # scans S3 once
        url = locator.get_presigned_url_for_charge(property_code, invoicedoc)
    """

    def __init__(
        self,
        s3_client,
        s3_property_mapping: List[Dict],
        prop_to_uuids: Optional[Dict[str, set]] = None,
    ):
        self.s3 = s3_client
        self.bucket = S3_BUCKET

        # Build indexes from S3 property mapping
        self._uuid_vendors: Dict[str, Dict] = {}
        self._s3_acct_to_uuid: Dict[str, Dict] = {}
        self._vendor_to_s3: Dict[str, List] = {}

        for entry in s3_property_mapping:
            uid = entry['s3_uuid']
            self._uuid_vendors[uid] = entry['vendors']
            for vnd, accts in entry['vendors'].items():
                if vnd not in self._vendor_to_s3:
                    self._vendor_to_s3[vnd] = []
                self._vendor_to_s3[vnd].append({'uuid': uid, 'accounts': accts})
                for a in accts:
                    if a != 'undefined':
                        self._s3_acct_to_uuid[a] = {'uuid': uid, 'vendor': vnd}

        # Property -> S3 UUIDs mapping (can be pre-built or built from AP data)
        self.prop_to_uuids = prop_to_uuids or {}

        # PDF date index: (uuid, YYYYMMDD) -> [s3_keys]
        self._pdf_index: Dict[Tuple[str, str], List[str]] = {}
        self._indexed = False

    def link_properties_from_ap(self, ap_rows: List[Tuple[str, str, str]]):
        """
        Link properties to S3 UUIDs using AP utility invoice data.

        Args:
            ap_rows: List of (lookup_code, vendor_name, account_number) tuples
                     from LAITMAN.ENTRATACORE.AP_UTILITY_INVOICES
        """
        prop_vendors: Dict[str, set] = {}
        for code, vname, acct in ap_rows:
            if code not in prop_vendors:
                prop_vendors[code] = set()
            prop_vendors[code].add(vname)

            # Direct account number match
            if acct in self._s3_acct_to_uuid:
                if code not in self.prop_to_uuids:
                    self.prop_to_uuids[code] = set()
                self.prop_to_uuids[code].add(self._s3_acct_to_uuid[acct]['uuid'])

        # Fuzzy vendor name matching for unlinked properties
        for pc, vns in prop_vendors.items():
            if pc in self.prop_to_uuids:
                continue
            for vn in vns:
                s3v = VENDOR_ALIASES.get(vn)
                if not s3v:
                    lower = vn.lower()
                    for alias, s3name in VENDOR_ALIASES.items():
                        if alias.lower() in lower or lower in alias.lower():
                            s3v = s3name
                            break
                if not s3v:
                    for sv in self._vendor_to_s3:
                        if sv.replace('_', ' ').lower() in vn.lower() or vn.lower() in sv.replace('_', ' ').lower():
                            s3v = sv
                            break
                if s3v and s3v in self._vendor_to_s3:
                    for se in self._vendor_to_s3[s3v]:
                        if pc not in self.prop_to_uuids:
                            self.prop_to_uuids[pc] = set()
                        self.prop_to_uuids[pc].add(se['uuid'])

        logger.info(f"Linked {len(self.prop_to_uuids)} properties to S3 UUIDs")

    def index_pdfs(self, uuid_filter: Optional[set] = None):
        """
        Scan S3 and build date index for all linked property UUIDs.

        Args:
            uuid_filter: Optional set of UUIDs to index (defaults to all linked)
        """
        linked = uuid_filter or set()
        if not linked:
            for uuids in self.prop_to_uuids.values():
                linked.update(uuids)

        if not linked:
            logger.warning("No UUIDs to index. Call link_properties_from_ap() first.")
            return

        seen = set()
        logger.info(f"Indexing PDFs for {len(linked)} UUIDs...")

        for uid in sorted(linked):
            if uid not in self._uuid_vendors:
                continue
            for vnd, accts in self._uuid_vendors[uid].items():
                for acct in accts:
                    if acct == 'undefined':
                        continue
                    pk = (uid, vnd, acct)
                    if pk in seen:
                        continue
                    seen.add(pk)
                    try:
                        paginator = self.s3.get_paginator('list_objects_v2')
                        for page in paginator.paginate(
                            Bucket=self.bucket,
                            Prefix=f"{uid}/bills/{vnd}/{acct}/",
                            MaxKeys=100,
                        ):
                            for obj in page.get('Contents', []):
                                key = obj['Key']
                                date_str = extract_date_from_filename(key.split('/')[-1])
                                if date_str:
                                    idx_key = (uid, date_str)
                                    if idx_key not in self._pdf_index:
                                        self._pdf_index[idx_key] = []
                                    self._pdf_index[idx_key].append(key)
                    except Exception as e:
                        logger.debug(f"Error indexing {pk}: {e}")

        total_pdfs = sum(len(v) for v in self._pdf_index.values())
        logger.info(f"Indexed {total_pdfs} PDFs across {len(self._pdf_index)} date entries")
        self._indexed = True

    def find_bill_pdf(self, property_code: str, invoicedoc: str) -> Optional[str]:
        """
        Find the best matching S3 key for a bill PDF.

        Args:
            property_code: Property lookup code (e.g. 'CHA', extracted from entityid)
            invoicedoc: Invoice document reference string (pipe-separated)

        Returns:
            S3 key string or None if no match found.
        """
        if property_code not in self.prop_to_uuids:
            return None

        dates = parse_invoicedoc_dates(invoicedoc)
        if not dates:
            return None

        uuids = self.prop_to_uuids[property_code]

        # Pass 1: Exact date match
        for ds in dates:
            if ds.startswith("MONTH_"):
                ym = ds.replace("MONTH_", "")
                for uid in uuids:
                    for (u, d), keys in self._pdf_index.items():
                        if u == uid and d.startswith(ym):
                            return keys[0]
                continue
            for uid in uuids:
                keys = self._pdf_index.get((uid, ds), [])
                if keys:
                    return keys[0]

        # Pass 2: Fuzzy match (within 46 days)
        for ds in dates:
            if ds.startswith("MONTH_"):
                continue
            try:
                tgt = datetime.strptime(ds, '%Y%m%d')
            except ValueError:
                continue
            best = None
            best_diff = 46
            for uid in uuids:
                for (u, d), keys in self._pdf_index.items():
                    if u != uid:
                        continue
                    try:
                        pdf_dt = datetime.strptime(d, '%Y%m%d')
                        diff = abs((pdf_dt - tgt).days)
                        if diff < best_diff:
                            best_diff = diff
                            best = keys[0]
                    except ValueError:
                        pass
            if best:
                return best

        return None

    def get_presigned_url(self, s3_key: str, expires: int = PRESIGNED_URL_EXPIRY) -> Optional[str]:
        """Generate a presigned URL for an S3 object."""
        try:
            return self.s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': s3_key},
                ExpiresIn=expires,
            )
        except Exception as e:
            logger.error(f"Failed to generate presigned URL for {s3_key}: {e}")
            return None

    def get_bill_url(self, property_code: str, invoicedoc: str) -> Optional[str]:
        """
        Convenience: find bill PDF and return presigned URL in one call.

        Args:
            property_code: Property lookup code (e.g. 'CHA')
            invoicedoc: Invoice document reference string

        Returns:
            Presigned URL string or None.
        """
        key = self.find_bill_pdf(property_code, invoicedoc)
        if key:
            return self.get_presigned_url(key)
        return None

    @staticmethod
    def extract_property_code(entity_id: str) -> str:
        """Extract property code from entityid (strip leading digits)."""
        return re.sub(r'^\d+', '', entity_id)
