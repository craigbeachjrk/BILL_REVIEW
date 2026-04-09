# Code Audit — April 2026

Full codebase review: ~45,000 lines Python, ~50 HTML templates, 15 Lambda functions.

## Status Legend
- [ ] Not started
- [x] Fixed
- [-] Won't fix / Not applicable
- [?] Needs clarification from user

---

## CRITICAL (3)

| # | Status | Location | Bug | Impact |
|---|--------|----------|-----|--------|
| C1 | [x] | main.py:23520-23524 | `api_delete_preentrata` uses `review#{sha1}#{idx}` PK format, but `put_status()` stores with raw `{s3_key}#{idx}`. Delete is always a no-op. | Stale "Submitted" badges persist after Pre-Entrata deletion |
| C2 | [x] | main.py:18338+18519 | Two functions named `api_add_to_tracker`. Second silently overwrites first. | Name collision, `url_for` resolves to wrong function |
| C3 | [x] | main.py:32680-32733 | `_compute_vendor_accuracy` ignores `vendor_id` param, aggregates ALL vendors. Comment: "Would need to look up the pdf to get vendor - for now just aggregate all" | Autonomy graduation decisions based on global accuracy, not per-vendor |

---

## HIGH (18)

| # | Status | Location | Bug | Impact |
|---|--------|----------|-----|--------|
| H1 | [x] | main.py:5783-5785 | `b["suggestion"]["confidence"]` crashes when `suggestion` is `None` (non-UBI bills) | TypeError crash on UBI suggestions page |
| H2 | [x] | main.py:6545,6550,6818,6828 | `period in asn.get("period", "")` substring match: `"1/2026"` matches `"11/2026"` | Wrong bills unassigned/reassigned |
| H3 | [x] | main.py:6452,6634 | Missing cache invalidation after UBI unassign. Comments say "CRITICAL: Invalidate..." but code is missing. | UI shows stale data until TTL expires |
| H4 | [x] | main.py:16841,16910,16984 | Snowflake `conn = _snowflake_connect()` without `try/finally`. Exceptions leak connections. | Connection pool exhaustion |
| H5 | [x] | main.py:9508 vs 13873 | Two `_normalize_account_number` definitions with different behavior. Second overwrites first silently. | Account matching failures between code paths |
| H6 | [x] | main.py:20241-20245 | Multi-period amount override sets ALL period assignments to the new amount instead of splitting proportionally | Dollar amounts tripled for 3-month splits |
| H7 | [x] | main.py:14839-14903 | `_get_last_ubi_periods_from_stage8` reads ALL Stage 8 files via S3 GET. Should use filename parsing or bill index. | Minutes-long blocking on AppRunner |
| H8 | [x] | main.py:15345-15432 | `_get_account_bill_history` does 360 sequential S3 prefix scans (no ThreadPoolExecutor) | 12-30 min per call on AppRunner |
| H9 | [x] | main.py:25196-25213 | Paginators `pi` and `rp` already exhausted from prior iteration. Fuzzy fallback search yields nothing. | Fuzzy PDF lookup silently broken |
| H10 | [-] | main.py:5004-5030 | `api_billback_submit` loop body is `pass`. Returns `{"ok": True, "submitted": 0}`. | **DEAD CODE** — see Half-Baked #1 |
| H11 | [-] | bill_review_app/main.py:4872 | `toggle_ubi_tracking` uses `account_number` (snake) vs `accountNumber` (camel) | **DEAD CODE** — bill_review_app/main.py deleted |
| H12 | [-] | bill_review_app/main.py:10040 | `/api/status` takes `user` as Form param, no auth | **DEAD CODE** — bill_review_app/main.py deleted |
| H13 | [-] | bill_review_app/main.py:10507 | `/pdf` endpoint has no authentication | **DEAD CODE** — bill_review_app/main.py deleted |
| H14 | [x] | vacant_electric/web_models.py:161-164 | `isinstance(v, (int,float))` matches before `isinstance(v, bool)` — bools stored as numbers | Data corruption in DynamoDB |
| H15 | [x] | vacant_electric/batch_runner.py:173 | Snowflake connection passed to background thread — caller may close it | Background pipeline failures |
| H16 | [-] | lambda_bill_parser.py:633-868 | Module-level globals (`__EXPECTED_LINES` etc.) — verified: properly reset per-record at lines 820-868 | Not a bug (false positive) |
| H17 | [x] | main.py:24318,24711,23835 | S3 `list_objects_v2` not paginated (max 1000). Stage 6 cleanup misses objects. | Silent data loss if >1000 objects in a day |
| H18 | [x] | main.py:25612,25624 | `api_day`/`api_invoices` crash on malformed date (`y,m,d = date.split("-")` no try/except) | 500 error on bad input |

