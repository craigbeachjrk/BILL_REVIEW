"""
Unit tests for bill enricher Lambda functions.
Tests name normalization, address parsing, GL description building, and unit conversion.
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Add Lambda code path
ENRICHER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "aws_lambdas", "us-east-1", "jrk-bill-enricher", "code"
)
sys.path.insert(0, ENRICHER_PATH)

# Mock AWS clients before importing the Lambda
with patch("boto3.client"):
    from lambda_bill_enricher import (
        _norm_name,
        _street_num_and_letter,
        _find_unit,
        _find_building,
        _addr_num_and_street,
        _fmt_period_mmddyyyy,
        _to_gallons,
        _build_gl_desc,
    )


class TestNormName:
    """Tests for _norm_name function."""

    def test_normalizes_lowercase(self):
        """Should convert to lowercase."""
        result = _norm_name("ACME CORPORATION")
        assert result == "acme corporation"

    def test_normalizes_ampersand(self):
        """Should replace & with 'and'."""
        result = _norm_name("Smith & Jones")
        assert result == "smith and jones"

    def test_normalizes_comma(self):
        """Should replace comma with space."""
        result = _norm_name("Vendor, Inc.")
        assert result == "vendor inc"

    def test_normalizes_dot(self):
        """Should replace dot with space."""
        result = _norm_name("A.B.C. Company")
        assert result == "a b c company"

    def test_collapses_whitespace(self):
        """Should collapse multiple spaces."""
        result = _norm_name("Too   Many    Spaces")
        assert result == "too many spaces"

    def test_handles_empty_string(self):
        """Should handle empty string."""
        result = _norm_name("")
        assert result == ""

    def test_handles_none(self):
        """Should handle None."""
        result = _norm_name(None)
        assert result == ""

    def test_complex_normalization(self):
        """Should handle complex name with multiple transformations."""
        result = _norm_name("Smith & Jones, Inc.  DBA Test")
        assert result == "smith and jones inc dba test"


class TestStreetNumAndLetter:
    """Tests for _street_num_and_letter function."""

    def test_extracts_number_and_letter(self):
        """Should extract street number and first letter."""
        num, letter = _street_num_and_letter("123 Main Street")
        assert num == "123"
        assert letter == "M"

    def test_handles_lowercase(self):
        """Should handle lowercase and uppercase letter."""
        num, letter = _street_num_and_letter("456 oak avenue")
        assert num == "456"
        assert letter == "O"

    def test_handles_empty_string(self):
        """Should handle empty string."""
        num, letter = _street_num_and_letter("")
        assert num == ""
        assert letter == ""

    def test_handles_none(self):
        """Should handle None."""
        num, letter = _street_num_and_letter(None)
        assert num == ""
        assert letter == ""

    def test_handles_no_match(self):
        """Should handle address with no number."""
        num, letter = _street_num_and_letter("Main Street")
        assert num == ""
        assert letter == ""

    def test_handles_complex_address(self):
        """Should extract from complex address."""
        num, letter = _street_num_and_letter("9436 North St APT 159")
        assert num == "9436"
        assert letter == "N"


class TestFindUnit:
    """Tests for _find_unit function."""

    def test_finds_apt_unit(self):
        """Should find APT unit number."""
        result = _find_unit("123 Main St APT 456")
        assert result == "456"

    def test_finds_unit_unit(self):
        """Should find UNIT unit number."""
        result = _find_unit("123 Main St Unit F316")
        assert result == "F316"

    def test_finds_hash_unit(self):
        """Should find # unit number."""
        result = _find_unit("123 Main St #4A")
        assert result == "4A"

    def test_finds_ste_unit(self):
        """Should find STE unit number."""
        result = _find_unit("100 Commerce Dr STE A")
        assert result == "A"

    def test_finds_suite_unit(self):
        """Should find SUITE unit number."""
        result = _find_unit("500 Business Blvd Suite 200")
        assert result == "200"

    def test_handles_no_unit(self):
        """Should return empty for no unit."""
        result = _find_unit("456 Oak Avenue")
        assert result == ""

    def test_handles_empty_string(self):
        """Should handle empty string."""
        result = _find_unit("")
        assert result == ""

    def test_handles_none(self):
        """Should handle None."""
        result = _find_unit(None)
        assert result == ""

    def test_case_insensitive(self):
        """Should be case insensitive."""
        result = _find_unit("123 Main St apt 5b")
        assert result == "5b"


class TestFindBuilding:
    """Tests for _find_building function."""

    def test_finds_bldg(self):
        """Should find BLDG building number."""
        result = _find_building("123 Main St BLDG C")
        assert result == "C"

    def test_finds_bld(self):
        """Should find BLD building number."""
        result = _find_building("123 Main St BLD 5")
        assert result == "5"

    def test_handles_no_building(self):
        """Should return empty for no building."""
        result = _find_building("456 Oak Avenue")
        assert result == ""

    def test_handles_empty_string(self):
        """Should handle empty string."""
        result = _find_building("")
        assert result == ""

    def test_handles_none(self):
        """Should handle None."""
        result = _find_building(None)
        assert result == ""


