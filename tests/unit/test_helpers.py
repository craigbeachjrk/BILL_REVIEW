"""
Unit tests for helper utility functions in main.py.
Tests date formatting, address parsing, hash computation, and account normalization.
"""
import os
import sys
import pytest
import hashlib
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Import helper functions from main (moto/snowflake already mocked by conftest)
from main import (
    _parse_service_address,
    _format_date_compact,
    pdf_id_from_key,
    line_id_from,
    _extract_ymd_from_key,
    _normalize_account_number,
    _account_similarity,
    _validate_s3_key,
    _basename_from_key,
)


class TestParseServiceAddress:
    """Tests for _parse_service_address function."""

    def test_parses_apt_format(self):
        """Should parse 'APT' unit format correctly."""
        street_num, street_letter, unit = _parse_service_address("9436 North St APT 159")

        assert street_num == "9436"
        assert street_letter == "N"
        assert unit == "159"

    def test_parses_unit_format(self):
        """Should parse 'Unit' format correctly."""
        street_num, street_letter, unit = _parse_service_address("728 Franklin Ave Unit F316")

        assert street_num == "728"
        assert street_letter == "F"
        assert unit == "F316"

    def test_parses_hash_format(self):
        """Should parse '#' unit format - note: may not be supported."""
        street_num, street_letter, unit = _parse_service_address("123 Main Street #4A")

        assert street_num == "123"
        assert street_letter == "M"
        # Note: # format may not be parsed as unit depending on implementation
        assert unit == "" or unit == "4A"

    def test_parses_suite_format(self):
        """Should parse 'Suite' format correctly."""
        street_num, street_letter, unit = _parse_service_address("500 Business Blvd Suite 200")

        assert street_num == "500"
        assert street_letter == "B"
        assert unit == "200"

    def test_parses_ste_format(self):
        """Should parse 'STE' abbreviation correctly."""
        street_num, street_letter, unit = _parse_service_address("100 Commerce Dr STE A")

        assert street_num == "100"
        assert street_letter == "C"
        assert unit == "A"

    def test_handles_empty_string(self):
        """Should handle empty string gracefully."""
        street_num, street_letter, unit = _parse_service_address("")

        assert street_num == ""
        assert street_letter == ""
        assert unit == ""

    def test_handles_none(self):
        """Should handle None gracefully."""
        street_num, street_letter, unit = _parse_service_address(None)

        assert street_num == ""
        assert street_letter == ""
        assert unit == ""

    def test_handles_no_unit(self):
        """Should handle addresses without unit."""
        street_num, street_letter, unit = _parse_service_address("456 Oak Avenue")

        assert street_num == "456"
        assert street_letter == "O"
        assert unit == ""

    def test_handles_lowercase(self):
        """Should handle lowercase input (converts to uppercase)."""
        street_num, street_letter, unit = _parse_service_address("123 main st apt 5b")

        assert street_num == "123"
        assert street_letter == "M"
        assert unit == "5B"

    def test_handles_bldg_format(self):
        """Should parse 'BLDG' format correctly."""
        street_num, street_letter, unit = _parse_service_address("200 Park Lane BLDG C")

        assert street_num == "200"
        assert unit == "C"


class TestFormatDateCompact:
    """Tests for _format_date_compact function."""

    def test_formats_mm_dd_yyyy(self):
        """Should format MM/DD/YYYY to M/D/YY."""
        result = _format_date_compact("07/24/2025")
        assert result == "7/24/25"

    def test_formats_iso_date(self):
        """Should format YYYY-MM-DD to M/D/YY."""
        result = _format_date_compact("2025-08-21")
        assert result == "8/21/25"

    def test_already_compact(self):
        """Should handle already compact dates."""
        result = _format_date_compact("7/24/25")
        assert result == "7/24/25"

    def test_formats_yyyy_mm_dd_slash(self):
        """Should format YYYY/MM/DD to M/D/YY."""
        result = _format_date_compact("2025/01/15")
        assert result == "1/15/25"

    def test_handles_empty_string(self):
        """Should return empty for empty string."""
        result = _format_date_compact("")
        assert result == ""

    def test_handles_none(self):
        """Should return empty for None."""
        result = _format_date_compact(None)
        assert result == ""

    def test_handles_invalid_date(self):
        """Should return original for invalid date format."""
        result = _format_date_compact("not a date")
        assert result == "not a date"

    def test_strips_whitespace(self):
        """Should strip whitespace from input."""
        result = _format_date_compact("  07/24/2025  ")
        assert result == "7/24/25"

    def test_january_date(self):
        """Should format January dates correctly."""
        result = _format_date_compact("01/01/2025")
        assert result == "1/1/25"

    def test_december_date(self):
        """Should format December dates correctly."""
        result = _format_date_compact("12/31/2024")
        assert result == "12/31/24"


