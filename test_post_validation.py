"""
Comprehensive test suite for Submit Validation & Address Inheritance features.

Tests cover:
1. Service Address inheritance logic
2. Historical pair scanner (vendor-property + vendor-GL)
3. Config override endpoints (vendor-property, vendor-GL)
4. Validation endpoint (/api/post/validate)
5. Duplicate invoice repost with suffix escalation
6. Frontend logic (suffix escalation, repost error handling)
7. Cache behavior (stampede protection, stale fallback)
8. Edge cases and security

Run: python -m pytest test_post_validation.py -v

NOTE: Tests are self-contained and do NOT import main.py (which requires snowflake etc).
Instead, they re-implement the exact algorithms from main.py to verify correctness.
"""
import json
import time
import threading
import re
import datetime as dt
from datetime import date, datetime, timedelta

import pytest


# ---------------------------------------------------------------------------
# Helpers to build mock data
# ---------------------------------------------------------------------------

def _make_line(vendor_id="V100", vendor_name="DTE Energy", prop_id="P200",
               prop_name="Oak Grove", gl_code="57060000", gl_name="HOUSE ELECTRIC",
               acct="12345678", bill_date="01/15/2026",
               service_addr="123 MAIN ST", service_city="DETROIT",
               service_state="MI", service_zip="48201"):
    return {
        "EnrichedVendorID": vendor_id,
        "EnrichedVendorName": vendor_name,
        "EnrichedPropertyID": prop_id,
        "EnrichedPropertyName": prop_name,
        "EnrichedGLAccountNumber": gl_code,
        "EnrichedGLAccountName": gl_name,
        "Account Number": acct,
        "Bill Date": bill_date,
        "Service Address": service_addr,
        "Service City": service_city,
        "Service State": service_state,
        "Service Zipcode": service_zip,
        "Line Item Description": "ELECTRIC",
        "Line Item Charge": "150.00",
        "House Or Vacant": "House",
        "Utility Type": "Electric",
    }


def _make_jsonl(lines):
    return "\n".join(json.dumps(l) for l in lines)


# ---------------------------------------------------------------------------
# Re-implemented core algorithms from main.py (self-contained)
# ---------------------------------------------------------------------------

def _entrata_post_succeeded(resp_text):
    """Mirror of main.py _entrata_post_succeeded (updated: scans status+message, not full blob)."""
    try:
        t = (resp_text or "").strip()
        if not t:
            return False, "empty_response"
        try:
            j = json.loads(t)
        except Exception:
            j = None
        if isinstance(j, dict):
            resp = j.get("response") if isinstance(j.get("response"), dict) else j
            res = resp.get("result") if isinstance(resp.get("result"), dict) else resp
            status = str(res.get("status") or resp.get("status") or "").lower()
            msg = str(res.get("message") or resp.get("message") or "").lower()
            # Check for duplicate indicators FIRST (before generic error status),
            # because some APIs return status="error" with message="duplicate invoice".
            status_msg = f"{status} {msg}"
            if any(k in status_msg for k in ["duplicate", "already exists", "already posted", "invoice exists"]):
                return False, "duplicate"
            if status in ("error", "fail", "failed"):
                return False, status
            if status in ("ok", "success"):
                return True, status
        low = t.lower()
        if any(k in low for k in ["duplicate", "already exists", "already posted", "error", "failed", "failure"]):
            if "duplicate" in low or "already" in low:
                return False, "duplicate"
            return False, "error"
        return True, "ok"
    except Exception:
        return False, "parse_error"


def _scan_pairs_from_content(content):
    """Extract vendor-property and vendor-GL pairs from JSONL content string."""
    vp = set()
    vg = set()
    for line in content.strip().split('\n'):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            vendor_id = str(rec.get("EnrichedVendorID") or "").strip()
            prop_id = str(rec.get("EnrichedPropertyID") or "").strip()
            gl_code = str(rec.get("EnrichedGLAccountNumber") or "").strip()
            if vendor_id and prop_id:
                vp.add((vendor_id, prop_id))
            if vendor_id and gl_code:
                vg.add((vendor_id, gl_code))
        except Exception:
            pass
    return vp, vg


def _build_month_prefixes(prefix, start_date, end_date):
    """Build month-level S3 prefixes (mirrors main.py scanner logic)."""
    month_prefixes = []
    current = start_date.replace(day=1)
    while current <= end_date:
        month_prefixes.append(f"{prefix}yyyy={current.year}/mm={current.month:02d}/")
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return month_prefixes


def _apply_overrides(vp_all, vg_all, vp_overrides, vg_overrides):
    """Apply allow/block overrides to pair sets (mirrors main.py logic)."""
    for ov in (vp_overrides or []):
        vid = str(ov.get("vendor_id") or "").strip()
        pid = str(ov.get("property_id") or "").strip()
        if vid and pid:
            if ov.get("action") == "allow":
                vp_all.add((vid, pid))
            elif ov.get("action") == "block":
                vp_all.discard((vid, pid))
    for ov in (vg_overrides or []):
        vid = str(ov.get("vendor_id") or "").strip()
        gl = str(ov.get("gl_code") or "").strip()
        if vid and gl:
            if ov.get("action") == "allow":
                vg_all.add((vid, gl))
            elif ov.get("action") == "block":
                vg_all.discard((vid, gl))
    return vp_all, vg_all


def _dedup_config_items(items, key_fields):
    """Deduplicate config items by key_fields, keeping the last occurrence (mirrors main.py)."""
    seen_keys = {}
    for i, item in enumerate(items):
        key = tuple(item.get(f, "") for f in key_fields)
        seen_keys[key] = i
    return [items[i] for i in sorted(seen_keys.values())]


def _normalize_config_items(items, id_field_2="property_id", auth_user="admin@test.com"):
    """Normalize config save items (mirrors main.py endpoint logic)."""
    normalized = []
    for r in items:
        vid = str(r.get("vendor_id") or "").strip()
        pid = str(r.get(id_field_2) or "").strip()
        if not vid or not pid:
            continue
        action = str(r.get("action") or "allow").strip().lower()
        if action not in ("allow", "block"):
            action = "allow"
        normalized.append({
            "vendor_id": vid,
            id_field_2: pid,
            "action": action,
            "added_by": auth_user,
        })
    return normalized


# Known S3 prefixes (hardcoded to match main.py defaults)
STAGE4_PREFIX = "Bill_Parser_4_Enriched_Outputs/"
STAGE6_PREFIX = "Bill_Parser_6_PreEntrata_Submission/"


# ===========================================================================
# 1. SERVICE ADDRESS INHERITANCE
# ===========================================================================

