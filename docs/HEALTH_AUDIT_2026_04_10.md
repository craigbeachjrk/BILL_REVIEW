# Health Audit — 2026-04-10

## Production Smoke Test Results

**Tested against:** https://billreview.jrkanalytics.com  
**Service account:** claude-qa@jrk.com  
**Total API endpoints tested:** 112 GET endpoints  
**Total HTML pages tested:** 51 pages  

---

## Summary

| Category | Count | Details |
|----------|-------|---------|
| API endpoints OK | 85 | Responded correctly <10s |
| API endpoints SLOW (>10s) | 7 | Need caching or optimization |
| API endpoints FAILED | 20 | 8 timeouts, 5 HTTP 500, 7 HTTP 400/422 |
| HTML pages OK | 49 | All loaded correctly |
| HTML pages SLOW | 1 | `/parse` cold start 15.6s |
| HTML pages FAILED | 1 | `/review` needs pdf_id param (expected) |

---

## CRITICAL: 500 Errors (5 endpoints)

These endpoints crash on every request:

| Endpoint | Error | Status |
|----------|-------|--------|
| `/api/billback/summary` | `Error during request` | INVESTIGATING |
| `/api/metrics/late-fees` | `Error during request` | INVESTIGATING |
| `/api/ai-review/stats` | `Error during ai stats` | INVESTIGATING |
| `/api/ai-learning/stats` | `Error during learning stats` | INVESTIGATING |
| `/api/ai-learning/quarantined` | `Error during quarantined patterns` | INVESTIGATING |

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
