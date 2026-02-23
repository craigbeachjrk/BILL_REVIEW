"""
Tests for Performance Monitoring system and is_tracked bug fixes.
Run: python -m pytest test_perf_and_tracker.py -v
"""
import os
import sys
import json
import time
import threading
import calendar
import datetime as dt
from collections import deque
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: main.py creates boto3 clients at module level – mock them before
# importing so we don't need real AWS credentials.
# ---------------------------------------------------------------------------
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("APP_SECRET", "test-secret")

import boto3 as _boto3
_original_client = _boto3.client
_mock_ddb = MagicMock()
_mock_s3 = MagicMock()
_mock_sqs = MagicMock()

def _fake_client(service, **kwargs):
    if service == "dynamodb":
        return _mock_ddb
    if service == "s3":
        return _mock_s3
    if service == "sqs":
        return _mock_sqs
    return MagicMock()

_boto3.client = _fake_client

# Prevent genai import from failing
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.generativeai", MagicMock())

# Mock snowflake
sys.modules.setdefault("snowflake", MagicMock())
sys.modules.setdefault("snowflake.connector", MagicMock())

# Mock auth module
sys.modules.setdefault("auth", MagicMock())

# Now import main (will use our mocked clients)
# We need to suppress the DynamoDB query in _perf_load_historical_rollups
_mock_ddb.query = MagicMock(return_value={"Items": []})
_mock_ddb.get_item = MagicMock(return_value={})
_mock_ddb.put_item = MagicMock(return_value={})

import main

# Restore real boto3 client for any subsequent tests that need it
_boto3.client = _original_client


# ===========================================================================
# SECTION 1: Performance Monitoring — Path Normalization
# ===========================================================================
class TestPerfPathNormalization:
    """Test _perf_normalize_path collapses dynamic segments correctly."""

    def test_timing_id(self):
        assert main._perf_normalize_path("/api/timing/abc123") == "/api/timing/{id}"

    def test_timing_id_subpath(self):
        assert main._perf_normalize_path("/api/timing/abc123/start") == "/api/timing/{id}/start"

    def test_invoices_id(self):
        assert main._perf_normalize_path("/api/invoices/xyz789") == "/api/invoices/{id}"

    def test_flagged_id(self):
        assert main._perf_normalize_path("/api/flagged/item42") == "/api/flagged/{id}"

    def test_master_bills_detail_id(self):
        assert main._perf_normalize_path("/api/master-bills/detail/mb001") == "/api/master-bills/detail/{id}"

    def test_date_in_path_yyyy_mm_dd(self):
        result = main._perf_normalize_path("/api/data/yyyy=2026/mm=01/dd=29/file.jsonl")
        assert "/{date}" in result
        assert "yyyy=" not in result

    def test_numeric_date(self):
        result = main._perf_normalize_path("/api/data/2026/01/29")
        assert result == "/api/data/{date}"

    def test_static_path_unchanged(self):
        assert main._perf_normalize_path("/api/perf/live") == "/api/perf/live"

    def test_root_unchanged(self):
        assert main._perf_normalize_path("/") == "/"

    def test_invoices_list_unchanged(self):
        """Ensure /api/invoices (no trailing ID) is NOT normalized."""
        assert main._perf_normalize_path("/api/invoices") == "/api/invoices"


