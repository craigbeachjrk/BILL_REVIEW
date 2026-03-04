"""
Parameterized Snowflake SQL templates for VE pipeline.
"""
from .config import GL_ACCOUNTS


def _gl_account_list():
    """Format GL accounts for SQL IN clause."""
    return ','.join(f"'{a}'" for a in GL_ACCOUNTS)


def bills_query(post_month_abbr: str) -> str:
    """
    GL transactions with VE memo filter.

    Args:
        post_month_abbr: Snowflake postMonth format, e.g. 'Jan,2026'
    """
    accts = _gl_account_list()
    return f"""
SELECT DISTINCT
    '01' || _raw_data:lookupCode::VARCHAR as "entityid",
    _raw_data:propertyName::VARCHAR as "Property",
    _raw_data:accountNumber::VARCHAR as "accountno",
    _raw_data:memo::VARCHAR as "description",
    TRY_TO_DATE(_raw_data:postDate::VARCHAR, 'YYYY/MM/DD') as "Created",
    'Y' as "ApprovedYN",
    TRY_TO_NUMBER(_raw_data:debit::VARCHAR, 18, 2) as "dramount",
    TRY_TO_NUMBER(_raw_data:credit::VARCHAR, 18, 2) as "cramount",
    _raw_data:reference::VARCHAR as "invoicedoc",
    _raw_data:glDetailId::VARCHAR as "glDetailId"
FROM RAW.ENTRATA.GL_TRANSACTIONS
WHERE _raw_data:accountNumber::VARCHAR IN ({accts})
  AND _raw_data:postMonth::VARCHAR = '{post_month_abbr}'
  AND _raw_data:memo::VARCHAR IS NOT NULL
  AND (
    _raw_data:memo::VARCHAR LIKE '%VE%'
    OR _raw_data:memo::VARCHAR LIKE '%VG%'
    OR _raw_data:memo::VARCHAR LIKE '%VW%'
    OR _raw_data:memo::VARCHAR LIKE '%VS%'
  )
  AND _raw_data:memo::VARCHAR LIKE '%/%'
  AND _raw_data:memo::VARCHAR NOT LIKE '%!%'
"""


def leases_query() -> str:
    """Full lease snapshot from LEASE_LIVE."""
    return """
SELECT DISTINCT
    '01' || LOOKUP_CODE as "PropertyId",
    BUILDING_NAME as "BldgId",
    UNIT_NUMBER as "UnitId",
    CASE
        WHEN LEASE_STATUS = 'Current' THEN 'C'
        WHEN LEASE_STATUS = 'Past' THEN 'P'
        WHEN LEASE_STATUS = 'Notice' THEN 'N'
        ELSE NULL
    END as "ResiStatus",
    LEASE_ID as "ResiId",
    CUSTOMER_NAME_FIRST as "ResiFirstName",
    CUSTOMER_NAME_LAST as "ResiLastName",
    ACTUAL_MOVE_IN_DATE as "MoveInDate",
    MOVE_OUT_DATE as "MoveOutDate"
FROM RAW.ENTRATA.LEASE_LIVE
WHERE LEASE_STATUS IN ('Current', 'Past', 'Notice')
  AND CUSTOMER_NAME_FIRST <> 'Model'
"""


def total_expense_query(post_month_abbr: str) -> str:
    """
    Total VE expense by property for billback % calculation.
    Deduped on glDetailId, debit-only (matching FPI methodology).

    Args:
        post_month_abbr: Snowflake postMonth format, e.g. 'Jan,2026'
    """
    accts = _gl_account_list()
    return f"""
WITH deduped AS (
    SELECT DISTINCT
        _raw_data:glDetailId::VARCHAR as glDetailId,
        '01' || _raw_data:lookupCode::VARCHAR as entityid,
        TRY_TO_NUMBER(_raw_data:debit::VARCHAR, 18, 2) as debit
    FROM RAW.ENTRATA.GL_TRANSACTIONS
    WHERE _raw_data:accountNumber::VARCHAR IN ({accts})
      AND _raw_data:postMonth::VARCHAR = '{post_month_abbr}'
      AND _raw_data:memo::VARCHAR IS NOT NULL
      AND (
        _raw_data:memo::VARCHAR LIKE '%VE%'
        OR _raw_data:memo::VARCHAR LIKE '%VG%'
        OR _raw_data:memo::VARCHAR LIKE '%VW%'
        OR _raw_data:memo::VARCHAR LIKE '%VS%'
      )
      AND _raw_data:memo::VARCHAR LIKE '%/%'
      AND _raw_data:memo::VARCHAR NOT LIKE '%!%'
)
SELECT
    entityid,
    SUM(COALESCE(debit, 0)) as total_debit
FROM deduped
GROUP BY 1
"""
