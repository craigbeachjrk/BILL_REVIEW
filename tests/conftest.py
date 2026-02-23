"""
Shared test fixtures for Bill Review Application.
Provides AWS mocking, FastAPI test client, and common test data.
"""
import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock
from moto import mock_aws
import boto3

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test environment variables BEFORE importing app modules
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["BUCKET"] = "test-bucket"
os.environ["DRAFTS_TABLE"] = "test-drafts"
os.environ["REVIEW_TABLE"] = "test-review"
os.environ["USERS_TABLE"] = "test-users"
os.environ["CONFIG_TABLE"] = "test-config"
os.environ["SHORT_TABLE"] = "test-short"
os.environ["DEBUG_TABLE"] = "test-debug"
os.environ["ERRORS_TABLE"] = "test-errors"
os.environ["APP_SECRET"] = "test-secret-key-for-testing-only"
os.environ["SECURE_COOKIES"] = "0"

# Mock snowflake BEFORE any test imports main
sys.modules['snowflake'] = MagicMock()
sys.modules['snowflake.connector'] = MagicMock()


# ============================================================================
# Session-scoped AWS mock that starts for all tests
# ============================================================================

# Start moto mock at module import time so main.py can be imported
_mock_aws_instance = mock_aws()
_mock_aws_instance.start()

# Pre-create required AWS resources that main.py expects at import time
_s3 = boto3.client("s3", region_name="us-east-1")
try:
    _s3.create_bucket(Bucket="test-bucket")
except Exception:
    pass

_ddb = boto3.client("dynamodb", region_name="us-east-1")
for _table_name in ["test-drafts", "test-review", "test-users", "test-config", "test-short", "test-debug", "test-errors"]:
    try:
        if _table_name == "test-users":
            _ddb.create_table(
                TableName=_table_name,
                KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
                AttributeDefinitions=[
                    {"AttributeName": "user_id", "AttributeType": "S"},
                    {"AttributeName": "role", "AttributeType": "S"}
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "role-index",
                        "KeySchema": [{"AttributeName": "role", "KeyType": "HASH"}],
                        "Projection": {"ProjectionType": "ALL"}
                    }
                ],
                BillingMode="PAY_PER_REQUEST"
            )
        elif _table_name == "test-config":
            _ddb.create_table(
                TableName=_table_name,
                KeySchema=[
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"}
                ],
                AttributeDefinitions=[
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"}
                ],
                BillingMode="PAY_PER_REQUEST"
            )
        else:
            _ddb.create_table(
                TableName=_table_name,
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST"
            )
    except Exception:
        pass


# ============================================================================
# AWS Credentials Fixture
# ============================================================================

@pytest.fixture(scope="function")
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    yield