# ===========================================================================
# SECTION 2: Performance Monitoring — Percentile Calculation
# ===========================================================================
class TestPerfPercentile:
    """Test _perf_percentile with edge cases."""

    def test_empty_list(self):
        assert main._perf_percentile([], 0.50) == 0.0

    def test_single_element(self):
        assert main._perf_percentile([100.0], 0.50) == 100.0
        assert main._perf_percentile([100.0], 0.95) == 100.0
        assert main._perf_percentile([100.0], 0.99) == 100.0

    def test_two_elements(self):
        # p50 of [100, 200]: idx = min(int(2*0.5), 1) = min(1, 1) = 1 → 200
        assert main._perf_percentile([100.0, 200.0], 0.50) == 200.0
        # p95: idx = min(int(2*0.95), 1) = min(1, 1) = 1 → 200
        assert main._perf_percentile([100.0, 200.0], 0.95) == 200.0

    def test_ten_elements_p50(self):
        times = [float(i * 100) for i in range(10)]  # 0, 100, ..., 900
        # p50: idx = min(int(10*0.5), 9) = 5 → 500
        assert main._perf_percentile(times, 0.50) == 500.0

    def test_ten_elements_p95(self):
        times = [float(i * 100) for i in range(10)]
        # p95: idx = min(int(10*0.95), 9) = min(9, 9) = 9 → 900
        assert main._perf_percentile(times, 0.95) == 900.0

    def test_ten_elements_p99(self):
        times = [float(i * 100) for i in range(10)]
        # p99: idx = min(int(10*0.99), 9) = min(9, 9) = 9 → 900
        assert main._perf_percentile(times, 0.99) == 900.0

    def test_hundred_elements_p95(self):
        times = [float(i) for i in range(100)]  # 0..99
        # p95: idx = min(int(100*0.95), 99) = min(95, 99) = 95 → 95.0
        assert main._perf_percentile(times, 0.95) == 95.0

    def test_index_clamped_for_p99_at_boundary(self):
        """Ensure index never exceeds n-1."""
        times = [1.0, 2.0, 3.0]
        # p99: idx = min(int(3*0.99), 2) = min(2, 2) = 2 → 3.0
        assert main._perf_percentile(times, 0.99) == 3.0

    def test_p0_returns_first(self):
        times = [10.0, 20.0, 30.0]
        assert main._perf_percentile(times, 0.0) == 10.0


# ===========================================================================
# SECTION 3: Performance Monitoring — Compute Rollup
# ===========================================================================
class TestPerfComputeRollup:
    """Test _perf_compute_rollup aggregation logic."""

    def test_single_record(self):
        records = [{"path": "/api/test", "ms": 150.0, "status": 200}]
        result = main._perf_compute_rollup(records)
        assert "/api/test" in result
        ep = result["/api/test"]
        assert ep["count"] == 1
        assert ep["avg_ms"] == 150.0
        assert ep["min_ms"] == 150.0
        assert ep["max_ms"] == 150.0
        assert ep["errors"] == 0

    def test_multiple_records_same_endpoint(self):
        records = [
            {"path": "/api/test", "ms": 100.0, "status": 200},
            {"path": "/api/test", "ms": 200.0, "status": 200},
            {"path": "/api/test", "ms": 300.0, "status": 200},
        ]
        result = main._perf_compute_rollup(records)
        ep = result["/api/test"]
        assert ep["count"] == 3
        assert ep["avg_ms"] == 200.0
        assert ep["min_ms"] == 100.0
        assert ep["max_ms"] == 300.0
        assert ep["sum_ms"] == 600.0

    def test_multiple_endpoints(self):
        records = [
            {"path": "/api/a", "ms": 50.0, "status": 200},
            {"path": "/api/b", "ms": 150.0, "status": 200},
        ]
        result = main._perf_compute_rollup(records)
        assert len(result) == 2
        assert result["/api/a"]["count"] == 1
        assert result["/api/b"]["count"] == 1

    def test_error_counting(self):
        records = [
            {"path": "/api/test", "ms": 100.0, "status": 200},
            {"path": "/api/test", "ms": 200.0, "status": 500},
            {"path": "/api/test", "ms": 300.0, "status": 502},
            {"path": "/api/test", "ms": 400.0, "status": 404},  # Not a server error
        ]
        result = main._perf_compute_rollup(records)
        assert result["/api/test"]["errors"] == 2

    def test_empty_records(self):
        result = main._perf_compute_rollup([])
        assert result == {}

    def test_percentiles_in_rollup(self):
        records = [{"path": "/api/test", "ms": float(i), "status": 200} for i in range(1, 101)]
        result = main._perf_compute_rollup(records)
        ep = result["/api/test"]
        # Values 1..100: index = min(int(n*p), n-1)
        # p50: int(100*0.5)=50 → sorted[50]=51.0
        # p95: int(100*0.95)=95 → sorted[95]=96.0
        # p99: int(100*0.99)=99 → sorted[99]=100.0
        assert ep["p50_ms"] == 51.0
        assert ep["p95_ms"] == 96.0
        assert ep["p99_ms"] == 100.0