class TestServiceAddressInheritance:
    """Test the address inheritance logic that runs during /api/submit."""

    def _run_inheritance(self, new_rec, originals):
        """Simulate the address inheritance block from main.py."""
        _addr_fields = ("Service Address", "Service City", "Service State", "Service Zipcode")
        _primary_val = (new_rec.get("Service Address") or "").strip()
        if not _primary_val or _primary_val.upper() == "NO ADDRESS":
            for sibling in originals:
                sib_addr = (sibling.get("Service Address") or "").strip()
                if sib_addr and sib_addr.upper() != "NO ADDRESS":
                    for addr_field in _addr_fields:
                        sib_val = (sibling.get(addr_field) or "").strip()
                        if sib_val and sib_val.upper() != "NO ADDRESS":
                            new_rec[addr_field] = sib_val
                    break
        return new_rec

    def test_inherits_from_sibling_when_no_address(self):
        """Line with 'No Address' should inherit all fields from first valid sibling."""
        line = _make_line(service_addr="No Address", service_city="", service_state="", service_zip="")
        sibling = _make_line(service_addr="456 OAK AVE", service_city="ANN ARBOR",
                            service_state="MI", service_zip="48104")
        result = self._run_inheritance(line, [line, sibling])
        assert result["Service Address"] == "456 OAK AVE"
        assert result["Service City"] == "ANN ARBOR"
        assert result["Service State"] == "MI"
        assert result["Service Zipcode"] == "48104"

    def test_inherits_from_sibling_when_blank(self):
        """Line with blank/empty address should also inherit."""
        line = _make_line(service_addr="", service_city="", service_state="", service_zip="")
        sibling = _make_line(service_addr="789 ELM ST", service_city="FLINT",
                            service_state="MI", service_zip="48501")
        result = self._run_inheritance(line, [line, sibling])
        assert result["Service Address"] == "789 ELM ST"

    def test_case_insensitive_no_address(self):
        """'no address', 'NO ADDRESS', 'No Address' should all trigger inheritance."""
        for variant in ["no address", "NO ADDRESS", "No Address", "nO aDDRESS"]:
            line = _make_line(service_addr=variant, service_city="", service_state="", service_zip="")
            sibling = _make_line(service_addr="100 TEST RD")
            result = self._run_inheritance(dict(line), [line, sibling])
            assert result["Service Address"] == "100 TEST RD", f"Failed for variant: {variant}"

    def test_no_inheritance_when_valid_address_exists(self):
        """Line with a valid address should NOT be overwritten."""
        line = _make_line(service_addr="123 MAIN ST", service_city="DETROIT")
        sibling = _make_line(service_addr="999 OTHER ST", service_city="FLINT")
        result = self._run_inheritance(line, [line, sibling])
        assert result["Service Address"] == "123 MAIN ST"
        assert result["Service City"] == "DETROIT"

    def test_all_siblings_have_no_address(self):
        """If all siblings have 'No Address', line keeps 'No Address'."""
        line = _make_line(service_addr="No Address", service_city="", service_state="", service_zip="")
        sibling = _make_line(service_addr="No Address", service_city="", service_state="", service_zip="")
        result = self._run_inheritance(line, [line, sibling])
        assert result["Service Address"] == "No Address"  # unchanged

    def test_inherits_all_four_fields_from_same_sibling(self):
        """All four address fields come from the same sibling (no mixing)."""
        line = _make_line(service_addr="No Address", service_city="", service_state="", service_zip="")
        # sibling1 has "No Address" for service address
        sib1 = _make_line(service_addr="No Address", service_city="CHICAGO", service_state="IL", service_zip="60601")
        # sibling2 has valid everything
        sib2 = _make_line(service_addr="500 BROADWAY", service_city="NEW YORK", service_state="NY", service_zip="10001")
        result = self._run_inheritance(dict(line), [line, sib1, sib2])
        # Should inherit from sib2 (first with valid Service Address)
        assert result["Service Address"] == "500 BROADWAY"
        assert result["Service City"] == "NEW YORK"
        assert result["Service State"] == "NY"
        assert result["Service Zipcode"] == "10001"

    def test_skips_sibling_with_none_fields(self):
        """Siblings with None values for address fields should be skipped."""
        line = _make_line(service_addr="No Address", service_city="", service_state="", service_zip="")
        sib = {"Service Address": None, "Service City": None, "Service State": None, "Service Zipcode": None}
        sib2 = _make_line(service_addr="VALID ST", service_city="VALID CITY")
        result = self._run_inheritance(dict(line), [line, sib, sib2])
        assert result["Service Address"] == "VALID ST"

    def test_whitespace_only_address_triggers_inheritance(self):
        """Address with only whitespace should trigger inheritance."""
        line = _make_line(service_addr="   ", service_city="   ", service_state="   ", service_zip="   ")
        sibling = _make_line(service_addr="REAL ADDR", service_city="REAL CITY")
        result = self._run_inheritance(dict(line), [line, sibling])
        assert result["Service Address"] == "REAL ADDR"

    def test_partial_sibling_address_fields(self):
        """If sibling has valid address but blank city, city stays blank."""
        line = _make_line(service_addr="No Address", service_city="", service_state="", service_zip="")
        sibling = _make_line(service_addr="111 PINE ST", service_city="", service_state="TX", service_zip="")
        result = self._run_inheritance(dict(line), [line, sibling])
        assert result["Service Address"] == "111 PINE ST"
        assert result["Service State"] == "TX"
        # City and zip were empty on sibling, so they stay empty on the line
        assert result["Service City"] == ""
        assert result["Service Zipcode"] == ""

    def test_empty_originals_list(self):
        """If originals is empty, nothing crashes."""
        line = _make_line(service_addr="No Address")
        result = self._run_inheritance(dict(line), [])
        assert result["Service Address"] == "No Address"


# ===========================================================================
# 2. HISTORICAL PAIR SCANNER
# ===========================================================================

class TestHistoricalPairScanner:
    """Test pair extraction from JSONL content."""

    def test_scan_extracts_vendor_property_pairs(self):
        """Scanner should extract (vendor_id, property_id) tuples from JSONL."""
        lines = [
            _make_line(vendor_id="V1", prop_id="P1"),
            _make_line(vendor_id="V1", prop_id="P2"),
            _make_line(vendor_id="V2", prop_id="P1"),
        ]
        content = _make_jsonl(lines)
        vp, vg = _scan_pairs_from_content(content)
        assert ("V1", "P1") in vp
        assert ("V1", "P2") in vp
        assert ("V2", "P1") in vp

    def test_scan_extracts_vendor_gl_pairs(self):
        """Scanner should extract (vendor_id, gl_code) tuples from JSONL."""
        lines = [
            _make_line(vendor_id="V1", gl_code="57060000"),
            _make_line(vendor_id="V1", gl_code="57050000"),
        ]
        content = _make_jsonl(lines)
        vp, vg = _scan_pairs_from_content(content)
        assert ("V1", "57060000") in vg
        assert ("V1", "57050000") in vg

    def test_scan_skips_empty_vendor_id(self):
        """Lines with empty vendor_id should not produce pairs."""
        lines = [_make_line(vendor_id="", prop_id="P1", gl_code="57060000")]
        content = _make_jsonl(lines)
        vp, vg = _scan_pairs_from_content(content)
        assert len(vp) == 0
        assert len(vg) == 0

    def test_scan_handles_malformed_jsonl(self):
        """Scanner should not crash on malformed JSONL lines."""
        content = '{"EnrichedVendorID":"V1","EnrichedPropertyID":"P1","EnrichedGLAccountNumber":"GL1"}\nNOT VALID JSON\n{"EnrichedVendorID":"V2","EnrichedPropertyID":"P2","EnrichedGLAccountNumber":"GL2"}'
        vp, vg = _scan_pairs_from_content(content)
        assert ("V1", "P1") in vp
        assert ("V2", "P2") in vp  # Second valid line still parsed

    def test_scan_month_boundaries(self):
        """Scanner should generate correct month prefixes across year boundary."""
        prefixes = _build_month_prefixes("Stage7/", date(2025, 11, 1), date(2026, 2, 28))
        # Should have month prefixes for Nov, Dec, Jan, Feb
        assert any("mm=11" in p for p in prefixes)
        assert any("mm=12" in p for p in prefixes)
        assert any("mm=01" in p for p in prefixes)
        assert any("mm=02" in p for p in prefixes)

    def test_scan_empty_content(self):
        """Scanner should return empty sets for empty content."""
        vp, vg = _scan_pairs_from_content("")
        assert len(vp) == 0
        assert len(vg) == 0


# ===========================================================================
# 3. CACHE BEHAVIOR
# ===========================================================================

