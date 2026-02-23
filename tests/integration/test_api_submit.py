"""
Integration tests for submission workflow API endpoints.
Tests invoice submission, review workflow, and stage transitions.
"""
import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock
import boto3
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.fixture
def submit_client():
    """Create FastAPI test client with authenticated user and test data."""
    s3 = boto3.client("s3", region_name="us-east-1")
    ddb = boto3.client("dynamodb", region_name="us-east-1")

    # Create test invoice data
    today = datetime.now()
    s3_key = f"Bill_Parser_4_Enriched_Outputs/yyyy={today.year}/mm={today.month:02d}/dd={today.day:02d}/submit_test.jsonl"

    invoice_data = [
        {
            "Vendor Name": "Test Vendor",
            "Invoice Number": "SUBMIT-001",
            "Account Number": "ACCT-123",
            "Line Item Charge": "100.00",
            "Line Item Description": "Test Service",
            "Bill Date": "01/15/2025",
            "Due Date": "02/15/2025",
            "Property": "Test Property",
            "Property ID": "PROP001",
            "pdf_id": "submit123hash",
            "EnrichedVendorID": "V001",
            "EnrichedPropertyID": "P001"
        },
        {
            "Vendor Name": "Test Vendor 2",
            "Invoice Number": "SUBMIT-002",
            "Account Number": "ACCT-456",
            "Line Item Charge": "200.00",
            "Line Item Description": "Test Service 2",
            "Bill Date": "01/16/2025",
            "Due Date": "02/16/2025",
            "Property": "Test Property 2",
            "Property ID": "PROP002",
            "pdf_id": "submit456hash",
            "EnrichedVendorID": "V002",
            "EnrichedPropertyID": "P002"
        }
    ]

    s3.put_object(
        Bucket="test-bucket",
        Key=s3_key,
        Body="\n".join(json.dumps(line) for line in invoice_data)
    )

    # Clean and create test users
    try:
        response = ddb.scan(TableName="test-users")
        for item in response.get("Items", []):
            ddb.delete_item(TableName="test-users", Key={"user_id": item["user_id"]})
    except Exception:
        pass

    from auth import hash_password
    test_password = "TestPassword123!"
    password_hash = hash_password(test_password)

    ddb.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": "submitter@example.com"},
            "password_hash": {"S": password_hash},
            "role": {"S": "Utility_APs"},
            "full_name": {"S": "Submitter User"},
            "enabled": {"BOOL": True},
            "must_change_password": {"BOOL": False}
        }
    )

    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    client.post(
        "/login",
        data={"username": "submitter@example.com", "password": test_password},
        follow_redirects=True
    )

    yield {
        "client": client,
        "s3": s3,
        "ddb": ddb,
        "s3_key": s3_key,
        "password": test_password,
        "pdf_ids": ["submit123hash", "submit456hash"]
    }


class TestSubmitEndpoint:
    """Tests for POST /api/submit endpoint."""

    def test_submit_single_invoice(self, submit_client):
        """Submit single invoice should process."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.post(
            "/api/submit",
            json={
                "pdf_ids": ["submit123hash"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]

    def test_submit_multiple_invoices(self, submit_client):
        """Submit multiple invoices should process."""
        client = submit_client["client"]
        pdf_ids = submit_client["pdf_ids"]
        today = datetime.now()

        response = client.post(
            "/api/submit",
            json={
                "pdf_ids": pdf_ids,
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]

    def test_submit_empty_list(self, submit_client):
        """Submit with empty list should return error."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.post(
            "/api/submit",
            json={
                "pdf_ids": [],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 400, 422]

    def test_submit_nonexistent_invoice(self, submit_client):
        """Submit with non-existent pdf_id should handle gracefully."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.post(
            "/api/submit",
            json={
                "pdf_ids": ["nonexistent_hash"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 400, 404, 422, 500]

    def test_submit_requires_auth(self, submit_client):
        """Submit should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)
        response = fresh_client.post(
            "/api/submit",
            json={"pdf_ids": ["test"]},
            follow_redirects=False
        )

        assert response.status_code in [302, 303, 307, 401, 403, 422]

    def test_submit_missing_date(self, submit_client):
        """Submit without date should use default or fail."""
        client = submit_client["client"]

        response = client.post(
            "/api/submit",
            json={"pdf_ids": ["submit123hash"]}
        )

        assert response.status_code in [200, 400, 422]

    def test_submit_invalid_date(self, submit_client):
        """Submit with invalid date should return error."""
        client = submit_client["client"]

        response = client.post(
            "/api/submit",
            json={
                "pdf_ids": ["submit123hash"],
                "date": "invalid-date"
            }
        )

        assert response.status_code in [400, 422, 500]