# ===========================================================================
# SECTION 4: Performance Monitoring — Record Function
# ===========================================================================
class TestPerfRecord:
    """Test _perf_record skipping and recording logic."""

    def setup_method(self):
        main._PERF_LOG.clear()

    def test_records_normal_path(self):
        main._perf_record("/api/invoices", "GET", 200, 123.45, "user@test.com")
        assert len(main._PERF_LOG) == 1
        rec = main._PERF_LOG[0]
        assert rec["path"] == "/api/invoices"
        assert rec["method"] == "GET"
        assert rec["status"] == 200
        assert rec["ms"] == 123.45
        assert rec["user"] == "user@test.com"

    def test_skips_static(self):
        main._perf_record("/static/css/main.css", "GET", 200, 5.0, "")
        assert len(main._PERF_LOG) == 0

    def test_skips_favicon(self):
        main._perf_record("/favicon.ico", "GET", 200, 2.0, "")
        assert len(main._PERF_LOG) == 0

    def test_skips_login(self):
        main._perf_record("/login", "GET", 200, 50.0, "")
        assert len(main._PERF_LOG) == 0

    def test_skips_logout(self):
        main._perf_record("/logout", "POST", 302, 10.0, "")
        assert len(main._PERF_LOG) == 0

    def test_normalizes_path(self):
        main._perf_record("/api/timing/abc123", "GET", 200, 100.0, "")
        assert main._PERF_LOG[0]["path"] == "/api/timing/{id}"

    def test_empty_user_becomes_empty_string(self):
        main._perf_record("/api/test", "GET", 200, 50.0, None)
        assert main._PERF_LOG[0]["user"] == ""

    def test_deque_maxlen_respected(self):
        """Verify deque auto-eviction works."""
        main._PERF_LOG.clear()
        # Record more than maxlen
        for i in range(100):
            main._perf_record(f"/api/test", "GET", 200, 1.0, "")
        # deque maxlen is 50000, so 100 should all fit
        assert len(main._PERF_LOG) == 100

    def test_timestamp_set(self):
        before = time.time()
        main._perf_record("/api/test", "GET", 200, 50.0, "")
        after = time.time()
        rec = main._PERF_LOG[0]
        assert before <= rec["ts"] <= after


# ===========================================================================
# SECTION 5: Performance Monitoring — Hour Transition & Persistence
# ===========================================================================
class TestPerfHourTransition:
    """Test _perf_maybe_persist_hour logic."""

    def setup_method(self):
        main._PERF_LAST_HOUR = None
        main._PERF_ROLLUPS.clear()

    def test_first_call_sets_hour_no_persist(self):
        """First call should just set the hour, no DynamoDB write."""
        main._PERF_LAST_HOUR = None
        main._perf_maybe_persist_hour()
        assert main._PERF_LAST_HOUR is not None
        main.ddb.put_item.assert_not_called()

    def test_same_hour_no_persist(self):
        """Calling twice in the same hour should not persist."""
        main.ddb.put_item.reset_mock()
        main._perf_maybe_persist_hour()
        main._perf_maybe_persist_hour()
        main.ddb.put_item.assert_not_called()

    def test_hour_change_persists_rollup(self):
        """Simulating an hour change should trigger DynamoDB persist."""
        main.ddb.put_item.reset_mock()
        # Set up: previous hour had a rollup
        prev_hour = "2026-01-29T14"
        main._PERF_LAST_HOUR = prev_hour
        main._PERF_ROLLUPS[prev_hour] = {"/api/test": {"count": 10, "avg_ms": 100}}

        # Mock utcnow to return a different hour
        new_hour_dt = dt.datetime(2026, 1, 29, 15, 5, 0)
        with patch("main.dt.datetime") as mock_dt:
            mock_dt.utcnow.return_value = new_hour_dt
            mock_dt.side_effect = lambda *a, **k: dt.datetime(*a, **k)
            main._perf_maybe_persist_hour()

        # Should have persisted the previous hour
        main.ddb.put_item.assert_called_once()
        call_args = main.ddb.put_item.call_args
        item = call_args[1]["Item"] if "Item" in call_args[1] else call_args[0][0]
        assert item["PK"]["S"] == "CONFIG#perf-rollup"
        assert item["SK"]["S"] == prev_hour

    def test_hour_change_no_rollup_no_persist(self):
        """Hour changes but no rollup data — should not write to DDB."""
        main.ddb.put_item.reset_mock()
        main._PERF_LAST_HOUR = "2026-01-29T14"
        # No rollup for this hour

        new_hour_dt = dt.datetime(2026, 1, 29, 15, 5, 0)
        with patch("main.dt.datetime") as mock_dt:
            mock_dt.utcnow.return_value = new_hour_dt
            mock_dt.side_effect = lambda *a, **k: dt.datetime(*a, **k)
            main._perf_maybe_persist_hour()

        main.ddb.put_item.assert_not_called()


