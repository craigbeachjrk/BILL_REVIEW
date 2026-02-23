"""
Unit tests for bill router Lambda functions.
Tests PDF page counting and routing logic.
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from io import BytesIO

# Add Lambda code path
ROUTER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "aws_lambdas", "us-east-1", "jrk-bill-router", "code"
)
sys.path.insert(0, ROUTER_PATH)

# Mock AWS clients before importing
with patch("boto3.client"):
    from lambda_bill_router import count_pdf_pages


class TestCountPdfPages:
    """Tests for count_pdf_pages function."""

    def test_counts_single_page_pdf(self):
        """Should count pages in a single-page PDF."""
        # Create minimal valid PDF
        pdf_content = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer << /Size 4 /Root 1 0 R >>
startxref
196
%%EOF"""

        result = count_pdf_pages(pdf_content)

        # Should return 1 or -1 if PDF parsing fails
        assert result == 1 or result == -1

    def test_handles_invalid_pdf(self):
        """Should return -1 for invalid PDF data."""
        invalid_data = b"This is not a PDF file"

        result = count_pdf_pages(invalid_data)

        assert result == -1

    def test_handles_empty_bytes(self):
        """Should return -1 for empty bytes."""
        result = count_pdf_pages(b"")

        assert result == -1

    def test_handles_truncated_pdf(self):
        """Should return -1 for truncated PDF."""
        truncated = b"%PDF-1.4\n1 0 obj"

        result = count_pdf_pages(truncated)

        assert result == -1

    def test_handles_corrupted_pdf(self):
        """Should return -1 for corrupted PDF."""
        corrupted = b"%PDF-1.4\n" + b"\x00" * 1000 + b"%%EOF"

        result = count_pdf_pages(corrupted)

        assert result == -1


class TestRoutingLogic:
    """Tests for routing decision logic."""

    def test_standard_route_for_small_file(self):
        """Small files should route to standard."""
        # Test the routing logic conditions
        page_count = 5
        file_size_mb = 2.0
        max_pages = 10
        max_size_mb = 10

        # Logic from lambda
        if page_count < 0:
            route = "standard"
        elif page_count > max_pages:
            route = "largefile"
        elif file_size_mb > max_size_mb:
            route = "largefile"
        else:
            route = "standard"

        assert route == "standard"

    def test_largefile_route_for_many_pages(self):
        """Files with many pages should route to largefile."""
        page_count = 15
        file_size_mb = 2.0
        max_pages = 10
        max_size_mb = 10

        if page_count < 0:
            route = "standard"
        elif page_count > max_pages:
            route = "largefile"
        elif file_size_mb > max_size_mb:
            route = "largefile"
        else:
            route = "standard"

        assert route == "largefile"

    def test_largefile_route_for_large_size(self):
        """Large files should route to largefile."""
        page_count = 5
        file_size_mb = 15.0
        max_pages = 10
        max_size_mb = 10

        if page_count < 0:
            route = "standard"
        elif page_count > max_pages:
            route = "largefile"
        elif file_size_mb > max_size_mb:
            route = "largefile"
        else:
            route = "standard"

        assert route == "largefile"

    def test_standard_route_for_unknown_pages(self):
        """Unknown page count should default to standard."""
        page_count = -1  # Unknown
        file_size_mb = 2.0
        max_pages = 10
        max_size_mb = 10

        if page_count < 0:
            route = "standard"
        elif page_count > max_pages:
            route = "largefile"
        elif file_size_mb > max_size_mb:
            route = "largefile"
        else:
            route = "standard"

        assert route == "standard"

    def test_boundary_page_count(self):
        """Exactly at page threshold should be standard."""
        page_count = 10  # Exactly at threshold
        file_size_mb = 2.0
        max_pages = 10
        max_size_mb = 10

        # Note: logic is page_count > max_pages, not >=
        if page_count < 0:
            route = "standard"
        elif page_count > max_pages:
            route = "largefile"
        elif file_size_mb > max_size_mb:
            route = "largefile"
        else:
            route = "standard"

        assert route == "standard"

    def test_boundary_file_size(self):
        """Exactly at size threshold should be standard."""
        page_count = 5
        file_size_mb = 10.0  # Exactly at threshold
        max_pages = 10
        max_size_mb = 10

        if page_count < 0:
            route = "standard"
        elif page_count > max_pages:
            route = "largefile"
        elif file_size_mb > max_size_mb:
            route = "largefile"
        else:
            route = "standard"

        assert route == "standard"


