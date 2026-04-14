# Code Audit — 2026-04-14

## Scope
Full deep dive of `main.py` (34,199 lines, 349 endpoints, 492 functions) + all HTML templates.
Audited in 6 parallel sections. Categories: Bugs, Dead Code, Security, Performance, Data Integrity, Code Quality.

---

## CRITICAL ISSUES

### 0. Hardcoded production API keys in source code (lines 148, 527)
**Severity: CRITICAL | Category: SECURITY**
- Line 148: `SCRAPER_API_TOKEN` default value contains a real production token committed to git
- Line 527: `EntrataARClient(api_key='288f3174-...')` — Entrata API key hardcoded in source
**Fix:** Move both to env vars or Secrets Manager immediately. Rotate the keys.

### 1. Corrupted records on JSON parse failure (lines 7111-7113, 7253-7256, 6632, 6837)
**Severity: CRITICAL | Category: DATA INTEGRITY**
During reassign/reassign-account operations, if a JSONL line fails to parse, the code appends empty `{}` to `modified_items`. This writes a corrupt empty JSON record back to S3, **permanently destroying the original data** for that line.
**Fix:** Skip unparseable lines entirely or preserve raw text.

### 2. `api_override_master_bill_amount` modifies Stage 8 billing data without locking (lines 20866-20926)
**Severity: CRITICAL | Category: DATA INTEGRITY**
Reads a Stage 8 JSONL file, modifies a line item's amount, writes back. No ETag checking. If another process touches the same file concurrently, data is silently lost. Stage 8 files are the source of truth for billing.
**Fix:** Add ETag-based conditional writes.

### 3. `api_portfolio_clear` and `api_portfolio_delete` — no admin check (lines 30185, 30212)
**Severity: CRITICAL | Category: SECURITY**
Any authenticated user can delete ALL portfolio data. No role check.
**Fix:** Add `require_admin` dependency.

---

## HIGH SEVERITY

### Performance — No caching on expensive operations
| # | Endpoint | Line | Issue |
|---|----------|------|-------|
| 4 | `/api/flagged` + `/api/flagged/stats` | 7580, 8032 | Scan 90 days of S3 files with no caching |
| 5 | `/api/ubi/stats/by_property` | 8160 | Reads every JSONL file body; could parse filenames |
| 6 | `/api/my-bills` | 12537 | Full DynamoDB table scan on pipeline tracker |
| 7 | `/api/metrics/overrides` | 14196 | Full DDB scan of drafts table |
| 8 | `/api/metrics/logins` | 16375 | Full DDB scan of drafts table |
| 9 | `_rebuild_ubi_periods_cache` | 15471 | Reads ALL Stage 8 S3 files |
| 10 | `_load_posted_invoices_from_ddb` | 30461 | Loads ALL posted invoices with no date filter |
| 11 | Meter endpoints (4x) | 28669+ | Load full `meter_master.json.gz` on every request |

### Performance — Excessive S3 operations
| # | Function | Line | Issue |
|---|----------|------|-------|
| 12 | `_infer_pdf_key_for_doc` | 25858 | Waterfall of 10+ full-prefix S3 listings |
| 13 | `api_generate_master_bills` | 19737 | 365 day-level prefixes (should be ~12 month-level) |
| 14 | `api_diagnose_master_bills` | 20505 | Same — 365 day-level prefixes |
| 15 | `_submeter_rates_scan` | 33849 | 365 day-level prefixes |

### Code Quality — Major duplication
| # | What | Lines | Scope |
|---|------|-------|-------|
| 16 | Stage 6 cleanup code | 24570, 25046, 25443 | ~150 lines × 3 copies |
| 17 | Bulk PDF = single PDF | 30780, 31034 | ~200 lines duplicated |

---

## MEDIUM SEVERITY

### Security — Missing authorization
| # | Endpoint | Line | Issue |
|---|----------|------|-------|
| 18 | `POST /api/master-bills/generate` | 19702 | Financial write, no admin check |
| 19 | `POST /api/master-bills/upload-manual` | 20965 | Financial write, no admin check |
| 20 | `POST /api/master-bills/exclude-line` | 20682 | Financial write, no admin check |
| 21 | `POST /api/master-bills/reclassify` | 20738 | Financial write, no admin check |
| 22 | `POST /api/master-bills/override-amount` | 20839 | Financial write, no admin check |
| 23 | `POST /api/meters/reading/update` | 28706 | Data modification, no admin check |
| 24 | `DELETE /api/debug/report/{id}` | 23523 | Any user can delete reports |
| 25 | Config saves (ap-team, ap-mapping, ubi-mapping) | 22547+ | Any user can modify mappings |