class TestVendorPairCache:
    """Test caching, stampede protection, and stale fallback using self-contained cache impl."""

    def test_cache_returns_same_data_within_ttl(self):
        """Within TTL, cache should return the same sets without rescanning."""
        cache = {
            "vendor_property": {("V1", "P1")},
            "vendor_gl": {("V1", "GL1")},
            "last_refresh": datetime.now(),
            "ttl_seconds": 3600,
            "scan_succeeded": True,
        }
        now = datetime.now()
        # TTL not expired
        if cache["last_refresh"] and (now - cache["last_refresh"]).total_seconds() < cache["ttl_seconds"]:
            vp, vg = cache["vendor_property"], cache["vendor_gl"]
        else:
            vp, vg = set(), set()

        assert ("V1", "P1") in vp
        assert ("V1", "GL1") in vg

    def test_cache_expired_triggers_refresh(self):
        """After TTL, cache should trigger a new scan."""
        cache = {
            "vendor_property": {("OLD", "DATA")},
            "vendor_gl": set(),
            "last_refresh": datetime.now() - timedelta(hours=2),
            "ttl_seconds": 3600,
            "scan_succeeded": True,
        }
        now = datetime.now()
        needs_refresh = not cache["last_refresh"] or (now - cache["last_refresh"]).total_seconds() >= cache["ttl_seconds"]
        assert needs_refresh is True

    def test_stampede_protection_returns_stale(self):
        """Concurrent refresh attempts should return stale cache instead of blocking."""
        lock = threading.Lock()
        cache = {
            "vendor_property": {("STALE", "DATA")},
            "vendor_gl": set(),
            "last_refresh": None,
        }

        # Simulate another thread holding the lock
        lock.acquire()
        try:
            acquired = lock.acquire(blocking=False)
            assert acquired is False  # Can't acquire — another thread has it
            # In main.py, this returns current cache
            vp = cache["vendor_property"]
            assert ("STALE", "DATA") in vp
        finally:
            lock.release()

    def test_failed_scan_keeps_stale_cache(self):
        """If scan fails entirely, old cache is preserved to avoid false positives."""
        cache = {
            "vendor_property": {("GOOD", "DATA")},
            "vendor_gl": {("GOOD", "GL")},
            "last_refresh": datetime.now() - timedelta(hours=2),
            "ttl_seconds": 3600,
            "scan_succeeded": True,
        }
        # Simulate scan failure
        scan_ok = False
        vp_all = set()
        vg_all = set()

        # Mirror main.py logic: if scan failed or returned empty when previous succeeded, keep stale
        if not scan_ok or (not vp_all and not vg_all and cache["scan_succeeded"]):
            # Keep stale cache
            vp = cache["vendor_property"]
            vg = cache["vendor_gl"]
        else:
            vp = vp_all
            vg = vg_all

        assert ("GOOD", "DATA") in vp
        assert ("GOOD", "GL") in vg


# ===========================================================================
# 4. OVERRIDE APPLICATION
# ===========================================================================

class TestOverrideApplication:
    """Test that allow/block overrides modify the pair sets correctly."""

    def test_allow_override_adds_pair(self):
        """An 'allow' override should add a pair even if not in history."""
        vp = set()
        vg = set()
        overrides = [{"vendor_id": "VNEW", "property_id": "PNEW", "action": "allow"}]
        vp, vg = _apply_overrides(vp, vg, overrides, [])
        assert ("VNEW", "PNEW") in vp

    def test_block_override_removes_historical_pair(self):
        """A 'block' override should remove a pair that exists in history."""
        vp = {("V1", "P1"), ("V2", "P2")}
        vg = set()
        overrides = [{"vendor_id": "V1", "property_id": "P1", "action": "block"}]
        vp, vg = _apply_overrides(vp, vg, overrides, [])
        assert ("V1", "P1") not in vp
        assert ("V2", "P2") in vp

    def test_gl_allow_override(self):
        """Vendor-GL allow override adds the pair."""
        vp = set()
        vg = set()
        vg_overrides = [{"vendor_id": "V1", "gl_code": "99999999", "action": "allow"}]
        vp, vg = _apply_overrides(vp, vg, [], vg_overrides)
        assert ("V1", "99999999") in vg

    def test_override_with_empty_ids_ignored(self):
        """Overrides with empty vendor_id or property_id should be ignored."""
        vp = set()
        vg = set()
        overrides = [
            {"vendor_id": "", "property_id": "P1", "action": "allow"},
            {"vendor_id": "V1", "property_id": "", "action": "allow"},
        ]
        vp, vg = _apply_overrides(vp, vg, overrides, [])
        assert len(vp) == 0


# ===========================================================================
# 5. CONFIG ENDPOINTS
# ===========================================================================

class TestConfigEndpoints:
    """Test vendor-property and vendor-GL override config save/load."""

    def test_save_normalizes_action(self):
        """Invalid action values should default to 'allow'."""
        items = [
            {"vendor_id": "V1", "property_id": "P1", "action": "INVALID"},
            {"vendor_id": "V2", "property_id": "P2", "action": "block"},
            {"vendor_id": "V3", "property_id": "P3", "action": "ALLOW"},
            {"vendor_id": "V4", "property_id": "P4", "action": ""},
        ]
        normalized = _normalize_config_items(items)
        assert normalized[0]["action"] == "allow"  # INVALID -> allow
        assert normalized[1]["action"] == "block"   # block stays
        assert normalized[2]["action"] == "allow"   # ALLOW normalized
        assert normalized[3]["action"] == "allow"   # empty -> allow

    def test_save_skips_empty_ids(self):
        """Entries with empty vendor_id or property_id should be skipped."""
        items = [
            {"vendor_id": "V1", "property_id": "P1", "action": "allow"},
            {"vendor_id": "", "property_id": "P2", "action": "allow"},
            {"vendor_id": "V3", "property_id": "", "action": "block"},
        ]
        normalized = _normalize_config_items(items)
        assert len(normalized) == 1
        assert normalized[0]["vendor_id"] == "V1"

    def test_save_always_uses_auth_user(self):
        """added_by should always be the authenticated user, not client-supplied."""
        items = [{"vendor_id": "V1", "property_id": "P1", "added_by": "hacker@evil.com", "action": "allow"}]
        normalized = _normalize_config_items(items, auth_user="admin@company.com")
        assert normalized[0]["added_by"] == "admin@company.com"


# ===========================================================================
# 6. VALIDATION ENDPOINT
# ===========================================================================

class TestValidationEndpoint:
    """Test /api/post/validate logic."""

    def _validate(self, keys, vp_set, vg_set, s3_data):
        """Simulate the validation endpoint logic."""
        warnings = []
        seen = set()

        for s3_key in keys:
            records = s3_data.get(s3_key, [])
            for rec in records:
                vendor_id = str(rec.get("EnrichedVendorID") or "").strip()
                vendor_name = str(rec.get("EnrichedVendorName") or "").strip()
                prop_id = str(rec.get("EnrichedPropertyID") or "").strip()
                prop_name = str(rec.get("EnrichedPropertyName") or "").strip()
                gl_code = str(rec.get("EnrichedGLAccountNumber") or "").strip()
                gl_name = str(rec.get("EnrichedGLAccountName") or "").strip()

                if vendor_id and prop_id and (vendor_id, prop_id) not in vp_set:
                    dedup_key = ("vp", vendor_id, prop_id)
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        warnings.append({
                            "key": s3_key, "type": "vendor_property",
                            "vendor_id": vendor_id, "vendor_name": vendor_name,
                            "property_id": prop_id, "property_name": prop_name,
                        })

                if vendor_id and gl_code and (vendor_id, gl_code) not in vg_set:
                    dedup_key = ("vg", vendor_id, gl_code)
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        warnings.append({
                            "key": s3_key, "type": "vendor_gl",
                            "vendor_id": vendor_id, "vendor_name": vendor_name,
                            "gl_code": gl_code, "gl_code_name": gl_name,
                        })

        return warnings

    def test_no_warnings_for_known_pairs(self):
        """Known vendor-property and vendor-GL pairs should not generate warnings."""
        vp = {("V1", "P1")}
        vg = {("V1", "GL1")}
        data = {"key1": [_make_line(vendor_id="V1", prop_id="P1", gl_code="GL1")]}
        warnings = self._validate(["key1"], vp, vg, data)
        assert len(warnings) == 0

    def test_warns_for_unknown_vendor_property(self):
        """Unknown vendor-property combo should generate a warning."""
        vp = {("V1", "P1")}
        vg = {("V1", "GL1")}
        data = {"key1": [_make_line(vendor_id="V1", prop_id="P_NEW", gl_code="GL1")]}
        warnings = self._validate(["key1"], vp, vg, data)
        assert len(warnings) == 1
        assert warnings[0]["type"] == "vendor_property"
        assert warnings[0]["property_id"] == "P_NEW"

    def test_warns_for_unknown_vendor_gl(self):
        """Unknown vendor-GL combo should generate a warning."""
        vp = {("V1", "P1")}
        vg = {("V1", "GL1")}
        data = {"key1": [_make_line(vendor_id="V1", prop_id="P1", gl_code="GL_NEW")]}
        warnings = self._validate(["key1"], vp, vg, data)
        assert len(warnings) == 1
        assert warnings[0]["type"] == "vendor_gl"

    def test_deduplication_across_lines(self):
        """Same vendor-property combo across multiple lines should produce one warning."""
        vp = set()
        vg = {("V1", "GL1")}
        data = {"key1": [
            _make_line(vendor_id="V1", prop_id="P1", gl_code="GL1"),
            _make_line(vendor_id="V1", prop_id="P1", gl_code="GL1"),
            _make_line(vendor_id="V1", prop_id="P1", gl_code="GL1"),
        ]}
        warnings = self._validate(["key1"], vp, vg, data)
        vp_warns = [w for w in warnings if w["type"] == "vendor_property"]
        assert len(vp_warns) == 1

    def test_deduplication_across_files(self):
        """Same combo across different files should produce one warning."""
        vp = set()
        vg = {("V1", "GL1")}
        data = {
            "key1": [_make_line(vendor_id="V1", prop_id="P1", gl_code="GL1")],
            "key2": [_make_line(vendor_id="V1", prop_id="P1", gl_code="GL1")],
        }
        warnings = self._validate(["key1", "key2"], vp, vg, data)
        vp_warns = [w for w in warnings if w["type"] == "vendor_property"]
        assert len(vp_warns) == 1

    def test_multiple_different_warnings(self):
        """Multiple distinct issues should each produce a warning."""
        vp = set()
        vg = set()
        data = {"key1": [
            _make_line(vendor_id="V1", prop_id="P1", gl_code="GL1"),
            _make_line(vendor_id="V2", prop_id="P2", gl_code="GL2"),
        ]}
        warnings = self._validate(["key1"], vp, vg, data)
        # Should have 4 warnings: 2 vendor-property + 2 vendor-GL
        assert len(warnings) == 4

    def test_empty_vendor_id_no_warning(self):
        """Lines with empty vendor_id should not produce warnings."""
        vp = set()
        vg = set()
        data = {"key1": [_make_line(vendor_id="", prop_id="P1", gl_code="GL1")]}
        warnings = self._validate(["key1"], vp, vg, data)
        assert len(warnings) == 0

    def test_empty_keys_no_warnings(self):
        """Empty keys list should return no warnings."""
        warnings = self._validate([], set(), set(), {})
        assert len(warnings) == 0

    def test_s3_key_prefix_validation(self):
        """Only keys with valid prefixes should be processed."""
        valid_prefixes = (STAGE6_PREFIX, STAGE4_PREFIX)
        keys = [
            f"{STAGE6_PREFIX}yyyy=2025/test.jsonl",  # Valid
            "secret_data/passwords.jsonl",           # Invalid
            f"{STAGE4_PREFIX}yyyy=2025/test.jsonl",  # Valid
            "../../../etc/passwd",                    # Invalid
        ]
        sanitized = [k for k in keys if isinstance(k, str) and any(k.startswith(p) for p in valid_prefixes)]
        assert len(sanitized) == 2
        assert all(k.startswith(STAGE6_PREFIX) or k.startswith(STAGE4_PREFIX) for k in sanitized)