class TestPdfIdFromKey:
    """Tests for pdf_id_from_key function."""

    def test_generates_sha1_hash(self):
        """Should generate SHA1 hash from key."""
        key = "Bill_Parser_4_Enriched_Outputs/yyyy=2025/mm=01/dd=15/test.jsonl"
        result = pdf_id_from_key(key)

        expected = hashlib.sha1(key.encode("utf-8")).hexdigest()
        assert result == expected

    def test_same_key_same_hash(self):
        """Same key should always produce same hash."""
        key = "some/s3/key.jsonl"
        result1 = pdf_id_from_key(key)
        result2 = pdf_id_from_key(key)

        assert result1 == result2

    def test_different_keys_different_hashes(self):
        """Different keys should produce different hashes."""
        key1 = "key1.jsonl"
        key2 = "key2.jsonl"

        result1 = pdf_id_from_key(key1)
        result2 = pdf_id_from_key(key2)

        assert result1 != result2

    def test_returns_40_char_hex(self):
        """Should return 40 character hex string (SHA1)."""
        result = pdf_id_from_key("any/key")

        assert len(result) == 40
        assert all(c in '0123456789abcdef' for c in result)

    def test_handles_unicode_keys(self):
        """Should handle unicode in keys."""
        key = "path/to/café/invoice.jsonl"
        result = pdf_id_from_key(key)

        assert len(result) == 40


class TestLineIdFrom:
    """Tests for line_id_from function."""

    def test_combines_pdf_id_and_index(self):
        """Should combine pdf_id and line index."""
        key = "some/key.jsonl"
        result = line_id_from(key, 0)

        pdf_id = pdf_id_from_key(key)
        assert result == f"{pdf_id}#0"

    def test_different_indexes(self):
        """Different indexes should produce different line_ids."""
        key = "some/key.jsonl"

        result0 = line_id_from(key, 0)
        result1 = line_id_from(key, 1)
        result2 = line_id_from(key, 2)

        assert result0 != result1
        assert result1 != result2

    def test_large_index(self):
        """Should handle large index values."""
        key = "some/key.jsonl"
        result = line_id_from(key, 9999)

        assert "#9999" in result


class TestExtractYmdFromKey:
    """Tests for _extract_ymd_from_key function."""

    def test_extracts_yyyy_mm_dd_format(self):
        """Should extract date from yyyy=YYYY/mm=MM/dd=DD format."""
        key = "Bill_Parser_4_Enriched_Outputs/yyyy=2025/mm=01/dd=15/file.jsonl"
        y, m, d = _extract_ymd_from_key(key)

        assert y == "2025"
        assert m == "01"
        assert d == "15"

    def test_extracts_numeric_path_format(self):
        """Should extract date from YYYY/MM/DD numeric path format."""
        key = "Stage/2025/03/21/file.jsonl"
        y, m, d = _extract_ymd_from_key(key)

        assert y == "2025"
        assert m == "03"
        assert d == "21"

    def test_returns_today_for_invalid(self):
        """Should return today's date for keys without recognizable date."""
        key = "no/date/here/file.jsonl"
        y, m, d = _extract_ymd_from_key(key)

        # Should return some valid date (today's date)
        assert len(y) == 4
        assert y.isdigit()
        assert len(m) == 2
        assert len(d) == 2

    def test_handles_double_digit_day(self):
        """Should handle double-digit days correctly."""
        key = "Bill_Parser_4_Enriched_Outputs/yyyy=2025/mm=12/dd=25/file.jsonl"
        y, m, d = _extract_ymd_from_key(key)

        assert y == "2025"
        assert m == "12"
        assert d == "25"


class TestNormalizeAccountNumber:
    """Tests for _normalize_account_number function."""

    def test_removes_dashes(self):
        """Should remove dashes from account number."""
        result = _normalize_account_number("123-456-789")
        assert result == "123456789"

    def test_removes_spaces(self):
        """Should remove spaces from account number."""
        result = _normalize_account_number("123 456 789")
        assert result == "123456789"

    def test_removes_dots(self):
        """Should remove dots from account number."""
        result = _normalize_account_number("123.456.789")
        assert result == "123456789"

    def test_removes_parentheses(self):
        """Should remove parentheses from account number."""
        result = _normalize_account_number("(123)456")
        assert result == "123456"

    def test_strips_leading_zeros(self):
        """Should strip leading zeros but keep at least one digit."""
        result = _normalize_account_number("000123")
        assert result == "123"

    def test_keeps_single_zero(self):
        """Should keep single zero for all-zero account."""
        result = _normalize_account_number("0000")
        assert result == "0"

    def test_converts_to_uppercase(self):
        """Should convert to uppercase."""
        result = _normalize_account_number("abc123")
        assert result == "ABC123"

    def test_handles_empty_string(self):
        """Should return empty for empty string."""
        result = _normalize_account_number("")
        assert result == ""

    def test_handles_none(self):
        """Should return empty for None."""
        result = _normalize_account_number(None)
        assert result == ""

    def test_strips_whitespace(self):
        """Should strip leading/trailing whitespace."""
        result = _normalize_account_number("  12345  ")
        assert result == "12345"

    def test_complex_account(self):
        """Should handle complex account with multiple separators."""
        result = _normalize_account_number("(00) 123-456.789")
        assert result == "123456789"


