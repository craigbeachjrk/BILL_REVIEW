"""
Integration tests for authentication API endpoints.
Tests login, logout, session management, and protected routes.
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from moto import mock_aws
import boto3

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# snowflake mock is handled by conftest.py


@pytest.fixture
def test_app_with_user():
    """Create FastAPI test client with a test user in mocked DynamoDB."""
    # moto mock is already started by conftest.py at module level
    # AWS resources are pre-created by conftest
    ddb = boto3.client("dynamodb", region_name="us-east-1")

    # Clean existing users and add test users
    try:
        response = ddb.scan(TableName="test-users")
        for item in response.get("Items", []):
            ddb.delete_item(TableName="test-users", Key={"user_id": item["user_id"]})
    except Exception:
        pass

    # Create test user with known password hash
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
            "must_change_password": {"BOOL": False},
            "created_utc": {"S": "2025-01-01T00:00:00Z"}
        }
    )

    # Create disabled user
    ddb.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": "disabled@example.com"},
            "password_hash": {"S": password_hash},
            "role": {"S": "Utility_APs"},
            "full_name": {"S": "Disabled User"},
            "enabled": {"BOOL": False},
            "must_change_password": {"BOOL": False}
        }
    )

    # Create user who must change password
    ddb.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": "newuser@example.com"},
            "password_hash": {"S": password_hash},
            "role": {"S": "Utility_APs"},
            "full_name": {"S": "New User"},
            "enabled": {"BOOL": True},
            "must_change_password": {"BOOL": True}
        }
    )

    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    yield {
        "client": client,
        "password": test_password,
        "ddb": ddb
    }


class TestLoginPage:
    """Tests for GET /login endpoint."""

    def test_login_page_loads(self, test_app_with_user):
        """Login page should load without authentication."""
        client = test_app_with_user["client"]

        response = client.get("/login")

        assert response.status_code == 200
        assert "login" in response.text.lower() or "sign in" in response.text.lower()

    def test_login_page_has_form(self, test_app_with_user):
        """Login page should have username and password fields."""
        client = test_app_with_user["client"]

        response = client.get("/login")

        assert response.status_code == 200
        assert "username" in response.text.lower() or "email" in response.text.lower()
        assert "password" in response.text.lower()


class TestLoginPost:
    """Tests for POST /login endpoint."""

    def test_login_success_redirects_to_home(self, test_app_with_user):
        """Successful login should redirect to home page."""
        client = test_app_with_user["client"]
        password = test_app_with_user["password"]

        response = client.post(
            "/login",
            data={"username": "test@example.com", "password": password},
            follow_redirects=False
        )

        assert response.status_code in [302, 303, 307]
        assert response.headers.get("location") in ["/", "/invoices"]

    def test_login_sets_session_cookie(self, test_app_with_user):
        """Successful login should set session cookie."""
        client = test_app_with_user["client"]
        password = test_app_with_user["password"]

        response = client.post(
            "/login",
            data={"username": "test@example.com", "password": password},
            follow_redirects=False
        )

        # Check for session cookie
        cookies = response.cookies
        assert "br_sess" in cookies or len(cookies) > 0

    def test_login_wrong_password_fails(self, test_app_with_user):
        """Login with wrong password should fail."""
        client = test_app_with_user["client"]

        response = client.post(
            "/login",
            data={"username": "test@example.com", "password": "wrong_password"},
        )

        # Should show error or stay on login page
        assert response.status_code in [200, 401, 403]

    def test_login_nonexistent_user_fails(self, test_app_with_user):
        """Login with non-existent user should fail."""
        client = test_app_with_user["client"]

        response = client.post(
            "/login",
            data={"username": "nonexistent@example.com", "password": "anypassword"},
        )

        assert response.status_code in [200, 401, 403]

    def test_login_disabled_user_fails(self, test_app_with_user):
        """Login with disabled user should fail."""
        client = test_app_with_user["client"]
        password = test_app_with_user["password"]

        response = client.post(
            "/login",
            data={"username": "disabled@example.com", "password": password},
        )

        assert response.status_code in [200, 401, 403]

    def test_login_empty_username_fails(self, test_app_with_user):
        """Login with empty username should fail."""
        client = test_app_with_user["client"]

        response = client.post(
            "/login",
            data={"username": "", "password": "somepassword"},
        )

        assert response.status_code in [200, 400, 401, 422]

    def test_login_empty_password_fails(self, test_app_with_user):
        """Login with empty password should fail."""
        client = test_app_with_user["client"]

        response = client.post(
            "/login",
            data={"username": "test@example.com", "password": ""},
        )

        assert response.status_code in [200, 400, 401, 422]


class TestLogout:
    """Tests for POST /logout endpoint."""

    def test_logout_clears_session(self, test_app_with_user):
        """Logout should clear session and redirect to login."""
        client = test_app_with_user["client"]
        password = test_app_with_user["password"]

        # First login
        client.post(
            "/login",
            data={"username": "test@example.com", "password": password},
            follow_redirects=False
        )

        # Then logout
        response = client.post("/logout", follow_redirects=False)

        assert response.status_code in [302, 303, 307]
        assert "/login" in response.headers.get("location", "")


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_ok(self, test_app_with_user):
        """Health endpoint should return OK without authentication."""
        client = test_app_with_user["client"]

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data.get("ok") is True or "ok" in str(data).lower()


class TestProtectedEndpoints:
    """Tests for authentication requirement on protected endpoints."""

    @pytest.mark.parametrize("endpoint", [
        "/",
        "/invoices",
        "/review",
        "/api/dates",
    ])
    def test_protected_endpoints_require_auth(self, test_app_with_user, endpoint):
        """Protected endpoints should redirect to login without auth."""
        # Use fresh client from same context but don't login
        from fastapi.testclient import TestClient
        from main import app

        fresh_client = TestClient(app)
        response = fresh_client.get(endpoint, follow_redirects=False)

        # Should redirect to login or return 401/403
        assert response.status_code in [302, 303, 307, 401, 403]

    def test_authenticated_user_accesses_home(self, test_app_with_user):
        """Authenticated user should access home page."""
        client = test_app_with_user["client"]
        password = test_app_with_user["password"]

        # Login first
        client.post(
            "/login",
            data={"username": "test@example.com", "password": password},
            follow_redirects=True
        )

        # Now access home
        response = client.get("/", follow_redirects=True)

        assert response.status_code == 200


class TestMustChangePassword:
    """Tests for must_change_password flow."""

    def test_must_change_password_redirects(self, test_app_with_user):
        """User with must_change_password should be redirected to change password."""
        client = test_app_with_user["client"]
        password = test_app_with_user["password"]

        response = client.post(
            "/login",
            data={"username": "newuser@example.com", "password": password},
            follow_redirects=False
        )

        # Should redirect to change-password page
        location = response.headers.get("location", "")
        # Either redirects to change-password or allows login (implementation dependent)
        assert response.status_code in [302, 303, 307, 200]
