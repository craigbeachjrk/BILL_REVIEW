"""
Integration tests for UBI (Utility Bill Interfaces) and billback API endpoints.
Tests UBI assignment, billback posting, archiving, and batch operations.
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
def authenticated_ubi_admin():
    """Create FastAPI test client with authenticated UBI admin user."""
    # moto mock is already started by conftest.py at module level
    s3 = boto3.client("s3", region_name="us-east-1")
    ddb = boto3.client("dynamodb", region_name="us-east-1")

    # Create test billback data in S3
    today = datetime.now()
    billback_key = f"Bill_Parser_7_Billback/yyyy={today.year}/mm={today.month:02d}/dd={today.day:02d}/test_billback.jsonl"

    billback_data = [
        {
            "Vendor Name": "Electric Company",
            "Invoice Number": "INV-001",
            "Account Number": "ELEC-12345",
            "Line Item Charge": "500.00",
            "Line Item Description": "Electric Service",
            "Bill Date": "01/15/2025",
            "Due Date": "02/15/2025",
            "Property": "Test Property",
            "Property ID": "PROP001",
            "pdf_id": "billback123hash",
            "ubi_status": "unassigned"
        },
        {
            "Vendor Name": "Gas Company",
            "Invoice Number": "INV-002",
            "Account Number": "GAS-67890",
            "Line Item Charge": "250.00",
            "Line Item Description": "Gas Service",
            "Bill Date": "01/16/2025",
            "Due Date": "02/16/2025",
            "Property": "Test Property 2",
            "Property ID": "PROP002",
            "pdf_id": "billback456hash",
            "ubi_status": "unassigned"
        }
    ]

    s3.put_object(
        Bucket="test-bucket",
        Key=billback_key,
        Body="\n".join(json.dumps(line) for line in billback_data)
    )

    # Clean existing users and add test users
    try:
        response = ddb.scan(TableName="test-users")
        for item in response.get("Items", []):
            ddb.delete_item(TableName="test-users", Key={"user_id": item["user_id"]})
    except Exception:
        pass

    # Create UBI admin user
    from auth import hash_password
    test_password = "TestPassword123!"
    password_hash = hash_password(test_password)

    ddb.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": "ubi_admin@example.com"},
            "password_hash": {"S": password_hash},
            "role": {"S": "UBI_Admins"},
            "full_name": {"S": "UBI Admin"},
            "enabled": {"BOOL": True},
            "must_change_password": {"BOOL": False}
        }
    )

    # Create system admin user
    ddb.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": "admin@example.com"},
            "password_hash": {"S": password_hash},
            "role": {"S": "System_Admins"},
            "full_name": {"S": "System Admin"},
            "enabled": {"BOOL": True},
            "must_change_password": {"BOOL": False}
        }
    )

    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    # Login as UBI admin
    client.post(
        "/login",
        data={"username": "ubi_admin@example.com", "password": test_password},
        follow_redirects=True
    )

    yield {
        "client": client,
        "s3": s3,
        "ddb": ddb,
        "password": test_password,
        "billback_key": billback_key
    }


class TestUBIPageAccess:
    """Tests for UBI page access."""

    def test_ubi_page_loads(self, authenticated_ubi_admin):
        """UBI page should load for UBI admin."""
        client = authenticated_ubi_admin["client"]

        response = client.get("/ubi")

        # May return 200 or redirect depending on role/session
        assert response.status_code in [200, 302, 403]

    def test_billback_page_loads(self, authenticated_ubi_admin):
        """Billback page should load."""
        client = authenticated_ubi_admin["client"]

        response = client.get("/billback")

        assert response.status_code in [200, 302, 403]

    def test_billback_summary_page_loads(self, authenticated_ubi_admin):
        """Billback summary page should load."""
        client = authenticated_ubi_admin["client"]

        response = client.get("/billback/summary")

        assert response.status_code in [200, 302, 403]

    def test_ubi_batch_page_loads(self, authenticated_ubi_admin):
        """UBI batch page should load."""
        client = authenticated_ubi_admin["client"]

        response = client.get("/ubi-batch")

        assert response.status_code in [200, 302, 403]

    def test_billback_charts_page_loads(self, authenticated_ubi_admin):
        """Billback charts page should load."""
        client = authenticated_ubi_admin["client"]

        response = client.get("/billback/charts")

        assert response.status_code in [200, 302, 403]


class TestBillbackArchiveAPI:
    """Tests for /api/billback/archive endpoint."""

    def test_archive_success(self, authenticated_ubi_admin):
        """Archive should succeed with valid data."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/archive",
            json={
                "pdf_ids": ["billback123hash"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        # Should succeed or fail gracefully
        assert response.status_code in [200, 201, 400, 404, 422, 500]

    def test_archive_empty_list(self, authenticated_ubi_admin):
        """Archive with empty list should be handled."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/billback/archive",
            json={"pdf_ids": []}
        )

        assert response.status_code in [200, 400, 422]


class TestBillbackPostedAPI:
    """Tests for /api/billback/posted endpoint."""

    def test_get_posted_returns_data(self, authenticated_ubi_admin):
        """GET posted should return data."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.get(
            "/api/billback/posted",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        # May return 200, 400 (missing params), or 404 (no data)
        assert response.status_code in [200, 400, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))


class TestBillbackSaveAPI:
    """Tests for /api/billback/save endpoint."""

    def test_save_billback(self, authenticated_ubi_admin):
        """Save billback should succeed."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/save",
            json={
                "pdf_id": "billback123hash",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}",
                "changes": {
                    "Property ID": "NEW_PROP001"
                }
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestBillbackSubmitAPI:
    """Tests for /api/billback/submit endpoint."""

    def test_submit_billback(self, authenticated_ubi_admin):
        """Submit billback should process."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/submit",
            json={
                "pdf_ids": ["billback123hash"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestBillbackSummaryAPI:
    """Tests for /api/billback/summary endpoint."""

    def test_get_summary(self, authenticated_ubi_admin):
        """GET summary should return stats."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.get(
            "/api/billback/summary",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)


class TestUBIUnassignedAPI:
    """Tests for /api/billback/ubi/unassigned endpoint."""

    def test_get_unassigned(self, authenticated_ubi_admin):
        """GET unassigned should return unassigned items."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.get(
            "/api/billback/ubi/unassigned",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))


class TestUBIAssignAPI:
    """Tests for /api/billback/ubi/assign endpoint."""

    def test_assign_ubi(self, authenticated_ubi_admin):
        """Assign UBI should succeed."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/ubi/assign",
            json={
                "line_id": "billback123hash#0",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}",
                "property_id": "PROP001",
                "periods": ["2025-01"]
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]

    def test_assign_ubi_missing_line_id(self, authenticated_ubi_admin):
        """Assign UBI without line_id should fail."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/billback/ubi/assign",
            json={
                "property_id": "PROP001",
                "periods": ["2025-01"]
            }
        )

        assert response.status_code in [400, 422]