---

## MEDIUM (35+)

| # | Status | Location | Bug |
|---|--------|----------|-----|
| M1 | [x] | main.py:2494 | `list.remove()` raises `ValueError` on duplicate invoice numbers in sync verification |
| M2 | [x] | main.py:6364,6564,6835 | `except` handler re-parses same invalid JSON line — double crash |
| M3 | [x] | main.py:8239 | Race condition: concurrent S3 read-modify-write on workflow notes |
| M4 | [x] | main.py:8828,8955,9344 | Missing cache invalidation after account archive/update/vendor correction |
| M5 | [x] | main.py:9078 | `isUBI` vs `is_ubi` field name mismatch — always returns False |
| M6 | [x] | main.py:11766 | DynamoDB scan not paginated for user timing metrics |
| M7 | [x] | main.py:12252 | Pipeline "stuck" query reports false positives (stale event snapshot) |
| M8 | [x] | main.py:15067 | Read-modify-write race on S3 UBI account history (cross-instance) |
| M9 | [x] | main.py:17767,17912,17807 | Check slip scan functions missing DynamoDB pagination |
| M10 | [x] | main.py:19586 | `existing['line_items']` KeyError — should be `source_line_items` |
| M11 | [x] | main.py:20540,20567 | Delete manual entry/batch has no admin check |
| M12 | [x] | main.py:21222 | Accrual reads accounts from DDB directly instead of `_get_accounts_to_track()` |
| M13 | [x] | main.py:24778 | `get_status_map` ignores DynamoDB `UnprocessedKeys` under throttling |
| M14 | [x] | main.py:29161 | Report periods: deduped set + `[:100]` — non-deterministic slice |
| M15 | [x] | main.py:30713 | Knowledge search uses DDB `Limit` (eval limit, not result limit) |
| M16 | [x] | main.py:30965 | Admin check uses `jrkholding.com` — doesn't match `ADMIN_USERS` set (`jrk.com`) |
| M17 | [x] | main.py:33310 | Submeter rates race: running check outside `_SUBMETER_RATES_LOCK` |
| M18 | [ ] | main.py:5869 | Accept-suggestion recalculates instead of using what user saw |
| M19 | [x] | main.py:2067 | `_POST_LOCK_NONCES` dict read/written without locking (cross-instance nonce mismatch) |
| M20 | [ ] | main.py:2121 | `_acquire_post_lock` fails open on DDB errors — allows duplicate Entrata posts |
| M21 | [x] | main.py:2863 | `api_advance_to_post_stage` + `api_archive_parsed` missing S3 key validation |
| M22 | [x] | main.py:4830 | `api_billback_archive` matches by only 3 fields — wrong line items can match |
| M23 | [x] | main.py:21438 vs 21533 | Duplicate accrual delete endpoints — second one doesn't bust caches |
| M24 | [x] | main.py:11496 | O(50*N) linear scan of search index per request |
| M25 | [x] | main.py:15175 | `_find_bills_for_account` generates 900 S3 prefix scans |
| M26 | [-] | main.py:26383 | `_ensure_hov` — verified: only fills empty HOV, doesn't overwrite existing. Correct behavior. | Not a bug (false positive) |
| M27 | [x] | post.html:837 | Currency sort regex `[$,\.\.\.]/g` strips decimal points |
| M28 | [x] | review.html:773 | `applyHeaderDraft` queries `.header-card` selector that doesn't exist |
| M29 | [x] | print_checks.html:358 | Single quotes in vendor names break inline `onclick` handlers |
| M30 | [x] | billback.html:1660 | XSS: `renderFilterList` inserts unescaped data into innerHTML |
| M31 | [x] | master-bills.html:807 | XSS: unescaped property/vendor names in `renderMasterBills` |
| M32 | [x] | track.html:500 | Event handlers re-bound on every `render()` call — duplicates accumulate |
| M33 | [x] | lambda_bill_enricher:912 | `gbest` can be `None` → `AttributeError` on `.get()` |
| M34 | [x] | lambda_bill_parser:502+920 | Double retry loop = up to 100 Gemini API calls per file |
| M35 | [x] | vendor-cache-builder:59 | `load_entrata_creds` returns `None` → crash on `creds['base_url']` |
| M36 | [x] | vacant_electric/web_models.py:284,310 | `batch_write_item` ignores `UnprocessedItems` — data loss under throttling |
| M37 | [x] | vacant_electric/property_maps.py:254 | `MAP_CHA` has unbound `prefix` variable for unexpected input |
| M38 | [x] | vacant_electric/property_maps.py:177 | `MAP_BOJ` crashes on unknown street numbers (`int(None)`) |
| M39 | [x] | vacant_electric/test_e2e.py:133 | Test assertion is always True (tautology: `bill_entities <= dispatch_keys | bill_entities`) |
| M40 | [-] | lambda_bill_large_parser:188 | Rework metadata — verified: uses key only for path construction, reads from PENDING_PREFIX | Not a bug (false positive) |

