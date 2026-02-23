"""
Authentication and Authorization Module for Bill Review App
Handles user management, password hashing, and role-based access control
"""
import os
import bcrypt
import datetime as dt
from typing import Optional, Dict, List
import boto3

# Initialize DynamoDB client
ddb = boto3.client("dynamodb", region_name=os.getenv("AWS_REGION", "us-east-1"))
USERS_TABLE = os.getenv("USERS_TABLE", "jrk-bill-review-users")

# Role definitions with permissions
ROLES = {
    "System_Admins": {
        "name": "System Administrator",
        "permissions": ["*"],  # Full access
        "pages": ["all"]
    },
    "UBI_Admins": {
        "name": "UBI Administrator",
        "permissions": [
            "ubi:read", "ubi:write", "ubi:config",
            "bills:read", "bills:review", "bills:approve",
            "config:read", "config:write:ubi",
            "reports:generate"
        ],
        "pages": ["/", "/ubi", "/ubi_mapping", "/uom_mapping", "/review", "/config", "/track", "/debug"]
    },
    "Utility_APs": {
        "name": "Utility AP Specialist",
        "permissions": [
            "bills:read", "bills:submit",
            "invoices:read", "invoices:process",
            "reports:read"
        ],
        "pages": ["/", "/review", "/invoices", "/track"]
    }
}


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception as e:
        print(f"[AUTH] Password verification error: {e}")
        return False


def get_user(user_id: str) -> Optional[Dict]:
    """Get user by user_id (email)."""
    try:
        response = ddb.get_item(
            TableName=USERS_TABLE,
            Key={"user_id": {"S": user_id}}
        )
        if "Item" not in response:
            return None

        item = response["Item"]
        return {
            "user_id": item.get("user_id", {}).get("S", ""),
            "password_hash": item.get("password_hash", {}).get("S", ""),
            "role": item.get("role", {}).get("S", ""),
            "full_name": item.get("full_name", {}).get("S", ""),
            "enabled": item.get("enabled", {}).get("BOOL", False),
            "must_change_password": item.get("must_change_password", {}).get("BOOL", False),
            "created_utc": item.get("created_utc", {}).get("S", ""),
            "last_login_utc": item.get("last_login_utc", {}).get("S", "")
        }
    except Exception as e:
        print(f"[AUTH] Error getting user {user_id}: {e}")
        return None


def create_user(user_id: str, password: str, role: str, full_name: str, created_by: str = "system") -> bool:
    """Create a new user."""
    if role not in ROLES:
        print(f"[AUTH] Invalid role: {role}")
        return False

    password_hash = hash_password(password)
    now_utc = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        ddb.put_item(
            TableName=USERS_TABLE,
            Item={
                "user_id": {"S": user_id},
                "password_hash": {"S": password_hash},
                "role": {"S": role},
                "full_name": {"S": full_name},
                "enabled": {"BOOL": True},
                "must_change_password": {"BOOL": True},
                "created_utc": {"S": now_utc},
                "created_by": {"S": created_by}
            },
            ConditionExpression="attribute_not_exists(user_id)"  # Prevent duplicates
        )
        print(f"[AUTH] User created: {user_id} ({role})")
        return True
    except ddb.exceptions.ConditionalCheckFailedException:
        print(f"[AUTH] User already exists: {user_id}")
        return False
    except Exception as e:
        print(f"[AUTH] Error creating user {user_id}: {e}")
        return False


def update_password(user_id: str, new_password: str, clear_must_change: bool = True) -> bool:
    """Update user password."""
    password_hash = hash_password(new_password)
    now_utc = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        update_expr = "SET password_hash = :hash, password_changed_utc = :changed"
        expr_values = {
            ":hash": {"S": password_hash},
            ":changed": {"S": now_utc}
        }

        if clear_must_change:
            update_expr += ", must_change_password = :must_change"
            expr_values[":must_change"] = {"BOOL": False}

        ddb.update_item(
            TableName=USERS_TABLE,
            Key={"user_id": {"S": user_id}},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values
        )
        print(f"[AUTH] Password updated for {user_id}")
        return True
    except Exception as e:
        print(f"[AUTH] Error updating password for {user_id}: {e}")
        return False