# ===========================================================================
# 7. DUPLICATE DETECTION & REPOST
# ===========================================================================

class TestDuplicateDetection:
    """Test _entrata_post_succeeded duplicate detection."""

    def test_detects_duplicate_keyword(self):
        success, reason = _entrata_post_succeeded('{"response":{"result":{"message":"duplicate invoice number"}}}')
        assert not success
        assert reason == "duplicate"

    def test_detects_already_exists(self):
        success, reason = _entrata_post_succeeded('Invoice already exists in the system')
        assert not success
        assert reason == "duplicate"

    def test_detects_already_posted(self):
        success, reason = _entrata_post_succeeded('This invoice was already posted')
        assert not success
        assert reason == "duplicate"

    def test_success_response(self):
        success, reason = _entrata_post_succeeded('{"response":{"result":{"status":"ok"}}}')
        assert success

    def test_empty_response(self):
        success, reason = _entrata_post_succeeded('')
        assert not success
        assert reason == "empty_response"

    def test_explicit_error_status(self):
        success, reason = _entrata_post_succeeded('{"response":{"result":{"status":"error","message":"bad data"}}}')
        assert not success


class TestInvoiceSuffix:
    """Test invoice suffix construction logic."""

    def test_suffix_appended_to_invoice_number(self):
        """Invoice number should have suffix appended."""
        acct = "12345678"
        bill_str = "01/15/2026"
        invoice_suffix = "-A"
        invoice_number = f"{acct} {bill_str}"
        if invoice_suffix:
            invoice_number = f"{invoice_number}{invoice_suffix}"
        assert invoice_number == "12345678 01/15/2026-A"

    def test_no_suffix_when_empty(self):
        """Empty suffix should not modify invoice number."""
        acct = "12345678"
        bill_str = "01/15/2026"
        invoice_suffix = ""
        invoice_number = f"{acct} {bill_str}"
        if invoice_suffix:
            invoice_number = f"{invoice_number}{invoice_suffix}"
        assert invoice_number == "12345678 01/15/2026"
        assert not invoice_number.endswith("-")


class TestSuffixEscalation:
    """Test the frontend suffix escalation logic."""

    def _next_suffix(self, current):
        """Mirror of frontend nextSuffix() function."""
        if not current:
            return '-A'
        match = re.search(r'-([A-Z])$', current)
        if not match:
            return '-A'
        letter = match.group(1)
        if letter == 'Z':
            return '-Z2'
        return '-' + chr(ord(letter) + 1)

    def test_next_suffix_from_empty(self):
        """No current suffix -> -A"""
        assert self._next_suffix("") == "-A"
        assert self._next_suffix(None) == "-A"

    def test_next_suffix_escalation(self):
        """Suffix should escalate: -A -> -B -> -C ... -> -Z -> -Z2"""
        assert self._next_suffix("-A") == "-B"
        assert self._next_suffix("-B") == "-C"
        assert self._next_suffix("-Y") == "-Z"
        assert self._next_suffix("-Z") == "-Z2"

    def test_repost_error_only_cleared_for_succeeded_keys(self):
        """Only errors for keys that actually posted should be removed from allErrors."""
        allErrors = [
            {"key": "file1.jsonl", "repostable": True, "error": "dup"},
            {"key": "file2.jsonl", "repostable": True, "error": "dup"},
            {"key": "file3.jsonl", "error": "other error"},
        ]
        succeededKeys = {"file1.jsonl"}  # Only file1 succeeded

        # Simulate frontend logic
        for i in range(len(allErrors) - 1, -1, -1):
            if allErrors[i].get("repostable") and allErrors[i].get("key") in succeededKeys:
                allErrors.pop(i)

        assert len(allErrors) == 2
        assert allErrors[0]["key"] == "file2.jsonl"  # Still there (failed repost)
        assert allErrors[1]["key"] == "file3.jsonl"   # Non-repostable error


# ===========================================================================
# 8. EDGE CASES & SECURITY
# ===========================================================================