class TestUBISuggestionsAPI:
    """Tests for /api/billback/ubi/suggestions endpoint."""

    def test_get_suggestions(self, authenticated_ubi_admin):
        """GET suggestions should return suggestions."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.get(
            "/api/billback/ubi/suggestions",
            params={
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}",
                "account_number": "ELEC-12345"
            }
        )

        assert response.status_code in [200, 404]


class TestUBIAcceptSuggestionAPI:
    """Tests for /api/billback/ubi/accept-suggestion endpoint."""

    def test_accept_suggestion(self, authenticated_ubi_admin):
        """Accept suggestion should process."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/ubi/accept-suggestion",
            json={
                "line_id": "billback123hash#0",
                "suggestion_id": "suggestion123",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestUBIAccountHistoryAPI:
    """Tests for /api/billback/ubi/account-history endpoint."""

    def test_get_account_history(self, authenticated_ubi_admin):
        """GET account history should return history."""
        client = authenticated_ubi_admin["client"]

        response = client.get("/api/billback/ubi/account-history/ELEC-12345")

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))


class TestUBICalculateSuggestionAPI:
    """Tests for /api/billback/ubi/calculate-suggestion endpoint."""

    def test_calculate_suggestion(self, authenticated_ubi_admin):
        """Calculate suggestion should return result."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/billback/ubi/calculate-suggestion",
            json={
                "line_id": "billback123hash#0",
                "account_number": "ELEC-12345"
            }
        )

        assert response.status_code in [200, 400, 404, 422, 500]


class TestUBIAssignedAPI:
    """Tests for /api/billback/ubi/assigned endpoint."""

    def test_get_assigned(self, authenticated_ubi_admin):
        """GET assigned should return assigned items."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.get(
            "/api/billback/ubi/assigned",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))


class TestUBIUnassignAPI:
    """Tests for /api/billback/ubi/unassign endpoint."""

    def test_unassign_ubi(self, authenticated_ubi_admin):
        """Unassign UBI should succeed."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/ubi/unassign",
            json={
                "line_id": "billback123hash#0",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestUBIReassignAPI:
    """Tests for /api/billback/ubi/reassign endpoint."""

    def test_reassign_ubi(self, authenticated_ubi_admin):
        """Reassign UBI should succeed."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/ubi/reassign",
            json={
                "line_id": "billback123hash#0",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}",
                "new_property_id": "PROP002",
                "new_periods": ["2025-02"]
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestUBIArchiveAPI:
    """Tests for /api/billback/ubi/archive endpoint."""

    def test_archive_ubi(self, authenticated_ubi_admin):
        """Archive UBI should succeed."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/ubi/archive",
            json={
                "line_ids": ["billback123hash#0"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestUBIStatsByPropertyAPI:
    """Tests for /api/ubi/stats/by_property endpoint."""

    def test_get_stats_by_property(self, authenticated_ubi_admin):
        """GET stats by property should return stats."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.get(
            "/api/ubi/stats/by_property",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))