def authenticate(user_id: str, password: str) -> Optional[Dict]:
    """Authenticate a user and update last login time."""
    user = get_user(user_id)
    if not user:
        print(f"[AUTH] User not found: {user_id}")
        return None

    if not user.get("enabled"):
        print(f"[AUTH] User disabled: {user_id}")
        return None

    if not verify_password(password, user.get("password_hash", "")):
        print(f"[AUTH] Invalid password for user: {user_id}")
        return None

    # Update last login time
    now_utc = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        ddb.update_item(
            TableName=USERS_TABLE,
            Key={"user_id": {"S": user_id}},
            UpdateExpression="SET last_login_utc = :login_time",
            ExpressionAttributeValues={":login_time": {"S": now_utc}}
        )
    except Exception as e:
        print(f"[AUTH] Error updating last login for {user_id}: {e}")

    print(f"[AUTH] User authenticated: {user_id} ({user.get('role')})")
    return user


def has_permission(user_role: str, permission: str) -> bool:
    """Check if a role has a specific permission."""
    if user_role not in ROLES:
        return False

    role_perms = ROLES[user_role]["permissions"]

    # System admins have all permissions
    if "*" in role_perms:
        return True

    # Check exact match or wildcard match
    for perm in role_perms:
        if perm == permission:
            return True
        if perm.endswith(":*") and permission.startswith(perm[:-1]):
            return True

    return False


def can_access_page(user_role: str, page_path: str) -> bool:
    """Check if a role can access a specific page."""
    if user_role not in ROLES:
        return False

    allowed_pages = ROLES[user_role]["pages"]

    # System admins can access all pages
    if "all" in allowed_pages:
        return True

    return page_path in allowed_pages


def list_users(role: Optional[str] = None) -> List[Dict]:
    """List all users, optionally filtered by role."""
    try:
        if role:
            # Query by role using GSI
            response = ddb.query(
                TableName=USERS_TABLE,
                IndexName="role-index",
                KeyConditionExpression="role = :role",
                ExpressionAttributeValues={":role": {"S": role}}
            )
        else:
            # Scan all users
            response = ddb.scan(TableName=USERS_TABLE)

        users = []
        for item in response.get("Items", []):
            users.append({
                "user_id": item.get("user_id", {}).get("S", ""),
                "role": item.get("role", {}).get("S", ""),
                "full_name": item.get("full_name", {}).get("S", ""),
                "enabled": item.get("enabled", {}).get("BOOL", False),
                "created_utc": item.get("created_utc", {}).get("S", ""),
                "last_login_utc": item.get("last_login_utc", {}).get("S", "")
            })

        return users
    except Exception as e:
        print(f"[AUTH] Error listing users: {e}")
        return []


def disable_user(user_id: str) -> bool:
    """Disable a user account."""
    try:
        ddb.update_item(
            TableName=USERS_TABLE,
            Key={"user_id": {"S": user_id}},
            UpdateExpression="SET enabled = :enabled",
            ExpressionAttributeValues={":enabled": {"BOOL": False}}
        )
        print(f"[AUTH] User disabled: {user_id}")
        return True
    except Exception as e:
        print(f"[AUTH] Error disabling user {user_id}: {e}")
        return False


def enable_user(user_id: str) -> bool:
    """Enable a user account."""
    try:
        ddb.update_item(
            TableName=USERS_TABLE,
            Key={"user_id": {"S": user_id}},
            UpdateExpression="SET enabled = :enabled",
            ExpressionAttributeValues={":enabled": {"BOOL": True}}
        )
        print(f"[AUTH] User enabled: {user_id}")
        return True
    except Exception as e:
        print(f"[AUTH] Error enabling user {user_id}: {e}")
        return False