### Security — Missing S3 key validation
| # | Endpoint | Line | Issue |
|---|----------|------|-------|
| 26 | `/api/billback/ubi/reassign-account` | 7020 | `s3_key` used without validation |
| 27 | `/api/billback/ubi/archive` | 7301 | Same |
| 28 | `/api/billback/ubi/reassign` | 7172 | Same |

### Data Integrity — Race conditions
| # | What | Line | Issue |
|---|------|------|-------|
| 29 | Master bills read-modify-write | 20696, 20752, 20822 | No ETag/locking on S3 writes |
| 30 | UBI batches in single DDB item | 22271 | Concurrent operations can lose writes |
| 31 | `_UBI_PERIODS_REBUILDING` flag | 15406 | No threading lock |
| 32 | `_METRICS_BUILDING` set | 12274 | Not thread-safe |
| 33 | Meter data read-modify-write | 28706+ | Same file, no locking |
| 34 | Completion tracker account normalization | 21399 vs 21514 | Different normalization for same data |

### Performance — DDB scans & missing caching
| # | Endpoint | Line | Issue |
|---|----------|------|-------|
| 35 | `/api/master-bills/list` | 20184 | Full DDB scan, no caching |
| 36 | `/api/master-bills/generate` | 19997 | Full DDB scan of manual entries |
| 37 | `/api/debug/release-notes` | 23305 | Full scan instead of using cached helper |
| 38 | `/api/pipeline/stuck` | 12847 | N+1 DDB queries (up to 200) |
| 39 | `/api/ai-review/stats` | 33109 | Full DDB scan |
| 40 | `/api/ai-learning/stats` | 33192 | TWO full DDB scans |
| 41 | `/api/validate-submit` | 33056 | Per-line S3 reads (not grouped by key) |
| 42 | Duplicate invoice check | 32976 | Triple-nested S3 listing loop |

### Data Integrity — Missing cache invalidation
| # | What | Line | Issue |
|---|------|------|-------|
| 43 | Archive doesn't invalidate `ubi_assigned` cache | 7423 | Stale data after archive |
| 44 | `api_delete_manual_batch` doesn't bust completion tracker | 21232 | Stale after batch delete |
| 45 | `api_accrual_create` doesn't bust completion tracker | 22045 | Stale after accrual create |
| 46 | `api_gap_analysis_add_missing` doesn't invalidate caches | 10049 | New accounts don't appear |

### Bugs
| # | What | Line | Issue |
|---|------|------|-------|
| 47 | Duplicate route `/api/debug/reports` | 23076, 23095 | First is helper with wrong decorator |
| 48 | `_ddb_get_config` silently drops dict configs | 18155 | Returns None for non-list configs |
| 49 | `api_meters_scan` caches Lambda invocation via `_metrics_serve` | 28404 | Prevents re-scans for 60 min |
| 50 | Gemini model mismatch | 28983 vs 4494 | Meters uses 1.5-flash, rest uses 2.5-flash |

---

## LOW SEVERITY (50+ items)

### Dead code
- `has_account_artifact()` defined twice, never called (24451, 26504)
- `_api_meters_scan_local` 200+ lines, never called (28435)
- `stage4`/`idx4` always empty in `/api/track` (24097)
- `if True:` no-op conditional (10574)
- Unused variables: `total_lines` (12150), `auto_apply_uom` (28920), `account_number` param (17790)
- Hardcoded December/January login metrics (16455)