---

## LOW (40+)

| # | Status | Location | Bug |
|---|--------|----------|-----|
| L1 | [-] | main.py:129,154 | Duplicate `REWORK_PREFIX` assignment (same value) |
| L2 | [x] | main.py:100 | `APP_SECRET` insecure default `"dev-secret-change-me"` — check if set in prod |
| L3 | [ ] | main.py:144 | `SCRAPER_API_TOKEN` hardcoded in source |
| L4 | [ ] | main.py:524 | `_ve_ar_client` hardcoded Entrata API key |
| L5 | [-] | Multiple (~20 locations) | Bare `except:` clauses (should be `except Exception:`) |
| L6 | [-] | Multiple (~30 locations) | `datetime.utcnow()` deprecated in Python 3.12+ |
| L7 | [x] | main.py:1407 | `_parse_service_address` regex: `AP` matches inside "APACHE" |
| L8 | [ ] | main.py:1541 | `require_user` raises HTTP 307 — wrong for API POST requests |
| L9 | [-] | main.py:10267 | `if True:` dead conditional in bill index parser |
| L10 | [-] | main.py:9802 | JSON serialization deep copy is slow (could use shallow copies) |
| L11 | [x] | main.py:6416 | `delete_item` never raises `ResourceNotFoundException` — dead except branch |
| L12 | [-] | main.py:10642 | `_cycle` variable initialized but never incremented |
| L13 | [ ] | main.py:7613 | Hardcoded AP supervisor emails |
| L14 | [-] | main.py:9732 | Gap analysis results truncated to 100 per category |
| L15 | [-] | main.py:17519 | `_ddb_get_config` redundant `A or A` expression |
| L16 | [-] | Multiple | `import uuid` / `import re` inside function bodies |
| L17 | [x] | main.py:15347 | `_get_account_bill_history` crashes on malformed `account_key` (missing `|` separator) |
| L18 | [-] | main.py:25270 | `or True` makes pdf_id filter meaningless in REWORK day search |
| L19 | [-] | main.py:22427+ | DynamoDB scans not paginated in debug endpoints (admin-only) |
| L20 | [ ] | lambda_presigned_upload:39 | IP allowlist bypassable via X-Forwarded-For spoofing |
| L21 | [-] | lambda_email_ingest:31,40 | `_CANON_MAP` assigned twice at module load |
| L22 | [x] | lambda_vendor_notifier:343 | HTML template injection in emails (vendor names unescaped) |
| L23 | [ ] | lambda_meter_cleaner vs enricher | CCF→gallons conversion factor inconsistency (748.0 vs 748.052) |
| L24 | [ ] | billback.html:563 | Email signed "Craig Beach" regardless of who sends it |
| L25 | [x] | review.html:2904 | `Ctrl+D` overrides browser bookmark shortcut |