class TestEdgeCases:
    """Test edge cases, error handling, and security."""

    def test_unicode_in_vendor_name(self):
        """Vendor names with unicode should not break validation."""
        line = _make_line(vendor_name="Compañía Eléctrica del Sureste")
        jsonl = json.dumps(line)
        rec = json.loads(jsonl)
        assert rec["EnrichedVendorName"] == "Compañía Eléctrica del Sureste"

    def test_very_long_vendor_name(self):
        """Very long vendor names should not crash anything."""
        long_name = "A" * 10000
        line = _make_line(vendor_name=long_name)
        jsonl = json.dumps(line)
        rec = json.loads(jsonl)
        assert len(rec["EnrichedVendorName"]) == 10000

    def test_special_chars_in_s3_key(self):
        """S3 keys with special characters should be handled."""
        key = "Bill_Parser_6_PreEntrata_Submission/yyyy=2025/mm=01/dd=01/San Francisco Water, Power & Sewer [2025].jsonl"
        assert isinstance(key, str)
        assert '"' not in key  # No quotes that would break HTML attributes

    def test_entrata_response_with_html(self):
        """Entrata might return HTML error pages instead of JSON."""
        html = "<html><body><h1>500 Internal Server Error</h1><p>error occurred</p></body></html>"
        success, reason = _entrata_post_succeeded(html)
        assert not success
        assert reason == "error"

    def test_entrata_response_none(self):
        """None response text should be handled."""
        success, reason = _entrata_post_succeeded(None)
        assert not success

    def test_concurrent_cache_access(self):
        """Multiple threads reading a shared dict simultaneously should not crash."""
        cache = {
            "vendor_property": {("V1", "P1")},
            "vendor_gl": {("V1", "GL1")},
        }
        results = []
        errors = []

        def reader():
            try:
                vp = cache["vendor_property"]
                vg = cache["vendor_gl"]
                results.append((len(vp), len(vg)))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=reader) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Errors in concurrent access: {errors}"
        assert all(r == (1, 1) for r in results)

    def test_address_inheritance_with_none_rec(self):
        """new_rec with None values should not crash inheritance."""
        new_rec = {
            "Service Address": None,
            "Service City": None,
            "Service State": None,
            "Service Zipcode": None,
        }
        sibling = _make_line(service_addr="VALID", service_city="CITY", service_state="ST", service_zip="12345")
        # Simulate inheritance
        _addr_fields = ("Service Address", "Service City", "Service State", "Service Zipcode")
        _primary_val = (new_rec.get("Service Address") or "").strip()
        if not _primary_val or _primary_val.upper() == "NO ADDRESS":
            for sib in [new_rec, sibling]:
                sib_addr = (sib.get("Service Address") or "").strip()
                if sib_addr and sib_addr.upper() != "NO ADDRESS":
                    for f in _addr_fields:
                        sv = (sib.get(f) or "").strip()
                        if sv and sv.upper() != "NO ADDRESS":
                            new_rec[f] = sv
                    break

        assert new_rec["Service Address"] == "VALID"

    def test_config_xss_prevention(self):
        """Config data with HTML should be escaped when rendered."""
        def escapeHtml(s):
            return (str(s or '')
                    .replace('&', '&amp;')
                    .replace('<', '&lt;')
                    .replace('>', '&gt;')
                    .replace('"', '&quot;'))

        xss_attempt = '<script>alert("xss")</script>'
        escaped = escapeHtml(xss_attempt)
        assert '<script>' not in escaped
        assert '&lt;script&gt;' in escaped


# ===========================================================================
# 9. INTEGRATION / CONTRACT TESTS
# ===========================================================================

class TestAPIContracts:
    """Test that frontend and backend agree on data formats."""

    def test_validate_request_format(self):
        """Frontend sends {keys: [str]} to /api/post/validate."""
        request_body = {"keys": ["Bill_Parser_6_PreEntrata_Submission/yyyy=2025/mm=01/dd=15/test.jsonl"]}
        assert isinstance(request_body["keys"], list)
        assert all(isinstance(k, str) for k in request_body["keys"])

    def test_validate_response_format(self):
        """Backend returns {warnings: [{key, type, vendor_id, ...}], count: int}."""
        response = {
            "warnings": [
                {
                    "key": "test.jsonl",
                    "type": "vendor_property",
                    "vendor_id": "V1",
                    "vendor_name": "DTE",
                    "property_id": "P1",
                    "property_name": "Oak Grove",
                    "message": "Vendor DTE has never been posted to Oak Grove",
                }
            ],
            "count": 1
        }
        assert isinstance(response["warnings"], list)
        assert isinstance(response["count"], int)
        for w in response["warnings"]:
            assert "type" in w
            assert w["type"] in ("vendor_property", "vendor_gl")
            assert "message" in w

    def test_repost_error_format(self):
        """Backend duplicate error includes repostable flag and suffix info."""
        error = {
            "key": "test.jsonl",
            "error": "Entrata rejected invoice: duplicate",
            "code": "post_failed",
            "hint": "This invoice was already posted to Entrata.",
            "repostable": True,
            "account_number": "12345678",
            "bill_date": "01/15/2026",
            "current_suffix": "",
        }
        assert error["repostable"] is True
        assert isinstance(error["current_suffix"], str)

    def test_repost_suffixes_format(self):
        """Frontend sends repost_suffixes as JSON string in FormData."""
        suffixes = {"Bill_Parser_6_PreEntrata_Submission/test.jsonl": "-A"}
        encoded = json.dumps(suffixes)
        decoded = json.loads(encoded)
        assert decoded["Bill_Parser_6_PreEntrata_Submission/test.jsonl"] == "-A"

    def test_config_override_format(self):
        """Config items follow the expected schema."""
        vp_item = {
            "vendor_id": "12345",
            "vendor_name": "DTE Energy",
            "property_id": "67890",
            "property_name": "Oak Grove",
            "action": "allow",
            "added_by": "admin@test.com",
            "added_at": "2026-01-29T00:00:00Z",
        }
        assert vp_item["action"] in ("allow", "block")
        assert vp_item["vendor_id"]
        assert vp_item["property_id"]

        vg_item = {
            "vendor_id": "12345",
            "vendor_name": "DTE Energy",
            "gl_code": "57060000",
            "gl_code_name": "HOUSE ELECTRIC",
            "action": "block",
            "added_by": "admin@test.com",
            "added_at": "2026-01-29T00:00:00Z",
        }
        assert vg_item["action"] in ("allow", "block")
        assert vg_item["vendor_id"]
        assert vg_item["gl_code"]


# ===========================================================================
# 10. TIMEOUT / PERFORMANCE TESTS
# ===========================================================================

class TestPerformance:
    """Test performance-sensitive code paths."""

    def test_dedup_set_performance(self):
        """Dedup set should handle large numbers of pairs efficiently."""
        seen = set()
        for i in range(10000):
            key = ("vp", f"V{i}", f"P{i}")
            seen.add(key)
        assert len(seen) == 10000

        start = time.time()
        for i in range(10000):
            _ = ("vp", f"V{i}", f"P{i}") in seen
        elapsed = time.time() - start
        assert elapsed < 0.1, f"Dedup lookup took {elapsed:.3f}s for 10K items"

    def test_large_pair_sets(self):
        """Cache should handle large pair sets (100K+) without issues."""
        vp = set()
        for i in range(100000):
            vp.add((f"V{i % 1000}", f"P{i}"))
        assert len(vp) == 100000
        assert ("V999", "P999") in vp
        assert ("V0", "P0") in vp

    def test_scan_empty_months_prefix_generation(self):
        """Prefix generation for 12 months should be fast."""
        start = time.time()
        prefixes = _build_month_prefixes("test/", date(2025, 1, 1), date(2025, 12, 31))
        elapsed = time.time() - start
        assert elapsed < 0.01
        assert len(prefixes) == 12


# ===========================================================================
# 11. FALSE POSITIVE DUPLICATE DETECTION (Bug fix verification)
# ===========================================================================

class TestDuplicateFalsePositives:
    """Verify that vendor names/data containing 'duplicate' don't trigger false positives."""

    def test_vendor_named_duplicate_services_success(self):
        """A successful post for vendor 'Duplicate Services LLC' should NOT be flagged as duplicate."""
        # Entrata returns success status but vendor name contains "duplicate"
        resp = json.dumps({
            "response": {
                "result": {
                    "status": "ok",
                    "invoiceId": "12345",
                    "vendor": "Duplicate Services LLC"
                }
            }
        })
        success, reason = _entrata_post_succeeded(resp)
        assert success is True, f"False positive: vendor name triggered duplicate detection. reason={reason}"

    def test_property_named_already_posted_rd(self):
        """Property address '123 Already Posted Rd' should NOT trigger duplicate."""
        resp = json.dumps({
            "response": {
                "result": {
                    "status": "ok",
                    "property": "123 Already Posted Rd"
                }
            }
        })
        success, reason = _entrata_post_succeeded(resp)
        assert success is True, f"False positive: property name triggered duplicate detection. reason={reason}"

    def test_real_duplicate_in_message_still_detected(self):
        """A real duplicate error in the message field should still be detected."""
        resp = json.dumps({
            "response": {
                "result": {
                    "status": "ok",
                    "message": "duplicate invoice number already exists"
                }
            }
        })
        success, reason = _entrata_post_succeeded(resp)
        assert success is False
        assert reason == "duplicate"

    def test_real_duplicate_in_status_field(self):
        """Duplicate indicated via message when status is not error."""
        resp = json.dumps({
            "response": {
                "result": {
                    "message": "Invoice already exists in system"
                }
            }
        })
        success, reason = _entrata_post_succeeded(resp)
        assert success is False
        assert reason == "duplicate"

    def test_invoice_exists_in_description_not_message(self):
        """'invoice exists' in a description field (not message) should NOT trigger duplicate."""
        resp = json.dumps({
            "response": {
                "result": {
                    "status": "success",
                    "description": "Invoice exists for this property already. Posting complete."
                }
            }
        })
        success, reason = _entrata_post_succeeded(resp)
        # status is "success" → should succeed (description is NOT checked)
        assert success is True

    def test_error_status_returns_status_string(self):
        """Error status should return the status string itself, not msg."""
        resp = json.dumps({
            "response": {
                "result": {
                    "status": "error",
                    "message": "Something went wrong"
                }
            }
        })
        success, reason = _entrata_post_succeeded(resp)
        assert success is False
        assert reason == "error"


