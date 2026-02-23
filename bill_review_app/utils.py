"""
Utility functions for the Bill Review application.

This module provides common helper functions to reduce code duplication
and improve maintainability across the application.
"""

from typing import Any, Optional, Union, List
import re


def get_field(record: dict, *field_names: str, default: str = "") -> str:
    """
    Extract a field from a record using multiple possible field names.

    This function checks each field name in order and returns the first
    non-empty value found. Useful for handling inconsistent field naming
    across different data sources.

    Args:
        record: Dictionary containing the data
        *field_names: Variable number of field names to check in order
        default: Default value to return if no field is found (default: "")

    Returns:
        The first non-empty field value found, stripped of whitespace, or default

    Examples:
        >>> record = {"PropertyID": "12345", "other": "data"}
        >>> get_field(record, "PropertyId", "Property Id", "PropertyID")
        "12345"

        >>> record = {"name": ""}
        >>> get_field(record, "missing_field", "name", default="N/A")
        "N/A"
    """
    for field_name in field_names:
        value = record.get(field_name)
        if value:
            return str(value).strip()
    return default


def get_numeric_field(record: dict, *field_names: str, default: float = 0.0) -> float:
    """
    Extract a numeric field from a record using multiple possible field names.

    Args:
        record: Dictionary containing the data
        *field_names: Variable number of field names to check in order
        default: Default value to return if no field is found or conversion fails

    Returns:
        The first valid numeric value found, or default

    Examples:
        >>> record = {"amount": "123.45", "total": "999"}
        >>> get_numeric_field(record, "Amount", "amount")
        123.45
    """
    for field_name in field_names:
        value = record.get(field_name)
        if value is not None:
            try:
                return float(str(value).replace(",", "").replace("$", "").strip())
            except (ValueError, AttributeError):
                continue
    return default


def normalize_string(s: str) -> str:
    """
    Normalize a string for comparison by removing extra whitespace and converting to lowercase.

    Args:
        s: String to normalize

    Returns:
        Normalized string

    Examples:
        >>> normalize_string("  Hello   World  ")
        "hello world"
    """
    return " ".join(str(s).split()).lower()


def safe_strip(value: Any) -> str:
    """
    Safely convert any value to a stripped string.

    Args:
        value: Any value to convert

    Returns:
        String representation of value, stripped of whitespace

    Examples:
        >>> safe_strip(None)
        ""
        >>> safe_strip("  hello  ")
        "hello"
        >>> safe_strip(12345)
        "12345"
    """
    if value is None:
        return ""
    return str(value).strip()


def parse_amount(amount_str: Union[str, int, float, None]) -> float:
    """
    Parse a currency amount from various string formats.

    Handles:
    - Dollar signs ($)
    - Commas (,)
    - Parentheses for negative numbers (123.45) -> -123.45
    - None/empty values -> 0.0

    Args:
        amount_str: Amount to parse (string, number, or None)

    Returns:
        Float representation of the amount

    Examples:
        >>> parse_amount("$1,234.56")
        1234.56
        >>> parse_amount("(123.45)")
        -123.45
        >>> parse_amount(None)
        0.0
    """
    if amount_str is None or amount_str == "":
        return 0.0

    amount_str = str(amount_str).strip()

    # Check for parentheses indicating negative
    is_negative = amount_str.startswith("(") and amount_str.endswith(")")

    # Remove currency symbols, commas, parentheses
    cleaned = re.sub(r"[$,() ]", "", amount_str)

    try:
        value = float(cleaned)
        return -value if is_negative else value
    except (ValueError, TypeError):
        return 0.0