---

## SECURITY SUMMARY

| Issue | Location | Severity | Status |
|---|---|---|---|
| Hardcoded Entrata API key | entrata_send_invoices_prototype.py:23 | HIGH | [ ] Rotate key, move to Secrets Manager |
| Hardcoded Entrata API key (2nd) | main.py:524 | MEDIUM | [ ] Move to env var |
| `APP_SECRET` insecure default | main.py:100 | HIGH if used | [ ] Verify prod env var is set |
| `SCRAPER_API_TOKEN` hardcoded | main.py:144 | MEDIUM | [ ] Move to env var |
| Unsanitized errors leaked to client | main.py:29208,29268,30600+ | LOW | [ ] Use `_sanitize_error()` |
| XSS via innerHTML | billback.html, master-bills.html, review.html | MEDIUM | [ ] Escape data before insertion |
| Stack traces returned to client | bill_review_app/main.py:1039,8712 | MEDIUM | [-] Dead code deleted |
| Lambda HTML template injection | lambda_vendor_notifier:343 | MEDIUM | [ ] HTML-escape template vars |

---

## DEAD CODE DELETED

| File | Lines | Reason | Action |
|---|---|---|---|
| `bill_review_app/main.py` | 10,670 | Never imported/mounted. Root main.py is the production app. Contains conflicting duplicates of `api_add_to_tracker`, `_normalize_account_number`, `_ddb_get_config`, etc. | **Deleted** |
| `bill_review_app/app.py` | 311 | Streamlit prototype. Cannot be served by uvicorn. Never referenced. | **Deleted** |
| `bill_review_app/auth.py` | 275 | Only imported by bill_review_app/main.py (dead). Root auth.py is the active module. | **Deleted** |

---

## HALF-BAKED / NEEDS CLARIFICATION

| # | Status | Location | Question |
|---|--------|----------|----------|
| HB1 | [?] | main.py:5004 | `api_billback_submit` is a no-op (loop body is `pass`). Was this intended for marking billback items as submitted? Is it called from the UI? Remove or implement? |
| HB2 | [?] | main.py:32614-32933 | Autonomy system (`shadow→assisted→autonomous`): `_compute_vendor_accuracy` doesn't filter by vendor, `_check_autonomy_health()` never called on schedule. Is this actively used? |
| HB3 | [?] | main.py:12320-12436 | Autonomy simulation: `historical_flags` list is always empty. What should populate it? |
| HB4 | [?] | vacant_electric/property_maps.py:595 | `MAP_SSG` returns `None` — SSG properties never match. Intentionally excluded or not built yet? |
| HB5 | [?] | vacant_electric/config.py:14 | `"EPS is Weird"` for GL 5708-0000, not in GL_ACCOUNTS. What is EPS? Include in processing? |
| HB6 | [?] | vacant_electric/classifier.py:193 | `get_suggested_action()` defined but never called. Was auto-suggest abandoned? |
| HB7 | [?] | main.py:28013-28292 | Meter management has no admin check. Should meter merge/dismiss be admin-only? |
| HB8 | [?] | main.py:32178 | Duplicate invoice detection uses filename substring matching. Prototype or needs proper index? |
| HB9 | [?] | main.py:9732 | Gap analysis truncated to 100 per category. Need pagination or is 100 sufficient? |
| HB10 | [?] | main.py:7613, billback.html:563 | Hardcoded AP supervisor emails and "Craig Beach" signature. Make configurable? |
| HB11 | [?] | lambda_chunk_processor:954 | `TODO: Trigger aggregator Lambda here` — how is the aggregator actually triggered? |
| HB12 | [?] | vacant_electric/pipeline.py:118-121 | CEH exclusion hardcoded (drops `200C@M14` records). What is this for? |
| HB13 | [?] | main.py:19534 vs 19348 | Two different manual entry tables for master bills. Intentionally separate workflows? |
| HB14 | [?] | main.py:17555 | `_ddb_get_draft`/`_ddb_put_draft` are misnamed — they actually read/write S3. Rename? |