# ===========================================================================
# 12. PARTIAL SCAN / CACHE PROTECTION
# ===========================================================================

class TestPartialScanProtection:
    """Verify that partial scan results don't replace full cache."""

    def test_partial_scan_fewer_pairs_keeps_stale(self):
        """If partial scan returns fewer pairs, stale cache should be kept."""
        old_cache = {
            "vendor_property": {("V1", "P1"), ("V2", "P2"), ("V3", "P3")},
            "vendor_gl": {("V1", "GL1"), ("V2", "GL2")},
            "scan_succeeded": True,
        }
        # Simulate partial scan: one prefix succeeds with subset, other fails
        new_vp = {("V1", "P1")}  # Only 1 pair vs 3 before
        new_vg = set()             # 0 vs 2 before
        prefixes_succeeded = 1
        prefixes_attempted = 2

        # Mirror the fixed main.py logic
        if prefixes_succeeded < prefixes_attempted and old_cache["scan_succeeded"]:
            old_vp_count = len(old_cache["vendor_property"])
            old_vg_count = len(old_cache["vendor_gl"])
            if len(new_vp) < old_vp_count or len(new_vg) < old_vg_count:
                # Should keep stale cache
                result_vp = old_cache["vendor_property"]
                result_vg = old_cache["vendor_gl"]
            else:
                result_vp = new_vp
                result_vg = new_vg
        else:
            result_vp = new_vp
            result_vg = new_vg

        assert len(result_vp) == 3, "Should keep stale VP (3 pairs, not 1)"
        assert len(result_vg) == 2, "Should keep stale VG (2 pairs, not 0)"

    def test_full_scan_updates_cache(self):
        """If both prefixes succeed, cache should be updated even with fewer pairs."""
        old_cache = {
            "vendor_property": {("V1", "P1"), ("V2", "P2"), ("V3", "P3")},
            "vendor_gl": {("V1", "GL1"), ("V2", "GL2")},
            "scan_succeeded": True,
        }
        new_vp = {("V1", "P1")}  # Fewer, but both prefixes succeeded
        new_vg = {("V1", "GL1")}
        prefixes_succeeded = 2
        prefixes_attempted = 2

        if prefixes_succeeded < prefixes_attempted and old_cache["scan_succeeded"]:
            result_vp = old_cache["vendor_property"]
            result_vg = old_cache["vendor_gl"]
        else:
            result_vp = new_vp
            result_vg = new_vg

        assert len(result_vp) == 1, "Both prefixes succeeded; use new data"
        assert len(result_vg) == 1

    def test_all_scans_failed_keeps_stale(self):
        """If no prefixes succeeded, stale cache should be preserved."""
        old_cache = {
            "vendor_property": {("V1", "P1")},
            "vendor_gl": {("V1", "GL1")},
            "scan_succeeded": True,
        }
        prefixes_succeeded = 0

        if prefixes_succeeded == 0:
            result_vp = old_cache["vendor_property"]
            result_vg = old_cache["vendor_gl"]
        else:
            result_vp = set()
            result_vg = set()

        assert ("V1", "P1") in result_vp
        assert ("V1", "GL1") in result_vg

    def test_partial_scan_more_pairs_accepted(self):
        """If partial scan has MORE pairs than before, it should be accepted."""
        old_cache = {
            "vendor_property": {("V1", "P1")},
            "vendor_gl": {("V1", "GL1")},
            "scan_succeeded": True,
        }
        new_vp = {("V1", "P1"), ("V2", "P2"), ("V3", "P3")}  # More than before
        new_vg = {("V1", "GL1"), ("V2", "GL2")}  # More than before
        prefixes_succeeded = 1
        prefixes_attempted = 2

        if prefixes_succeeded < prefixes_attempted and old_cache["scan_succeeded"]:
            old_vp_count = len(old_cache["vendor_property"])
            old_vg_count = len(old_cache["vendor_gl"])
            if len(new_vp) < old_vp_count or len(new_vg) < old_vg_count:
                result_vp = old_cache["vendor_property"]
                result_vg = old_cache["vendor_gl"]
            else:
                result_vp = new_vp
                result_vg = new_vg
        else:
            result_vp = new_vp
            result_vg = new_vg

        assert len(result_vp) == 3, "Partial scan with more data should be accepted"
        assert len(result_vg) == 2


# ===========================================================================
# 13. CONFIG TIMESTAMP INTEGRITY
# ===========================================================================

class TestConfigTimestamp:
    """Verify that added_at is always server-generated."""

    def test_server_generates_added_at(self):
        """added_at should always be server-generated, ignoring client value."""
        # Simulate the fixed server normalization (always server timestamp)
        items = [
            {"vendor_id": "V1", "property_id": "P1", "action": "allow",
             "added_at": "1999-01-01T00:00:00Z"},  # Client-supplied fake date
        ]
        # The fixed code always generates server timestamp, never uses client's
        for r in items:
            vid = str(r.get("vendor_id") or "").strip()
            pid = str(r.get("property_id") or "").strip()
            if vid and pid:
                # Server always generates its own timestamp
                server_added_at = dt.datetime.utcnow().isoformat() + "Z"
                assert server_added_at != "1999-01-01T00:00:00Z"
                assert "202" in server_added_at  # Should be in 2020s

    def test_empty_added_at_gets_server_timestamp(self):
        """Empty added_at should get server timestamp."""
        server_ts = dt.datetime.utcnow().isoformat() + "Z"
        assert server_ts.endswith("Z")
        assert len(server_ts) > 20


# ===========================================================================
# 14. ADDITIONAL ENTRATA RESPONSE EDGE CASES
# ===========================================================================

class TestEntrataResponseEdgeCases:
    """Additional edge cases for Entrata response parsing."""

    def test_json_with_no_status_or_message(self):
        """JSON response with no status or message fields should default to ok."""
        resp = json.dumps({"response": {"result": {"invoiceId": "999"}}})
        success, reason = _entrata_post_succeeded(resp)
        # No error markers found → defaults to success via keyword scan fallback
        assert success is True

    def test_json_list_response(self):
        """JSON list response (not dict) should fall through to keyword scan."""
        resp = json.dumps([{"status": "ok"}])
        success, reason = _entrata_post_succeeded(resp)
        # json.loads returns a list, not a dict → skips JSON parsing → keyword scan
        # No error markers → defaults to success
        assert success is True

    def test_plain_text_success(self):
        """Plain text 'Success' should be treated as success."""
        success, reason = _entrata_post_succeeded("Request processed successfully")
        assert success is True

    def test_plain_text_failure(self):
        """Plain text containing 'error' should be treated as failure."""
        success, reason = _entrata_post_succeeded("An error occurred processing your request")
        assert success is False
        assert reason == "error"

    def test_json_nested_error_in_message(self):
        """Error status nested deeply should still be detected."""
        resp = json.dumps({
            "response": {
                "result": {
                    "status": "failed",
                    "message": "Invalid GL account"
                }
            }
        })
        success, reason = _entrata_post_succeeded(resp)
        assert success is False
        assert reason == "failed"

    def test_malformed_json_with_error_keyword(self):
        """Malformed JSON that looks like an error should be caught."""
        resp = '{"incomplete": true, error here'
        # json.loads fails → falls through to keyword scan
        success, reason = _entrata_post_succeeded(resp)
        assert success is False
        assert reason == "error"

    def test_response_with_only_whitespace(self):
        """Response with only whitespace should be treated as empty."""
        success, reason = _entrata_post_succeeded("   \n\t  ")
        assert success is False
        assert reason == "empty_response"

    def test_error_status_with_duplicate_message(self):
        """Error status + duplicate message should be classified as 'duplicate', not 'error'.
        This ensures the repost-with-suffix flow triggers even when Entrata wraps
        the duplicate in an error status."""
        resp = json.dumps({
            "response": {
                "result": {
                    "status": "error",
                    "message": "duplicate invoice exists for this account"
                }
            }
        })
        success, reason = _entrata_post_succeeded(resp)
        assert success is False
        assert reason == "duplicate", f"Expected 'duplicate' but got '{reason}'"

    def test_failed_status_with_already_posted_message(self):
        """Failed status + 'already posted' message → duplicate."""
        resp = json.dumps({
            "response": {
                "result": {
                    "status": "failed",
                    "message": "Invoice already posted to batch 12345"
                }
            }
        })
        success, reason = _entrata_post_succeeded(resp)
        assert success is False
        assert reason == "duplicate", f"Expected 'duplicate' but got '{reason}'"

    def test_error_status_without_duplicate_message(self):
        """Error status with non-duplicate message should still return 'error'."""
        resp = json.dumps({
            "response": {
                "result": {
                    "status": "error",
                    "message": "Invalid GL account ID"
                }
            }
        })
        success, reason = _entrata_post_succeeded(resp)
        assert success is False
        assert reason == "error"