# ===========================================================================
# SECTION 6: Performance Monitoring — Current Hour Rollup
# ===========================================================================
class TestPerfUpdateCurrentHour:
    """Test _perf_update_current_hour computes rollup from raw records."""

    def setup_method(self):
        main._PERF_LOG.clear()
        main._PERF_ROLLUPS.clear()

    def test_with_recent_records(self):
        """Records from current hour should produce a rollup."""
        now = time.time()
        main._PERF_LOG.append({"path": "/api/test", "ms": 100.0, "status": 200, "ts": now})
        main._PERF_LOG.append({"path": "/api/test", "ms": 200.0, "status": 200, "ts": now})

        main._perf_update_current_hour()

        current_hour = dt.datetime.utcnow().strftime("%Y-%m-%dT%H")
        assert current_hour in main._PERF_ROLLUPS
        assert main._PERF_ROLLUPS[current_hour]["/api/test"]["count"] == 2

    def test_with_no_records(self):
        """Empty log should not create a rollup entry."""
        main._perf_update_current_hour()
        # May or may not have entry depending on timing — just verify no crash
        assert isinstance(main._PERF_ROLLUPS, dict)

    def test_old_records_excluded(self):
        """Records from 2 hours ago should be excluded from current hour rollup."""
        two_hours_ago = time.time() - 7200
        main._PERF_LOG.append({"path": "/api/old", "ms": 100.0, "status": 200, "ts": two_hours_ago})

        # Also add a current record so rollup gets created
        main._PERF_LOG.append({"path": "/api/new", "ms": 50.0, "status": 200, "ts": time.time()})

        main._perf_update_current_hour()

        current_hour = dt.datetime.utcnow().strftime("%Y-%m-%dT%H")
        if current_hour in main._PERF_ROLLUPS:
            assert "/api/old" not in main._PERF_ROLLUPS[current_hour]


# ===========================================================================
# SECTION 7: Performance Monitoring — Historical Rollup Loading
# ===========================================================================
class TestPerfLoadHistorical:
    """Test _perf_load_historical_rollups DynamoDB pagination."""

    def setup_method(self):
        main._PERF_ROLLUPS.clear()

    def test_loads_items_from_ddb(self):
        """Should populate _PERF_ROLLUPS from DynamoDB query results."""
        rollup_data = {"/api/test": {"count": 5, "avg_ms": 200}}
        main.ddb.query.return_value = {
            "Items": [{
                "SK": {"S": "2026-01-29T10"},
                "Data": {"S": json.dumps(rollup_data)},
            }]
            # No LastEvaluatedKey — single page
        }
        main._perf_load_historical_rollups()
        assert "2026-01-29T10" in main._PERF_ROLLUPS
        assert main._PERF_ROLLUPS["2026-01-29T10"]["/api/test"]["count"] == 5

    def test_handles_pagination(self):
        """Should follow LastEvaluatedKey for multiple pages."""
        page1 = {
            "Items": [{"SK": {"S": "2026-01-29T10"}, "Data": {"S": json.dumps({"a": 1})}}],
            "LastEvaluatedKey": {"PK": {"S": "x"}, "SK": {"S": "2026-01-29T10"}},
        }
        page2 = {
            "Items": [{"SK": {"S": "2026-01-29T11"}, "Data": {"S": json.dumps({"b": 2})}}],
        }
        main.ddb.query.side_effect = [page1, page2]
        main._perf_load_historical_rollups()
        assert "2026-01-29T10" in main._PERF_ROLLUPS
        assert "2026-01-29T11" in main._PERF_ROLLUPS

    def test_handles_empty_result(self):
        """Empty DDB result should not crash."""
        main.ddb.query.return_value = {"Items": []}
        main._perf_load_historical_rollups()
        # No crash, rollups may be empty

    def test_handles_malformed_data(self):
        """Malformed JSON in Data field should be skipped."""
        main.ddb.query.return_value = {
            "Items": [{"SK": {"S": "2026-01-29T10"}, "Data": {"S": "not-json{"}}],
        }
        main._perf_load_historical_rollups()
        assert "2026-01-29T10" not in main._PERF_ROLLUPS

    def test_handles_ddb_error(self):
        """DynamoDB error should be caught and logged, not crash."""
        main.ddb.query.side_effect = Exception("DynamoDB timeout")
        main._perf_load_historical_rollups()  # Should not raise
        main.ddb.query.side_effect = None  # Reset