---

## FIXES APPLIED (this session)

### Fix 1: C1 — `api_delete_preentrata` wrong PK format
- **File:** main.py:23520-23524
- **Problem:** Used `review#{sha1}#{idx}` but `put_status()` stores with raw `{s3_key}#{idx}`
- **Fix:** Changed to use `f"{orig_key}#{row_idx}"` (matching `put_status()` format), removed `review#` prefix

### Fix 2: C2 — Duplicate function name `api_add_to_tracker`
- **File:** main.py:18519
- **Fix:** Renamed second function to `api_ubi_add_to_tracker`

### Fix 3: H1 — Crash on non-UBI confidence summary
- **File:** main.py:5783-5785
- **Fix:** Changed to safe access: `(b.get("suggestion") or {}).get("confidence", "")`

### Fix 4: H2 — Substring period matching in unassign/reassign
- **File:** main.py:6545,6550,6818,6828
- **Fix:** Removed `or period in asn.get("period", "")` — use exact match only

### Fix 5: H3 — Missing cache invalidation after UBI unassign
- **File:** main.py:6452,6634
- **Fix:** Added `_CACHE.pop(("ubi_unassigned",), None)` and `_remove_bill_from_ubi_cache(s3_key)` calls

### Fix 6: H9 — Exhausted S3 paginator reuse
- **File:** main.py:25196-25213
- **Fix:** Created fresh paginators for each fuzzy search iteration

### Fix 7: Dead code deletion
- Deleted `bill_review_app/main.py` (10,670 lines), `bill_review_app/app.py` (311 lines), `bill_review_app/auth.py` (275 lines)
- These were conflicting duplicates never imported by the production app

### Fix 8: H4 — Snowflake connection leaks (3 locations)
- **Files:** main.py `_write_to_snowflake`, `_read_historical_from_snowflake`, `_load_invoice_history_cache`
- **Fix:** Wrapped all `conn`/`cursor` usage in `try/finally` blocks to ensure cleanup on exception

### Fix 9: H5 — Duplicate `_normalize_account_number`
- **File:** main.py:9511
- **Fix:** Removed dead first definition (second definition at ~13876 already wins at runtime). Added comment pointing to canonical location.

### Fix 10: H6 — Multi-period amount override
- **File:** main.py:20246
- **Fix:** Changed from setting all periods to `new_amount` to proportional redistribution. Includes rounding correction on last period to ensure sum matches exactly.

### Fix 11: H14 — Bool/int serialization order
- **File:** vacant_electric/web_models.py:161
- **Fix:** Moved `isinstance(v, bool)` check BEFORE `isinstance(v, (int, float))` since `bool` is a subclass of `int`

### Fix 12: H17 — S3 list_objects_v2 pagination (3 locations)
- **Files:** main.py bulk_rework, rework, delete_parsed Stage 6 cleanup
- **Fix:** Replaced `s3.list_objects_v2()` with `s3.get_paginator('list_objects_v2').paginate()` to handle >1000 objects

### Fix 13: H18 — Date validation on api_day/api_invoices
- **File:** main.py:25616,25628
- **Fix:** Wrapped `date.split("-")` in try/except ValueError, returns 400 with clear error message

