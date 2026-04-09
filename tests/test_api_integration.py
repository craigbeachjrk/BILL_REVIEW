"""
Integration tests for Bill Review API endpoints.
Tests core functionality: auth, dates, search, timeline, transactions, my-bills.

Run: python -m pytest tests/test_api_integration.py -v
"""
import os
import sys
import json
import time
from unittest.mock import MagicMock, patch

import pytest

# Bootstrap: mock AWS clients before importing main
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("APP_SECRET", "test-secret-for-testing")

import boto3 as _boto3
_original_client = _boto3.client
_mock_ddb = MagicMock()
_mock_s3 = MagicMock()
_mock_sqs = MagicMock()
_mock_secrets = MagicMock()


def _fake_client(service, **kwargs):
    if service == "dynamodb":
        return _mock_ddb
    if service == "s3":
        return _mock_s3
    if service == "sqs":
        return _mock_sqs
    if service == "secretsmanager":
        return _mock_secrets
    return _original_client(service, **kwargs)


_boto3.client = _fake_client

# Now import the app
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from main import app
from fastapi.testclient import TestClient

# Create a signed session cookie for test user
from main import APP_SECRET
import hashlib
import hmac


def _make_session_cookie(user: str = "testuser@jrk.com") -> dict:
    """Create a valid session cookie for testing."""
    payload = f"{user}|{int(time.time()) + 86400}"
    sig = hmac.new(APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    cookie_val = f"{payload}|{sig}"
    return {"br_sess": cookie_val}


@pytest.fixture
def client():
    """Test client with auth cookie."""
    c = TestClient(app)
    # We need to mock get_current_user to return our test user
    return c


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all mocks between tests."""
    _mock_ddb.reset_mock()
    _mock_s3.reset_mock()
    _mock_sqs.reset_mock()
    yield


# --- Test: Unauthenticated access returns 401 for API, 307 for pages ---

class TestAuth:
    def test_api_endpoint_returns_401_when_unauthenticated(self, client):
        resp = client.get("/api/dates", follow_redirects=False)
        assert resp.status_code in (401, 307)

    def test_page_returns_307_redirect_when_unauthenticated(self, client):
        resp = client.get("/parse", follow_redirects=False)
        assert resp.status_code == 307
        assert "/login" in resp.headers.get("location", "")

    def test_login_page_loads(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"login" in resp.content.lower() or b"Login" in resp.content


# --- Test: Date validation ---

class TestDateValidation:
    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_api_day_rejects_no_dashes(self, mock_user, client):
        resp = client.get("/api/day?date=20260101")
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_api_day_rejects_too_few_parts(self, mock_user, client):
        resp = client.get("/api/day?date=2026-01")
        assert resp.status_code == 400

    @patch("main.get_current_user", return_value="testuser@jrk.com")
    @patch("main.load_day", return_value=[])
    def test_api_day_accepts_valid_date(self, mock_load, mock_user, client):
        resp = client.get("/api/day?date=2026-01-15")
        assert resp.status_code == 200
        data = resp.json()
        assert "rows" in data

    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_api_invoices_rejects_malformed_date(self, mock_user, client):
        resp = client.get("/api/invoices?date=abc")
        assert resp.status_code == 400


# --- Test: Bill Timeline API ---

class TestBillTimeline:
    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_bill_events_returns_empty_for_unknown(self, mock_user, client):
        _mock_ddb.query.return_value = {"Items": []}
        resp = client.get("/api/bill/abc123/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["event_count"] == 0

    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_bill_events_returns_timeline(self, mock_user, client):
        _mock_ddb.query.return_value = {"Items": [
            {
                "pk": {"S": "BILL#abc123"},
                "sk": {"S": "EVENT#2026-04-09T10:00:00Z"},
                "event_type": {"S": "RECEIVED"},
                "stage": {"S": "S1"},
                "source": {"S": "email:test@jrk.com"},
                "filename": {"S": "test.pdf"},
                "s3_key": {"S": "Bill_Parser_1_Pending_Parsing/test.pdf"},
                "metadata": {"S": '{"submitted_by":"test"}'},
            },
            {
                "pk": {"S": "BILL#abc123"},
                "sk": {"S": "EVENT#2026-04-09T10:01:00Z"},
                "event_type": {"S": "PARSE_COMPLETED"},
                "stage": {"S": "S3"},
                "source": {"S": "lambda:parser"},
                "filename": {"S": "test.pdf"},
                "s3_key": {"S": "Bill_Parser_3_Parsed_Outputs/test.jsonl"},
                "metadata": {"S": '{"lines":5}'},
            },
        ]}
        resp = client.get("/api/bill/abc123/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["event_count"] == 2
        assert data["current_stage"] == "S3"
        assert data["events"][0]["event_type"] == "RECEIVED"
        assert data["events"][1]["event_type"] == "PARSE_COMPLETED"


# --- Test: Transaction Summary ---

class TestTransactions:
    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_transactions_summary_returns_structure(self, mock_user, client):
        # Clear cache
        from main import _CACHE
        _CACHE.pop(("transaction_summary", 24), None)

        _mock_ddb.query.return_value = {"Items": []}
        resp = client.get("/api/transactions/summary?hours=24")
        assert resp.status_code == 200
        data = resp.json()
        assert "stage_counts" in data
        assert "hourly" in data
        assert "by_user" in data
        assert "recent_events" in data


# --- Test: My Bills ---

class TestMyBills:
    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_my_bills_returns_empty(self, mock_user, client):
        paginator_mock = MagicMock()
        paginator_mock.paginate.return_value = [{"Items": []}]
        _mock_ddb.get_paginator.return_value = paginator_mock

        resp = client.get("/api/my-bills?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert "bills" in data
        assert data["user"] == "testuser@jrk.com"


# --- Test: Safe S3 Move ---

class TestSafeMove:
    def test_safe_move_copies_then_deletes(self):
        from main import _safe_move_s3
        _mock_s3.copy_object.return_value = {}
        _mock_s3.head_object.return_value = {"ContentLength": 100}
        _mock_s3.delete_object.return_value = {}

        result = _safe_move_s3("source/key.jsonl", "dest/key.jsonl")
        assert result is True
        _mock_s3.copy_object.assert_called_once()
        _mock_s3.head_object.assert_called_once()
        _mock_s3.delete_object.assert_called_once()

    def test_safe_move_preserves_source_on_copy_failure(self):
        from main import _safe_move_s3
        _mock_s3.copy_object.side_effect = Exception("S3 error")

        result = _safe_move_s3("source/key.jsonl", "dest/key.jsonl")
        assert result is False
        _mock_s3.delete_object.assert_not_called()

    def test_safe_move_preserves_source_on_empty_dest(self):
        from main import _safe_move_s3
        _mock_s3.copy_object.return_value = {}
        _mock_s3.head_object.return_value = {"ContentLength": 0}

        result = _safe_move_s3("source/key.jsonl", "dest/key.jsonl")
        assert result is False
        _mock_s3.delete_object.assert_not_called()


# --- Test: Error Sanitization ---

class TestErrorSanitization:
    def test_sanitize_error_strips_internal_details(self):
        from main import _sanitize_error
        e = Exception("An error occurred (ResourceNotFoundException) when calling GetItem: table arn:aws:dynamodb:us-east-1:123456:table/jrk-bill-drafts")
        result = _sanitize_error(e, "test")
        # Should not contain ARN or table name
        assert "arn:aws" not in result
        assert "123456" not in result


# --- Test: Normalize Account Number ---

class TestNormalize:
    def test_normalize_strips_separators(self):
        from main import _normalize_account_number
        assert _normalize_account_number("123-456-789") == "123456789"
        assert _normalize_account_number("00012345") == "12345"
        assert _normalize_account_number("") == "0"  # empty normalizes to "0" (at least one digit)
        assert _normalize_account_number("0") == "0"


# --- Test: Pipeline Pages Load ---

class TestPagesLoad:
    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_timeline_page_loads(self, mock_user, client):
        resp = client.get("/bill/timeline")
        assert resp.status_code == 200
        assert b"Bill Timeline" in resp.content

    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_my_bills_page_loads(self, mock_user, client):
        resp = client.get("/my-bills")
        assert resp.status_code == 200
        assert b"My Bills" in resp.content

    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_transactions_page_loads(self, mock_user, client):
        resp = client.get("/transactions")
        assert resp.status_code == 200
        assert b"Transactions" in resp.content

    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_landing_page_loads(self, mock_user, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"MY BILLS" in resp.content
        assert b"TRANSACTIONS" in resp.content
        assert b"BILL TIMELINE" in resp.content


# --- Test: Pipeline Tracking ---

class TestPipelineTracking:
    def test_pipeline_track_writes_to_ddb(self):
        from main import _pipeline_track
        _mock_ddb.put_item.return_value = {}
        _pipeline_track("Bill_Parser_4/test.jsonl", "SUBMITTED", "app:submit:testuser", "S6", {"lines": 5})
        assert _mock_ddb.put_item.called
        call_args = _mock_ddb.put_item.call_args
        item = call_args[1]["Item"] if "Item" in call_args[1] else call_args[0][0]
        assert item["event_type"]["S"] == "SUBMITTED"
        assert item["stage"]["S"] == "S6"

    def test_pipeline_track_silent_on_failure(self):
        from main import _pipeline_track
        _mock_ddb.put_item.side_effect = Exception("DDB down")
        # Should not raise
        _pipeline_track("test.jsonl", "TEST", "test", "S1")
        _mock_ddb.put_item.side_effect = None


# --- Test: Safe Write and Delete ---

class TestSafeWriteAndDelete:
    def test_safe_write_and_delete_success(self):
        from main import _safe_write_and_delete
        _mock_s3.put_object.return_value = {}
        _mock_s3.head_object.return_value = {"ContentLength": 500}
        _mock_s3.delete_object.return_value = {}

        result = _safe_write_and_delete("Bill_Parser_7_PostEntrata/", "2026", "04", "09", "test", [{"a": 1}], "source/key.jsonl")
        assert result is not None
        assert result.startswith("Bill_Parser_7_PostEntrata/")
        _mock_s3.delete_object.assert_called_once()

    def test_safe_write_and_delete_preserves_source_on_failure(self):
        from main import _safe_write_and_delete
        _mock_s3.put_object.side_effect = Exception("S3 error")

        result = _safe_write_and_delete("dest/", "2026", "04", "09", "test", [{"a": 1}], "source/key.jsonl")
        assert result is None
        _mock_s3.delete_object.assert_not_called()
        _mock_s3.put_object.side_effect = None


# --- Test: Billback Summary Caching ---

class TestBillbackSummaryCaching:
    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_billback_summary_uses_cache(self, mock_user, client):
        from main import _CACHE
        # Seed cache
        cache_key = ("billback_summary", "month")
        _CACHE[cache_key] = {"ts": time.time(), "data": {"summary": [], "group_by": "month"}}
        resp = client.get("/api/billback/summary?group_by=month")
        assert resp.status_code == 200
        data = resp.json()
        assert data["group_by"] == "month"
        # Clean up
        _CACHE.pop(cache_key, None)


# --- Test: Config Endpoints ---

class TestConfigEndpoints:
    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_catalogs_returns_structure(self, mock_user, client):
        _mock_s3.get_object.side_effect = Exception("NoSuchKey")
        resp = client.get("/api/catalogs")
        assert resp.status_code == 200
        data = resp.json()
        assert "properties" in data
        assert "vendors" in data


# --- Test: Auth Behavior ---

class TestAuthBehavior:
    def test_api_returns_401_not_307(self, client):
        """API endpoints should return 401, not redirect to login."""
        resp = client.get("/api/dates", follow_redirects=False)
        # Should be 401 for API paths (not 307 redirect)
        assert resp.status_code == 401

    def test_page_returns_307_to_login(self, client):
        resp = client.get("/parse", follow_redirects=False)
        assert resp.status_code == 307
        assert "/login" in resp.headers.get("location", "")

    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_admin_endpoint_returns_403_for_non_admin(self, mock_user, client):
        """Non-admin users should get 403 on admin endpoints."""
        resp = client.get("/api/pipeline/stuck?threshold_minutes=60")
        assert resp.status_code == 403


# --- Test: Search Returns Posted Bills ---

class TestSearchIncludesPosted:
    @patch("main.get_current_user", return_value="testuser@jrk.com")
    def test_search_endpoint_works(self, mock_user, client):
        from main import _SEARCH_INDEX
        # Seed search index with test data
        _SEARCH_INDEX["ready"] = True
        _SEARCH_INDEX["entries"] = [
            {"pdf_id": "test123", "date": "2026-04-09", "account": "12345",
             "account_l": "12345", "vendor": "Test Vendor", "vendor_l": "test vendor",
             "property": "Test Property", "property_l": "test property", "amount": 100.0},
        ]
        resp = client.get("/api/search?account=12345")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        # Clean up
        _SEARCH_INDEX["entries"] = []
        _SEARCH_INDEX["ready"] = False


# --- Test: Invoices Page Includes submitted_by ---

class TestInvoicesSubmittedBy:
    @patch("main.get_current_user", return_value="testuser@jrk.com")
    @patch("main.load_day")
    @patch("main.get_header_drafts_batch", return_value={})
    def test_invoices_page_has_scanned_by_column(self, mock_drafts, mock_load, mock_user, client):
        mock_load.return_value = [{
            "__s3_key__": "Bill_Parser_4_Enriched_Outputs/yyyy=2026/mm=04/dd=09/test.jsonl",
            "__id__": "test_id",
            "Account Number": "12345",
            "EnrichedVendorName": "Test Vendor",
            "EnrichedPropertyName": "Test Property",
            "Line Item Charge": "100.00",
            "Line Item Description": "Electric",
            "submitted_by": "dgonzalez",
        }]
        resp = client.get("/invoices?date=2026-04-09")
        assert resp.status_code == 200
        assert b"Scanned By" in resp.content