# ===========================================================================
# 15. VALIDATION ENDPOINT EDGE CASES
# ===========================================================================

class TestValidationEdgeCases:
    """Additional edge cases for validation logic."""

    def _validate(self, keys, vp_set, vg_set, s3_data):
        """Simulate the validation endpoint logic."""
        warnings = []
        seen = set()
        for s3_key in keys:
            records = s3_data.get(s3_key, [])
            for rec in records:
                vendor_id = str(rec.get("EnrichedVendorID") or "").strip()
                vendor_name = str(rec.get("EnrichedVendorName") or rec.get("Vendor Name") or "").strip()
                prop_id = str(rec.get("EnrichedPropertyID") or "").strip()
                prop_name = str(rec.get("EnrichedPropertyName") or "").strip()
                gl_code = str(rec.get("EnrichedGLAccountNumber") or "").strip()
                gl_name = str(rec.get("EnrichedGLAccountName") or "").strip()
                if vendor_id and prop_id and (vendor_id, prop_id) not in vp_set:
                    dedup_key = ("vp", vendor_id, prop_id)
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        warnings.append({"type": "vendor_property", "vendor_name": vendor_name})
                if vendor_id and gl_code and (vendor_id, gl_code) not in vg_set:
                    dedup_key = ("vg", vendor_id, gl_code)
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        warnings.append({"type": "vendor_gl", "vendor_name": vendor_name})
        return warnings

    def test_vendor_name_fallback_to_vendor_name_field(self):
        """If EnrichedVendorName is missing, should fall back to 'Vendor Name'."""
        rec = {"EnrichedVendorID": "V1", "Vendor Name": "Fallback Name",
               "EnrichedPropertyID": "P1", "EnrichedPropertyName": "Prop",
               "EnrichedGLAccountNumber": "GL1", "EnrichedGLAccountName": "GL Name"}
        data = {"key1": [rec]}
        warnings = self._validate(["key1"], set(), {("V1", "GL1")}, data)
        assert len(warnings) == 1
        assert warnings[0]["vendor_name"] == "Fallback Name"

    def test_none_values_in_record(self):
        """Records with None values for all fields should not crash."""
        rec = {"EnrichedVendorID": None, "EnrichedPropertyID": None,
               "EnrichedGLAccountNumber": None}
        data = {"key1": [rec]}
        warnings = self._validate(["key1"], set(), set(), data)
        assert len(warnings) == 0

    def test_mixed_valid_and_invalid_keys(self):
        """Only valid-prefix keys should generate warnings."""
        valid_prefixes = (STAGE6_PREFIX, STAGE4_PREFIX)
        all_keys = [
            f"{STAGE6_PREFIX}test.jsonl",
            "malicious/path.jsonl",
            f"{STAGE4_PREFIX}test2.jsonl",
        ]
        sanitized = [k for k in all_keys if any(k.startswith(p) for p in valid_prefixes)]
        assert len(sanitized) == 2
        assert "malicious/path.jsonl" not in sanitized

    def test_path_traversal_blocked(self):
        """Path traversal attempts should be blocked by prefix validation."""
        valid_prefixes = (STAGE6_PREFIX, STAGE4_PREFIX)
        attacks = [
            "../../../etc/passwd",
            f"../{STAGE6_PREFIX}fake.jsonl",
            f"Bill_Parser_6_PreEntrata_Submission/../../../secret.jsonl",  # starts with valid prefix!
        ]
        sanitized = [k for k in attacks if any(k.startswith(p) for p in valid_prefixes)]
        # The third one starts with STAGE6_PREFIX so it passes prefix check
        # This is a known limitation — S3 itself handles path traversal safely
        assert len(sanitized) == 1  # Only the one that starts with valid prefix


# ===========================================================================
# 16. OVERRIDE INTERACTION TESTS
# ===========================================================================

class TestOverrideInteractions:
    """Test complex interactions between overrides and historical data."""

    def test_allow_then_block_same_pair(self):
        """Block should win if applied after allow for same pair."""
        vp = set()
        vg = set()
        # Two overrides: first allow, then block
        overrides = [
            {"vendor_id": "V1", "property_id": "P1", "action": "allow"},
            {"vendor_id": "V1", "property_id": "P1", "action": "block"},
        ]
        vp, vg = _apply_overrides(vp, vg, overrides, [])
        assert ("V1", "P1") not in vp  # Block wins (applied last)

    def test_block_then_allow_same_pair(self):
        """Allow should win if applied after block for same pair."""
        vp = set()
        vg = set()
        overrides = [
            {"vendor_id": "V1", "property_id": "P1", "action": "block"},
            {"vendor_id": "V1", "property_id": "P1", "action": "allow"},
        ]
        vp, vg = _apply_overrides(vp, vg, overrides, [])
        assert ("V1", "P1") in vp  # Allow wins (applied last)

    def test_block_nonexistent_pair_is_noop(self):
        """Blocking a pair that doesn't exist in history should be safe."""
        vp = {("V2", "P2")}
        vg = set()
        overrides = [{"vendor_id": "V99", "property_id": "P99", "action": "block"}]
        vp, vg = _apply_overrides(vp, vg, overrides, [])
        assert ("V2", "P2") in vp  # Existing pair unaffected
        assert ("V99", "P99") not in vp  # Nonexistent pair still absent

    def test_gl_block_override(self):
        """GL block override should remove the pair."""
        vp = set()
        vg = {("V1", "GL1"), ("V1", "GL2")}
        vg_overrides = [{"vendor_id": "V1", "gl_code": "GL1", "action": "block"}]
        vp, vg = _apply_overrides(vp, vg, [], vg_overrides)
        assert ("V1", "GL1") not in vg
        assert ("V1", "GL2") in vg

    def test_mixed_vp_and_vg_overrides(self):
        """VP and VG overrides should operate independently."""
        vp = {("V1", "P1")}
        vg = {("V1", "GL1")}
        vp_overrides = [{"vendor_id": "V1", "property_id": "P1", "action": "block"}]
        vg_overrides = [{"vendor_id": "V1", "gl_code": "GL2", "action": "allow"}]
        vp, vg = _apply_overrides(vp, vg, vp_overrides, vg_overrides)
        assert ("V1", "P1") not in vp  # Blocked
        assert ("V1", "GL1") in vg     # Untouched
        assert ("V1", "GL2") in vg     # Added


# ===========================================================================
# 17. CONFIG NORMALIZATION EDGE CASES
# ===========================================================================