def truncate_string(s: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    Truncate a string to a maximum length, adding a suffix if truncated.

    Args:
        s: String to truncate
        max_length: Maximum length (including suffix)
        suffix: Suffix to add if truncated (default: "...")

    Returns:
        Truncated string

    Examples:
        >>> truncate_string("This is a very long string", max_length=10)
        "This is..."
    """
    s = str(s)
    if len(s) <= max_length:
        return s
    return s[: max_length - len(suffix)] + suffix


def extract_account_number(text: str) -> Optional[str]:
    """
    Extract an account number from text using common patterns.

    Looks for patterns like:
    - Account: 12345
    - Acct #12345
    - Account Number: 12345-67

    Args:
        text: Text to search

    Returns:
        Extracted account number or None

    Examples:
        >>> extract_account_number("Account: 12345-67")
        "12345-67"
    """
    patterns = [
        r"account\s*(?:number|#|no\.?)?\s*:?\s*([A-Z0-9\-]+)",
        r"acct\s*(?:#|no\.?)?\s*:?\s*([A-Z0-9\-]+)",
    ]

    text_lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, text_lower, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def build_date_range_filter(start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict:
    """
    Build a DynamoDB filter expression for date range queries.

    Args:
        start_date: Start date in ISO format (YYYY-MM-DD)
        end_date: End date in ISO format (YYYY-MM-DD)

    Returns:
        Dictionary with filter expression components

    Examples:
        >>> build_date_range_filter("2024-01-01", "2024-12-31")
        {'expression': '#date BETWEEN :start AND :end', 'names': {'#date': 'date'}, 'values': {':start': {'S': '2024-01-01'}, ':end': {'S': '2024-12-31'}}}
    """
    if not start_date and not end_date:
        return {"expression": "", "names": {}, "values": {}}

    if start_date and end_date:
        return {
            "expression": "#date BETWEEN :start AND :end",
            "names": {"#date": "date"},
            "values": {":start": {"S": start_date}, ":end": {"S": end_date}},
        }
    elif start_date:
        return {
            "expression": "#date >= :start",
            "names": {"#date": "date"},
            "values": {":start": {"S": start_date}},
        }
    else:  # end_date only
        return {
            "expression": "#date <= :end",
            "names": {"#date": "date"},
            "values": {":end": {"S": end_date}},
        }


def chunk_list(lst: List[Any], chunk_size: int) -> List[List[Any]]:
    """
    Split a list into chunks of specified size.

    Args:
        lst: List to chunk
        chunk_size: Size of each chunk

    Returns:
        List of chunked lists

    Examples:
        >>> chunk_list([1, 2, 3, 4, 5], 2)
        [[1, 2], [3, 4], [5]]
    """
    return [lst[i : i + chunk_size] for i in range(0, len(lst), chunk_size)]


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename by removing/replacing unsafe characters.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename safe for filesystem use

    Examples:
        >>> sanitize_filename("my/file<name>.txt")
        "my_file_name_.txt"
    """
    # Replace unsafe characters with underscore
    unsafe_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(unsafe_chars, "_", filename)

    # Remove leading/trailing dots and spaces
    sanitized = sanitized.strip(". ")

    # Limit length
    if len(sanitized) > 255:
        name, ext = sanitized.rsplit(".", 1) if "." in sanitized else (sanitized, "")
        sanitized = name[: 255 - len(ext) - 1] + "." + ext if ext else name[:255]

    return sanitized or "unnamed"


def is_valid_email(email: str) -> bool:
    """
    Validate email address format.

    Args:
        email: Email address to validate

    Returns:
        True if email format is valid, False otherwise

    Examples:
        >>> is_valid_email("user@example.com")
        True
        >>> is_valid_email("invalid.email")
        False
    """
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, str(email).strip()))


def format_currency(amount: Union[float, int, None], decimals: int = 2) -> str:
    """
    Format a number as currency string.

    Args:
        amount: Amount to format
        decimals: Number of decimal places (default: 2)

    Returns:
        Formatted currency string

    Examples:
        >>> format_currency(1234.56)
        "$1,234.56"
        >>> format_currency(-1234.5)
        "$-1,234.50"
        >>> format_currency(None)
        "$0.00"
    """
    if amount is None:
        amount = 0.0
    return f"${amount:,.{decimals}f}"


# Validation functions

def validate_required_fields(record: dict, *required_fields: str) -> tuple[bool, List[str]]:
    """
    Validate that required fields are present and non-empty in a record.

    Args:
        record: Dictionary to validate
        *required_fields: Field names that must be present and non-empty

    Returns:
        Tuple of (is_valid, list_of_missing_fields)

    Examples:
        >>> record = {"name": "John", "age": 30}
        >>> validate_required_fields(record, "name", "email")
        (False, ['email'])
    """
    missing = []
    for field in required_fields:
        value = record.get(field)
        if not value or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return (len(missing) == 0, missing)


def validate_date_format(date_str: str, format_pattern: str = r"^\d{4}-\d{2}-\d{2}$") -> bool:
    """
    Validate date string format.

    Args:
        date_str: Date string to validate
        format_pattern: Regex pattern for validation (default: YYYY-MM-DD)

    Returns:
        True if format is valid, False otherwise

    Examples:
        >>> validate_date_format("2024-01-31")
        True
        >>> validate_date_format("01/31/2024")
        False
    """
    return bool(re.match(format_pattern, str(date_str).strip()))
