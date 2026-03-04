"""
GL memo description parsing: extract bill dates and unit strings.
"""
from .property_maps import UNITSTRING


def parse_bill_start(description: str):
    """Extract bill start date string from GL memo description."""
    try:
        clean_desc = description.lstrip('(')
        index = clean_desc.index('-')
        return clean_desc[:index]
    except:
        return None


def parse_bill_end(description: str):
    """Extract bill end date string from GL memo description."""
    try:
        clean_desc = description.lstrip('(')
        index_one = clean_desc.index('-')
        bill_cutoff = clean_desc[index_one + 1:]
        index_two = bill_cutoff.index(" ")
        return bill_cutoff[:index_two]
    except:
        return None


def parse_unit_string(description: str):
    """Extract unit string (e.g. '4601F@203') from GL memo description."""
    try:
        return UNITSTRING(description)
    except:
        return None


def parse_gl_memo(description: str):
    """
    Parse all components from a GL memo description.
    Returns (bill_start, bill_end, unit_string).
    """
    return (
        parse_bill_start(description),
        parse_bill_end(description),
        parse_unit_string(description),
    )