### Fix 14: M4 — Missing cache invalidation (5 endpoints)
- **File:** main.py archive, restore, update, bulk-update, vendor correction apply
- **Fix:** Added `_CACHE.pop(("accounts_to_track",), None)` and `_CACHE.pop(("workflow_tracker",), None)` after each `_put_accounts_to_track()` call

### Fix 15: M5 — isUBI field name mismatch
- **File:** main.py:9081
- **Fix:** Changed `acct.get("isUBI", False)` to `acct.get("is_ubi", acct.get("isUBI", False))` to handle both naming conventions

### Fix 16: M16 — Wrong admin domain in knowledge base delete
- **File:** main.py:30969
- **Fix:** Changed `user in ("cbeach@jrkholding.com", "admin")` to `user in ADMIN_USERS`

### Fix 17: M27 — Currency sort regex strips decimals
- **File:** templates/post.html:837
- **Fix:** Changed `/$,\.\.\.]/g` to `/[$,]/g` — strips `$` and `,` but preserves decimal point

### Fix 18: M1 — list.remove() ValueError on duplicate invoice numbers
- **File:** main.py:2494
- **Fix:** Added `if inv_num in results["extra_in_entrata"]:` guard before `.remove()`

### Fix 19: M2 — Double JSON parse crash in except handlers (4 locations)
- **Files:** main.py UBI unassign, unassign-account, reassign-account, reassign
- **Fix:** Replaced `json.loads(line)` in except handlers with `{}` — don't re-parse already-failed JSON

### Fix 20: M6 — DynamoDB scan pagination for user timing
- **File:** main.py:11772
- **Fix:** Added `LastEvaluatedKey` pagination loop

### Fix 21: M10 — KeyError on `line_items` in master bill merge
- **File:** main.py:19601
- **Fix:** Changed `existing['line_items'].append()` to `existing.setdefault('line_items', []).append()`

### Fix 22: M11 — Delete manual entry/batch missing admin check
- **Files:** main.py:20572, 20610
- **Fix:** Changed `Depends(require_user)` to `Depends(require_admin)` on both delete endpoints

### Fix 23: M12 — Accrual reads stale accounts from DDB
- **File:** main.py:21265
- **Fix:** Replaced manual DDB+S3 fallback with `_get_accounts_to_track()` (canonical function)

### Fix 24: M13 — get_status_map ignores UnprocessedKeys
- **File:** main.py:24823
- **Fix:** Added retry loop for `UnprocessedKeys` from `batch_get_item`

### Fix 25: M17 — Submeter rates race condition
- **File:** main.py:33363
- **Fix:** Moved `st["running"]` check inside `_SUBMETER_RATES_LOCK` block

### Fix 26: M21 — Missing S3 key validation on advance/archive endpoints
- **Files:** main.py:2879, 2922
- **Fix:** Added `_require_valid_s3_key()` calls to both `api_advance_to_post_stage` and `api_archive_parsed`

### Fix 27: M28 — review.html header draft selector doesn't exist
- **File:** templates/review.html:773
- **Fix:** Changed `document.querySelector('.header-card')` to `document.getElementById('headerBox')`

### Fix 28: L17 — account_key split crash on malformed input
- **File:** main.py:15353
- **Fix:** Added validation: `parts = account_key.split("|"); if len(parts) != 3: return []`

### Fix 29: M9 — Check slip scan functions missing DDB pagination (3 functions)
- **Files:** main.py `_ddb_list_check_slips_by_status_date`, `_ddb_list_check_slips_by_user`, `_ddb_list_all_check_slips_for_date`
- **Fix:** Added `LastEvaluatedKey` pagination loops to all three scan functions

### Fix 30: M15 — Knowledge search DDB Limit (eval limit, not result limit)
- **File:** main.py:30788
- **Fix:** Replaced `Limit=limit` with pagination loop that continues scanning until `limit` filtered results are collected

