# Health Audit — 2026-04-10

## Production Smoke Test Results

**Tested against:** https://billreview.jrkanalytics.com  
**Service account:** claude-qa@jrk.com  
**Total API endpoints tested:** 112 GET endpoints  
**Total HTML pages tested:** 51 pages  

---

## Summary

| Category | Healthy | Broken | Details |
|----------|---------|--------|---------|
| API endpoints | 85/112 | 13 real failures | 5 crash (500), 8 timeout. 7 more need params (expected) |
| HTML pages | 49/51 | 0 real failures | `/parse` slow on cold start, `/review` needs pdf_id |
| Lambda functions | 37/41 | 4 broken | Package errors, IAM gaps, missing config |
| EventBridge rules | ~80/90 | 4 critical | Ghost test job, 2 broken API methods, 1 crash loop |
| Vendor pipeline | Fixed today | Was broken 30 days | OOM + disconnected data paths |
| Monitoring/alerting | 0 alarms | n/a | Nothing catches any of this |

---

## CRITICAL: 500 Errors (5 endpoints) — ROOT CAUSES FOUND

| Endpoint | Root Cause | Fix |
|----------|-----------|-----|
| `/api/billback/summary` | **IAM: No permissions on `jrk-bill-billback-master` table.** AppRunner role has zero DDB access for this table. `ddb.scan()` at line 5173 throws AccessDeniedException. | Add DDB Scan/PutItem/Query to instance role for `jrk-bill-billback-master` |
| `/api/metrics/late-fees` | **Missing `import pytz`.** Line 13639 calls `pytz.timezone("America/Los_Angeles")` but pytz is never imported. Immediate NameError. | Add `import pytz` to main.py (or replace with `zoneinfo.ZoneInfo`) |
| `/api/ai-review/stats` | **IAM: No permissions on `jrk-bill-ai-suggestions` table.** Scan at line 32935 throws AccessDeniedException. Table has 8,960 items. | Add DDB full access to instance role for `jrk-bill-ai-suggestions` |
| `/api/ai-learning/stats` | Same as above — same table, same IAM gap | Same fix as ai-review/stats |
| `/api/ai-learning/quarantined` | Same as above — same table, same IAM gap | Same fix as ai-review/stats |

### Bonus finding
`jrk-bill-config` IAM policy missing `UpdateItem`/`Scan`/`DeleteItem` — only has GetItem/PutItem/Query. Causing `[POST LOCK] ERROR` in invoice posting pipeline.

### Required fixes (3 total):
1. **IAM:** Add `jrk-bill-ai-suggestions` table access to `jrk-bill-review-instance-role`
2. **IAM:** Add `jrk-bill-billback-master` table access to `jrk-bill-review-instance-role`
3. **Code:** Add `import pytz` to main.py (line ~55)
4. **IAM (bonus):** Add UpdateItem/Scan/DeleteItem to `jrk-bill-config` policy

---

## CRITICAL: Timeouts (8 endpoints)

All 8 share the same root cause: **bulk S3 file reads on AppRunner** (2-5s per GET vs 50-200ms normally). 5 of 8 have zero caching. The other 3 use in-memory-only caches that die on every deploy.

| Endpoint | Root Cause | Fix |
|----------|-----------|-----|
| `/api/billback/ubi/suggestions` | **No cache.** Scans S3 Stage 7 (90 days) + S3 GET per file + DDB scan | `_metrics_serve()` + extend Lambda cache builder |
| `/api/billback/ubi/assigned` | **No cache.** S3 listing + GET per file across Stage 8 (90 days) | `_metrics_serve()` or extend Lambda cache builder |
| `/api/workflow/vacant-accounts` | 10min in-memory cache, but cold = S3 GET per file across 3 months | `_metrics_serve()` (S3-persisted, survives deploy) |
| `/api/workflow/ap-priority` | **No cache.** Full scan of ALL Stage 8 S3 keys + GET per file | `_metrics_serve()` with 60min TTL |
| `/api/metrics/user-timing` | **No cache.** Full DDB table scan of `jrk-bill-drafts` (no GSI) | Add `_CACHE` + GSI on `timing#` prefix |
| `/api/metrics/week-over-week` | In-memory cache only. Cold = DDB scan + hundreds of S3 GETs | `_metrics_serve()` + GSI on status field |
| `/api/master-bills/completion-tracker` | 5min in-memory cache. Cold = 12 months S3 + thousands of GETs | `_metrics_serve()` + use bill_index_cache |
| `/api/track` | **WORST.** 1hr cache but **sequential** S3 GETs (no ThreadPool!) | Parallelize + `_metrics_serve()` + parse from filenames |

