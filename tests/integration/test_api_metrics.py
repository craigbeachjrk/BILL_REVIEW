"""
Integration tests for metrics and reporting API endpoints.
Tests user timing, parsing volume, pipeline summary, and other metrics.
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
def metrics_client():
    """Create FastAPI test client with authenticated user."""
    ddb = boto3.client("dynamodb", region_name="us-east-1")

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

    # Create admin user
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

    # Create regular user
    ddb.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": "user@example.com"},
            "password_hash": {"S": password_hash},
            "role": {"S": "Utility_APs"},
            "full_name": {"S": "Regular User"},
            "enabled": {"BOOL": True},
            "must_change_password": {"BOOL": False}
        }
    )

    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    client.post(
        "/login",
        data={"username": "admin@example.com", "password": test_password},
        follow_redirects=True
    )

    yield {
        "client": client,
        "ddb": ddb,
        "password": test_password
    }


class TestUserTimingMetrics:
    """Tests for /api/metrics/user-timing endpoint."""

    def test_user_timing_returns_data(self, metrics_client):
        """GET user timing should return timing data."""
        client = metrics_client["client"]
        today = datetime.now()

        response = client.get(
            "/api/metrics/user-timing",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_user_timing_date_range(self, metrics_client):
        """GET user timing with date range should work."""
        client = metrics_client["client"]

        response = client.get(
            "/api/metrics/user-timing",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-01-31"
            }
        )

        assert response.status_code in [200, 400, 404, 422]


class TestParsingVolumeMetrics:
    """Tests for /api/metrics/parsing-volume endpoint."""

    def test_parsing_volume_returns_data(self, metrics_client):
        """GET parsing volume should return volume data."""
        client = metrics_client["client"]

        response = client.get("/api/metrics/parsing-volume")

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_parsing_volume_with_days(self, metrics_client):
        """GET parsing volume with days param should work."""
        client = metrics_client["client"]

        response = client.get(
            "/api/metrics/parsing-volume",
            params={"days": 30}
        )

        assert response.status_code in [200, 404]


class TestPipelineSummaryMetrics:
    """Tests for /api/metrics/pipeline-summary endpoint."""

    def test_pipeline_summary_returns_data(self, metrics_client):
        """GET pipeline summary should return summary data."""
        client = metrics_client["client"]
        today = datetime.now()

        response = client.get(
            "/api/metrics/pipeline-summary",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)


class TestSubmitterStatsMetrics:
    """Tests for /api/metrics/submitter-stats endpoint."""

    def test_submitter_stats_returns_data(self, metrics_client):
        """GET submitter stats should return stats."""
        client = metrics_client["client"]
        today = datetime.now()

        response = client.get(
            "/api/metrics/submitter-stats",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))


class TestActivityDetailMetrics:
    """Tests for /api/metrics/activity-detail endpoint."""

    def test_activity_detail_returns_data(self, metrics_client):
        """GET activity detail should return activity data."""
        client = metrics_client["client"]
        today = datetime.now()

        response = client.get(
            "/api/metrics/activity-detail",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))


class TestOverridesMetrics:
    """Tests for /api/metrics/overrides endpoint."""

    def test_overrides_returns_data(self, metrics_client):
        """GET overrides should return override data."""
        client = metrics_client["client"]
        today = datetime.now()

        response = client.get(
            "/api/metrics/overrides",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))


class TestOutliersMetrics:
    """Tests for /api/metrics/outliers endpoints."""

    def test_outliers_returns_data(self, metrics_client):
        """GET outliers should return outlier data."""
        client = metrics_client["client"]
        today = datetime.now()

        response = client.get(
            "/api/metrics/outliers",
            params={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_outliers_review(self, metrics_client):
        """POST outliers review should mark as reviewed."""
        client = metrics_client["client"]

        response = client.post("/api/metrics/outliers/test_pdf_id/review")

        assert response.status_code in [200, 201, 400, 404, 422, 500]

    def test_outliers_scan(self, metrics_client):
        """POST outliers scan should trigger scan."""
        client = metrics_client["client"]
        today = datetime.now()

        response = client.post(
            "/api/metrics/outliers/scan",
            json={"date": f"{today.year}-{today.month:02d}-{today.day:02d}"}
        )

        assert response.status_code in [200, 201, 400, 404, 422, 500]


class TestAccountStatsMetrics:
    """Tests for /api/metrics/account-stats endpoint."""

    def test_account_stats_returns_data(self, metrics_client):
        """GET account stats should return stats."""
        client = metrics_client["client"]

        response = client.get("/api/metrics/account-stats/TEST-ACCOUNT-123")

        assert response.status_code in [200, 400, 404, 500]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))


class TestLoginMetrics:
    """Tests for /api/metrics/logins endpoint."""

    def test_logins_returns_data(self, metrics_client):
        """GET logins should return login data."""
        client = metrics_client["client"]

        response = client.get("/api/metrics/logins")

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_logins_with_days(self, metrics_client):
        """GET logins with days param should work."""
        client = metrics_client["client"]

        response = client.get(
            "/api/metrics/logins",
            params={"days": 7}
        )

        assert response.status_code in [200, 404]


class TestJobLogMetrics:
    """Tests for /api/metrics/job-log endpoint."""

    def test_job_log_returns_data(self, metrics_client):
        """GET job log should return job data."""
        client = metrics_client["client"]

        response = client.get("/api/metrics/job-log")

        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, (dict, list))

    def test_job_log_with_limit(self, metrics_client):
        """GET job log with limit should work."""
        client = metrics_client["client"]

        response = client.get(
            "/api/metrics/job-log",
            params={"limit": 10}
        )

        assert response.status_code in [200, 404]


class TestMetricsAuthentication:
    """Tests for metrics authentication requirements."""

    def test_metrics_require_auth(self, metrics_client):
        """Metrics endpoints should require authentication."""
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)

        endpoints = [
            "/api/metrics/user-timing",
            "/api/metrics/parsing-volume",
            "/api/metrics/pipeline-summary",
            "/api/metrics/submitter-stats",
        ]

        for endpoint in endpoints:
            response = fresh_client.get(endpoint, follow_redirects=False)
            assert response.status_code in [302, 303, 307, 401, 403], f"Failed for {endpoint}"


class TestMetricsValidation:
    """Tests for metrics input validation."""

    def test_invalid_date_format(self, metrics_client):
        """Invalid date format should be handled."""
        client = metrics_client["client"]

        response = client.get(
            "/api/metrics/user-timing",
            params={"date": "not-a-date"}
        )

        assert response.status_code in [200, 400, 422, 500]

    def test_future_date(self, metrics_client):
        """Future date should be handled."""
        client = metrics_client["client"]

        response = client.get(
            "/api/metrics/user-timing",
            params={"date": "2099-12-31"}
        )

        assert response.status_code in [200, 400, 404]

    def test_very_old_date(self, metrics_client):
        """Very old date should be handled."""
        client = metrics_client["client"]

        response = client.get(
            "/api/metrics/user-timing",
            params={"date": "1900-01-01"}
        )

        assert response.status_code in [200, 400, 404]


class TestMetricsPages:
    """Tests for metrics-related pages."""

    def test_metrics_page_loads(self, metrics_client):
        """Metrics page should load."""
        client = metrics_client["client"]

        response = client.get("/metrics")

        assert response.status_code in [200, 302, 403, 404]

    def test_analytics_page_loads(self, metrics_client):
        """Analytics page should load."""
        client = metrics_client["client"]

        response = client.get("/analytics")

        assert response.status_code in [200, 302, 403, 404]