### Fix 31: M23 — Duplicate accrual delete endpoint
- **File:** main.py:21600
- **Fix:** Removed incomplete `/api/accrual/entry` DELETE (query-param version). Canonical endpoint `/api/accrual/entry/{entry_id}` deletes from both tables and busts caches.

### Fix 32: M24 — O(50*N) linear scan of search index
- **File:** main.py:11490
- **Fix:** Pre-index search entries by account into a dict for O(1) lookup. Vendor/property fallback still uses linear scan (unavoidable for substring matching).

### Fix 33: M29 — print_checks.html single quotes in vendor names break onclick
- **File:** templates/print_checks.html:339,358+
- **Fix:** Added `vendorKeyJs` with escaped quotes/backslashes; used in all inline onclick handlers. Also added single-quote escaping to `escapeHtml`.

### Fix 34: M30 — billback.html XSS in renderFilterList
- **File:** templates/billback.html:1660
- **Fix:** Wrapped `item` values with `escapeHtml()` before inserting into innerHTML

### Fix 35: M31 — master-bills.html XSS in renderMasterBills
- **File:** templates/master-bills.html:812
- **Fix:** Added `escapeHtml` function and applied it to property_name, utility_name, chargeCodeDisplay, property_lookup_code

### Fix 36: M32 — track.html event handlers re-bound on every render()
- **File:** templates/track.html:500
- **Fix:** Added `_renderBound` guard so event listeners are only attached on first render call

### Fix 37: M37 — MAP_CHA unbound prefix variable
- **File:** vacant_electric/property_maps.py:241
- **Fix:** Initialize `prefix = ""` and return `[None, None]` when no prefix matched

### Fix 38: M38 — MAP_BOJ crashes on unknown street numbers
- **File:** vacant_electric/property_maps.py:171
- **Fix:** Added `if bldg_id is None: return [None, None]` guard before `int(bldg_id)`

### Fix 39: M39 — test_e2e tautology assertion
- **File:** vacant_electric/test_e2e.py:133
- **Fix:** Changed `bill_entities <= dispatch_keys | bill_entities` to `bill_entities <= dispatch_keys`

### Fix 40: M22 — Billback archive matches too loosely
- **File:** main.py:4837
- **Fix:** Added `Bill Date` to the matching criteria (was only Account Number + Bill Period Start/End)

### Fix 41: M33 — Lambda enricher `gbest` can be None
- **File:** lambda_bill_enricher.py:912
- **Fix:** Changed `gbest.get(...)` to `(gbest or {}).get(...)` to handle None case

### Fix 42: M35 — vendor-cache-builder returns None on credential failure
- **File:** build_vendor_cache.py:58
- **Fix:** Changed bare `pass` to `raise RuntimeError(...)` with descriptive message

### Fix 43: M36 — DynamoDB batch_write_item ignores UnprocessedItems
- **File:** vacant_electric/web_models.py:284,310
- **Fix:** Added retry loops for `UnprocessedItems` in both `delete_batch()` and `put_lines_batch()`

### Fix 44: H15 — Snowflake connection leak in batch_runner
- **File:** vacant_electric/batch_runner.py:342
- **Fix:** Added `finally` block to close `snowflake_conn` after worker completes (success or failure)

### Fix 45: M14 — Report periods non-deterministic
- **File:** main.py:29236
- **Fix:** Replaced 558 day-level prefixes (deduped via set → random order) with 18 month-level prefixes (deterministic, faster)

### Fix 46: M19 — POST_LOCK_NONCES thread safety
- **File:** main.py:2067
- **Fix:** Added `_POST_LOCK_NONCES_LOCK = threading.Lock()` and wrapped all dict accesses (set/get/pop)

### Fix 47: M34 — Lambda parser double retry loop (up to 100 calls)
- **File:** lambda_bill_parser.py:920
- **Fix:** Reduced outer loop from `MAX_ATTEMPTS` (10) to `min(3, len(keys))`. Inner loop still handles content retries.