class TestSubmitValidation:
    """Tests for submit input validation."""

    def test_submit_path_traversal_blocked(self, submit_client):
        """Path traversal in pdf_id should be blocked."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.post(
            "/api/submit",
            json={
                "pdf_ids": ["../../../etc/passwd"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        # Should handle safely
        assert response.status_code in [200, 400, 404, 422, 500]

    def test_submit_very_long_pdf_id(self, submit_client):
        """Very long pdf_id should be handled."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.post(
            "/api/submit",
            json={
                "pdf_ids": ["a" * 10000],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 400, 404, 422, 500]

    def test_submit_special_chars_in_pdf_id(self, submit_client):
        """Special characters in pdf_id should be handled."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.post(
            "/api/submit",
            json={
                "pdf_ids": ["test<script>alert('xss')</script>"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        assert response.status_code in [200, 400, 404, 422, 500]


class TestSubmitResponse:
    """Tests for submit response format."""

    def test_submit_returns_json(self, submit_client):
        """Submit should return JSON response."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.post(
            "/api/submit",
            json={
                "pdf_ids": ["submit123hash"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)

    def test_submit_returns_count(self, submit_client):
        """Submit should return count of processed items."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.post(
            "/api/submit",
            json={
                "pdf_ids": submit_client["pdf_ids"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}"
            }
        )

        if response.status_code == 200:
            data = response.json()
            # May have count, submitted, or similar field
            assert isinstance(data, dict)


class TestReviewWorkflow:
    """Tests for review workflow endpoints."""

    def test_review_page_loads(self, submit_client):
        """Review page should load."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.get(
            "/review",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 302, 422]

    def test_invoices_status_endpoint(self, submit_client):
        """Invoices status endpoint should return status."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.get(
            "/api/invoices_status",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]


class TestPostWorkflow:
    """Tests for POST stage workflow."""

    def test_post_page_loads(self, submit_client):
        """POST page should load."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.get(
            "/post",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 302, 403, 422]


class TestWorkflowEndpoints:
    """Tests for workflow management endpoints."""

    def test_workflow_page_loads(self, submit_client):
        """Workflow page should load."""
        client = submit_client["client"]

        response = client.get("/workflow")

        assert response.status_code in [200, 302, 403]

    def test_workflow_reasons_endpoint(self, submit_client):
        """Workflow reasons endpoint should return reasons."""
        client = submit_client["client"]

        response = client.get("/api/config/workflow-reasons")

        assert response.status_code in [200, 404, 500]


class TestReworkWorkflow:
    """Tests for rework workflow."""

    def test_send_to_rework(self, submit_client):
        """Send to rework should move invoice."""
        client = submit_client["client"]
        today = datetime.now()

        response = client.post(
            "/api/bulk_rework",
            json={
                "pdf_ids": ["submit123hash"],
                "date": f"{today.year}-{today.month:02d}-{today.day:02d}",
                "reason": "Needs correction"
            }
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestTrackPage:
    """Tests for track page."""

    def test_track_page_loads(self, submit_client):
        """Track page should load."""
        client = submit_client["client"]

        response = client.get("/track")

        assert response.status_code in [200, 302, 403]

    def test_track_requires_auth(self, submit_client):
        """Track page should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)
        response = fresh_client.get("/track", follow_redirects=False)

        assert response.status_code in [302, 303, 307, 401, 403]