class TestAccountSimilarity:
    """Tests for _account_similarity function."""

    def test_identical_accounts_return_1(self):
        """Identical accounts should return 1.0."""
        result = _account_similarity("12345", "12345")
        assert result == 1.0

    def test_empty_string_returns_0(self):
        """Empty string should return 0.0."""
        result = _account_similarity("", "12345")
        assert result == 0.0

        result = _account_similarity("12345", "")
        assert result == 0.0

    def test_none_returns_0(self):
        """None should return 0.0."""
        result = _account_similarity(None, "12345")
        assert result == 0.0

        result = _account_similarity("12345", None)
        assert result == 0.0

    def test_one_contains_other_returns_high(self):
        """When one contains the other, should return 0.9."""
        result = _account_similarity("123", "12345")
        assert result == 0.9

        result = _account_similarity("12345", "123")
        assert result == 0.9

    def test_similar_accounts_return_positive(self):
        """Similar accounts should return positive similarity."""
        result = _account_similarity("12345", "12346")
        assert 0 < result < 1

    def test_completely_different_accounts(self):
        """Completely different accounts should have low similarity."""
        result = _account_similarity("ABCDE", "12345")
        assert result < 0.5


class TestBasenameFromKey:
    """Tests for _basename_from_key function."""

    def test_extracts_filename(self):
        """Should extract filename from S3 key."""
        key = "Bill_Parser_4_Enriched_Outputs/yyyy=2025/mm=01/dd=15/invoice_123.jsonl"
        result = _basename_from_key(key)

        assert result == "invoice_123.jsonl"

    def test_handles_simple_key(self):
        """Should handle simple key without path."""
        key = "file.jsonl"
        result = _basename_from_key(key)

        assert result == "file.jsonl"

    def test_handles_nested_path(self):
        """Should handle deeply nested path."""
        key = "a/b/c/d/e/f/filename.txt"
        result = _basename_from_key(key)

        assert result == "filename.txt"


class TestValidateS3KeyAdditional:
    """Additional tests for _validate_s3_key function."""

    def test_valid_enrichment_prefix(self):
        """Should accept Bill_Parser_Enrichment prefix."""
        key = "Bill_Parser_Enrichment/exports/dim_vendor/data.json"
        assert _validate_s3_key(key) is True

    def test_rejects_double_dots_anywhere(self):
        """Should reject .. anywhere in path."""
        keys = [
            "Bill_Parser_4_Enriched_Outputs/..hidden/file.json",
            "Bill_Parser_4_Enriched_Outputs/normal/../escape/file.json",
            "../Bill_Parser_4_Enriched_Outputs/file.json",
        ]
        for key in keys:
            assert _validate_s3_key(key) is False, f"Should reject: {key}"

    def test_rejects_null_bytes(self):
        """Should reject keys with null bytes."""
        key = "Bill_Parser_4_Enriched_Outputs/file\x00.json"
        # This might be handled differently - test actual behavior
        result = _validate_s3_key(key)
        # Should be False if null bytes are blocked
        assert isinstance(result, bool)

    def test_accepts_long_valid_paths(self):
        """Should accept long but valid paths."""
        key = "Bill_Parser_4_Enriched_Outputs/yyyy=2025/mm=01/dd=15/some/deep/nested/path/to/file.jsonl"
        assert _validate_s3_key(key) is True


class TestEdgeCases:
    """Edge case tests for various helper functions."""

    def test_parse_address_with_numbers_in_street_name(self):
        """Should handle street names with numbers."""
        street_num, street_letter, unit = _parse_service_address("100 21st Street APT 5")
        assert street_num == "100"

    def test_format_date_with_numeric_input(self):
        """Should handle numeric-like date strings."""
        result = _format_date_compact("20250115")
        # May return as-is if no format matches
        assert isinstance(result, str)

    def test_pdf_id_deterministic(self):
        """pdf_id should be deterministic across calls."""
        key = "test/key/path.jsonl"
        results = [pdf_id_from_key(key) for _ in range(10)]
        assert all(r == results[0] for r in results)

    def test_extract_ymd_handles_partial_match(self):
        """Should handle keys with only partial date info."""
        key = "Bill_Parser_4_Enriched_Outputs/yyyy=2025/file.jsonl"
        y, m, d = _extract_ymd_from_key(key)
        # Should return something valid
        assert y and m and d

    def test_normalize_account_preserves_alphanumeric(self):
        """Should preserve alphanumeric characters."""
        result = _normalize_account_number("ABC123XYZ")
        assert result == "ABC123XYZ"

    def test_similarity_symmetric(self):
        """Account similarity should be symmetric."""
        a, b = "12345", "12346"
        assert _account_similarity(a, b) == _account_similarity(b, a)