### Fix 48: L7 — Regex `AP` matches inside words like "APACHE"
- **File:** main.py:1407
- **Fix:** Removed standalone `AP` from keyword list (already covered by `APT`), added `\b` word boundary after keyword group

### Fix 49: L11 — Dead `ResourceNotFoundException` except branch
- **File:** main.py:6429
- **Fix:** Removed unreachable `except ddb.exceptions.ResourceNotFoundException` (DDB delete_item is idempotent)

### Fix 50: L22 — Lambda vendor notifier HTML template injection
- **File:** lambda_function.py:343
- **Fix:** HTML-escape all string event values before formatting into HTML template body

### Fix 51: L25 — Ctrl+D overrides browser bookmark
- **File:** review.html:2905
- **Fix:** Skip shortcut when focus is on `input`, `textarea`, or `select` elements

### Fix 52: H7 — `_get_last_ubi_periods_from_stage8` performance
- **File:** main.py:14848
- **Fix:** Added S3-cached results pattern (`config/ubi_last_periods_cache.json.gz`). Serves from S3 cache immediately, rebuilds in background thread. Cache survives deploys and is shared across instances.

### Fix 53: H8 — `_get_account_bill_history` 360 sequential S3 scans
- **File:** main.py:15434
- **Fix:** Replaced 360 day-level prefixes with ~12 month-level prefixes. Added filename-based account filtering. Added parallel reads with `ThreadPoolExecutor(max_workers=10)`.

### Fix 54: M7 — Pipeline stuck query false positives
- **File:** main.py:12293
- **Fix:** For each "stuck" candidate, verify current stage by querying latest event. Skip if bill has moved on.

### Fix 55: M25 — `_find_bills_for_account` 900 S3 prefix scans
- **File:** main.py:15265
- **Fix:** Replaced 900 day-level prefixes with ~30 month-level prefixes. Added filename-based account filtering before reading. Still uses parallel workers.

### Fix 56: L2 — APP_SECRET insecure default
- **File:** main.py:101
- **Fix:** Added startup warning when default secret is used in deployed environment (checks `AWS_EXECUTION_ENV`)

### Fix 57: C3 — `_compute_vendor_accuracy` ignores vendor_id
- **Files:** main.py:31771 (_track_ai_accuracy), main.py:31284 (_save_ai_suggestion), main.py:32862 (_compute_vendor_accuracy)
- **Fix:** (1) Added `vendor_id` and `property_id` fields to ACCURACY and SUGGESTION DynamoDB records at write time. (2) Updated `_compute_vendor_accuracy` to filter by `vendor_id` in the scan FilterExpression, with optional `property_id` post-filter. Old records without vendor_id will be excluded from per-vendor queries but still appear in global queries.

### Fix 58: M3 — Workflow notes S3 race condition
- **Files:** main.py:8114 (_s3_get_workflow_notes), main.py:8127 (_s3_put_workflow_notes), main.py:8217 (single save), main.py:8277 (bulk save)
- **Fix:** Added ETag-based optimistic locking. `_s3_get_workflow_notes(return_etag=True)` returns the S3 ETag. `_s3_put_workflow_notes(notes, expected_etag=etag)` uses S3 `IfMatch` conditional write. Both save endpoints retry up to 3 times on ETag mismatch (concurrent modification).

### Fix 59: M8 — UBI account history S3 race condition
- **Files:** main.py:14812 (_s3_get_ubi_account_history), main.py:14824 (_s3_put_ubi_account_history), main.py:15204 (_update_ubi_account_history)
- **Fix:** Same ETag-based optimistic locking pattern. `_update_ubi_account_history` now retries up to 3 times if the S3 conditional write fails due to concurrent modification from another instance.

### Verified as not-a-bug (false positives):
- H16: Lambda parser globals — properly reset per-record at lines 820-868
- M26: `_ensure_hov` — only fills empty HOV, doesn't overwrite existing values
- M40: Large parser metadata — uses key only for path construction, reads from PENDING_PREFIX