### Code quality
- 60+ redundant inner imports (`re`, `json`, `datetime`, `uuid`, `traceback`, `threading`, etc.)
- 4x duplicated check slip DDB parsing (18311, 18422, 18474, 18577)
- 2x duplicated add-to-tracker/add-to-ubi endpoints (19167 vs 19279)
- 4x duplicated property/vendor ID lookup (19179, 19198, 19291, 19310)
- 3x duplicated `parse_utc_timestamp` helper (13094, 13969, 13984)
- 2x duplicated `normalize_for_comparison` (28755, 28930)
- `_ddb_get_draft`/`_ddb_put_draft` read S3, not DDB (misleading names)
- `_TRACK_CACHE` type hint wrong (Tuple[str,str] should be Tuple[str,str,int])
- `or True` always-true condition (26036)
- Inconsistent `dt.datetime.utcnow()` vs `datetime.utcnow()`

### Misc bugs
- Month arithmetic using `timedelta(days=i*30)` can skip months (15833, 16017, 29947)
- `api_invoices_status` no ValueError handling on date split (26441)
- Sort produces inconsistent order in review-checks (31282)
- `daily_avg` counts calendar days but `needed_per_day` counts weekdays (11412)

---

## FROM LINES 1-7000 (additional findings)

### Bugs
- `api_billback_submit` is a complete **no-op** — always returns `{"submitted": 0}` (line 5230)
- `api_billback_archive` matches too broadly (account+dates only) — archives unintended lines (line 5046)
- `_entrata_post_succeeded` defined before imports (line 1)
- `api_verify_entrata_sync` O(n²) list.remove (line 2595)

### Security
- `APP_SECRET` has predictable default `"dev-secret-change-me"` (line 102)
- `api_scraper_import` copies arbitrary keys from scraper bucket without validation (line 4400)

### Performance
- `_scan_historical_pairs_for_prefix` reads every JSONL file body instead of filenames (line 1039)
- `post_view` reads first 16KB of every Stage 6 file sequentially, not parallel (line 4748)

### Data Integrity
- UBI cache has no thread synchronization — `_remove_bill_from_ubi_cache` and `_load_ubi_cache_from_s3` race (lines 898, 1194)

---

## TEMPLATE AUDIT (26 findings)

### XSS — Unescaped data in innerHTML (9 instances)
Most templates have `escapeHtml()`/`esc()` functions but miss them in spots:
- **billback.html** (4 spots): notes, charge code, vendor name, account in assigned bills
- **review.html** (2 spots): unit modal fields, AI garbage line reason
- **workflow.html** (1 spot): skip modal property/vendor/account
- **ai_review_dashboard.html** (1 spot): error message
- **debug.html** (1 spot): type/status/requestor fields

### Dead Templates
- `config_old.html` (832 lines) — deprecated, should be deleted
- `invoices.html.bak` (123 lines) — backup file, should be deleted

### Hardcoded Values
- `billback.html:563` — hardcoded `Craig Beach` in email template signature
- `billback.html:565` — hardcoded CC email addresses

---

## FINAL SUMMARY

| Severity | Count |
|----------|-------|
| CRITICAL | 4 |
| HIGH | 21 |
| MEDIUM | 39 |
| LOW | 70+ |
| **Total** | **134+** |

## PRIORITY FIX ORDER

### Immediate (today)
1. **CRITICAL-0:** Rotate and remove hardcoded API keys (Entrata, Scraper)
2. **CRITICAL-1:** Fix corrupted records on JSON parse failure (4 locations)
3. **CRITICAL-2:** Add ETag locking to Stage 8 override writes
4. **CRITICAL-3:** Add admin checks to portfolio clear/delete

### This week
5. **HIGH security:** Add admin checks to all financial write endpoints (5 endpoints)
6. **HIGH security:** Add S3 key validation to 3 reassign/archive endpoints
7. **HIGH bug:** Implement or remove no-op `api_billback_submit`
8. **HIGH performance:** Switch 365-day prefixes to month-level (3 endpoints)
9. **HIGH performance:** Cache meter data (4 endpoints loading full file each request)
10. **HIGH dedup:** Extract Stage 6 cleanup helper, PDF generation helper

### Next sprint
11. **MEDIUM integrity:** Add ETag-based writes for master bills
12. **MEDIUM integrity:** Fix threading locks on 4 shared state locations
13. **MEDIUM integrity:** Fix `api_billback_archive` broad matching
14. **MEDIUM caching:** Add cache invalidation to 4 missing locations
15. **MEDIUM XSS:** Fix 9 unescaped innerHTML spots in templates
16. **MEDIUM quality:** Extract duplicated DDB parsing, property/vendor lookups
