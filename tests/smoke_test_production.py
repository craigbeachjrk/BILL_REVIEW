"""
Production smoke test — verifies deployed app is working correctly.
Run AFTER every deploy to confirm changes didn't break anything.

Usage:
    python tests/smoke_test_production.py

Uses the claude-qa@jrk.com service account.
"""
import sys
import json
import urllib.request
import urllib.parse
import http.cookiejar

BASE = "https://billreview.jrkanalytics.com"
USER = "claude-qa@jrk.com"
PASS = "fbt_cAgq3JzD3MKEEmhrBfvreRVw6UNY"

passed = 0
failed = 0
warnings = 0


def ok(msg):
    global passed
    passed += 1
    print(f"  \033[92mOK\033[0m   {msg}")


def fail(msg):
    global failed
    failed += 1
    print(f"  \033[91mFAIL\033[0m {msg}")


def warn(msg):
    global warnings
    warnings += 1
    print(f"  \033[93mWARN\033[0m {msg}")


def make_session():
    """Create an authenticated HTTP session using cookies."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPRedirectHandler(),
    )
    # Login
    data = urllib.parse.urlencode({"username": USER, "password": PASS}).encode()
    req = urllib.request.Request(f"{BASE}/login", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        opener.open(req)
    except urllib.error.HTTPError as e:
        if e.code not in (302, 303, 200):
            raise
    return opener


def get_json(opener, path):
    """Fetch a JSON API endpoint."""
    req = urllib.request.Request(f"{BASE}{path}")
    resp = opener.open(req)
    return json.loads(resp.read().decode("utf-8"))


def get_html(opener, path):
    """Fetch an HTML page."""
    req = urllib.request.Request(f"{BASE}{path}")
    resp = opener.open(req)
    return resp.read().decode("utf-8")


def test_login(opener):
    """Verify login works and we can access the home page."""
    print("\n=== Login & Home ===")
    html = get_html(opener, "/")
    if len(html) > 500:
        ok(f"Home page loaded ({len(html)} bytes)")
    else:
        fail(f"Home page too small ({len(html)} bytes)")


def test_transactions_api(opener):
    """Verify /api/transactions/summary returns correct funnel data."""
    print("\n=== Transactions API ===")
    d = get_json(opener, "/api/transactions/summary?hours=24")

    # Check pipeline funnel exists
    funnel = d.get("pipeline_funnel", [])
    if not funnel:
        fail("pipeline_funnel missing from response")
        return

    ok(f"pipeline_funnel has {len(funnel)} stages")

    # Check funnel has non-zero counts (at least one stage should have data)
    total = sum(f["count"] for f in funnel)
    if total > 0:
        ok(f"Funnel total count = {total}")
    else:
        warn("Funnel total is 0 (might be off-hours)")

    for f in funnel:
        label = f["label"]
        count = f["count"]
        failed_count = f.get("failed", 0)
        extra = f" ({failed_count} failed)" if failed_count else ""
        print(f"       {label:15s} {count:>5d}{extra}")

    # Check event_type_counts exists
    etc = d.get("event_type_counts", {})
    if etc:
        ok(f"event_type_counts has {len(etc)} types: {', '.join(sorted(etc.keys()))}")
    else:
        warn("event_type_counts empty")

    # Check recent events have epoch field
    events = d.get("recent_events", [])
    if events:
        ev = events[0]
        if "epoch" in ev:
            ok(f"Recent events have epoch field (first={ev['epoch']})")
        else:
            fail("Recent events MISSING epoch field")
        if "time" in ev:
            ok(f"Recent events have time field (first={ev['time'][:30]}...)")
        else:
            warn("Recent events missing time field")
    else:
        warn("No recent events in 24h window")


def test_transactions_page(opener):
    """Verify the transactions HTML page renders correctly."""
    print("\n=== Transactions Page ===")
    html = get_html(opener, "/transactions")

    if "ev.epoch" in html:
        ok("Frontend uses epoch-based date rendering")
    else:
        fail("Frontend does NOT use epoch (still parsing ISO strings)")

    if "Invalid Date" not in html:
        ok("No 'Invalid Date' string in template")
    else:
        warn("'Invalid Date' found in template source")

    if "pipeline_funnel" in html or "funnel" in html:
        ok("Frontend references pipeline_funnel data")
    else:
        warn("Frontend may not be using pipeline_funnel")


def test_pages_load(opener):
    """Verify key pages load without 500 errors."""
    print("\n=== Page Load Tests ===")
    import datetime
    today = datetime.date.today().isoformat()
    pages = [
        ("/", "Home"),
        ("/transactions", "Transactions"),
        ("/parse", "Parse"),
        (f"/invoices?date={today}", "Invoices"),
        ("/billback", "Billback"),
        ("/master-bills", "Master Bills"),
        ("/config", "Config"),
        ("/pipeline", "Pipeline"),
        ("/perf", "Performance"),
    ]
    for path, name in pages:
        try:
            html = get_html(opener, path)
            if len(html) > 200:
                ok(f"{name} ({path}) — {len(html)} bytes")
            else:
                warn(f"{name} ({path}) — suspiciously small ({len(html)} bytes)")
        except urllib.error.HTTPError as e:
            fail(f"{name} ({path}) — HTTP {e.code}")
        except Exception as e:
            fail(f"{name} ({path}) — {e}")


def test_api_endpoints(opener):
    """Verify key API endpoints return valid JSON."""
    print("\n=== API Endpoint Tests ===")
    endpoints = [
        ("/api/transactions/summary?hours=24", "Transactions Summary"),
        ("/api/perf/live?minutes=60", "Perf Live"),
    ]
    for path, name in endpoints:
        try:
            d = get_json(opener, path)
            if isinstance(d, dict):
                ok(f"{name} — {len(d)} keys")
            else:
                warn(f"{name} — unexpected response type: {type(d).__name__}")
        except urllib.error.HTTPError as e:
            fail(f"{name} — HTTP {e.code}")
        except json.JSONDecodeError:
            fail(f"{name} — invalid JSON response")
        except Exception as e:
            fail(f"{name} — {e}")


def test_previously_broken(opener):
    """Verify all endpoints that were broken during the Apr 10-13 health audit."""
    import time
    print("\n=== Previously Broken Endpoints ===")
    endpoints = [
        # Were 500 crashes (IAM + pytz fixes)
        ("/api/billback/summary", "billback summary"),
        ("/api/metrics/late-fees", "late fees"),
        ("/api/ai-review/stats", "AI review stats"),
        ("/api/ai-learning/stats", "AI learning stats"),
        ("/api/ai-learning/quarantined", "AI quarantined"),
        # Were timeouts (now _metrics_serve cached)
        ("/api/billback/ubi/suggestions", "UBI suggestions"),
        ("/api/workflow/ap-priority", "AP priority"),
        ("/api/metrics/user-timing", "user timing"),
        ("/api/track", "track"),
        # Were slow (now cached)
        ("/api/catalog/vendors", "vendor catalog"),
        ("/api/workflow/completion-tracker", "completion tracker"),
        ("/api/config/accounts-to-track", "accounts to track"),
    ]
    for path, name in endpoints:
        try:
            t0 = time.time()
            d = get_json(opener, path)
            elapsed = time.time() - t0
            is_error = isinstance(d, dict) and d.get("error")
            is_building = isinstance(d, dict) and d.get("building")
            if is_error:
                fail(f"{name} ({path}) — error: {d['error'][:50]}")
            elif is_building:
                ok(f"{name} ({path}) — building (async cold cache) {elapsed:.1f}s")
            elif elapsed > 30:
                warn(f"{name} ({path}) — slow {elapsed:.1f}s")
            else:
                ok(f"{name} ({path}) — {elapsed:.1f}s")
        except urllib.error.HTTPError as e:
            fail(f"{name} ({path}) — HTTP {e.code}")
        except Exception as e:
            err = str(e)[:50]
            if "timed out" in err.lower():
                fail(f"{name} ({path}) — TIMEOUT")
            else:
                fail(f"{name} ({path}) — {err}")


def main():
    print("=" * 60)
    print("PRODUCTION SMOKE TEST")
    print(f"Target: {BASE}")
    print("=" * 60)

    try:
        opener = make_session()
    except Exception as e:
        print(f"\n\033[91mFATAL: Could not log in: {e}\033[0m")
        sys.exit(1)

    test_login(opener)
    test_transactions_api(opener)
    test_transactions_page(opener)
    test_pages_load(opener)
    test_api_endpoints(opener)
    test_previously_broken(opener)

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {warnings} warnings")
    print("=" * 60)

    if failed > 0:
        print("\n\033[91mSMOKE TEST FAILED\033[0m")
        sys.exit(1)
    else:
        print("\n\033[92mSMOKE TEST PASSED\033[0m")
        sys.exit(0)


if __name__ == "__main__":
    main()
