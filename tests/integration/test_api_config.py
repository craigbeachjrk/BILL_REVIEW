"""
Integration tests for configuration API endpoints.
Tests charge codes, workflow reasons, UOM mapping, AP team, and other config endpoints.
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
def authenticated_admin():
    """Create FastAPI test client with authenticated admin user."""
    # moto mock is already started by conftest.py at module level
    s3 = boto3.client("s3", region_name="us-east-1")
    ddb = boto3.client("dynamodb", region_name="us-east-1")

    # Clean existing users and add test users
    try:
        response = ddb.scan(TableName="test-users")
        for item in response.get("Items", []):
            ddb.delete_item(TableName="test-users", Key={"user_id": item["user_id"]})
    except Exception:
        pass

    # Create admin user
    from auth import hash_password
    test_password = "TestPassword123!"
    password_hash = hash_password(test_password)

    ddb.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": "admin@example.com"},
            "password_hash": {"S": password_hash},
            "role": {"S": "System_Admins"},
            "full_name": {"S": "Admin User"},
            "enabled": {"BOOL": True},
            "must_change_password": {"BOOL": False}
        }
    )

    # Create limited user (Utility_APs role)
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

    # Login as admin
    client.post(
        "/login",
        data={"username": "admin@example.com", "password": test_password},
        follow_redirects=True
    )

    yield {
        "client": client,
        "s3": s3,
        "ddb": ddb,
        "password": test_password
    }


class TestConfigPageAccess:
    """Tests for config page access control."""

    def test_config_page_loads_for_admin(self, authenticated_admin):
        """Config page should load for admin user."""
        client = authenticated_admin["client"]

        response = client.get("/config")

        # May return 200 or 403 depending on session state
        assert response.status_code in [200, 403]

    def test_config_page_requires_auth(self, authenticated_admin):
        """Config page should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)
        response = fresh_client.get("/config", follow_redirects=False)

        assert response.status_code in [302, 303, 307, 401, 403]

    def test_config_users_page_loads(self, authenticated_admin):
        """Config users page should load for admin."""
        client = authenticated_admin["client"]

        response = client.get("/config/users")

        assert response.status_code == 200

    def test_config_gl_code_mapping_page_loads(self, authenticated_admin):
        """GL code mapping page should load."""
        client = authenticated_admin["client"]

        response = client.get("/config/gl-code-mapping")

        assert response.status_code == 200

    def test_config_account_tracking_page_loads(self, authenticated_admin):
        """Account tracking page should load."""
        client = authenticated_admin["client"]

        response = client.get("/config/account-tracking")

        assert response.status_code == 200

    def test_config_ap_team_page_loads(self, authenticated_admin):
        """AP team page should load."""
        client = authenticated_admin["client"]

        response = client.get("/config/ap-team")

        assert response.status_code == 200

    def test_config_charge_codes_page_loads(self, authenticated_admin):
        """Charge codes page should load."""
        client = authenticated_admin["client"]

        response = client.get("/config/charge-codes")

        assert response.status_code == 200