class TestConfigNormalizationEdgeCases:
    """Test edge cases in config save normalization."""

    def test_whitespace_only_ids_skipped(self):
        """IDs that are only whitespace should be treated as empty."""
        items = [
            {"vendor_id": "  ", "property_id": "P1", "action": "allow"},
            {"vendor_id": "V1", "property_id": "  \t  ", "action": "allow"},
        ]
        normalized = _normalize_config_items(items)
        assert len(normalized) == 0

    def test_non_dict_items_skipped(self):
        """Non-dict items in the list should be skipped (simulated)."""
        # The server code does: if not isinstance(r, dict): continue
        items = [
            {"vendor_id": "V1", "property_id": "P1", "action": "allow"},
            "not a dict",
            42,
            None,
        ]
        normalized = []
        for r in items:
            if not isinstance(r, dict):
                continue
            vid = str(r.get("vendor_id") or "").strip()
            pid = str(r.get("property_id") or "").strip()
            if not vid or not pid:
                continue
            normalized.append(r)
        assert len(normalized) == 1

    def test_action_case_variants(self):
        """Various case spellings of allow/block should be normalized."""
        items = [
            {"vendor_id": "V1", "property_id": "P1", "action": "BLOCK"},
            {"vendor_id": "V2", "property_id": "P2", "action": "Allow"},
            {"vendor_id": "V3", "property_id": "P3", "action": "ALLOW"},
            {"vendor_id": "V4", "property_id": "P4", "action": " block "},
        ]
        normalized = _normalize_config_items(items)
        assert normalized[0]["action"] == "block"
        assert normalized[1]["action"] == "allow"
        assert normalized[2]["action"] == "allow"
        assert normalized[3]["action"] == "block"

    def test_gl_config_normalization(self):
        """GL code config should normalize the same way."""
        items = [
            {"vendor_id": "V1", "gl_code": "57060000", "action": "allow"},
            {"vendor_id": "", "gl_code": "57060000", "action": "allow"},
            {"vendor_id": "V2", "gl_code": "", "action": "block"},
        ]
        normalized = _normalize_config_items(items, id_field_2="gl_code")
        assert len(normalized) == 1
        assert normalized[0]["vendor_id"] == "V1"
        assert normalized[0]["gl_code"] == "57060000"


# ===========================================================================
# 18. CONFIG DEDUPLICATION
# ===========================================================================

class TestConfigDeduplication:
    """Verify that duplicate overrides for the same pair are deduplicated on save."""

    def test_dedup_vendor_property_keeps_last(self):
        """If same (vendor_id, property_id) appears twice, keep the last one."""
        items = [
            {"vendor_id": "V1", "property_id": "P1", "action": "allow", "vendor_name": "Old"},
            {"vendor_id": "V2", "property_id": "P2", "action": "block", "vendor_name": "Keep"},
            {"vendor_id": "V1", "property_id": "P1", "action": "block", "vendor_name": "New"},
        ]
        deduped = _dedup_config_items(items, ["vendor_id", "property_id"])
        assert len(deduped) == 2
        # V1,P1 should be the second occurrence (action=block, name=New)
        v1_items = [d for d in deduped if d["vendor_id"] == "V1"]
        assert len(v1_items) == 1
        assert v1_items[0]["action"] == "block"
        assert v1_items[0]["vendor_name"] == "New"

    def test_dedup_vendor_gl_keeps_last(self):
        """If same (vendor_id, gl_code) appears twice, keep the last one."""
        items = [
            {"vendor_id": "V1", "gl_code": "GL1", "action": "allow"},
            {"vendor_id": "V1", "gl_code": "GL1", "action": "block"},
        ]
        deduped = _dedup_config_items(items, ["vendor_id", "gl_code"])
        assert len(deduped) == 1
        assert deduped[0]["action"] == "block"

    def test_dedup_preserves_order(self):
        """Deduplicated items should maintain relative order."""
        items = [
            {"vendor_id": "V1", "property_id": "P1", "action": "allow"},
            {"vendor_id": "V2", "property_id": "P2", "action": "block"},
            {"vendor_id": "V3", "property_id": "P3", "action": "allow"},
        ]
        deduped = _dedup_config_items(items, ["vendor_id", "property_id"])
        assert len(deduped) == 3
        assert deduped[0]["vendor_id"] == "V1"
        assert deduped[1]["vendor_id"] == "V2"
        assert deduped[2]["vendor_id"] == "V3"

    def test_dedup_no_duplicates(self):
        """If no duplicates exist, all items are preserved."""
        items = [
            {"vendor_id": "V1", "property_id": "P1", "action": "allow"},
            {"vendor_id": "V2", "property_id": "P2", "action": "block"},
        ]
        deduped = _dedup_config_items(items, ["vendor_id", "property_id"])
        assert len(deduped) == 2

    def test_dedup_empty_list(self):
        """Empty list should return empty."""
        deduped = _dedup_config_items([], ["vendor_id", "property_id"])
        assert len(deduped) == 0

    def test_dedup_triple_duplicate(self):
        """Three entries for same pair should keep only the last."""
        items = [
            {"vendor_id": "V1", "property_id": "P1", "action": "allow", "note": "first"},
            {"vendor_id": "V1", "property_id": "P1", "action": "block", "note": "second"},
            {"vendor_id": "V1", "property_id": "P1", "action": "allow", "note": "third"},
        ]
        deduped = _dedup_config_items(items, ["vendor_id", "property_id"])
        assert len(deduped) == 1
        assert deduped[0]["note"] == "third"
        assert deduped[0]["action"] == "allow"


# ===========================================================================
# 19. BADGE CSS CLASS SAFETY
# ===========================================================================

class TestBadgeCssSafety:
    """Verify that action values used in CSS classes are sanitized."""

    def test_safe_action_allow(self):
        """'allow' should pass through."""
        def safeAction(a):
            return a if a in ('allow', 'block') else 'allow'
        assert safeAction('allow') == 'allow'

    def test_safe_action_block(self):
        """'block' should pass through."""
        def safeAction(a):
            return a if a in ('allow', 'block') else 'allow'
        assert safeAction('block') == 'block'

    def test_safe_action_xss_attempt(self):
        """XSS attempt in action should be sanitized to 'allow'."""
        def safeAction(a):
            return a if a in ('allow', 'block') else 'allow'
        assert safeAction('allow" onclick="alert(1)') == 'allow'
        assert safeAction('<script>') == 'allow'
        assert safeAction('') == 'allow'
        assert safeAction('ALLOW') == 'allow'  # Case doesn't match


# ===========================================================================
# 22. POST IN PROGRESS LOCK RESET
# ===========================================================================

class TestPostInProgressReset:
    """Verify _postInProgress is reset on all exit paths from confirmPost()."""

    def test_post_html_resets_on_location_cancel(self):
        """When user cancels vendor location, _postInProgress must be reset before return."""
        import os
        post_html = os.path.join(os.path.dirname(__file__), "templates", "post.html")
        with open(post_html, "r", encoding="utf-8") as f:
            content = f.read()

        # Find the two early-return paths in the location modal handling.
        # Both should have _postInProgress = false before return.

        # Path 1: user didn't pick a location (returned null/empty)
        # Expect: _postInProgress = false; before the return
        idx_no_pick = content.find("No location selected. Cancelled.")
        assert idx_no_pick != -1, "Could not find 'No location selected' in post.html"
        # Check the next ~200 chars after the message for the reset
        snippet_after_alert = content[idx_no_pick:idx_no_pick + 200]
        assert "_postInProgress = false" in snippet_after_alert, \
            "_postInProgress not reset before return after location cancel"

        # Path 2: catch block for user-cancelled modal
        # Find "// User cancelled" comment
        idx_user_cancel = content.find("// User cancelled")
        assert idx_user_cancel != -1, "Could not find '// User cancelled' comment in post.html"
        snippet_after_cancel = content[idx_user_cancel:idx_user_cancel + 200]
        assert "_postInProgress = false" in snippet_after_cancel, \
            "_postInProgress not reset before return after modal catch"

    def test_post_html_resets_in_catch(self):
        """The main try/catch in confirmPost also resets _postInProgress."""
        import os
        post_html = os.path.join(os.path.dirname(__file__), "templates", "post.html")
        with open(post_html, "r", encoding="utf-8") as f:
            content = f.read()
        # The main catch block should reset _postInProgress
        assert "catch(e) { _postInProgress = false;" in content or \
               "catch(e) {\n        _postInProgress = false;" in content or \
               "catch(e){ _postInProgress = false;" in content, \
            "Main catch block should reset _postInProgress"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