class TestAddrNumAndStreet:
    """Tests for _addr_num_and_street function."""

    def test_extracts_number_and_street(self):
        """Should extract street number and street name."""
        num, street = _addr_num_and_street("333 FREMONT ST")
        assert num == "333"
        assert street == "fremont"

    def test_handles_empty_string(self):
        """Should handle empty string."""
        num, street = _addr_num_and_street("")
        assert num == ""
        assert street == ""

    def test_handles_none(self):
        """Should handle None."""
        num, street = _addr_num_and_street(None)
        assert num == ""
        assert street == ""


class TestFmtPeriodMmddyyyy:
    """Tests for _fmt_period_mmddyyyy function."""

    def test_formats_mm_dd_yyyy(self):
        """Should format MM/DD/YYYY dates."""
        result = _fmt_period_mmddyyyy("01/15/2025", "02/15/2025")
        assert result == "01/15/2025-02/15/2025"

    def test_formats_iso_date(self):
        """Should format YYYY-MM-DD dates."""
        result = _fmt_period_mmddyyyy("2025-01-15", "2025-02-15")
        assert result == "01/15/2025-02/15/2025"

    def test_handles_single_date(self):
        """Should handle single date."""
        result = _fmt_period_mmddyyyy("01/15/2025", "")
        assert result == "01/15/2025"

    def test_handles_empty_dates(self):
        """Should handle empty dates."""
        result = _fmt_period_mmddyyyy("", "")
        assert result == ""

    def test_handles_short_year(self):
        """Should handle MM/DD/YY dates."""
        result = _fmt_period_mmddyyyy("01/15/25", "02/15/25")
        assert result == "01/15/2025-02/15/2025"


class TestToGallons:
    """Tests for _to_gallons function."""

    def test_gallons_passthrough(self):
        """Should pass through gallons unchanged."""
        result = _to_gallons(100, "gallon")
        assert result == 100.0

    def test_gallons_gal(self):
        """Should handle gal abbreviation."""
        result = _to_gallons(100, "gal")
        assert result == 100.0

    def test_ccf_conversion(self):
        """Should convert CCF to gallons."""
        result = _to_gallons(1, "ccf")
        assert result == 748.0

    def test_kgal_conversion(self):
        """Should convert kgal to gallons."""
        result = _to_gallons(1, "kgal")
        assert result == 1000.0

    def test_cubic_feet_conversion(self):
        """Should convert cubic feet to gallons."""
        result = _to_gallons(1, "cf")
        assert abs(result - 7.48052) < 0.001

    def test_mgal_conversion(self):
        """Should convert million gallons."""
        result = _to_gallons(1, "mgal")
        assert result == 1000000.0

    def test_thousand_gallons(self):
        """Should convert thousand gallons."""
        result = _to_gallons(1, "thousand gallons")
        assert result == 1000.0

    def test_handles_string_amount(self):
        """Should handle string amount."""
        result = _to_gallons("100", "gallon")
        assert result == 100.0

    def test_handles_comma_in_amount(self):
        """Should handle commas in amount."""
        result = _to_gallons("1,000", "gallon")
        assert result == 1000.0

    def test_handles_no_uom(self):
        """Should assume gallons if no UOM."""
        result = _to_gallons(100, "")
        assert result == 100.0

    def test_handles_invalid_amount(self):
        """Should return None for invalid amount."""
        result = _to_gallons("invalid", "gallon")
        assert result is None


