"""
Entrata AR Transaction client for posting vacant utility billback charges.

Modeled on production-proven patterns from SECDP_ENTRATA/lambdas/secdp/handler.py.

Key API rules:
- Endpoint: /artransactions (NOT /leases)
- Date format: MM/DD/YYYY for transactionDate, MM/YYYY for arPostMonth
- Rate limit: 300/hr → 12s between calls for bulk operations
- Auth: X-Api-Key header + body auth type: apikey
"""
import time
import logging
import requests
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

# Entrata AR code IDs for vacant utility charge codes
AR_CODE_MAP = {
    'ELECR': 230916,
    'GASRE': 230917,
    'WATRR': 230919,
    'SEWRR': 230829,
}

# Reverse lookup: charge code string -> AR code ID
CHARGE_CODE_TO_AR = {
    'ELECR - Util. Non-Comp-Elect': 230916,
    'SEWRR - SEWER ADJ': 230829,
    'WATRR - WATER': 230919,
    'GASRE - Util Non-Compli GAS': 230917,
}

DEFAULT_BASE_URL = 'https://apis.entrata.com/ext/orgs/jrkpropertyholdingsentratacore/v1/'
MIN_CALL_INTERVAL = 12.0  # seconds between API calls (300/hr limit)
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0


@dataclass
class ARPostResult:
    """Result of a single AR transaction post."""
    success: bool
    lease_id: int
    amount: float
    transaction_id: str
    entrata_response: Optional[Dict] = None
    error: Optional[str] = None


@dataclass
class ARReversalResult:
    """Result of a single AR transaction reversal."""
    success: bool
    original_transaction_id: int
    entrata_response: Optional[Dict] = None
    error: Optional[str] = None


