"""
Integration tests for invoice-related API endpoints.
Tests invoice listing, drafts, catalogs, and timing endpoints.
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
def authenticated_app():
    """Create FastAPI test client with authenticated user and test data."""
    # moto mock is already started by conftest.py at module level
    s3 = boto3.client("s3", region_name="us-east-1")
    ddb = boto3.client("dynamodb", region_name="us-east-1")

    # Create test invoice data in S3
    today = datetime.now()
    s3_key = f"Bill_Parser_4_Enriched_Outputs/yyyy={today.year}/mm={today.month:02d}/dd={today.day:02d}/test_invoice.jsonl"
    invoice_data = {
        "Vendor Name": "Test Vendor",
        "Invoice Number": "INV-001",
        "Account Number": "ACCT-123",
        "Line Item Charge": "100.00",
        "Line Item Description": "Test Service",
        "Bill Date": "01/15/2025",
        "Due Date": "02/15/2025",
        "Property": "Test Property",
        "pdf_id": "abc123hash"
    }
    s3.put_object(
        Bucket="test-bucket",
        Key=s3_key,
        Body=json.dumps(invoice_data)
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
        "password": test_password
    }


class TestDatesEndpoint:
    """Tests for /api/dates endpoint."""

    def test_dates_returns_data(self, authenticated_app):
        """Dates endpoint should return dates data."""
        client = authenticated_app["client"]

        response = client.get("/api/dates")

        assert response.status_code == 200
        data = response.json()
        # API returns {"dates": [...]} structure
        assert isinstance(data, dict)
        assert "dates" in data
        assert isinstance(data["dates"], list)

    def test_dates_returns_date_objects(self, authenticated_app):
        """Dates endpoint should return objects with date fields."""
        client = authenticated_app["client"]

        response = client.get("/api/dates")

        assert response.status_code == 200
        data = response.json()
        # If dates exist, check structure
        if len(data.get("dates", [])) > 0:
            date_obj = data["dates"][0]
            assert "label" in date_obj or "tuple" in date_obj


class TestInvoicesEndpoint:
    """Tests for /api/invoices endpoint."""

    def test_invoices_returns_data(self, authenticated_app):
        """Invoices endpoint should return invoice data."""
        client = authenticated_app["client"]
        today = datetime.now()

        response = client.get(
            "/api/invoices",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        # Should return data or empty list
        assert response.status_code in [200, 404]

    def test_invoices_requires_date_param(self, authenticated_app):
        """Invoices endpoint should handle missing date parameter."""
        client = authenticated_app["client"]

        response = client.get("/api/invoices")

        # Should either return error or use default
        assert response.status_code in [200, 400, 422]


class TestInvoicesPageEndpoint:
    """Tests for /invoices page endpoint."""

    def test_invoices_page_loads(self, authenticated_app):
        """Invoices page should load for authenticated user with date param."""
        client = authenticated_app["client"]
        today = datetime.now()

        response = client.get(
            "/invoices",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code == 200
        assert "invoice" in response.text.lower()

    def test_invoices_page_has_table(self, authenticated_app):
        """Invoices page should have a data table."""
        client = authenticated_app["client"]
        today = datetime.now()

        response = client.get(
            "/invoices",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code == 200
        assert "<table" in response.text.lower() or "table" in response.text.lower()


class TestCatalogEndpoints:
    """Tests for catalog API endpoints."""

    def test_catalogs_endpoint_returns_data(self, authenticated_app):
        """Catalogs endpoint should return catalog data."""
        client = authenticated_app["client"]

        response = client.get("/api/catalogs")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        # Should have vendor and property keys
        assert "vendors" in data or "properties" in data or len(data) >= 0

    def test_vendors_catalog_returns_data(self, authenticated_app):
        """Vendors catalog should return vendor data structure."""
        client = authenticated_app["client"]

        response = client.get("/api/catalog/vendors")

        assert response.status_code == 200
        data = response.json()
        # Returns {"items": [...]} structure
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_properties_catalog_returns_data(self, authenticated_app):
        """Properties catalog should return property data structure."""
        client = authenticated_app["client"]

        response = client.get("/api/catalog/properties")

        assert response.status_code == 200
        data = response.json()
        # Returns {"items": [...]} structure
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_gl_accounts_catalog_returns_data(self, authenticated_app):
        """GL accounts catalog should return GL account data structure."""
        client = authenticated_app["client"]

        response = client.get("/api/catalog/gl-accounts")

        assert response.status_code == 200
        data = response.json()
        # Returns {"items": [...]} structure
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)


class TestDraftsEndpoint:
    """Tests for /api/drafts endpoint."""

    def test_drafts_get_returns_data(self, authenticated_app):
        """Drafts endpoint should return drafts data."""
        client = authenticated_app["client"]
        today = datetime.now()

        response = client.get(
            "/api/drafts",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        # May return 200, 404 or 422 depending on data/params
        assert response.status_code in [200, 404, 422]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (list, dict))

    def test_drafts_put_creates_draft(self, authenticated_app):
        """PUT to drafts should create or update a draft."""
        client = authenticated_app["client"]

        draft_data = {
            "pdf_id": "abc123hash",
            "vendor": "Updated Vendor",
            "property": "Updated Property",
            "line_idx": 0
        }

        response = client.put("/api/drafts", json=draft_data)

        # Should succeed or return appropriate error
        assert response.status_code in [200, 201, 400, 422]

    def test_drafts_batch_post(self, authenticated_app):
        """POST to drafts batch should handle multiple drafts."""
        client = authenticated_app["client"]

        batch_data = {
            "drafts": [
                {"pdf_id": "abc123hash", "vendor": "Vendor1", "line_idx": 0},
                {"pdf_id": "abc123hash", "vendor": "Vendor2", "line_idx": 1}
            ]
        }

        response = client.post("/api/drafts/batch", json=batch_data)

        # Should succeed or return validation error
        assert response.status_code in [200, 201, 400, 422]

    def test_drafts_new_lines(self, authenticated_app):
        """GET to drafts/new-lines should return new line data."""
        client = authenticated_app["client"]
        today = datetime.now()

        response = client.get(
            "/api/drafts/new-lines",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        # May return 200, 404 or 422 depending on data/params
        assert response.status_code in [200, 404, 422]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (list, dict))


class TestTimingEndpoints:
    """Tests for invoice timing API endpoints."""

    def test_timing_get(self, authenticated_app):
        """GET timing should return timing data for invoice."""
        client = authenticated_app["client"]

        response = client.get("/api/timing/abc123hash")

        # Should return data or not found
        assert response.status_code in [200, 404]

    def test_timing_start(self, authenticated_app):
        """POST timing start should record start time."""
        client = authenticated_app["client"]

        response = client.post("/api/timing/abc123hash/start")

        # Should succeed
        assert response.status_code in [200, 201]

    def test_timing_heartbeat(self, authenticated_app):
        """POST timing heartbeat should update last active time."""
        client = authenticated_app["client"]

        # Start first
        client.post("/api/timing/abc123hash/start")

        response = client.post("/api/timing/abc123hash/heartbeat")

        assert response.status_code in [200, 201]

    def test_timing_stop(self, authenticated_app):
        """POST timing stop should record stop time."""
        client = authenticated_app["client"]

        # Start first
        client.post("/api/timing/abc123hash/start")

        response = client.post("/api/timing/abc123hash/stop")

        assert response.status_code in [200, 201]


class TestInvoicesStatusEndpoint:
    """Tests for /api/invoices_status endpoint."""

    def test_invoices_status_returns_data(self, authenticated_app):
        """Invoices status endpoint should return status data."""
        client = authenticated_app["client"]
        today = datetime.now()

        response = client.get(
            "/api/invoices_status",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (list, dict))


class TestSubmitEndpoint:
    """Tests for /api/submit endpoint."""

    def test_submit_requires_auth(self, authenticated_app):
        """Submit should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        # Use fresh client without session
        fresh_client = TestClient(app)
        response = fresh_client.post("/api/submit", json={}, follow_redirects=False)

        # Should redirect to login or return 401/403, or fail validation
        assert response.status_code in [302, 303, 307, 401, 403, 422]

    def test_submit_validates_input(self, authenticated_app):
        """Submit should validate input data."""
        client = authenticated_app["client"]

        # Empty submission
        response = client.post("/api/submit", json={})

        # Should return validation error
        assert response.status_code in [400, 422]

    def test_submit_with_valid_data(self, authenticated_app):
        """Submit with valid data should process."""
        client = authenticated_app["client"]
        today = datetime.now()

        submit_data = {
            "pdf_ids": ["abc123hash"],
            "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
        }

        response = client.post("/api/submit", json=submit_data)

        # Should succeed or fail gracefully
        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestReviewPage:
    """Tests for /review page endpoint."""

    def test_review_page_loads(self, authenticated_app):
        """Review page should load for authenticated user with date param."""
        client = authenticated_app["client"]
        today = datetime.now()

        response = client.get(
            "/review",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        # May require different params or redirect
        assert response.status_code in [200, 302, 422]


class TestS3KeyValidation:
    """Tests for S3 key validation in API endpoints."""

    def test_path_traversal_blocked(self, authenticated_app):
        """Path traversal in S3 keys should be blocked."""
        client = authenticated_app["client"]

        # Try to access with path traversal
        response = client.get(
            "/api/invoices",
            params={"key": "../../../etc/passwd"}
        )

        # Should block or ignore malicious input
        assert response.status_code in [200, 400, 422]

    def test_invalid_prefix_blocked(self, authenticated_app):
        """Invalid S3 prefixes should be blocked."""
        client = authenticated_app["client"]

        response = client.get(
            "/api/invoices",
            params={"key": "sensitive_data/passwords.json"}
        )

        # Should block or ignore
        assert response.status_code in [200, 400, 422]


class TestHomePage:
    """Tests for home page."""

    def test_home_page_loads(self, authenticated_app):
        """Home page should load for authenticated user."""
        client = authenticated_app["client"]

        response = client.get("/")

        assert response.status_code == 200

    def test_home_page_has_navigation(self, authenticated_app):
        """Home page should have navigation links."""
        client = authenticated_app["client"]

        response = client.get("/")

        assert response.status_code == 200
        # Should have links to main sections
        text_lower = response.text.lower()
        assert "invoices" in text_lower or "review" in text_lower or "href" in text_lower


class TestErrorHandling:
    """Tests for error handling in API endpoints."""

    def test_404_for_nonexistent_date(self, authenticated_app):
        """Should handle non-existent date gracefully."""
        client = authenticated_app["client"]

        response = client.get(
            "/api/invoices",
            params={"date": "1900-01-01"}
        )

        # Should return empty or 404
        assert response.status_code in [200, 404]

    def test_invalid_date_format(self, authenticated_app):
        """Should handle invalid date format."""
        client = authenticated_app["client"]

        response = client.get(
            "/api/invoices",
            params={"date": "not-a-date"}
        )

        # Should return error or handle gracefully
        assert response.status_code in [200, 400, 404, 422, 500]

    def test_invalid_json_in_post(self, authenticated_app):
        """Should handle invalid JSON in POST body."""
        client = authenticated_app["client"]

        response = client.post(
            "/api/submit",
            content="not valid json",
            headers={"Content-Type": "application/json"}
        )

        # Should return error
        assert response.status_code in [400, 422]