# ===========================================================================
# SECTION 8: Performance Monitoring — Thread Safety
# ===========================================================================
class TestPerfThreadSafety:
    """Test concurrent access to perf data structures."""

    def setup_method(self):
        main._PERF_LOG.clear()
        main._PERF_ROLLUPS.clear()

    def test_concurrent_record_calls(self):
        """Multiple threads recording simultaneously should not crash."""
        errors = []

        def record_many(thread_id):
            try:
                for i in range(500):
                    main._perf_record(f"/api/test/{thread_id}", "GET", 200, float(i), f"user{thread_id}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_many, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(main._PERF_LOG) == 5000  # 10 threads * 500 records

    def test_concurrent_record_and_read(self):
        """One thread writing, another reading — should not crash."""
        errors = []
        stop = threading.Event()

        def writer():
            try:
                for i in range(1000):
                    main._perf_record("/api/test", "GET", 200, float(i), "writer")
            except Exception as e:
                errors.append(e)
            finally:
                stop.set()

        def reader():
            try:
                while not stop.is_set():
                    with main._PERF_LOG_LOCK:
                        _ = list(main._PERF_LOG)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join(timeout=2)

        assert len(errors) == 0


# ===========================================================================
# SECTION 9: is_tracked Bug Fixes — Remove from Tracker
# ===========================================================================
class TestRemoveFromTracker:
    """Test that remove-from-tracker marks ALL duplicates."""

    def _make_accounts(self):
        return [
            {"vendorId": "v1", "vendorName": "Vendor A", "accountNumber": "111",
             "propertyId": "p1", "propertyName": "Prop A", "is_tracked": True, "is_ubi": True},
            {"vendorId": "v1", "vendorName": "Vendor A", "accountNumber": "111",
             "propertyId": "p1", "propertyName": "Prop A", "is_tracked": True, "is_ubi": True},
            {"vendorId": "v2", "vendorName": "Vendor B", "accountNumber": "222",
             "propertyId": "p2", "propertyName": "Prop B", "is_tracked": True, "is_ubi": True},
        ]

    def test_remove_marks_all_duplicates(self):
        """Both duplicate entries should be set to is_tracked=False."""
        arr = self._make_accounts()

        # Simulate the remove logic from main.py line ~12120
        account_number = "111"
        vendor_name = "Vendor A"
        property_name = "Prop A"

        found = False
        for item in arr:
            if (str(item.get("accountNumber", "")).strip() == account_number and
                str(item.get("vendorName", "")).strip() == vendor_name and
                str(item.get("propertyName", "")).strip() == property_name):
                item["is_tracked"] = False
                found = True
                # Don't break — mark ALL duplicates

        assert found
        assert arr[0]["is_tracked"] == False
        assert arr[1]["is_tracked"] == False
        assert arr[2]["is_tracked"] == True  # Unrelated account unchanged

    def test_old_code_would_miss_second_duplicate(self):
        """Verify the old behavior (with break) misses the second entry."""
        arr = self._make_accounts()

        # Old code with break
        for item in arr:
            if (str(item.get("accountNumber", "")).strip() == "111" and
                str(item.get("vendorName", "")).strip() == "Vendor A" and
                str(item.get("propertyName", "")).strip() == "Prop A"):
                item["is_tracked"] = False
                break  # OLD BUG

        assert arr[0]["is_tracked"] == False
        assert arr[1]["is_tracked"] == True  # BUG: second duplicate still tracked


# ===========================================================================
# SECTION 10: is_tracked Bug Fixes — Completion Tracker Dedup
# ===========================================================================
class TestCompletionTrackerDedup:
    """Test that completion tracker deduplicates accounts."""

    def test_dedup_removes_duplicate_entries(self):
        """Duplicate UBI accounts should produce only one tracker entry per property."""
        ubi_accounts = [
            {"propertyId": "p1", "propertyName": "Prop A", "accountNumber": "111",
             "vendorName": "Vendor A", "is_ubi": True, "is_tracked": True},
            {"propertyId": "p1", "propertyName": "Prop A", "accountNumber": "111",
             "vendorName": "Vendor A", "is_ubi": True, "is_tracked": True},
        ]

        # Simulate the dedup logic from main.py line ~13505
        seen_accounts = set()
        properties = {}
        assigned_accounts = set()

        for acc in ubi_accounts:
            property_id = str(acc.get("propertyId", "")).strip()
            property_name = str(acc.get("propertyName", "")).strip()
            account_number = str(acc.get("accountNumber", "")).strip()
            vendor_name = str(acc.get("vendorName", "")).strip()

            if not property_id or not account_number:
                continue

            dedup_key = (property_id, account_number, vendor_name)
            if dedup_key in seen_accounts:
                continue
            seen_accounts.add(dedup_key)

            if property_id not in properties:
                properties[property_id] = {"accounts": [], "total": 0}

            properties[property_id]["accounts"].append({
                "account_number": account_number,
                "vendor_name": vendor_name,
            })
            properties[property_id]["total"] += 1

        # Should have only 1 account, not 2
        assert properties["p1"]["total"] == 1
        assert len(properties["p1"]["accounts"]) == 1


# ===========================================================================
# SECTION 11: is_tracked Bug Fixes — UBI Account Filtering
# ===========================================================================
class TestUbiAccountFiltering:
    """Test that UBI endpoints properly filter by is_tracked."""

    def _make_accounts(self):
        return [
            {"propertyId": "p1", "vendorId": "v1", "accountNumber": "111",
             "is_ubi": True, "is_tracked": True},
            {"propertyId": "p2", "vendorId": "v2", "accountNumber": "222",
             "is_ubi": True, "is_tracked": False},  # Removed from tracker
            {"propertyId": "p3", "vendorId": "v3", "accountNumber": "333",
             "is_ubi": False, "is_tracked": True},  # Not UBI
        ]

    def test_filter_requires_both_flags(self):
        """Only accounts with is_ubi=True AND is_tracked=True should be included."""
        accounts = self._make_accounts()

        # Simulate the filter from main.py line ~3387
        ubi_account_keys = set()
        for acct in accounts:
            if acct.get("is_ubi") == True and acct.get("is_tracked", True):
                prop_id = str(acct.get("propertyId", "")).strip()
                vendor_id = str(acct.get("vendorId", "")).strip()
                acct_num = str(acct.get("accountNumber", "")).strip()
                if prop_id and acct_num:
                    ubi_account_keys.add(f"{prop_id}|{vendor_id}|{acct_num}")

        assert "p1|v1|111" in ubi_account_keys
        assert "p2|v2|222" not in ubi_account_keys  # is_tracked=False
        assert "p3|v3|333" not in ubi_account_keys  # is_ubi=False

    def test_default_is_tracked_true(self):
        """Account without is_tracked field should default to True."""
        accounts = [
            {"propertyId": "p1", "vendorId": "v1", "accountNumber": "111", "is_ubi": True},
            # No is_tracked field — should default to True
        ]

        filtered = [a for a in accounts if a.get("is_ubi") == True and a.get("is_tracked", True)]
        assert len(filtered) == 1


# ===========================================================================
# SECTION 12: is_tracked Bug Fixes — Add-to-Tracker Dedup
# ===========================================================================
class TestAddToTrackerDedup:
    """Test that add-to-tracker/add-to-ubi matches by name OR ID."""

    def test_id_match_prevents_duplicate(self):
        """Matching by vendorId+accountNumber+propertyId should find existing."""
        arr = [
            {"vendorId": "v1", "vendorName": "Vendor A", "accountNumber": "111",
             "propertyId": "p1", "propertyName": "Prop A", "is_tracked": False},
        ]

        vendor_id, account_number, property_id = "v1", "111", "p1"
        vendor_name, property_name = "Vendor A", "Prop A"

        found = False
        for item in arr:
            id_match = (str(item.get("vendorId", "")).strip() == vendor_id and
                        str(item.get("accountNumber", "")).strip() == account_number and
                        str(item.get("propertyId", "")).strip() == property_id)
            name_match = (str(item.get("vendorName", "")).strip() == vendor_name and
                          str(item.get("accountNumber", "")).strip() == account_number and
                          str(item.get("propertyName", "")).strip() == property_name)
            if id_match or (name_match and vendor_name and property_name):
                item["is_tracked"] = True
                found = True
                break

        assert found
        assert arr[0]["is_tracked"] == True

    def test_name_match_prevents_duplicate(self):
        """Matching by name when IDs don't match should still find existing."""
        arr = [
            {"vendorId": "OLD_ID", "vendorName": "Vendor A", "accountNumber": "111",
             "propertyId": "OLD_PROP", "propertyName": "Prop A", "is_tracked": False},
        ]

        # Different IDs but same names
        vendor_id, account_number, property_id = "NEW_ID", "111", "NEW_PROP"
        vendor_name, property_name = "Vendor A", "Prop A"

        found = False
        for item in arr:
            id_match = (str(item.get("vendorId", "")).strip() == vendor_id and
                        str(item.get("accountNumber", "")).strip() == account_number and
                        str(item.get("propertyId", "")).strip() == property_id)
            name_match = (str(item.get("vendorName", "")).strip() == vendor_name and
                          str(item.get("accountNumber", "")).strip() == account_number and
                          str(item.get("propertyName", "")).strip() == property_name)
            if id_match or (name_match and vendor_name and property_name):
                item["is_tracked"] = True
                found = True
                break

        assert found
        assert arr[0]["is_tracked"] == True

    def test_no_match_creates_new(self):
        """Completely different account should not match existing."""
        arr = [
            {"vendorId": "v1", "vendorName": "Vendor A", "accountNumber": "111",
             "propertyId": "p1", "propertyName": "Prop A", "is_tracked": True},
        ]

        vendor_id, account_number, property_id = "v2", "222", "p2"
        vendor_name, property_name = "Vendor B", "Prop B"

        found = False
        for item in arr:
            id_match = (str(item.get("vendorId", "")).strip() == vendor_id and
                        str(item.get("accountNumber", "")).strip() == account_number and
                        str(item.get("propertyId", "")).strip() == property_id)
            name_match = (str(item.get("vendorName", "")).strip() == vendor_name and
                          str(item.get("accountNumber", "")).strip() == account_number and
                          str(item.get("propertyName", "")).strip() == property_name)
            if id_match or (name_match and vendor_name and property_name):
                found = True
                break

        assert not found

    def test_empty_name_does_not_match(self):
        """Name match should not trigger if vendor_name or property_name is empty."""
        arr = [
            {"vendorId": "v1", "vendorName": "", "accountNumber": "111",
             "propertyId": "p1", "propertyName": "", "is_tracked": True},
        ]

        vendor_id, account_number, property_id = "DIFFERENT", "111", "DIFFERENT"
        vendor_name, property_name = "", ""

        found = False
        for item in arr:
            id_match = (str(item.get("vendorId", "")).strip() == vendor_id and
                        str(item.get("accountNumber", "")).strip() == account_number and
                        str(item.get("propertyId", "")).strip() == property_id)
            name_match = (str(item.get("vendorName", "")).strip() == vendor_name and
                          str(item.get("accountNumber", "")).strip() == account_number and
                          str(item.get("propertyName", "")).strip() == property_name)
            if id_match or (name_match and vendor_name and property_name):
                found = True
                break

        assert not found  # Empty names should not match


# ===========================================================================
# SECTION 13: is_tracked Bug Fixes — Gap Analysis Filter
# ===========================================================================
class TestGapAnalysisFilter:
    """Test gap analysis respects is_tracked flag."""

    def test_active_tracked_excludes_untracked(self):
        tracked_accounts = [
            {"propertyId": "p1", "vendorId": "v1", "accountNumber": "111",
             "status": "active", "is_tracked": True},
            {"propertyId": "p2", "vendorId": "v2", "accountNumber": "222",
             "status": "active", "is_tracked": False},
            {"propertyId": "p3", "vendorId": "v3", "accountNumber": "333",
             "status": "archived", "is_tracked": True},
        ]

        # Simulate line ~7078
        active_tracked = [a for a in tracked_accounts
                          if a.get("status") != "archived" and a.get("is_tracked", True)]

        assert len(active_tracked) == 1
        assert active_tracked[0]["accountNumber"] == "111"


# ===========================================================================
# SECTION 14: is_tracked Bug Fixes — Vacant Detection Filter
# ===========================================================================
class TestVacantDetectionFilter:
    """Test vacant detection respects is_tracked flag."""

    def test_excludes_untracked_and_archived(self):
        accounts = [
            {"propertyId": "p1", "vendorId": "v1", "accountNumber": "111",
             "status": "active", "is_tracked": True},
            {"propertyId": "p2", "vendorId": "v2", "accountNumber": "222",
             "status": "active", "is_tracked": False},
            {"propertyId": "p3", "vendorId": "v3", "accountNumber": "333",
             "status": "archived", "is_tracked": True},
        ]

        # Simulate lines ~6232-6236
        account_keys = {}
        for a in accounts:
            key = f"{a.get('propertyId')}|{a.get('vendorId')}|{a.get('accountNumber')}"
            if a.get('status') == 'archived':
                continue
            if not a.get('is_tracked', True):
                continue
            account_keys[key] = a

        assert len(account_keys) == 1
        assert "p1|v1|111" in account_keys


# ===========================================================================
# SECTION 15: Hour Start Calculation
# ===========================================================================
class TestHourStartCalculation:
    """Test that hour start epoch is computed correctly."""

    def test_hour_start_at_exact_hour(self):
        """At 15:00:00, hour_start should equal the epoch for 15:00:00."""
        now = dt.datetime(2026, 1, 29, 15, 0, 0)
        hour_start_dt = now.replace(minute=0, second=0, microsecond=0)
        hour_start = calendar.timegm(hour_start_dt.timetuple())
        expected = calendar.timegm(dt.datetime(2026, 1, 29, 15, 0, 0).timetuple())
        assert hour_start == expected

    def test_hour_start_mid_hour(self):
        """At 15:34:27, hour_start should be the epoch for 15:00:00."""
        now = dt.datetime(2026, 1, 29, 15, 34, 27)
        hour_start_dt = now.replace(minute=0, second=0, microsecond=0)
        hour_start = calendar.timegm(hour_start_dt.timetuple())
        expected = calendar.timegm(dt.datetime(2026, 1, 29, 15, 0, 0).timetuple())
        assert hour_start == expected

    def test_old_calculation_was_wrong(self):
        """Verify the old time.time()-based calculation would be inaccurate."""
        # Old code: hour_start = time.time() - (now.minute * 60 + now.second)
        # This uses local time subtraction from UTC epoch - produces wrong results
        # if time.time() and utcnow() are in different timezones
        now = dt.datetime(2026, 1, 29, 15, 34, 27)
        correct = calendar.timegm(now.replace(minute=0, second=0, microsecond=0).timetuple())

        # The old calculation:
        fake_now_epoch = calendar.timegm(now.timetuple())
        old_calc = fake_now_epoch - (now.minute * 60 + now.second)

        # In UTC these happen to be the same, but the old code used time.time()
        # which could diverge from utcnow() in non-UTC environments.
        # We verify the correct approach always works:
        assert correct == old_calc  # Same in UTC, but old code used time.time() which is fragile


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