class EntrataARClient:
    """Client for Entrata AR transaction operations with built-in rate limiting."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, dry_run: bool = False):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.dry_run = dry_run
        self._last_call_time = 0.0
        self._call_count = 0
        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json',
            'X-Api-Key': api_key,
        })

    def _rate_limit(self):
        """Enforce minimum interval between API calls."""
        now = time.time()
        elapsed = now - self._last_call_time
        if elapsed < MIN_CALL_INTERVAL:
            sleep_time = MIN_CALL_INTERVAL - elapsed
            logger.debug(f"Rate limiting: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)
        self._last_call_time = time.time()
        self._call_count += 1

    def _call_api(self, endpoint: str, method_name: str, params: Dict) -> Dict:
        """Make an Entrata API call with retry and rate limiting."""
        url = f"{self.base_url}/{endpoint}"
        payload = {
            "auth": {"type": "apikey"},
            "method": {
                "name": method_name,
                "params": params,
            }
        }

        for attempt in range(1, MAX_RETRIES + 1):
            self._rate_limit()
            try:
                resp = self._session.post(url, json=payload, timeout=30)

                # Handle rate limiting
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get('Retry-After', 30))
                    logger.warning(f"Rate limited (429). Retrying in {retry_after}s (attempt {attempt}/{MAX_RETRIES})")
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # Check for Entrata-level errors in response body
                response = data.get('response', {})
                if response.get('code', 0) not in (0, 200):
                    error_msg = response.get('result', {}).get('error', str(response))
                    raise EntrataAPIError(f"Entrata error {response.get('code')}: {error_msg}")

                return data

            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF ** attempt
                    logger.warning(f"Request failed (attempt {attempt}/{MAX_RETRIES}): {e}. Retrying in {wait}s")
                    time.sleep(wait)
                else:
                    raise EntrataAPIError(f"Failed after {MAX_RETRIES} attempts: {e}") from e

        raise EntrataAPIError(f"Failed after {MAX_RETRIES} attempts")

    def post_charge(
        self,
        lease_id: int,
        ar_code_id: int,
        amount: float,
        transaction_date: str,
        post_month: str,
        description: str,
        transaction_id: str = "",
    ) -> ARPostResult:
        """
        Post a charge to a tenant ledger via sendLeaseArTransactions.

        Args:
            lease_id: Entrata lease ID (integer)
            ar_code_id: AR code ID (e.g. 230916 for ELECR)
            amount: Charge amount (positive = charge, negative = credit)
            transaction_date: MM/DD/YYYY format
            post_month: MM/YYYY format (NOT MM/DD/YYYY)
            description: Memo text for the charge
            transaction_id: Our reference ID (e.g. VE-CHA-012025-001)

        Returns:
            ARPostResult with success status and details.
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] post_charge: lease={lease_id}, amount={amount}, "
                        f"ar_code={ar_code_id}, txn_id={transaction_id}")
            return ARPostResult(
                success=True,
                lease_id=lease_id,
                amount=amount,
                transaction_id=transaction_id,
                entrata_response={"dry_run": True},
            )

        params = {
            "transaction": [{
                "transactionId": transaction_id,
                "leaseId": int(lease_id),
                "arCodeId": int(ar_code_id),
                "transactionAmount": float(amount),
                "transactionDate": transaction_date,
                "arPostMonth": post_month,
                "description": description,
            }]
        }

        try:
            response = self._call_api("artransactions", "sendLeaseArTransactions", params)
            logger.info(f"Posted charge: lease={lease_id}, amount={amount}, txn_id={transaction_id}")
            return ARPostResult(
                success=True,
                lease_id=lease_id,
                amount=amount,
                transaction_id=transaction_id,
                entrata_response=response,
            )
        except EntrataAPIError as e:
            logger.error(f"Failed to post charge: lease={lease_id}, error={e}")
            return ARPostResult(
                success=False,
                lease_id=lease_id,
                amount=amount,
                transaction_id=transaction_id,
                error=str(e),
            )

    def reverse_charge(
        self,
        entrata_transaction_id: int,
        amount: str,
        description: str = "",
    ) -> ARReversalResult:
        """
        Reverse an existing AR transaction via sendLeaseArTransactionReversals.

        Args:
            entrata_transaction_id: The Entrata-assigned transaction ID (integer)
            amount: Original transaction amount as string
            description: Reversal reason/memo

        Returns:
            ARReversalResult with success status and details.
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] reverse_charge: txn_id={entrata_transaction_id}, amount={amount}")
            return ARReversalResult(
                success=True,
                original_transaction_id=entrata_transaction_id,
                entrata_response={"dry_run": True},
            )

        params = {
            "Transactions": {
                "Transaction": [{
                    "TransactionId": int(entrata_transaction_id),
                    "TransactionAmount": str(amount),
                    "Description": description,
                }]
            }
        }

        try:
            response = self._call_api("artransactions", "sendLeaseArTransactionReversals", params)
            logger.info(f"Reversed transaction: {entrata_transaction_id}")
            return ARReversalResult(
                success=True,
                original_transaction_id=entrata_transaction_id,
                entrata_response=response,
            )
        except EntrataAPIError as e:
            logger.error(f"Failed to reverse transaction {entrata_transaction_id}: {e}")
            return ARReversalResult(
                success=False,
                original_transaction_id=entrata_transaction_id,
                error=str(e),
            )

    def get_ar_transactions(
        self,
        property_id: int,
        from_date: str,
        to_date: str,
        lease_ids: Optional[List[int]] = None,
    ) -> Dict:
        """
        Retrieve AR transactions for a property via getLeaseArTransactions.

        Args:
            property_id: Numeric Entrata property ID
            from_date: MM/DD/YYYY
            to_date: MM/DD/YYYY
            lease_ids: Optional list of specific lease IDs (for properties with >500 leases)

        Returns:
            Raw Entrata API response dict.
        """
        params = {
            "propertyId": int(property_id),
            "transactionFromDate": from_date,
            "transactionToDate": to_date,
            "showFullLedger": "1",
            "residentFriendlyMode": "0",
            "includeOtherIncomeLeases": "0",
            "includeReversals": "1",
        }
        if lease_ids:
            params["leaseIds"] = ",".join(str(lid) for lid in lease_ids)

        return self._call_api("artransactions", "getLeaseArTransactions", params)

    @property
    def stats(self) -> Dict[str, int]:
        """Return call statistics."""
        return {
            "total_calls": self._call_count,
            "dry_run": self.dry_run,
        }


class EntrataAPIError(Exception):
    """Raised when an Entrata API call fails."""
    pass


def generate_transaction_id(property_code: str, month: int, year: int, sequence: int) -> str:
    """
    Generate a VE transaction reference ID.

    Format: VE-{PROP}-{MMYYYY}-{NNN}
    Example: VE-CHA-012026-001
    """
    return f"VE-{property_code}-{month:02d}{year}-{sequence:03d}"


def resolve_ar_code_id(charge_code: str) -> Optional[int]:
    """
    Resolve a charge code string to its Entrata AR code ID.

    Accepts both short form ('ELECR') and full form ('ELECR - Util. Non-Comp-Elect').
    """
    # Try short form first
    if charge_code in AR_CODE_MAP:
        return AR_CODE_MAP[charge_code]
    # Try full form
    if charge_code in CHARGE_CODE_TO_AR:
        return CHARGE_CODE_TO_AR[charge_code]
    # Try extracting short code from full form
    short = charge_code.split(' - ')[0].strip() if ' - ' in charge_code else charge_code
    return AR_CODE_MAP.get(short)
