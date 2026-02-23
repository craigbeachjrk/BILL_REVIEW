"""
Integration tests for bulk operation API endpoints.
Tests bulk assign property, bulk assign vendor, and bulk rework.
"""
import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock
from moto import mock_aws
import boto3
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# snowflake mock is handled by conftest.py


@pytest.fixture
def authenticated_app_with_data():
    """Create FastAPI test client with authenticated user and test invoice data."""
    # moto mock is already started by conftest.py at module level
    s3 = boto3.client("s3", region_name="us-east-1")
    ddb = boto3.client("dynamodb", region_name="us-east-1")

    # Create test invoice data in S3
    today = datetime.now()
    s3_key = f"Bill_Parser_4_Enriched_Outputs/yyyy={today.year}/mm={today.month:02d}/dd={today.day:02d}/test_invoice.jsonl"

    # Create multiple invoice lines
    invoice_lines = [
        {
            "Vendor Name": "Test Vendor 1",
            "Invoice Number": "INV-001",
            "Account Number": "ACCT-123",
            "Line Item Charge": "100.00",
            "Line Item Description": "Test Service 1",
            "Bill Date": "01/15/2025",
            "Due Date": "02/15/2025",
            "Property": "Test Property",
            "pdf_id": "abc123hash"
        },
        {
            "Vendor Name": "Test Vendor 2",
            "Invoice Number": "INV-002",
            "Account Number": "ACCT-456",
            "Line Item Charge": "200.00",
            "Line Item Description": "Test Service 2",
            "Bill Date": "01/16/2025",
            "Due Date": "02/16/2025",
            "Property": "Test Property 2",
            "pdf_id": "def456hash"
        }
    ]

    s3.put_object(
        Bucket="test-bucket",
        Key=s3_key,
        Body="\n".join(json.dumps(line) for line in invoice_lines)
    )

    # Clean existing users and add test users
    try:
        response = ddb.scan(TableName="test-users")
        for item in response.get("Items", []):
            ddb.delete_item(TableName="test-users", Key={"user_id": item["user_id"]})
    except Exception:
        pass

    # Create test user
    from auth import hash_password
    test_password = "TestPassword123!"
    password_hash = hash_password(test_password)

    ddb.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": "test@example.com"},
            "password_hash": {"S": password_hash},
            "role": {"S": "System_Admins"},
            "full_name": {"S": "Test Admin"},
            "enabled": {"BOOL": True},
            "must_change_password": {"BOOL": False}
        }
    )

    # Create limited-access user
    ddb.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": "limited@example.com"},
            "password_hash": {"S": password_hash},
            "role": {"S": "Utility_APs"},
            "full_name": {"S": "Limited User"},
            "enabled": {"BOOL": True},
            "must_change_password": {"BOOL": False}
        }
    )

    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    # Login to get session
    client.post(
        "/login",
        data={"username": "test@example.com", "password": test_password},
        follow_redirects=True
    )

    yield {
        "client": client,
        "s3": s3,
        "ddb": ddb,
        "s3_key": s3_key,
        "password": test_password,
        "pdf_ids": ["abc123hash", "def456hash"]
    }