# ============================================================================
# S3 Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def mock_s3(aws_credentials):
    """Create mocked S3 bucket with test structure."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")

        # Create standard directory structure markers
        prefixes = [
            "Bill_Parser_2_Parsed_Inputs/",
            "Bill_Parser_4_Enriched_Outputs/",
            "Bill_Parser_5_Overrides/",
            "Bill_Parser_6_PreEntrata_Submission/",
            "Bill_Parser_7_PostEntrata_Submission/",
            "Bill_Parser_8_UBI_Assigned/",
            "Bill_Parser_Config/",
            "Bill_Parser_Enrichment/exports/",
        ]
        for prefix in prefixes:
            s3.put_object(Bucket="test-bucket", Key=f"{prefix}.keep", Body=b"")

        yield s3


@pytest.fixture(scope="function")
def mock_s3_with_invoice(mock_s3, sample_invoice_jsonl):
    """S3 bucket pre-populated with a test invoice."""
    key = "Bill_Parser_4_Enriched_Outputs/yyyy=2025/mm=01/dd=15/test_invoice.jsonl"
    mock_s3.put_object(
        Bucket="test-bucket",
        Key=key,
        Body=sample_invoice_jsonl.encode("utf-8")
    )
    return mock_s3


# ============================================================================
# DynamoDB Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def mock_dynamodb(aws_credentials):
    """Create mocked DynamoDB tables."""
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")

        # Create drafts table
        ddb.create_table(
            TableName="test-drafts",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST"
        )

        # Create review table (status tracking)
        ddb.create_table(
            TableName="test-review",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"}
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "status-index",
                    "KeySchema": [{"AttributeName": "status", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"}
                }
            ],
            BillingMode="PAY_PER_REQUEST"
        )

        # Create users table
        ddb.create_table(
            TableName="test-users",
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "role", "AttributeType": "S"}
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "role-index",
                    "KeySchema": [{"AttributeName": "role", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"}
                }
            ],
            BillingMode="PAY_PER_REQUEST"
        )

        # Create config table
        ddb.create_table(
            TableName="test-config",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"}
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST"
        )

        # Create errors table
        ddb.create_table(
            TableName="test-errors",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST"
        )

        yield ddb


@pytest.fixture(scope="function")
def mock_dynamodb_with_user(mock_dynamodb):
    """DynamoDB with a test user pre-created."""
    # Pre-hashed password for "testpassword123"
    # In real tests, we'll mock verify_password
    mock_dynamodb.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": "test@example.com"},
            "password_hash": {"S": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.WQjCYf0eDFYWDi"},
            "role": {"S": "System_Admins"},
            "full_name": {"S": "Test User"},
            "enabled": {"BOOL": True},
            "must_change_password": {"BOOL": False},
            "created_utc": {"S": "2025-01-01T00:00:00Z"}
        }
    )
    return mock_dynamodb


# ============================================================================
# Secrets Manager Fixture
# ============================================================================

@pytest.fixture(scope="function")
def mock_secrets(aws_credentials):
    """Create mocked Secrets Manager with test secrets."""
    with mock_aws():
        secrets = boto3.client("secretsmanager", region_name="us-east-1")

        # Create Gemini parser keys secret
        secrets.create_secret(
            Name="gemini/parser-keys",
            SecretString=json.dumps({"keys": ["test-parser-key-1", "test-parser-key-2"]})
        )

        # Create Gemini matcher keys secret
        secrets.create_secret(
            Name="gemini/matcher-keys",
            SecretString=json.dumps({"keys": ["test-matcher-key-1"]})
        )

        yield secrets


# ============================================================================
# Combined AWS Fixture
# ============================================================================

@pytest.fixture(scope="function")
def mock_all_aws(mock_s3, mock_dynamodb, mock_secrets):
    """Combined fixture providing all AWS services mocked."""
    return {
        "s3": mock_s3,
        "dynamodb": mock_dynamodb,
        "secrets": mock_secrets
    }


# ============================================================================
# FastAPI Test Client Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def app_client(mock_s3, mock_dynamodb):
    """Create FastAPI test client with mocked AWS services."""
    # Patch boto3 clients at module level before importing main
    with mock_aws():
        # Re-create clients inside mock context
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")

        ddb = boto3.client("dynamodb", region_name="us-east-1")

        # Create tables
        for table_name in ["test-drafts", "test-review", "test-users", "test-config", "test-errors"]:
            try:
                if table_name == "test-users":
                    ddb.create_table(
                        TableName=table_name,
                        KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
                        AttributeDefinitions=[
                            {"AttributeName": "user_id", "AttributeType": "S"},
                            {"AttributeName": "role", "AttributeType": "S"}
                        ],
                        GlobalSecondaryIndexes=[
                            {
                                "IndexName": "role-index",
                                "KeySchema": [{"AttributeName": "role", "KeyType": "HASH"}],
                                "Projection": {"ProjectionType": "ALL"}
                            }
                        ],
                        BillingMode="PAY_PER_REQUEST"
                    )
                elif table_name == "test-config":
                    ddb.create_table(
                        TableName=table_name,
                        KeySchema=[
                            {"AttributeName": "pk", "KeyType": "HASH"},
                            {"AttributeName": "sk", "KeyType": "RANGE"}
                        ],
                        AttributeDefinitions=[
                            {"AttributeName": "pk", "AttributeType": "S"},
                            {"AttributeName": "sk", "AttributeType": "S"}
                        ],
                        BillingMode="PAY_PER_REQUEST"
                    )
                else:
                    ddb.create_table(
                        TableName=table_name,
                        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                        BillingMode="PAY_PER_REQUEST"
                    )
            except ddb.exceptions.ResourceInUseException:
                pass  # Table already exists

        # Import FastAPI app after setting up mocks
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        yield client


@pytest.fixture(scope="function")
def authenticated_client(app_client, mock_dynamodb_with_user):
    """Test client with an authenticated session."""
    # Mock the authentication to bypass password verification
    with patch("auth.verify_password", return_value=True):
        response = app_client.post(
            "/login",
            data={"username": "test@example.com", "password": "testpassword123"},
            follow_redirects=False
        )

    # Client now has session cookie set
    yield app_client


# ============================================================================
# Test Data Fixtures
# ============================================================================

@pytest.fixture
def sample_invoice_record():
    """Sample invoice line item for testing."""
    return {
        "Bill To Name First Line": "TEST PROPERTY LLC",
        "Vendor Name": "Test Utility Company",
        "Invoice Number": "INV-12345",
        "Account Number": "67890-001",
        "Service Address": "123 Main St APT 101, Anytown, ST 12345",
        "Line Item Description": "Monthly Service Charge - Electric",
        "Line Item Charge": "150.00",
        "Bill Period Start": "01/01/2025",
        "Bill Period End": "01/31/2025",
        "Utility Type": "Electricity",
        "Consumption Amount": "1250",
        "UOM": "kWh",
        "EnrichedPropertyID": "P001",
        "EnrichedPropertyName": "Test Property",
        "EnrichedVendorID": "V001",
        "EnrichedVendorName": "Test Vendor",
        "EnrichedGLAccountNumber": "6200",
        "EnrichedGLAccountName": "Utilities Expense",
        "Status": "Review"
    }


@pytest.fixture
def sample_invoice_jsonl(sample_invoice_record):
    """Sample invoice as JSONL string."""
    return json.dumps(sample_invoice_record) + "\n"


@pytest.fixture
def sample_multi_line_invoice():
    """Sample invoice with multiple line items."""
    base = {
        "Bill To Name First Line": "MULTI LINE PROPERTY LLC",
        "Vendor Name": "Multi Utility Co",
        "Invoice Number": "INV-MULTI-001",
        "Account Number": "MULTI-12345",
        "Service Address": "456 Oak Ave, Testville, TX 75001",
        "Bill Period Start": "01/01/2025",
        "Bill Period End": "01/31/2025",
        "EnrichedPropertyID": "P002",
        "EnrichedPropertyName": "Multi Line Property",
        "EnrichedVendorID": "V002",
        "EnrichedVendorName": "Multi Utility",
        "Status": "Review"
    }

    lines = []
    for i, (desc, charge, utype) in enumerate([
        ("Electric Service", "100.00", "Electricity"),
        ("Water Service", "50.00", "Water"),
        ("Sewer Service", "35.00", "Sewer"),
    ]):
        line = base.copy()
        line["Line Item Description"] = desc
        line["Line Item Charge"] = charge
        line["Utility Type"] = utype
        line["LineIndex"] = str(i)
        lines.append(line)

    return lines


@pytest.fixture
def sample_user_data():
    """Sample user data for testing user management."""
    return {
        "user_id": "newuser@example.com",
        "password": "SecurePassword123!",
        "role": "Utility_APs",
        "full_name": "New Test User"
    }


# ============================================================================
# Helper Functions
# ============================================================================

def create_test_invoice_in_s3(s3_client, key: str, records: list):
    """Helper to create test invoice file in S3."""
    content = "\n".join(json.dumps(r) for r in records)
    s3_client.put_object(
        Bucket="test-bucket",
        Key=key,
        Body=content.encode("utf-8")
    )


def create_test_user_in_dynamodb(ddb_client, user_id: str, role: str = "Utility_APs", enabled: bool = True):
    """Helper to create test user in DynamoDB."""
    ddb_client.put_item(
        TableName="test-users",
        Item={
            "user_id": {"S": user_id},
            "password_hash": {"S": "$2b$12$test_hash"},
            "role": {"S": role},
            "full_name": {"S": f"Test User {user_id}"},
            "enabled": {"BOOL": enabled},
            "must_change_password": {"BOOL": False},
            "created_utc": {"S": "2025-01-01T00:00:00Z"}
        }
    )


# ============================================================================
# Markers
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (fast, no external dependencies)")
    config.addinivalue_line("markers", "integration: Integration tests (require mocked AWS)")
    config.addinivalue_line("markers", "slow: Slow tests")
    config.addinivalue_line("markers", "lambda_test: Lambda function tests")
