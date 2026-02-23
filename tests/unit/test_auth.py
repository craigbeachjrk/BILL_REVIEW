"""
Unit tests for auth.py authentication module.
Tests password hashing, verification, permissions, and user management.
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from moto import mock_aws
import boto3

# Set up environment before importing auth
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["USERS_TABLE"] = "test-users"

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Import auth module functions that don't need DynamoDB
from auth import hash_password, verify_password, has_permission, can_access_page, ROLES


class TestPasswordHashing:
    """Tests for password hashing functions."""

    def test_hash_password_returns_string(self):
        """hash_password should return a bcrypt hash string."""
        result = hash_password("test_password")

        assert isinstance(result, str)
        assert result.startswith("$2b$")  # bcrypt prefix

    def test_hash_password_creates_unique_hashes(self):
        """Same password should produce different hashes due to unique salts."""
        hash1 = hash_password("same_password")
        hash2 = hash_password("same_password")

        assert hash1 != hash2  # Different salts

    def test_hash_password_handles_unicode(self):
        """hash_password should handle unicode characters."""
        result = hash_password("pässwörd123!")

        assert isinstance(result, str)
        assert result.startswith("$2b$")

    def test_hash_password_handles_empty_string(self):
        """hash_password should handle empty string."""
        result = hash_password("")

        assert isinstance(result, str)
        assert result.startswith("$2b$")


class TestPasswordVerification:
    """Tests for password verification function."""

    def test_verify_password_correct(self):
        """verify_password should return True for correct password."""
        password = "my_secret_password"
        hashed = hash_password(password)

        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """verify_password should return False for incorrect password."""
        hashed = hash_password("correct_password")

        assert verify_password("wrong_password", hashed) is False

    def test_verify_password_case_sensitive(self):
        """Password verification should be case-sensitive."""
        hashed = hash_password("Password123")

        assert verify_password("password123", hashed) is False
        assert verify_password("PASSWORD123", hashed) is False
        assert verify_password("Password123", hashed) is True

    def test_verify_password_invalid_hash_returns_false(self):
        """verify_password should return False for invalid hash."""
        assert verify_password("password", "not_a_valid_hash") is False
        assert verify_password("password", "") is False
        assert verify_password("password", "abc123") is False

    def test_verify_password_unicode(self):
        """verify_password should handle unicode passwords."""
        password = "contraseña123!"
        hashed = hash_password(password)

        assert verify_password(password, hashed) is True
        assert verify_password("contrasena123!", hashed) is False


class TestRoleDefinitions:
    """Tests for role definitions and structure."""

    def test_roles_dict_exists(self):
        """ROLES dictionary should be defined."""
        assert isinstance(ROLES, dict)
        assert len(ROLES) > 0

    def test_system_admins_role_exists(self):
        """System_Admins role should be defined."""
        assert "System_Admins" in ROLES
        assert ROLES["System_Admins"]["permissions"] == ["*"]
        assert "all" in ROLES["System_Admins"]["pages"]

    def test_utility_aps_role_exists(self):
        """Utility_APs role should be defined."""
        assert "Utility_APs" in ROLES
        assert "bills:read" in ROLES["Utility_APs"]["permissions"]
        assert "bills:submit" in ROLES["Utility_APs"]["permissions"]

    def test_ubi_admins_role_exists(self):
        """UBI_Admins role should be defined."""
        assert "UBI_Admins" in ROLES
        assert "ubi:read" in ROLES["UBI_Admins"]["permissions"]
        assert "ubi:write" in ROLES["UBI_Admins"]["permissions"]

    def test_all_roles_have_required_fields(self):
        """All roles should have name, permissions, and pages."""
        for role_name, role_data in ROLES.items():
            assert "name" in role_data, f"Role {role_name} missing 'name'"
            assert "permissions" in role_data, f"Role {role_name} missing 'permissions'"
            assert "pages" in role_data, f"Role {role_name} missing 'pages'"
            assert isinstance(role_data["permissions"], list)
            assert isinstance(role_data["pages"], list)


class TestHasPermission:
    """Tests for permission checking function."""

    def test_system_admin_has_all_permissions(self):
        """System_Admins should have all permissions via wildcard."""
        assert has_permission("System_Admins", "bills:read") is True
        assert has_permission("System_Admins", "config:write") is True
        assert has_permission("System_Admins", "any:random:permission") is True
        assert has_permission("System_Admins", "completely:made:up") is True

    def test_utility_ap_has_limited_permissions(self):
        """Utility_APs should have only their defined permissions."""
        # Should have these
        assert has_permission("Utility_APs", "bills:read") is True
        assert has_permission("Utility_APs", "bills:submit") is True
        assert has_permission("Utility_APs", "invoices:read") is True

        # Should NOT have these
        assert has_permission("Utility_APs", "config:write") is False
        assert has_permission("Utility_APs", "ubi:write") is False

    def test_ubi_admin_permissions(self):
        """UBI_Admins should have UBI-related permissions."""
        assert has_permission("UBI_Admins", "ubi:read") is True
        assert has_permission("UBI_Admins", "ubi:write") is True
        assert has_permission("UBI_Admins", "bills:read") is True
        assert has_permission("UBI_Admins", "bills:review") is True

    def test_invalid_role_has_no_permissions(self):
        """Invalid/unknown role should have no permissions."""
        assert has_permission("Invalid_Role", "bills:read") is False
        assert has_permission("", "bills:read") is False
        assert has_permission("NonExistent", "any:permission") is False

    def test_wildcard_permission_matching(self):
        """Wildcard permissions (ending in :*) should match sub-permissions."""
        # UBI_Admins has "config:read" permission
        assert has_permission("UBI_Admins", "config:read") is True


class TestCanAccessPage:
    """Tests for page access control function."""

    def test_system_admin_accesses_all_pages(self):
        """System_Admins should access all pages."""
        assert can_access_page("System_Admins", "/") is True
        assert can_access_page("System_Admins", "/config") is True
        assert can_access_page("System_Admins", "/any-page") is True
        assert can_access_page("System_Admins", "/admin/secret") is True

    def test_utility_ap_restricted_pages(self):
        """Utility_APs should have restricted page access."""
        # Should have access
        assert can_access_page("Utility_APs", "/") is True
        assert can_access_page("Utility_APs", "/review") is True
        assert can_access_page("Utility_APs", "/invoices") is True
        assert can_access_page("Utility_APs", "/track") is True

        # Should NOT have access
        assert can_access_page("Utility_APs", "/config") is False

    def test_ubi_admin_pages(self):
        """UBI_Admins should access UBI-related pages."""
        assert can_access_page("UBI_Admins", "/") is True
        assert can_access_page("UBI_Admins", "/ubi") is True
        assert can_access_page("UBI_Admins", "/config") is True
        assert can_access_page("UBI_Admins", "/track") is True

    def test_invalid_role_no_page_access(self):
        """Invalid role should have no page access."""
        assert can_access_page("Invalid_Role", "/") is False
        assert can_access_page("", "/") is False


class TestUserCRUDWithMocks:
    """Tests for user CRUD operations with mocked DynamoDB."""

    @pytest.fixture
    def mock_ddb(self):
        """Get DynamoDB client from moto mock (started by conftest)."""
        # moto mock is already started by conftest.py at module level
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        # Table is already created by conftest, just clean it for this test
        # Delete any existing items in test-users table
        try:
            response = ddb.scan(TableName="test-users")
            for item in response.get("Items", []):
                ddb.delete_item(
                    TableName="test-users",
                    Key={"user_id": item["user_id"]}
                )
        except Exception:
            pass
        yield ddb

    def test_get_user_not_found(self, mock_ddb):
        """get_user should return None for non-existent user."""
        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import get_user

            result = get_user("nonexistent@example.com")
            assert result is None

    def test_get_user_found(self, mock_ddb):
        """get_user should return user dict for existing user."""
        # Create user directly
        mock_ddb.put_item(
            TableName="test-users",
            Item={
                "user_id": {"S": "test@example.com"},
                "password_hash": {"S": "$2b$12$test"},
                "role": {"S": "Utility_APs"},
                "full_name": {"S": "Test User"},
                "enabled": {"BOOL": True},
                "must_change_password": {"BOOL": False}
            }
        )

        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import get_user

            result = get_user("test@example.com")

            assert result is not None
            assert result["user_id"] == "test@example.com"
            assert result["role"] == "Utility_APs"
            assert result["enabled"] is True

    def test_create_user_success(self, mock_ddb):
        """create_user should create user and return True."""
        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import create_user, get_user

            result = create_user(
                user_id="new@example.com",
                password="SecurePass123",
                role="Utility_APs",
                full_name="New User"
            )

            assert result is True

            # Verify user was created
            user = get_user("new@example.com")
            assert user is not None
            assert user["role"] == "Utility_APs"
            assert user["must_change_password"] is True

    def test_create_user_invalid_role(self, mock_ddb):
        """create_user should fail for invalid role."""
        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import create_user

            result = create_user(
                user_id="test@example.com",
                password="password",
                role="Invalid_Role",
                full_name="Test"
            )

            assert result is False

    def test_create_user_duplicate(self, mock_ddb):
        """create_user should fail for duplicate user_id."""
        mock_ddb.put_item(
            TableName="test-users",
            Item={
                "user_id": {"S": "existing@example.com"},
                "password_hash": {"S": "$2b$12$test"},
                "role": {"S": "Utility_APs"},
                "full_name": {"S": "Existing User"},
                "enabled": {"BOOL": True}
            }
        )

        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import create_user

            result = create_user(
                user_id="existing@example.com",
                password="password",
                role="Utility_APs",
                full_name="Duplicate"
            )

            assert result is False

    def test_authenticate_success(self, mock_ddb):
        """authenticate should return user dict for valid credentials."""
        from auth import hash_password

        password = "correct_password"
        hashed = hash_password(password)

        mock_ddb.put_item(
            TableName="test-users",
            Item={
                "user_id": {"S": "auth@example.com"},
                "password_hash": {"S": hashed},
                "role": {"S": "Utility_APs"},
                "full_name": {"S": "Auth User"},
                "enabled": {"BOOL": True}
            }
        )

        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import authenticate

            result = authenticate("auth@example.com", password)

            assert result is not None
            assert result["user_id"] == "auth@example.com"

    def test_authenticate_wrong_password(self, mock_ddb):
        """authenticate should return None for wrong password."""
        from auth import hash_password

        hashed = hash_password("correct_password")

        mock_ddb.put_item(
            TableName="test-users",
            Item={
                "user_id": {"S": "auth@example.com"},
                "password_hash": {"S": hashed},
                "role": {"S": "Utility_APs"},
                "full_name": {"S": "Auth User"},
                "enabled": {"BOOL": True}
            }
        )

        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import authenticate

            result = authenticate("auth@example.com", "wrong_password")

            assert result is None

    def test_authenticate_disabled_user(self, mock_ddb):
        """authenticate should return None for disabled user."""
        from auth import hash_password

        hashed = hash_password("password")

        mock_ddb.put_item(
            TableName="test-users",
            Item={
                "user_id": {"S": "disabled@example.com"},
                "password_hash": {"S": hashed},
                "role": {"S": "Utility_APs"},
                "full_name": {"S": "Disabled User"},
                "enabled": {"BOOL": False}  # Disabled
            }
        )

        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import authenticate

            result = authenticate("disabled@example.com", "password")

            assert result is None

    def test_disable_user(self, mock_ddb):
        """disable_user should set enabled=False."""
        mock_ddb.put_item(
            TableName="test-users",
            Item={
                "user_id": {"S": "todisable@example.com"},
                "password_hash": {"S": "$2b$12$test"},
                "role": {"S": "Utility_APs"},
                "full_name": {"S": "To Disable"},
                "enabled": {"BOOL": True}
            }
        )

        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import disable_user, get_user

            result = disable_user("todisable@example.com")

            assert result is True

            user = get_user("todisable@example.com")
            assert user["enabled"] is False

    def test_enable_user(self, mock_ddb):
        """enable_user should set enabled=True."""
        mock_ddb.put_item(
            TableName="test-users",
            Item={
                "user_id": {"S": "toenable@example.com"},
                "password_hash": {"S": "$2b$12$test"},
                "role": {"S": "Utility_APs"},
                "full_name": {"S": "To Enable"},
                "enabled": {"BOOL": False}
            }
        )

        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import enable_user, get_user

            result = enable_user("toenable@example.com")

            assert result is True

            user = get_user("toenable@example.com")
            assert user["enabled"] is True

    def test_update_password(self, mock_ddb):
        """update_password should update the password hash."""
        from auth import hash_password

        old_hash = hash_password("old_password")

        mock_ddb.put_item(
            TableName="test-users",
            Item={
                "user_id": {"S": "updatepw@example.com"},
                "password_hash": {"S": old_hash},
                "role": {"S": "Utility_APs"},
                "full_name": {"S": "Update PW User"},
                "enabled": {"BOOL": True},
                "must_change_password": {"BOOL": True}
            }
        )

        with patch("auth.ddb", mock_ddb), patch("auth.USERS_TABLE", "test-users"):
            from auth import update_password, get_user, verify_password

            result = update_password("updatepw@example.com", "new_password")

            assert result is True

            user = get_user("updatepw@example.com")
            assert user["must_change_password"] is False
            assert verify_password("new_password", user["password_hash"]) is True