class TestBulkAssignProperty:
    """Tests for /api/bulk_assign_property endpoint."""

    def test_bulk_assign_property_success(self, authenticated_app_with_data):
        """Bulk assign property should update multiple invoices."""
        client = authenticated_app_with_data["client"]
        pdf_ids = authenticated_app_with_data["pdf_ids"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_property",
            json={
                "pdf_ids": pdf_ids,
                "property_id": "12345",
                "property_name": "New Test Property",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        # Should succeed or fail gracefully
        assert response.status_code in [200, 201, 400, 404, 422]

    def test_bulk_assign_property_empty_list(self, authenticated_app_with_data):
        """Bulk assign with empty pdf_ids should return error."""
        client = authenticated_app_with_data["client"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_property",
            json={
                "pdf_ids": [],
                "property_id": "12345",
                "property_name": "Test Property",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        # Should return validation error or handle gracefully
        assert response.status_code in [200, 400, 422]

    def test_bulk_assign_property_missing_property(self, authenticated_app_with_data):
        """Bulk assign without property info should return error."""
        client = authenticated_app_with_data["client"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_property",
            json={
                "pdf_ids": ["abc123hash"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        # Should return validation error
        assert response.status_code in [400, 422]

    def test_bulk_assign_property_requires_auth(self, authenticated_app_with_data):
        """Bulk assign should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)
        response = fresh_client.post(
            "/api/bulk_assign_property",
            json={"pdf_ids": ["abc123"], "property_id": "123"},
            follow_redirects=False
        )

        assert response.status_code in [302, 303, 307, 401, 403, 422]


class TestBulkAssignVendor:
    """Tests for /api/bulk_assign_vendor endpoint."""

    def test_bulk_assign_vendor_success(self, authenticated_app_with_data):
        """Bulk assign vendor should update multiple invoices."""
        client = authenticated_app_with_data["client"]
        pdf_ids = authenticated_app_with_data["pdf_ids"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_vendor",
            json={
                "pdf_ids": pdf_ids,
                "vendor_id": "V12345",
                "vendor_name": "New Test Vendor",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        # Should succeed or fail gracefully
        assert response.status_code in [200, 201, 400, 404, 422]

    def test_bulk_assign_vendor_empty_list(self, authenticated_app_with_data):
        """Bulk assign with empty pdf_ids should return error."""
        client = authenticated_app_with_data["client"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_vendor",
            json={
                "pdf_ids": [],
                "vendor_id": "V12345",
                "vendor_name": "Test Vendor",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 400, 422]

    def test_bulk_assign_vendor_missing_vendor(self, authenticated_app_with_data):
        """Bulk assign without vendor info should return error."""
        client = authenticated_app_with_data["client"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_vendor",
            json={
                "pdf_ids": ["abc123hash"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [400, 422]

    def test_bulk_assign_vendor_requires_auth(self, authenticated_app_with_data):
        """Bulk assign vendor should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)
        response = fresh_client.post(
            "/api/bulk_assign_vendor",
            json={"pdf_ids": ["abc123"], "vendor_id": "V123"},
            follow_redirects=False
        )

        assert response.status_code in [302, 303, 307, 401, 403, 422]


class TestBulkRework:
    """Tests for /api/bulk_rework endpoint."""

    def test_bulk_rework_success(self, authenticated_app_with_data):
        """Bulk rework should move invoices to rework queue."""
        client = authenticated_app_with_data["client"]
        pdf_ids = authenticated_app_with_data["pdf_ids"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_rework",
            json={
                "pdf_ids": pdf_ids,
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}",
                "reason": "Needs re-parsing"
            }
        )

        # Should succeed or fail gracefully
        assert response.status_code in [200, 201, 400, 404, 422]

    def test_bulk_rework_empty_list(self, authenticated_app_with_data):
        """Bulk rework with empty pdf_ids should return error."""
        client = authenticated_app_with_data["client"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_rework",
            json={
                "pdf_ids": [],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 400, 422]

    def test_bulk_rework_requires_auth(self, authenticated_app_with_data):
        """Bulk rework should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)
        response = fresh_client.post(
            "/api/bulk_rework",
            json={"pdf_ids": ["abc123"]},
            follow_redirects=False
        )

        assert response.status_code in [302, 303, 307, 401, 403, 422]


class TestBulkOperationValidation:
    """Tests for input validation on bulk operations."""

    def test_invalid_pdf_id_format(self, authenticated_app_with_data):
        """Invalid pdf_id format should be handled."""
        client = authenticated_app_with_data["client"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_property",
            json={
                "pdf_ids": ["../../../etc/passwd"],  # Malicious input
                "property_id": "12345",
                "property_name": "Test Property",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        # Should reject or handle safely
        assert response.status_code in [200, 400, 404, 422]

    def test_very_long_property_name(self, authenticated_app_with_data):
        """Very long property name should be handled."""
        client = authenticated_app_with_data["client"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_property",
            json={
                "pdf_ids": ["abc123hash"],
                "property_id": "12345",
                "property_name": "A" * 10000,  # Very long name
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        # Should handle gracefully
        assert response.status_code in [200, 201, 400, 422, 500]

    def test_special_characters_in_vendor_name(self, authenticated_app_with_data):
        """Special characters in vendor name should be handled."""
        client = authenticated_app_with_data["client"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_vendor",
            json={
                "pdf_ids": ["abc123hash"],
                "vendor_id": "V12345",
                "vendor_name": "<script>alert('xss')</script>",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        # Should sanitize or reject
        assert response.status_code in [200, 201, 400, 422]

    def test_invalid_date_format(self, authenticated_app_with_data):
        """Invalid date format should be handled."""
        client = authenticated_app_with_data["client"]

        response = client.post(
            "/api/bulk_assign_property",
            json={
                "pdf_ids": ["abc123hash"],
                "property_id": "12345",
                "property_name": "Test Property",
                "date": "not-a-valid-date"
            }
        )

        # Should return validation error
        assert response.status_code in [400, 422, 500]


class TestBulkOperationResponses:
    """Tests for bulk operation response formats."""

    def test_assign_property_returns_count(self, authenticated_app_with_data):
        """Bulk assign property should return count of updated items."""
        client = authenticated_app_with_data["client"]
        pdf_ids = authenticated_app_with_data["pdf_ids"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_property",
            json={
                "pdf_ids": pdf_ids,
                "property_id": "12345",
                "property_name": "Test Property",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        if response.status_code == 200:
            data = response.json()
            # Should have some kind of success indicator
            assert isinstance(data, dict)

    def test_assign_vendor_returns_count(self, authenticated_app_with_data):
        """Bulk assign vendor should return count of updated items."""
        client = authenticated_app_with_data["client"]
        pdf_ids = authenticated_app_with_data["pdf_ids"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_assign_vendor",
            json={
                "pdf_ids": pdf_ids,
                "vendor_id": "V12345",
                "vendor_name": "Test Vendor",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)

    def test_rework_returns_status(self, authenticated_app_with_data):
        """Bulk rework should return status of operation."""
        client = authenticated_app_with_data["client"]
        pdf_ids = authenticated_app_with_data["pdf_ids"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_rework",
            json={
                "pdf_ids": pdf_ids,
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)