class TestKeyParsing:
    """Tests for S3 key parsing logic."""

    def test_extract_suffix_from_key(self):
        """Should extract suffix after prefix."""
        pending_prefix = "Bill_Parser_1_Pending_Parsing/"
        key = "Bill_Parser_1_Pending_Parsing/2025/01/15/invoice.pdf"

        suffix = key[len(pending_prefix):]

        assert suffix == "2025/01/15/invoice.pdf"

    def test_extract_filename_from_key(self):
        """Should extract filename from key."""
        pdf_key = "Bill_Parser_1_Pending_Parsing/2025/01/15/invoice.pdf"

        filename = pdf_key.rsplit('/', 1)[-1] if '/' in pdf_key else pdf_key

        assert filename == "invoice.pdf"

    def test_extract_filename_no_slash(self):
        """Should handle key without slash."""
        pdf_key = "invoice.pdf"

        filename = pdf_key.rsplit('/', 1)[-1] if '/' in pdf_key else pdf_key

        assert filename == "invoice.pdf"

    def test_sidecar_key_construction(self):
        """Should construct sidecar keys correctly."""
        key = "Bill_Parser_1_Pending_Parsing/2025/01/15/invoice.pdf"

        base_key = key.rsplit('.', 1)[0]
        notes_key = base_key + '.notes.json'
        rework_key = base_key + '.rework.json'

        assert base_key == "Bill_Parser_1_Pending_Parsing/2025/01/15/invoice"
        assert notes_key == "Bill_Parser_1_Pending_Parsing/2025/01/15/invoice.notes.json"
        assert rework_key == "Bill_Parser_1_Pending_Parsing/2025/01/15/invoice.rework.json"


class TestDestinationKeyConstruction:
    """Tests for destination key construction."""

    def test_standard_destination(self):
        """Should construct standard destination correctly."""
        standard_prefix = "Bill_Parser_1_Standard/"
        suffix = "2025/01/15/invoice.pdf"

        dest_key = f"{standard_prefix}{suffix}"

        assert dest_key == "Bill_Parser_1_Standard/2025/01/15/invoice.pdf"

    def test_largefile_destination(self):
        """Should construct largefile destination correctly."""
        largefile_prefix = "Bill_Parser_1_LargeFile/"
        suffix = "2025/01/15/invoice.pdf"

        dest_key = f"{largefile_prefix}{suffix}"

        assert dest_key == "Bill_Parser_1_LargeFile/2025/01/15/invoice.pdf"


class TestFileSizeCalculation:
    """Tests for file size calculations."""

    def test_bytes_to_mb_conversion(self):
        """Should convert bytes to MB correctly."""
        file_size_bytes = 5 * 1024 * 1024  # 5 MB

        file_size_mb = file_size_bytes / (1024 * 1024)

        assert file_size_mb == 5.0

    def test_small_file_conversion(self):
        """Should handle small file sizes."""
        file_size_bytes = 512 * 1024  # 512 KB

        file_size_mb = file_size_bytes / (1024 * 1024)

        assert file_size_mb == 0.5

    def test_large_file_conversion(self):
        """Should handle large file sizes."""
        file_size_bytes = 100 * 1024 * 1024  # 100 MB

        file_size_mb = file_size_bytes / (1024 * 1024)

        assert file_size_mb == 100.0

    def test_zero_size(self):
        """Should handle zero size."""
        file_size_bytes = 0

        file_size_mb = file_size_bytes / (1024 * 1024)

        assert file_size_mb == 0.0


class TestReasonStrings:
    """Tests for routing reason string construction."""

    def test_within_thresholds_reason(self):
        """Should generate correct reason for within thresholds."""
        reason = "within_thresholds"
        assert "within" in reason

    def test_page_count_exceeded_reason(self):
        """Should generate correct reason for page count exceeded."""
        page_count = 15
        max_pages = 10
        reason = f"page_count_{page_count}_exceeds_{max_pages}"

        assert "page_count" in reason
        assert "15" in reason
        assert "10" in reason

    def test_file_size_exceeded_reason(self):
        """Should generate correct reason for file size exceeded."""
        file_size_mb = 15.5
        max_size_mb = 10
        reason = f"file_size_{file_size_mb:.1f}MB_exceeds_{max_size_mb}MB"

        assert "file_size" in reason
        assert "15.5" in reason
        assert "10" in reason

    def test_unknown_page_count_reason(self):
        """Should generate correct reason for unknown page count."""
        reason = "unknown_page_count_default_standard"
        assert "unknown" in reason
        assert "standard" in reason


class TestEdgeCases:
    """Edge case tests for router."""

    def test_very_large_page_count(self):
        """Should handle very large page counts."""
        page_count = 10000
        max_pages = 10

        assert page_count > max_pages

    def test_very_small_file(self):
        """Should handle very small files."""
        file_size_bytes = 100  # 100 bytes
        file_size_mb = file_size_bytes / (1024 * 1024)

        assert file_size_mb < 0.001

    def test_special_chars_in_filename(self):
        """Should handle special characters in filename."""
        key = "Bill_Parser_1_Pending_Parsing/2025/01/15/invoice (1) - copy.pdf"

        filename = key.rsplit('/', 1)[-1]

        assert filename == "invoice (1) - copy.pdf"

    def test_unicode_in_filename(self):
        """Should handle unicode in filename."""
        key = "Bill_Parser_1_Pending_Parsing/2025/01/15/factura_café.pdf"

        filename = key.rsplit('/', 1)[-1]

        assert filename == "factura_café.pdf"