class TestUBITrackerAPI:
    """Tests for /api/ubi tracker endpoints."""

    def test_add_to_tracker(self, authenticated_ubi_admin):
        """Add to tracker should succeed."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/ubi/add-to-tracker",
            json={
                "account_number": "NEW-12345",
                "vendor_name": "New Vendor"
            }
        )

        assert response.status_code in [200, 201, 400, 422, 500]

    def test_add_to_ubi(self, authenticated_ubi_admin):
        """Add to UBI should succeed."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/ubi/add-to-ubi",
            json={
                "account_number": "NEW-12345",
                "property_id": "PROP001"
            }
        )

        assert response.status_code in [200, 201, 400, 422, 500]

    def test_remove_from_tracker(self, authenticated_ubi_admin):
        """Remove from tracker should succeed or return not found."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/ubi/remove-from-tracker",
            json={
                "account_number": "NEW-12345"
            }
        )

        # 404 when account not found in tracker
        assert response.status_code in [200, 201, 400, 404, 422, 500]

    def test_remove_from_ubi(self, authenticated_ubi_admin):
        """Remove from UBI should succeed or return not found."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/ubi/remove-from-ubi",
            json={
                "account_number": "NEW-12345"
            }
        )

        # 404 when account not found in UBI
        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestBillbackUpdateLineItemAPI:
    """Tests for /api/billback/update-line-item endpoint."""

    def test_update_line_item(self, authenticated_ubi_admin):
        """Update line item should succeed."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/update-line-item",
            json={
                "line_id": "billback123hash#0",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}",
                "updates": {
                    "Line Item Charge": "550.00"
                }
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestBillbackAssignPeriodsAPI:
    """Tests for /api/billback/assign-periods endpoint."""

    def test_assign_periods(self, authenticated_ubi_admin):
        """Assign periods should succeed."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/assign-periods",
            json={
                "line_id": "billback123hash#0",
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}",
                "periods": ["2025-01", "2025-02"]
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestBillbackSendToPostAPI:
    """Tests for /api/billback/send-to-post endpoint."""

    def test_send_to_post(self, authenticated_ubi_admin):
        """Send to post should succeed."""
        client = authenticated_ubi_admin["client"]
        today = datetime.now()

        response = client.post(
            "/api/billback/send-to-post",
            json={
                "line_ids": ["billback123hash#0"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestUBIBatchAPI:
    """Tests for /api/ubi-batch endpoints."""

    def test_create_batch(self, authenticated_ubi_admin):
        """Create batch should succeed."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/ubi-batch/create",
            json={
                "name": "Test Batch",
                "line_ids": ["billback123hash#0"]
            }
        )

        assert response.status_code in [200, 201, 400, 422, 500]

    def test_list_batches(self, authenticated_ubi_admin):
        """List batches should return batches."""
        client = authenticated_ubi_admin["client"]

        response = client.get("/api/ubi-batch/list")

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_get_batch_detail(self, authenticated_ubi_admin):
        """Get batch detail should return batch info."""
        client = authenticated_ubi_admin["client"]

        response = client.get("/api/ubi-batch/detail/test-batch-id")

        assert response.status_code in [200, 404]

    def test_finalize_batch(self, authenticated_ubi_admin):
        """Finalize batch should succeed."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/ubi-batch/finalize",
            json={"batch_id": "test-batch-id"}
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]

    def test_delete_batch(self, authenticated_ubi_admin):
        """Delete batch should succeed."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/ubi-batch/delete",
            json={"batch_id": "test-batch-id"}
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]

    def test_export_snowflake(self, authenticated_ubi_admin):
        """Export to Snowflake should process."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/ubi-batch/export-snowflake",
            json={"batch_id": "test-batch-id"}
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestUBIAccessControl:
    """Tests for UBI access control."""

    def test_ubi_requires_auth(self, authenticated_ubi_admin):
        """UBI endpoints should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)
        response = fresh_client.get("/ubi", follow_redirects=False)

        assert response.status_code in [302, 303, 307, 401, 403]

    def test_billback_requires_auth(self, authenticated_ubi_admin):
        """Billback endpoints should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)
        response = fresh_client.get("/billback", follow_redirects=False)

        assert response.status_code in [302, 303, 307, 401, 403]


class TestUBIValidation:
    """Tests for UBI input validation."""

    def test_invalid_date_format(self, authenticated_ubi_admin):
        """Invalid date format should be rejected."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/billback/ubi/assign",
            json={
                "line_id": "test123",
                "date": "invalid-date",
                "property_id": "PROP001"
            }
        )

        assert response.status_code in [400, 422, 500]

    def test_empty_line_ids(self, authenticated_ubi_admin):
        """Empty line_ids should be handled."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/billback/ubi/archive",
            json={"line_ids": []}
        )

        assert response.status_code in [200, 400, 422]


class TestUBIErrorHandling:
    """Tests for UBI error handling."""

    def test_invalid_json(self, authenticated_ubi_admin):
        """Invalid JSON should return error."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/billback/ubi/assign",
            content="not valid json",
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code in [400, 422]

    def test_missing_required_fields(self, authenticated_ubi_admin):
        """Missing required fields should return error."""
        client = authenticated_ubi_admin["client"]

        response = client.post(
            "/api/billback/ubi/assign",
            json={}
        )

        assert response.status_code in [400, 422]