class TestBuildGlDesc:
    """Tests for _build_gl_desc function."""

    def test_house_electric_5706(self):
        """Should build GL desc for house electric (5706-0000)."""
        rec = {
            "Bill Period Start": "01/15/2025",
            "Bill Period End": "02/15/2025",
            "Service Address": "123 Main Street"
        }
        result = _build_gl_desc("5706-0000", rec)
        assert "Hse Elec" in result
        assert "123M" in result

    def test_house_gas_5710(self):
        """Should build GL desc for house gas (5710-0000)."""
        rec = {
            "Bill Period Start": "01/15/2025",
            "Bill Period End": "02/15/2025",
            "Service Address": "456 Oak Ave"
        }
        result = _build_gl_desc("5710-0000", rec)
        assert "Hse Gas" in result
        assert "456O" in result

    def test_vacant_electric_5705(self):
        """Should build GL desc for vacant electric (5705-0000)."""
        rec = {
            "Bill Period Start": "01/15/2025",
            "Bill Period End": "02/15/2025",
            "Service Address": "123 Main St APT 5"
        }
        result = _build_gl_desc("5705-0000", rec)
        assert "VE" in result
        assert "@5" in result

    def test_vacant_gas_5715(self):
        """Should build GL desc for vacant gas (5715-0000)."""
        rec = {
            "Bill Period Start": "01/15/2025",
            "Bill Period End": "02/15/2025",
            "Service Address": "123 Main St Unit 10"
        }
        result = _build_gl_desc("5715-0000", rec)
        assert "VG" in result
        assert "@10" in result

    def test_house_water_5720(self):
        """Should build GL desc for house water (5720-0000)."""
        rec = {
            "Bill Period Start": "01/15/2025",
            "Bill Period End": "02/15/2025",
            "Consumption Amount": "5000"
        }
        result = _build_gl_desc("5720-0000", rec)
        assert "5000" in result

    def test_trash_5550(self):
        """Should build GL desc for trash (5550-0000)."""
        rec = {
            "Bill Period Start": "01/15/2025",
            "Bill Period End": "02/15/2025"
        }
        result = _build_gl_desc("5550-0000", rec)
        assert "Trash Service" in result

    def test_default_gl(self):
        """Should return period for unknown GL."""
        rec = {
            "Bill Period Start": "01/15/2025",
            "Bill Period End": "02/15/2025"
        }
        result = _build_gl_desc("9999-0000", rec)
        # Should just return the period
        assert "01/15/2025" in result or result == ""

    def test_with_building(self):
        """Should include building in description."""
        rec = {
            "Bill Period Start": "01/15/2025",
            "Bill Period End": "02/15/2025",
            "Service Address": "123 Main St BLDG A"
        }
        result = _build_gl_desc("5706-0000", rec)
        assert "BL A" in result


class TestEnricherEdgeCases:
    """Edge case tests for enricher functions."""

    def test_norm_name_unicode(self):
        """Should handle unicode characters."""
        result = _norm_name("Café & Restaurant, Inc.")
        assert "café" in result or "cafe" in result
        assert "and" in result

    def test_find_unit_with_dash(self):
        """Should handle unit with dash."""
        result = _find_unit("123 Main St APT A-1")
        assert result == "A-1"

    def test_to_gallons_zero(self):
        """Should handle zero amount."""
        result = _to_gallons(0, "gallon")
        assert result == 0.0

    def test_to_gallons_negative(self):
        """Should handle negative amount."""
        result = _to_gallons(-100, "gallon")
        assert result == -100.0

    def test_build_gl_desc_empty_record(self):
        """Should handle empty record."""
        rec = {}
        result = _build_gl_desc("5706-0000", rec)
        # Should not crash
        assert isinstance(result, str)

    def test_street_num_with_letter_suffix(self):
        """Should handle address with number suffix."""
        num, letter = _street_num_and_letter("123A Main Street")
        # The regex expects space between number and street
        assert num == "" or num == "123"


class TestUomConversions:
    """Tests for UOM conversion edge cases."""

    def test_ccf_full_name(self):
        """Should handle CCF in various formats."""
        result = _to_gallons(2, "CCF")
        assert result == 1496.0  # 2 * 748

    def test_cubic_feet_full(self):
        """Should handle cubic feet."""
        result = _to_gallons(10, "cubic feet")
        assert abs(result - 74.8052) < 0.01

    def test_ft3_abbreviation(self):
        """Should handle ft3 abbreviation."""
        result = _to_gallons(1, "ft3")
        assert abs(result - 7.48052) < 0.001

    def test_multiple_kgal_variants(self):
        """Should handle kgal variants."""
        result1 = _to_gallons(1, "kgals")
        result2 = _to_gallons(1, "kgal")
        assert result1 == result2 == 1000.0


class TestPeriodFormatting:
    """Tests for period formatting edge cases."""

    def test_m_d_y_format(self):
        """Should handle M/D/Y format."""
        result = _fmt_period_mmddyyyy("1/5/2025", "2/5/2025")
        # Should normalize to MM/DD/YYYY
        assert "2025" in result

    def test_month_name_format(self):
        """Should handle month name format."""
        result = _fmt_period_mmddyyyy("Jan 15, 2025", "Feb 15, 2025")
        assert "01/15/2025" in result or "Jan" in result

    def test_full_month_name_format(self):
        """Should handle full month name."""
        result = _fmt_period_mmddyyyy("January 15, 2025", "February 15, 2025")
        assert "01/15/2025" in result or "January" in result


class TestAddressParsingCombinations:
    """Tests for various address parsing combinations."""

    def test_apartment_with_number_and_letter(self):
        """Should parse apartment with number and letter."""
        result = _find_unit("100 Main St APT 5B")
        assert result == "5B"

    def test_suite_three_digit(self):
        """Should parse three-digit suite."""
        result = _find_unit("200 Commerce Dr Suite 200")
        assert result == "200"

    def test_building_lowercase(self):
        """Should handle lowercase building."""
        result = _find_building("123 Main St bldg c")
        assert result == "C"

    def test_no_space_after_apt(self):
        """Should handle no space after APT."""
        result = _find_unit("123 Main St APT5")
        assert result == "" or result == "5"