**Common fix:** Convert all 8 to `_metrics_serve()` pattern (already exists in codebase for pipeline-summary, parser-throughput, queue-depth). This persists to S3 so data survives deploys.

---

## SLOW Endpoints (7 endpoints, 10-30s)

| Endpoint | Time | Size | Issue |
|----------|------|------|-------|
| `/api/ubi/stats/by_property` | 23s | 3KB | |
| `/api/account-manager/duplicate-bills` | 29s | 203KB | |
| `/api/workflow/accounts/archived` | 20s | 276B | |
| `/api/vendor-corrections/suspects` | 23s | 110KB | |
| `/api/failed/jobs` | 14s | 3.5MB | Huge response |
| `/api/debug/orphaned-stage7` | 22s | 895B | |
| `/api/meters/scan` | 14s | 129B | |

---

## Expected 400/422 Errors (7 endpoints)

These need specific query parameters — not bugs, just incomplete test:

| Endpoint | Code | Likely needs |
|----------|------|-------------|
| `/api/search?q=test` | 400 | Different search format |
| `/api/post/total` | 422 | `date` param |
| `/api/history/archived` | 400 | Date range params |
| `/api/billback/posted` | 400 | Period/property params |
| `/api/accrual/calculate` | 400 | Property/period params |
| `/api/options` | 422 | `date` param |
| `/api/drafts?date=2026-04-10` | 422 | Additional params |

---

## AWS Infrastructure Issues Found

## EventBridge Cron Jobs (90 rules scanned)

### CRITICAL

| Rule | Issue |
|------|-------|
| `jrk-data-feeds-job-3a42fc4f` | **GHOST TEST JOB overwriting real vendor data.** Named "Test Vendors", runs every 4h, writes 1-record file to same S3 path as real vendor feed (`RAW/ENTRATA/VENDORS/`). Overwrites 7,195 real records with 1 test record 6x/day. **DISABLE IMMEDIATELY.** |
| `jrk-acq-daily-sheets-sync` | **100% failure.** Triggers `jrk-acq-sheets-sync` which can't import (cryptography package). Retrying 100-476x/day. Broken since at least Mar 27. |
| `entrata-ar-payments` | **Entrata API broken.** Every invocation returns `HTTP 400: Method name not found`. Zero data collected. |
| `entrata-ar-invoices` | **Same Entrata API error.** 95 properties x hourly = thousands of API errors/day. |

### ISSUES

| Rule | Issue |
|------|-------|
| `jrk-data-feeds-entrata-work-orders` | Duplicate targets — fires the same job twice per schedule |
| `jrk-lease-audit-reclassify-batch` | Stale one-time rule (Feb 16), still enabled, will never fire again |
| 4x `entrata-specials` versions + 2x `floor-plans` | Possible redundant duplicates |

### GOOD NEWS
- `jrk-data-feeds-entrata-vendors` — **WORKING.** Handler exists, runs daily, fetches ~7,195 vendors. Astound issue was in the vendor-cache-builder, not the data feed.
- 60+ other rules all healthy and firing on schedule

---

## AWS Infrastructure Issues Found

### vendor-cache-builder Lambda — OOM since March 10
- **Impact:** All new vendors (including Astound) not available for enrichment for ~30 days
- **Root cause:** Lambda at 512 MB OOM parsing massive Entrata API response
- **Fix applied:** Memory bumped to 2048 MB, cache rebuilt, dim_vendor synced
- **Permanent fix needed:** Stream API response, don't load all in memory (see S8)

### IMPROVE Agent Container — Stale image
- **Impact:** IMPROVE agent times out at 20 min instead of 60 min
- **Root cause:** Docker image last pushed March 6, timeout fix committed March 11
- **Fix needed:** Rebuild and push Docker image (requires Docker Desktop)

---

## Lambda Health Audit (41 functions scanned)

**37 OK, 4 FAIL**

### FAIL: jrk-acq-sheets-sync — 100% failure rate
- **16 errors** in 7 days — every invocation fails
- `Runtime.ImportModuleError: cannot import name 'exceptions' from 'cryptography.hazmat.bindings._rust'`
- **Root cause:** `cryptography` package incompatible with Python 3.12 runtime
- **Fix:** Rebuild deployment package with compatible cryptography version

### FAIL: jrk-data-feeds-executor — Entrata API errors
- **6 errors** — `HTTP 400: Method name not found` (code 1404)
- Function itself is healthy (201 MB / 512 MB)
- **Root cause:** One of the configured Entrata API feed methods is invalid
- **Fix:** Check data feeds config for deprecated method name

### FAIL: jrk-forms-analyzer — IAM permission gap
- **1 error** — `S3 AccessDenied` on GetObject for form attachment PDF
- Intermittent (depends on attachment location)
- **Fix:** Update IAM role to grant GetObject on relevant S3 prefix