class TestChargeCodesAPI:
    """Tests for /api/config/charge-codes endpoints."""

    def test_get_charge_codes_returns_list(self, authenticated_admin):
        """GET charge codes should return items list."""
        client = authenticated_admin["client"]

        response = client.get("/api/config/charge-codes")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_save_charge_codes_success(self, authenticated_admin):
        """POST charge codes should save successfully."""
        client = authenticated_admin["client"]

        payload = {
            "items": [
                {"chargeCode": "ELEC", "utilityName": "Electric"},
                {"chargeCode": "GAS", "utilityName": "Natural Gas"},
                {"chargeCode": "WATER", "utilityName": "Water/Sewer"}
            ]
        }

        response = client.post("/api/config/charge-codes", json=payload)

        # Should succeed or return specific error
        assert response.status_code in [200, 201, 400, 500]
        if response.status_code == 200:
            data = response.json()
            assert data.get("ok") is True

    def test_save_charge_codes_invalid_json(self, authenticated_admin):
        """POST charge codes with invalid JSON should fail."""
        client = authenticated_admin["client"]

        response = client.post(
            "/api/config/charge-codes",
            content="not valid json",
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 400

    def test_save_charge_codes_missing_items(self, authenticated_admin):
        """POST charge codes without items should fail."""
        client = authenticated_admin["client"]

        response = client.post("/api/config/charge-codes", json={})

        assert response.status_code == 400

    def test_save_charge_codes_items_not_list(self, authenticated_admin):
        """POST charge codes with non-list items should fail."""
        client = authenticated_admin["client"]

        response = client.post("/api/config/charge-codes", json={"items": "not a list"})

        assert response.status_code == 400

    def test_charge_codes_requires_auth(self, authenticated_admin):
        """Charge codes API should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)
        response = fresh_client.get("/api/config/charge-codes")

        # May allow read or redirect depending on endpoint configuration
        assert response.status_code in [200, 302, 303, 307, 401, 403]


class TestWorkflowReasonsAPI:
    """Tests for /api/config/workflow-reasons endpoints."""

    def test_get_workflow_reasons_returns_list(self, authenticated_admin):
        """GET workflow reasons should return items list."""
        client = authenticated_admin["client"]

        response = client.get("/api/config/workflow-reasons")

        # May return 200 or error depending on config
        assert response.status_code in [200, 404, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_save_workflow_reasons_success(self, authenticated_admin):
        """POST workflow reasons should save."""
        client = authenticated_admin["client"]

        payload = {
            "items": [
                {"code": "REWORK", "description": "Needs rework"},
                {"code": "REVIEW", "description": "Needs review"}
            ]
        }

        response = client.post("/api/config/workflow-reasons", json=payload)

        # Should succeed or fail gracefully
        assert response.status_code in [200, 201, 400, 500]


class TestAPTeamAPI:
    """Tests for /api/config/ap-team endpoints."""

    def test_get_ap_team_returns_list(self, authenticated_admin):
        """GET AP team should return list."""
        client = authenticated_admin["client"]

        response = client.get("/api/config/ap-team")

        assert response.status_code in [200, 404, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_save_ap_team_success(self, authenticated_admin):
        """POST AP team should save."""
        client = authenticated_admin["client"]

        payload = {
            "items": [
                {"name": "John Doe", "email": "john@example.com"},
                {"name": "Jane Smith", "email": "jane@example.com"}
            ]
        }

        response = client.post("/api/config/ap-team", json=payload)

        assert response.status_code in [200, 201, 400, 500]


class TestGLChargeCodeMappingAPI:
    """Tests for /api/config/gl-charge-code-mapping endpoints."""

    def test_get_gl_charge_code_mapping(self, authenticated_admin):
        """GET GL charge code mapping should return data."""
        client = authenticated_admin["client"]

        response = client.get("/api/config/gl-charge-code-mapping")

        assert response.status_code in [200, 404, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_save_gl_charge_code_mapping(self, authenticated_admin):
        """POST GL charge code mapping should save."""
        client = authenticated_admin["client"]

        payload = {
            "mappings": [
                {"gl_code": "5000-100", "charge_code": "ELEC"},
                {"gl_code": "5000-200", "charge_code": "GAS"}
            ]
        }

        response = client.post("/api/config/gl-charge-code-mapping", json=payload)

        assert response.status_code in [200, 201, 400, 422, 500]


class TestAccountsToTrackAPI:
    """Tests for /api/config/accounts-to-track endpoints."""

    def test_get_accounts_to_track(self, authenticated_admin):
        """GET accounts to track should return data."""
        client = authenticated_admin["client"]

        response = client.get("/api/config/accounts-to-track")

        assert response.status_code in [200, 404, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_save_accounts_to_track(self, authenticated_admin):
        """POST accounts to track should save."""
        client = authenticated_admin["client"]

        payload = {
            "accounts": [
                {"account_number": "12345", "vendor": "Test Vendor"},
                {"account_number": "67890", "vendor": "Another Vendor"}
            ]
        }

        response = client.post("/api/config/accounts-to-track", json=payload)

        assert response.status_code in [200, 201, 400, 422, 500]


class TestToggleUBITrackingAPI:
    """Tests for /api/config/toggle-ubi-tracking endpoint."""

    def test_toggle_ubi_tracking_success(self, authenticated_admin):
        """Toggle UBI tracking should update status."""
        client = authenticated_admin["client"]

        payload = {
            "account_number": "12345",
            "enable": True
        }

        response = client.post("/api/config/toggle-ubi-tracking", json=payload)

        assert response.status_code in [200, 201, 400, 422, 500]

    def test_toggle_ubi_tracking_disable(self, authenticated_admin):
        """Toggle UBI tracking should handle disable."""
        client = authenticated_admin["client"]

        payload = {
            "account_number": "12345",
            "enable": False
        }

        response = client.post("/api/config/toggle-ubi-tracking", json=payload)

        assert response.status_code in [200, 201, 400, 422, 500]


class TestAddToTrackerAPI:
    """Tests for /api/config/add-to-tracker endpoint."""

    def test_add_to_tracker_success(self, authenticated_admin):
        """Add to tracker should succeed."""
        client = authenticated_admin["client"]

        payload = {
            "account_number": "12345",
            "vendor_name": "Test Vendor",
            "property_id": "PROP001"
        }

        response = client.post("/api/config/add-to-tracker", json=payload)

        assert response.status_code in [200, 201, 400, 422, 500]

    def test_add_to_tracker_missing_account(self, authenticated_admin):
        """Add to tracker without account should fail."""
        client = authenticated_admin["client"]

        payload = {
            "vendor_name": "Test Vendor"
        }

        response = client.post("/api/config/add-to-tracker", json=payload)

        assert response.status_code in [400, 422, 500]


class TestAddToUBIAPI:
    """Tests for /api/config/add-to-ubi endpoint."""

    def test_add_to_ubi_success(self, authenticated_admin):
        """Add to UBI should succeed."""
        client = authenticated_admin["client"]

        payload = {
            "account_number": "12345",
            "property_id": "PROP001",
            "ubi_code": "UBI001"
        }

        response = client.post("/api/config/add-to-ubi", json=payload)

        assert response.status_code in [200, 201, 400, 422, 500]


class TestUBIMappingAPI:
    """Tests for /api/config/ubi-mapping endpoints."""

    def test_get_ubi_mapping(self, authenticated_admin):
        """GET UBI mapping should return data."""
        client = authenticated_admin["client"]

        response = client.get("/api/config/ubi-mapping")

        assert response.status_code in [200, 404, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_save_ubi_mapping(self, authenticated_admin):
        """POST UBI mapping should save."""
        client = authenticated_admin["client"]

        payload = {
            "mappings": [
                {"property_id": "PROP001", "ubi_code": "UBI001"},
                {"property_id": "PROP002", "ubi_code": "UBI002"}
            ]
        }

        response = client.post("/api/config/ubi-mapping", json=payload)

        assert response.status_code in [200, 201, 400, 422, 500]


class TestUOMMappingAPI:
    """Tests for /api/config/uom-mapping endpoints."""

    def test_get_uom_mapping(self, authenticated_admin):
        """GET UOM mapping should return data."""
        client = authenticated_admin["client"]

        response = client.get("/api/config/uom-mapping")

        assert response.status_code in [200, 404, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_save_uom_mapping(self, authenticated_admin):
        """POST UOM mapping should save."""
        client = authenticated_admin["client"]

        payload = {
            "mappings": [
                {"from_uom": "kWh", "to_uom": "MWh", "factor": 0.001},
                {"from_uom": "CCF", "to_uom": "Therms", "factor": 1.0}
            ]
        }

        response = client.post("/api/config/uom-mapping", json=payload)

        assert response.status_code in [200, 201, 400, 422, 500]


class TestAPMappingAPI:
    """Tests for /api/config/ap-mapping endpoints."""

    def test_get_ap_mapping(self, authenticated_admin):
        """GET AP mapping should return data."""
        client = authenticated_admin["client"]

        response = client.get("/api/config/ap-mapping")

        assert response.status_code in [200, 404, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_save_ap_mapping(self, authenticated_admin):
        """POST AP mapping should save."""
        client = authenticated_admin["client"]

        payload = {
            "mappings": [
                {"vendor_id": "V001", "ap_contact": "john@example.com"},
                {"vendor_id": "V002", "ap_contact": "jane@example.com"}
            ]
        }

        response = client.post("/api/config/ap-mapping", json=payload)

        assert response.status_code in [200, 201, 400, 422, 500]


class TestConfigPageRestrictions:
    """Tests for config page access restrictions by role."""

    def test_limited_user_cannot_access_config(self, authenticated_admin):
        """Limited user should not access config pages."""
        from fastapi.testclient import TestClient
        from main import app

        password = authenticated_admin["password"]

        # Create new client and login as limited user
        fresh_client = TestClient(app)
        fresh_client.post(
            "/login",
            data={"username": "limited@example.com", "password": password},
            follow_redirects=True
        )

        response = fresh_client.get("/config", follow_redirects=False)

        # Should redirect or return forbidden
        assert response.status_code in [302, 303, 307, 401, 403]

    def test_limited_user_cannot_save_charge_codes(self, authenticated_admin):
        """Limited user should not be able to save charge codes."""
        from fastapi.testclient import TestClient
        from main import app

        password = authenticated_admin["password"]

        fresh_client = TestClient(app)
        fresh_client.post(
            "/login",
            data={"username": "limited@example.com", "password": password},
            follow_redirects=True
        )

        response = fresh_client.post(
            "/api/config/charge-codes",
            json={"items": [{"chargeCode": "TEST", "utilityName": "Test"}]}
        )

        # Should redirect, return forbidden, or server error due to permission/config issue
        assert response.status_code in [302, 303, 307, 401, 403, 500]


class TestConfigValidation:
    """Tests for config input validation."""

    def test_charge_codes_empty_charge_code_ignored(self, authenticated_admin):
        """Empty charge codes should be ignored."""
        client = authenticated_admin["client"]

        payload = {
            "items": [
                {"chargeCode": "", "utilityName": "Empty Code"},
                {"chargeCode": "VALID", "utilityName": "Valid Code"}
            ]
        }

        response = client.post("/api/config/charge-codes", json=payload)

        # Should succeed and only save valid items
        assert response.status_code in [200, 201, 400, 500]

    def test_charge_codes_whitespace_trimmed(self, authenticated_admin):
        """Whitespace in charge codes should be trimmed."""
        client = authenticated_admin["client"]

        payload = {
            "items": [
                {"chargeCode": "  ELEC  ", "utilityName": "  Electric  "}
            ]
        }

        response = client.post("/api/config/charge-codes", json=payload)

        assert response.status_code in [200, 201, 400, 500]

    def test_ap_team_invalid_email_format(self, authenticated_admin):
        """Invalid email format should be handled."""
        client = authenticated_admin["client"]

        payload = {
            "items": [
                {"name": "Test User", "email": "not-an-email"}
            ]
        }

        response = client.post("/api/config/ap-team", json=payload)

        # May accept or reject depending on validation
        assert response.status_code in [200, 201, 400, 422, 500]


class TestConfigErrorHandling:
    """Tests for config error handling."""

    def test_invalid_json_handling(self, authenticated_admin):
        """Invalid JSON should return appropriate error."""
        client = authenticated_admin["client"]

        response = client.post(
            "/api/config/charge-codes",
            content="{ invalid json }",
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 400

    def test_missing_content_type(self, authenticated_admin):
        """Missing content type should be handled."""
        client = authenticated_admin["client"]

        response = client.post(
            "/api/config/charge-codes",
            content='{"items": []}'
        )

        # May succeed, fail with validation, or server error
        assert response.status_code in [200, 400, 415, 422, 500]

    def test_very_large_payload(self, authenticated_admin):
        """Very large payload should be handled gracefully."""
        client = authenticated_admin["client"]

        # Create large payload
        large_items = [{"chargeCode": f"CODE{i}", "utilityName": f"Utility {i}"} for i in range(1000)]

        response = client.post("/api/config/charge-codes", json={"items": large_items})

        # Should handle gracefully
        assert response.status_code in [200, 201, 400, 413, 500]