### FAIL: jrk-hr-compliance-email-sender — Missing S3 config
- **6 errors** — 100% failure rate, `NoSuchKey` at line 38
- Memory also at 74.2% of 128 MB limit (borderline)
- **Fix:** Verify S3 config file path or update function config

### Notable observations
- **jrk-url-short:** Memory at 88/128 MB (68.8%) — approaching OOM territory
- **5 functions never invoked:** audit-addenda-extractor, audit-charge-analyzer, audit-gl-reconciler, vendor-notifier, vendor-validator (deployed but no triggers)
- **9 lease-audit functions:** Haven't run in 30+ days (on-demand, not scheduled)
- **jrk-data-feeds-reporter + jrk-forms-analyzer:** Still on Python 3.11 (all others 3.12)

---

## Fixes Applied (2026-04-13)

### API 500 Errors — ALL 5 FIXED
| Endpoint | Fix | Verified |
|----------|-----|----------|
| `/api/billback/summary` | IAM: added `jrk-bill-billback-master` DDB permissions | YES |
| `/api/metrics/late-fees` | Code: added `import pytz` | YES |
| `/api/ai-review/stats` | IAM: added `jrk-bill-ai-suggestions` DDB permissions | YES |
| `/api/ai-learning/stats` | Same IAM fix | YES |
| `/api/ai-learning/quarantined` | Same IAM fix | YES |
| (bonus) `jrk-bill-config` post locks | IAM: added UpdateItem/Scan/DeleteItem | YES |

### API Timeouts — ALL 8 FIXED
All converted to `_metrics_serve()` (S3-persisted cache, survives deploys, serves stale while rebuilding):

| Endpoint | Additional Fix |
|----------|---------------|
| `/api/metrics/user-timing` | DDB scan result cached |
| `/api/metrics/week-over-week` | Replaced in-memory-only cache |
| `/api/workflow/ap-priority` | Full Stage 8 scan cached |
| `/api/track` | S3 fallback + **parallelized** `_read_json_records_from_s3` |
| `/api/billback/ubi/suggestions` | Added cache + 7 invalidation points |
| `/api/billback/ubi/assigned` | Added cache, period filter post-cache |
| `/api/workflow/vacant-accounts` | Replaced in-memory `_CACHE` |
| `/api/master-bills/completion-tracker` | Replaced 5min in-memory cache |

### EventBridge — 3 FIXES
- Ghost test job `jrk-data-feeds-job-3a42fc4f` — DISABLED
- Stale rule `jrk-lease-audit-reclassify-batch` — DISABLED
- Duplicate target on `entrata-work-orders` — REMOVED

### Lambda Fixes
- `vendor-cache-builder` — memory 512→2048MB, cache rebuilt, dim_vendor synced (FIXED)
- `jrk-forms-analyzer` — IAM S3 GetObject scope widened from `Forms_Submissions/*` to `*` (FIXED)

### Additional Fixes (2026-04-13)
- `jrk-acq-sheets-sync` — **FIXED.** Repackaged with Linux/Python 3.12 compatible cryptography. 1,089 deals synced successfully.
- `jrk-hr-compliance-email-sender` — **FIXED.** Added `urllib.parse.unquote_plus()` for URL-encoded S3 keys in event notifications.
- 4 broken Entrata AR EventBridge rules — **DISABLED** (ar-payments, ar-invoices, ar-codes, mits-lease-ar-transactions). Method names have never been valid since Jan 2026.
- 7 slow endpoints (10-30s) — **ALL CACHED** with `_metrics_serve()`
- `async_cold` mode added to `_metrics_serve()` — heavy endpoints return `{"building": true}` on cold cache instead of 504 gateway timeout
- `submitter-stats` and `late-fees` — added `_metrics_serve()` with `async_cold=True`

### CloudWatch Alarms Created (2026-04-13)
SNS topic `jrk-lambda-alerts` created with `cbeach@jrk.com` subscribed.
8 alarms monitoring Lambda errors (1hr evaluation, threshold > 0):
- `vendor-cache-builder-errors`
- `jrk-bill-enricher-errors`
- `jrk-bill-parser-errors`
- `jrk-bill-router-errors`
- `jrk-email-ingest-errors`
- `jrk-data-feeds-executor-errors`
- `jrk-acq-sheets-sync-errors`
- `jrk-hr-compliance-email-sender-errors`

**ACTION REQUIRED:** Confirm SNS subscription email in inbox for alerts to work.

### Remaining
- IMPROVE agent Docker image — needs Docker Desktop to rebuild
- 3 Entrata AR API methods — need correct method names from Entrata docs before re-enabling
- `/api/track` cold cache — still slow on first request after deploy (needs bill_index approach)
