# Module 2 — Parse & Input (Pipeline Entry Point)

**Scope of review:**
- `main.py:1604-1703` — list_dates, load_day, _fetch_s3_file helpers
- `main.py:1239-1275` — `_retrigger_uppercase_pdfs` + background loop
- `main.py:1277-1416` — `@app.on_event("startup")` block (all cache pre-warming)
- `main.py:3427-3497` — `parse_dashboard` (`/parse` → `index.html`)
- `main.py:3500-3507` — `input_view` (`/input` → `input.html`), `search_view` (`/search` → `search.html`)
- `main.py:3510-3528` — `_calc_invoice_total`
- `main.py:3531-3860` — Search Index Builder (save/load/remove/build + 3 endpoints)
- `main.py:3863-3924` — `api_upload_input`, `api_retrigger_pending_pdfs`
- `main.py:3927-4738` — Scraper Import APIs (~800 lines)
- Templates: `input.html` (1,361 lines), `search.html`, `index.html`
- Lambdas: `jrk-bill-router`, `jrk-bill-parser` (header), `jrk-bill-large-parser`, `jrk-bill-chunk-processor`, `jrk-email-ingest` (header), `jrk-presigned-upload`
- DDB: `jrk-bill-router-log`, `jrk-bill-pipeline-tracker`, `jrk-bill-parser-errors`, `jrk-bill-ai-suggestions` (cached scraper dates)

---

## 1. Module Purpose (Business)

This module is **the entry point** to the Bill Review pipeline. Its one job: **get a PDF utility bill into the system, parsed and discoverable, so downstream review can happen**. Secondary job: let users find a specific bill once it's in the system.

Three ingestion paths exist:
1. **Web form upload** — AP clerk drags-and-drops a PDF (or many PDFs) at `/input`
2. **Scraper import** — pulls PDFs harvested automatically from utility company websites (external scraper service in its own S3 bucket)
3. **Email ingest** — bills arriving via `@jrk.com` email addresses get PDFs extracted and queued automatically

All three funnel into `Bill_Parser_1_Pending_Parsing/`, which triggers the asynchronous Lambda pipeline (router → parser → enricher).

## 2. User Personas & Roles

| Persona | What they do in this module |
|---|---|
| **AP Clerk** | Uploads bills via `/input`, browses scraper archive, imports selected PDFs, searches for specific invoices, watches `/parse` dashboard for what's pending review |
| **System Admin** | Runs "Retrigger Uppercase .PDFs" (admin-only), rebuilds search index |
| **Scraper automation** | No login — dumps PDFs into scraper bucket out-of-band; users then pull via `/api/scraper/import` |
| **Email sender (vendors)** | No login — sends bill as email attachment; `jrk-email-ingest` Lambda processes |

## 3. End-to-End Workflow Walkthrough

### 3a. Web upload (happy path)
1. User visits `/input` → renders `templates/input.html`
2. User drags one or more PDFs into drop zone
3. Client JS POSTs each file to `/api/upload_input` (multipart form)
4. Backend validates `.pdf` extension; rejects non-PDF
5. Backend reads bytes, validates non-empty
6. Backend normalizes filename extension to lowercase: `foo.PDF` → `foo.pdf`
7. Backend constructs S3 key: `Bill_Parser_1_Pending_Parsing/{yyyymmddThhmmssZ}_{basename}.pdf`
8. Backend `s3.put_object(...)` writes the file
9. Returns `{ok: true, key: "..."}`
10. S3 event fires `jrk-bill-router` Lambda (matches `.pdf` lowercase only)
11. Router reads size + page count, routes to `Bill_Parser_1_Standard/` (≤10 pages, ≤10MB) or `Bill_Parser_1_LargeFile/`
12. S3 event on destination triggers `jrk-bill-parser` (for Standard) or `jrk-bill-large-parser` (for LargeFile)
13. Parser uses Gemini 2.5 Pro to extract line items → writes to `Bill_Parser_2_Parsed_Inputs/yyyy=YYYY/mm=MM/dd=DD/foo.jsonl`
14. Enricher Lambda reads S2, adds vendor/property/GL enrichment → writes to `Bill_Parser_4_Enriched_Outputs/yyyy=YYYY/mm=MM/dd=DD/foo.jsonl`
15. Search index background thread picks up new date on next rebuild cycle
16. Bill appears on `/parse` dashboard under its parse date
17. Bill becomes searchable via `/api/search`

### 3b. Web upload (uppercase `.PDF` edge case)
Same as 3a, but step 6 normalizes the extension. Safe from the /api/upload_input path.

However, uppercase `.PDF` bills show up from **scanners** and external scraper imports that don't go through `/api/upload_input`:
- Background thread `_uppercase_pdf_retrigger_loop` runs every 10 minutes
- Scans S1 for `*.PDF`, renames to `*.pdf` (copy-then-delete)
- New S3 event fires → router picks it up
- **Lag: up to 10 minutes** between arrival and pipeline resume

Admin can force-run the scan via `/api/retrigger_pending_pdfs` button (hardcoded `ADMIN_USERS` check).

### 3c. Scraper import
1. User visits `/input` → sees "Scraper Import" section
2. Client calls `GET /api/scraper/providers` — returns list of integrations (e.g., "PECO Energy", "DTE", "ComEd") with metadata: utility_type, service_region, account_count, latest_statement_date
3. User picks provider → `GET /api/scraper/accounts/{provider_folder}` lists accounts
4. User picks account → `GET /api/scraper/pdfs/{provider_folder}/{account_folder:path}` lists PDFs
5. User selects PDFs → clicks "Extract Dates" (optional) → `POST /api/scraper/extract-dates` calls Gemini Flash to pull service dates from each PDF preview (cached to `jrk-bill-ai-suggestions`)
6. User clicks "Import" → `POST /api/scraper/import` copies selected PDFs from scraper bucket to S1
7. Rest of flow matches 3a

### 3d. Email ingest (external to this app)
1. Vendor emails bill to `jrk.com` address
2. SES routes to `jrk-email-ingest` Lambda
3. Lambda extracts PDFs from attachments, filters by filename canonical map (env var `ATTACHMENT_CANON_MAP`)
4. Writes email body to `jrk-email-partitioned-us-east-1/emails/` + attachments to `attachments/`
5. Writes PDFs to `jrk-analytics-billing/Bill_Parser_1_Pending_Parsing/` → pipeline
6. User never sees this happen; PDFs just appear in `/parse` dashboard

### 3e. Parse dashboard view
1. User visits `/parse?limit=8&offset=0`
2. Server calls `list_dates()` → paginates S3 `Bill_Parser_4_Enriched_Outputs/` looking for `yyyy=`/`mm=`/`dd=` partitions, returns sorted list (cached)
3. Server slices list to first 8 days
4. For each day, spawns thread in pool (10 workers) → calls `day_status_counts(y, m, d)` (defined at main.py:25613, in Review module)
5. Each thread returns `{REVIEW, PARTIAL, COMPLETE}` counts for that day
6. Template renders `index.html` with date cards + Load More button
7. User clicks "Load More" → server returns next 8 days

### 3f. Search
1. User visits `/search` → renders `search.html` with form (account, vendor, property, date range)
2. User submits → client calls `GET /api/search?account=X&vendor=Y&property=Z&start_date=A&end_date=B`
3. Server checks `_SEARCH_INDEX["ready"]`; if not, returns 503 with `indexing: true`
4. Server iterates in-memory entries, contains-match (case-insensitive), date filter
5. Results capped at 500, sorted by date desc
6. Returns `{results, truncated, index_size, index_dates}`

---

## 4. 🚨 Clunkiness / Workflow Gaps

### 4a. Uppercase `.PDF` problem has 3 overlapping mitigations → **[ISSUE-020]**

The root cause: the S3 event filter for `Bill_Parser_1_Pending_Parsing/` matches only lowercase `.pdf`. Scanners, external scraper exports, and some email attachments save with `.PDF`. Bills with uppercase extension sit in S1 invisibly.

**Three overlapping fixes exist:**
1. `/api/upload_input` normalizes extension on web upload (main.py:3881-3883)
2. `_uppercase_pdf_retrigger_loop` background thread scans every 10 min and renames (main.py:1268-1274)
3. `/api/retrigger_pending_pdfs` admin endpoint for on-demand scan (main.py:3896)

**Problems:**
- 10-minute lag for scanner/scraper/email uploads where the normalization doesn't happen pre-upload
- User sees uploaded bill as "not here" for up to 10 minutes — confusing
- Three separate code paths doing the same thing
- The admin button existed before the background loop — stale UX

**Simpler fix:** configure the S3 event notification to match `.pdf` and `.PDF` (or unfilter by extension and let the router validate). One line of infrastructure config, delete ~40 lines of Python, no 10-minute lag.

**User Job Affected:** "I uploaded my bill, why isn't it showing up?" — repeatedly.

### 4b. Router intermediate stages undocumented → **[ISSUE-021]** (DATA / DRIFT)

The canonical data model (`CLAUDE.md`, `04_data_architecture.md`) shows 9 stages: S1 (Pending), S2 (Parsed), S4 (Enriched), S6, S7, S8, S9, S99 (Archive). But the router Lambda routes to undocumented intermediates:
- `Bill_Parser_1_Standard/` — router output for ≤10 page, ≤10MB PDFs
- `Bill_Parser_1_LargeFile/` — router output for larger PDFs
- `Bill_Parser_3_Parsed_Outputs/` — referenced in parser Lambda env vars

Where does "Stage 3" fit in our mental model? Is it a legacy stage, an intermediate we shouldn't mention, or missing from docs? Needs investigation.

### 4c. `/parse` renders `index.html` not `parse.html` → **[ISSUE-022]** (UX / TECH-DEBT)

Template naming is misleading. Landing page (home `/`) renders `landing.html`, and `/parse` renders `index.html`. A reasonable new developer will assume `/parse` uses `parse.html`. Worth renaming or at least documenting.

### 4d. No user attribution on uploads → **[ISSUE-023]** (JTBD / INTEGRATION)

`api_upload_input` accepts the file, writes to S3, returns `{ok, key}`. The user who uploaded is **not stored anywhere** — not in S3 object metadata, not in pipeline tracker, not in any DDB table. So when a bill causes problems, "who uploaded this?" is unanswerable.

This matters for:
- Metrics (who's uploading bills, how many per day) — **breaks the SSO migration scoping concern**
- Audit (needed for TODO-AUTH-005 audit log)
- Accountability (fraud prevention, compliance)

**Fix:** write the user to S3 object metadata via `x-amz-meta-uploader` on `put_object`. Router Lambda can propagate it via CopyObject metadata options.

### 4e. Search index cap silently truncates → **[ISSUE-024]** (UX)

`/api/search` caps at 500 results, returns `truncated: true`. But there's no way to paginate beyond 500 — no cursor, no offset, no sort parameter. If you have 600 bills matching "PECO Energy 2025", only the first 500 (by iteration order, which is insertion order — roughly chronological but not guaranteed) come back. Users silently lose the last 100.

**Fix:** Add `offset` / `limit` params, or add `order_by` (by date asc/desc).

### 4f. Search "contains" matching is naive → **[ISSUE-025]** (UX)

`_SEARCH_INDEX` stores lowercased fields. Search is `account in entry["account_l"]`. So:
- Search "ACC-123" doesn't match "ACC123"
- Search "PECO" matches "PECO Energy" ✓
- Search "CON EDISON" matches "CON_EDISON"? No — space ≠ underscore

No normalization. For a tool used daily, the search feels dumb to power users. Options:
- Strip punctuation/spacing before comparing
- Fuzzy match (Levenshtein) with a threshold
- Accept partial account match by numeric suffix

### 4g. Scraper API has CSV fallback → **[ISSUE-026]** (TECH-DEBT / DATA)

If the scraper API is down, `_load_scraper_mappings()` falls back to `integration_uuid_provider_map.csv` and `account_uuid_provider_map.csv` (main.py:3978-4004). These CSV files:
- Are read from the app's source directory (bundled at build time)
- Are stale by design — they reflect whatever was true when last built
- Silently take over when the API errors

Consequence: during a scraper API outage, users see stale providers and might import bills that no longer match the current scraper schema. Better: hard-fail with "Scraper API unavailable, try again later" rather than serve stale data.

### 4h. Pipeline tracker errors silently swallowed → **[ISSUE-027]** (OBSERVABILITY)

The `_pipeline_track` function in router, parser, and email-ingest Lambdas all have:
```python
except Exception:
    pass  # Non-blocking — never fail the Lambda over tracking
```

Intent is good (don't kill the pipeline over a logging failure). But there's no counter, no CloudWatch metric, no alarm. If the tracker table is misconfigured, pipeline lifecycle events are silently dropped. The only way to know is to check the tracker table and notice it's empty.

**Fix:** at minimum, emit a CloudWatch metric `PipelineTrackerWriteFailed` and alarm on non-zero rate.

### 4i. Router doesn't validate PDF integrity → **[ISSUE-028]** (DATA)

Router uses `PyPDF2.PdfReader(pdf_bytes).pages` to count pages. On a corrupted/truncated PDF, PyPDF2 might:
- Raise an exception → returns `-1`, defaults to `standard` route (main.py/router_lambda:130-131)
- Return 0 pages silently
- Return a wrong page count

A corrupt PDF routed to standard parser will fail parsing downstream. No upfront rejection — the file journeys all the way to `Bill_Parser_Failed_Jobs/` before anyone learns it was garbage. If we validated at router ingress, we'd save Lambda costs and give users faster failure feedback.

### 4j. `list_dates()` scans entire `ENRICH_PREFIX` → **[ISSUE-029]** (PERF)

`list_dates()` paginates every object in `Bill_Parser_4_Enriched_Outputs/` and extracts `yyyy=`/`mm=`/`dd=` prefix partitions from keys. Grows linearly with data. Cached for CACHE_TTL_SECONDS (not read inline but probably 300s per `_get_cache_ttl`).

Scalability concern: at 100K invoices, list_objects_v2 call is 100 pages (1000/page), ~5-10 seconds on AppRunner's slow S3 GET. First request after cache expires is slow.

**Mitigation path:** maintain an explicit set of known dates (in DDB or config/dates.json) updated by the enricher Lambda on each write. O(1) lookup.

### 4k. `load_day()` fires 50 concurrent S3 GETs → **[ISSUE-030]** (PERF)

`ThreadPoolExecutor(max_workers=50)` (main.py:1690) for fetching a day's JSONL files. On AppRunner (2 instances × 50 threads = 100 concurrent S3 GETs possible). S3 limits are generous but this saturates the connection pool quickly.

Given AppRunner's slow S3 GET (~2-5s per request per memory notes), 50 concurrent helps, but also 50 threads × 500ms startup time = 25 seconds of thread overhead in the worst case.

### 4l. Search rebuild on startup is slow without S3 cache → **[ISSUE-031]** (PERF / STARTUP)

`_search_index_backfill()` (main.py:3375-3395) on startup:
- Tries `_load_search_index_from_s3()` first (fast, ~5 seconds)
- If that fails, does `force_full=True` which iterates every date via `_index_one_day` → each calls `load_day()` → each does 50-concurrent S3 GETs

Starting from scratch, 300 dates × (300 invoices/day × 2s per file) = hours. The S3 cache is critical. If it gets corrupted, the next deploy is effectively down until backfill completes.

**Fix:** track the age of the S3 cache, alert if it's > 24h old. Also consider batching the rebuild across multiple startups (resume from last-indexed date).

### 4m. Failed parses disappear → **[ISSUE-032]** (JTBD / OBSERVABILITY)

When the parser fails on a bill, the PDF moves to `Bill_Parser_Failed_Jobs/` and an error is logged to `jrk-bill-parser-errors` DDB. But from the user's perspective on `/parse` dashboard, the bill **never appears**. There's no row saying "PDF X failed to parse — retry?".

Users have to navigate to the Failed Jobs module (`/failed`) which is a separate UX. But most users don't know that module exists, so bills fail silently.

**Fix:** surface failed-parse count on `/parse` dashboard alongside REVIEW/PARTIAL/COMPLETE. Or, better, have a dedicated "Pending Parse / Failed" section.

### 4n. Rework invalidates search but re-parse doesn't update → **[ISSUE-033]** (DATA INTEGRITY)

`_search_index_remove(pdf_ids)` is called when bills are reworked or deleted. After removal, until the next `_build_search_index(force_full=False)` cycle runs, the bill is invisible to search even after successful re-parse.

Incremental rebuild only runs on startup and via explicit call — it doesn't have a polling loop. So between rework and server restart, search misses the re-parsed bill.

**Fix:** schedule a recurring incremental rebuild (every 5 minutes on a background thread) OR have the rework endpoint trigger an async rebuild of just the date impacted.

### 4o. Unlinked accounts use a sentinel constant → **[ISSUE-034]** (TECH-DEBT)

`_UNLINKED_SENTINEL = "__unlinked__"` (main.py:4006) acts as a fake provider folder for accounts not tied to any integration. Works, but:
- Fragile: any scraper folder accidentally named `__unlinked__` would collide
- Opaque: the UI shows "Unlinked Accounts" as a virtual provider; new users don't know where these came from

Suggests the scraper's data model has holes in it (accounts without integrations). Investigate whether we should push back on scraper to provide clean mappings.

### 4p. Scraper import blocks on S3 copies → **[ISSUE-035]** (PERF / UX)

`/api/scraper/import` copies each PDF one-by-one via `s3.copy_object` (S3 cross-bucket). If user selects 50 PDFs, that's 50 sequential copies. No progress bar on frontend. If AppRunner times out (default 120s), import fails partially and retrying imports duplicates.

**Fix:** batch copies in parallel (like `load_day` does with ThreadPool), return a job ID, let client poll.

### 4q. Presigned upload Lambda exists but isn't used by `/api/upload_input` → **[ISSUE-036]** (TECH-DEBT)

`aws_lambdas/us-east-1/jrk-presigned-upload/code/lambda_presigned_upload.py` exists to generate presigned S3 URLs (~87 lines). This would let the browser upload directly to S3, bypassing the AppRunner app entirely.

But `/api/upload_input` (main.py:3867) reads the entire file body into memory and does `s3.put_object` through the app. For >30MB files this fails due to AppRunner request size limits (and memory).

Either:
- Use `jrk-presigned-upload` in the `/input` UI (requires frontend work)
- Delete the Lambda if it's not needed

Currently a half-built feature. Classic "design complete, integration missing" drift.

---

## 5. Integration Gaps

### 5a. Per-user attribution gap (feeds ISSUE-023)
Uploads don't record who uploaded. Propagates forward to metrics, audit log, directed work.

### 5b. Failed parses → rework handshake
`/api/rework` (Review module) writes bills to `Bill_Parser_Rework_Input/`. But failed parses go to `Bill_Parser_Failed_Jobs/` and require manual retrigger via Failed Jobs module. Two separate flows for what's fundamentally the same concern ("reparse this"). Consolidation opportunity.

### 5c. Scraper integration is read-only
App can list and import from scraper, but cannot push back. If a PDF was imported but turns out to be the wrong one, there's no way to tell scraper "skip this next time". Scraper thinks it did its job.

### 5d. Email ingest is a black box
Separate Lambda, separate bucket (`jrk-email-partitioned`), separate config. No app-level visibility into "emails received today that didn't produce bills" (e.g., non-PDF attachments, spam, etc.). Debugging email ingest requires SSH to Lambda logs.

### 5e. Stage 3 ambiguity
Parser Lambda env `PARSED_OUTPUTS_PREFIX = "Bill_Parser_3_Parsed_Outputs/"` — not in our documented stage list. Either it's dead, or our stage list is wrong. Investigate.

---

## 6. Feature Inventory (UI vs. what works)

| Feature | UI exists? | Works end-to-end? | Notes |
|---|---|---|---|
| Drag-drop PDF upload | ✅ (`input.html`) | ✅ | For files that fit in memory; >30MB may fail |
| Multiple simultaneous uploads | ✅ | ✅ | Sequential, one POST per file |
| Progress indicator per file | Partial | Partial | Client shows "uploading..." but no byte progress |
| Scraper provider browser | ✅ | ✅ | Metadata from scraper API; CSV fallback if API down |
| Scraper account browser | ✅ | ✅ | Shows PDF counts per account |
| Scraper PDF preview | ✅ | ✅ | Per-PDF list |
| Scraper date extraction (Gemini) | ✅ | ✅ | Cached to DDB; 1-shot per PDF |
| Scraper import (selected) | ✅ | ✅ | Sequential, no progress |
| Scraper all-PDFs for provider | ✅ | ✅ | Massive operation; used sparingly |
| Parse dashboard (days) | ✅ | ✅ | Lazy-loads status counts per day |
| Parse dashboard "Load More" | ✅ | ✅ | |
| Parse dashboard date range filter | ✅ | ✅ | |
| Search by account/vendor/property | ✅ (`search.html`) | Partially | 500 cap silent; naive match |
| Search index status indicator | ✅ | ✅ | Shows "still indexing" message |
| Admin: rebuild search index | ✅ | ✅ | `ADMIN_USERS` gated |
| Admin: retrigger uppercase PDFs | ✅ | ✅ | `ADMIN_USERS` gated (redundant with auto-loop) |
| Email inbound | N/A | ✅ | External; no app UI |
| Failed-parse visibility on /parse | ❌ | — | Failed bills hidden |
| Upload resumability | ❌ | — | No chunked upload |
| Presigned direct-to-S3 upload | ❌ (Lambda exists, unused) | — | |
| Per-user upload tracking | ❌ | — | No attribution |
| Pagination of search results | ❌ | — | 500 cap silent |
| Search fuzzy matching | ❌ | — | Literal substring only |
| "Files I uploaded today" view | ❌ | — | Would need per-user attribution |

---

## 7. Technical Implementation

### 7a. Helpers (main.py:1604-1702)

**`list_dates()`** — Paginates `ENRICH_PREFIX`, extracts partitioned yyyy=/mm=/dd= triplets, returns sorted list. Cached in `_CACHE[("list_dates",)]` with TTL.

**`_fetch_s3_file(key)`** — Single-file JSONL reader; injects `__s3_key__`, `__row_idx__`, `__id__` fields. Broad try/except returning empty list on error.

**`load_day(y, m, d, force_refresh=False)`** — Lists JSONL files in day partition, dedupes LARGEFILE variants (if both `foo.jsonl` and `foo_LARGEFILE_.jsonl` exist, drops the normal one). 50-concurrent ThreadPool fetch. Cached per day with `_get_cache_ttl()` (longer TTL for past days per memory convention).

### 7b. Uppercase PDF handler (main.py:1239-1274)

`_retrigger_uppercase_pdfs()` + `_uppercase_pdf_retrigger_loop()` — 30-second startup delay, then every 600 seconds: scan INPUT_PREFIX for `*.PDF`, copy-rename to `*.pdf`, delete original. Blind catch-all exception handling. 2 AppRunner instances means this runs 2x concurrently — not harmful (S3 copy+delete is idempotent) but wasteful.

### 7c. Startup pre-warming (main.py:1277-1416)

`@app.on_event("startup")` fires many background threads:
1. `prewarm()` — exclusion hash cache (90-day lookback)
2. `prewarm_invoice_cache()` — invoice history
3. `_vendor_pair_refresh_loop` — vendor-property/GL pair history (refresh loop)
4. `prewarm_post_helpers()` — vendor cache, GL maps, accounts-to-track
5. `ubi_cache_startup_and_poll` — loads UBI cache from S3, polls ETag every 5 min
6. `_invoice_history_refresh_loop` — refresh every 2 hours
7. `_audit_digest_loop` — daily email at 5PM Pacific
8. `_search_index_backfill` — load from S3, do incremental catch-up
9. `_search_index_refresh_loop` — periodic rebuild (cut off in my read)

All `daemon=True`, so process exit doesn't wait. 9+ concurrent threads. AppRunner cold-start time is noticeable after deploy.

### 7d. Search index (main.py:3531-3860)

In-memory dict: `_SEARCH_INDEX = {"entries": [], "dates_indexed": set(), "by_date": {}, "ready": False, "loading": False, "entry_count": 0, "last_refresh": 0}` with `_SEARCH_INDEX_LOCK: threading.Lock`.

Persistence: `Bill_Parser_Config/search_index.json.gz` — gzipped JSON, stripped of `_l` lowercase fields (rebuilt on load).

Build path:
1. If not force_full, find dates in `list_dates()` not in `dates_indexed` (+ always re-index today)
2. ThreadPool(10 workers) `_index_one_day` for each
3. Merge into existing entries
4. Also query DDB `POSTED_INVOICES` for posted bills not in S4
5. Persist to S3

### 7e. Scraper integration (main.py:3927-4738)

- `_fetch_scraper_integrations()` — scraper API `/api/integrations` with 5-min TTL cache, Bearer token from `SCRAPER_API_TOKEN` env var
- `_load_scraper_mappings()` — maps UUID to provider; API-first, CSV fallback
- `_list_unlinked_account_folders()` — root-level non-UUID folders (skip special: `traces`, `unknown`, `deploy`, `aps`)
- `_build_account_map_from_s3()` — 10-concurrent ThreadPool scan across integrations

Scraper bucket structure:
- `{integration_uuid}/bills/{provider_name}/{account_number}/` — official
- `{account_number}/` — flat, legacy/orphaned
- Special skip list: `traces`, `unknown`, `deploy`, `aps`

Endpoints (9 total): providers, accounts, pdfs (per-account), all-pdfs (whole provider), import (copies PDFs to S1), extract-dates (Gemini), save-dates (cache), get-cached-dates.

### 7f. Scraper date extraction (main.py:4610)

`_extract_dates_from_pdf` — Gemini Flash with structured prompt asking for service dates. Cached per-PDF in `jrk-bill-ai-suggestions` table with TTL (default 30 days). Users can override by calling `save-dates` with manual values.

### 7g. File name convention

Upload: `{INPUT_PREFIX}{yyyymmddThhmmssZ}_{sanitized_basename}.pdf`
Post-enrichment: `{Property}-{Vendor}-{Account}-{StartDate}-{EndDate}-{BillDate}_{timestamp}.jsonl` (per memory)

---

## 8. Data Touchpoints

### DDB
| Table | Usage |
|---|---|
| `jrk-bill-config` | `_CACHE` in-memory only; not DDB (clarification: search index persists to S3, not DDB) |
| `jrk-bill-ai-suggestions` | `SUGGESTION#PDF_ID` keys for cached scraper dates |
| `jrk-bill-router-log` | Router decisions: `ROUTE#filename` |
| `jrk-bill-parser-errors` | Parser errors: `PDF_ID#ERROR_TYPE` |
| `jrk-bill-pipeline-tracker` | Lifecycle events: `BILL#{sha1}` + `EVENT#{iso}` |

### S3 (within `jrk-analytics-billing`)
| Prefix | Usage |
|---|---|
| `Bill_Parser_1_Pending_Parsing/` | Upload target (INPUT_PREFIX); router source |
| `Bill_Parser_1_Standard/` | **Router destination; not in canonical stage list** → ISSUE-021 |
| `Bill_Parser_1_LargeFile/` | **Router destination; not in canonical stage list** → ISSUE-021 |
| `Bill_Parser_2_Parsed_Inputs/` | Parser output |
| `Bill_Parser_3_Parsed_Outputs/` | **Parser env var references this; unclear role** → ISSUE-021 |
| `Bill_Parser_4_Enriched_Outputs/` | Enricher output; search index source; /parse dashboard source |
| `Bill_Parser_Config/search_index.json.gz` | Search index persistence |
| `Bill_Parser_Failed_Jobs/` | Parser failure destination |
| `Bill_Parser_Rework_Input/` | Rework target (owned by Review module) |

### External S3 (scraper)
- `jrk-utility-pdfs` (SCRAPER_BUCKET) — external scraper's PDF archive

### External APIs
- Scraper API: `SCRAPER_API_URL/api/integrations`, Bearer token
- Gemini: `gemini-2.5-pro` (parser), `gemini-1.5-flash` (enrichment + date extraction)

### Secrets
- `gemini/parser-keys` — Pro keys
- `gemini/matcher-keys` — Flash keys

---

## 9. Drift vs. Existing Docs

| Claim | Source | Reality | Verdict |
|---|---|---|---|
| "9 stages: S1, S2, S4, S5, S6, S7, S8, S9, S99" | CLAUDE.md, 04_data_architecture.md | Missing intermediate routing stages: S1_Standard, S1_LargeFile, S3 Parsed_Outputs | 🔴 DRIFT |
| "Search index uses DDB `jrk-bill-config`" | Inferred from module taxonomy | Actually uses in-memory + S3 gzipped persistence, not DDB | 🔴 DRIFT |
| "Parse dashboard is visible at /parse" | Accurate | Template is `index.html` not `parse.html` | Minor naming drift |
| Scraper is documented (BIG_BILL_DEPLOYMENT.md references it in passing) | Partial | Scraper has full implementation but no dedicated doc | Gap |
| Email ingest flow documented | ❌ | `jrk-email-ingest` Lambda exists but no user-facing doc explains the `@jrk.com` email → bill flow | Gap |
| `_pipeline_track` events go somewhere | Implicit | Writes to `jrk-bill-pipeline-tracker` with 90-day TTL; errors silently swallowed | Accurate + ISSUE-027 |

---

## 10. Issues Flagged (summary → ISSUES.md)

| ID | Severity | Scope | Title |
|---|---|---|---|
| ISSUE-020 | P2 | JTBD / INTEGRATION | Uppercase .PDF handling has 3 overlapping fixes + 10-min lag |
| ISSUE-021 | P2 | DATA / DRIFT | Router intermediate stages undocumented (S1_Standard, S1_LargeFile, S3) |
| ISSUE-022 | P4 | UX / TECH-DEBT | `/parse` renders `index.html` not `parse.html` |
| ISSUE-023 | P1 | JTBD / INTEGRATION | No user attribution on uploaded bills |
| ISSUE-024 | P2 | UX | Search 500-result cap silent; no pagination |
| ISSUE-025 | P3 | UX | Search is naive substring match; no normalization |
| ISSUE-026 | P3 | TECH-DEBT / DATA | Scraper CSV fallback silently serves stale data on API outage |
| ISSUE-027 | P2 | OBSERVABILITY | Pipeline tracker errors swallowed; no alarm |
| ISSUE-028 | P2 | DATA | Router doesn't validate PDF integrity; corrupt files journey to Failed Jobs |
| ISSUE-029 | P3 | PERF | `list_dates()` scans entire enrich prefix; scales linearly |
| ISSUE-030 | P3 | PERF | `load_day()` fires 50 concurrent S3 GETs; AppRunner saturation risk |
| ISSUE-031 | P2 | PERF / STARTUP | Search rebuild without S3 cache is hours |
| ISSUE-032 | P1 | JTBD / OBSERVABILITY | Failed parses hidden from /parse dashboard |
| ISSUE-033 | P2 | DATA INTEGRITY | Rework gap: bill invisible to search between rework and next rebuild |
| ISSUE-034 | P4 | TECH-DEBT | Scraper "unlinked" sentinel is fragile; model holes |
| ISSUE-035 | P2 | PERF / UX | Scraper import is sequential; 50 PDFs blocks 100s; AppRunner timeout |
| ISSUE-036 | P3 | TECH-DEBT | `jrk-presigned-upload` Lambda exists but unused; upload path limited to ~30MB |

Top priorities for Module 2:
1. **ISSUE-023** (P1): add user attribution to uploads — unblocks audit + metrics
2. **ISSUE-032** (P1): surface failed parses on /parse — stop silent loss
3. **ISSUE-020** (P2): fix S3 event filter case-sensitivity at infrastructure level — delete 3 redundant mitigations

---

## 11. Open Questions for User

**[Q-10]** **Uppercase .PDF root fix:** Are you open to updating the S3 event notification rule to be case-insensitive (or unfiltered), which would let us delete the `_retrigger_uppercase_pdfs` auto-loop, the admin button, AND the extension normalization in `/api/upload_input`? That's an infrastructure change — needs approval.

**✅ ANSWERED (2026-04-16):** User approved the fix. Attempted; see ISSUE-020 postmortem.

**Outcome:** Rolled back cleanly to pre-session state. The attempt revealed that:
1. Adding an S3 event rule is easy (no code change)
2. Updating the router Lambda in place via `aws lambda update-function-code` is risky — my zip-repackaging broke boto3's vendored structure; production router was down ~3 min; 9 stuck PDFs recovered via manual invocation
3. The fix needs to be traced through ALL 4 pipelines that have `.pdf` suffix filters (router, standard-parser, large-parser, chunk-processor), not just the router

**Recommendation / deferred plan:**
- Build a proper Lambda deploy pipeline (staging → prod) before touching Lambda code again
- OR add twin `_UppercasePDF` S3 rules for all 4 .pdf-filtered triggers (config-only change, no code). Verify downstream chain of each.
- Current mitigations (auto-loop, admin button, upload normalization) remain in place
- Revisit as a focused "S3 event case sensitivity" session, not as part of a module review

**Status:** deferred. Production state: unchanged from pre-session.

**[Q-11]** **Router intermediate stages:** Do `Bill_Parser_1_Standard/`, `Bill_Parser_1_LargeFile/`, and `Bill_Parser_3_Parsed_Outputs/` exist as real operational stages, or are some legacy/dead? Should they be surfaced in CLAUDE.md and 04_data_architecture.md?

**✅ ANSWERED (2026-04-16):** (1) Yes, document them. (2) Don't know the S2 vs Standard/LargeFile relationship — flag for follow-up. (3) Stage 3 missing from public docs is probably a mistake.

**Updates landed:**
- `CLAUDE.md` Data Pipeline section expanded to include Stages 1a, 1b, 1c, 1d, 2, 3, 5, 99 + off-main prefixes + TODO flag for Q-2
- `04_data_architecture.md` addendum: corrected stage map, S3 event trigger table, `jrk-bill-router-log` DDB table
- **Open TODO:** investigate Stage 2 role in a future session (does parser need to write PDF to both S2 AND S3? Is S2 a redundant archive?)

**[Q-12]** **Failed parses visibility:** Should failed-parse count surface as a 4th column on `/parse` dashboard (REVIEW / PARTIAL / COMPLETE / FAILED)? Or better to merge `/parse` and `/failed` entirely?

**✅ ANSWERED (2026-04-16):** Option 1 — add FAILED as a 4th column on /parse day cards.

**Fix plan (not landing this session):**
- Extend `day_status_counts(y, m, d)` (main.py:25613) to scan `Bill_Parser_Failed_Jobs/` for that date and return a 4th count
- Update `/parse` template (`index.html`) to render 4 columns with FAILED styling (red/warning)
- Clicking FAILED opens a list of failed bills for that day with a "retry" button (that touches the `/api/failed/retry` endpoint)
- Update ISSUE-032 → status: planned-fix

**[Q-13]** **Upload user attribution:** OK to add `x-amz-meta-uploader={email}` to S3 puts on upload, and propagate through router/parser/enricher? Small schema change, no breaking migration required.

**✅ ANSWERED (2026-04-16):** Yes, build it now. Keep SSO coordination in mind.

**Fix plan (build as focused mini-project, not in module review session):**
- **Identity format:** use a single field `uploaded_by` that holds the current session user's email (e.g., `cbeach@jrk.com`). Post-SSO, this maps to the IdP email claim. Forward-compatible — no schema change needed when SSO lands.
- **Touchpoints:**
  1. `api_upload_input` (main.py:3867) — add `Metadata={"uploader": user, "source": "web-upload"}` to `s3.put_object()`
  2. `api_scraper_import` (main.py ~4411) — same, with `source="scraper"`
  3. `jrk-email-ingest` Lambda — mark `source="email"`, uploader = sender email (if we can identify) or "system@jrk.com" sentinel
  4. `jrk-bill-router` Lambda — `CopyObject` calls must preserve metadata: add `MetadataDirective="COPY"` (or `REPLACE` with explicit pass-through)
  5. `jrk-bill-large-parser` and `jrk-bill-chunk-processor` Lambdas — same preservation
  6. `jrk-bill-parser` Lambda — when writing Stage 3 JSONL, read the PDF's metadata via `s3.head_object().Metadata` and add `uploaded_by` + `source` to each row
  7. `jrk-bill-enricher` Lambda — preserve fields in Stage 4 output
  8. Backward compatibility: old bills without metadata get `uploaded_by = ""` → treat as "unknown/historical"
- **SSO coordination:** when SSO lands, the session user object gains more fields (email, name, id, role). At that point, enrich the stored `uploaded_by` payload to `{"email": ..., "sub": ..., "name": ...}` (JSON-encoded in metadata). Plan the upgrade path from plain-email to structured format.
- **Tests needed:** integration test that uploads a PDF and asserts the enriched JSONL has `uploaded_by` set.
- Update ISSUE-023 → status: planned-fix (prereq for audit log, SSO, per-user metrics)

**[Q-14]** **Scraper import concurrency:** OK to parallelize scraper import (50 workers like load_day)? Risk: more CloudWatch log noise; reward: 10x faster imports.

**✅ ANSWERED (2026-04-16):** Use async job-queue pattern. And apply this pattern MORE FREQUENTLY across the app, not just here.

**Implications for Module 2 (scraper import):**
- Client POSTs `/api/scraper/import` with list of UUIDs → returns `{job_id, poll_url}` immediately (202 Accepted)
- Background worker: copies PDFs in parallel, updates progress in DDB
- Client polls `GET /api/scraper/import/status/{job_id}` → `{status, completed, failed, total, log_tail}`
- On completion, UI can auto-navigate or show link to /parse

**Cross-cutting architectural preference (new strategic memory):**
- User wants async job-queue pattern applied broadly across operations
- Long-running user-initiated ops should NOT block HTTP requests
- Every module review should flag places where this pattern would help (imports, exports, bulk updates, search rebuilds, etc.)
- Infrastructure needed: a canonical job-tracking table + polling endpoint convention + async worker (threading.Thread for in-app, Lambda for heavy)

**Scoping for Module 2 specifically:**
- `/api/scraper/import` (Q-14)
- `/api/scraper/extract-dates` (already async per-PDF but could batch)
- `/api/search/rebuild-index` (already async internally, but client doesn't poll for progress)
- `/api/retrigger_pending_pdfs` (admin button; if kept, should be async)
- `/api/upload_input` (large files; currently sync, could be async with presigned URL)

**[Q-15]** **Presigned upload Lambda:** Keep or delete? If keep, wire into frontend for large-file support. If delete, reclaim 87 lines.

**✅ ANSWERED (2026-04-16):** Use it. Wire into `/input` UI.

**Fix plan:**
- Frontend (`input.html`) calls `jrk-presigned-upload` Lambda (via a new wrapper endpoint or direct Lambda URL) to get a presigned PUT URL
- Browser PUTs the PDF directly to S3 (bypasses AppRunner entirely — unlimited file size)
- Presigned URL must include metadata (`Metadata-Uploader`, `Metadata-Source=web-upload`) so Q-13 attribution is preserved
- S3 event fires router → standard pipeline
- Frontend shows progress via job-queue pattern (Q-14) — poll for when the bill appears in Stage 4
- Fallback: if presigned upload fails (network, etc.), fall back to legacy `/api/upload_input` for small files
- Delete `/api/upload_input`'s 30MB memory limit concern (it still exists as fallback)
- Update ISSUE-036 → status: planned-fix

**Depends on:** Q-13 user attribution landing (so metadata shape is known) + Q-14 async pattern (for the polling UI)

**[Q-16]** **Search pagination:** Implement offset+limit for `/api/search` so users can get past 500? Or accept 500 cap as "good enough"?

**✅ ANSWERED (2026-04-16):** Use cursor-based pagination + infinite scroll + server-side sort.

**Fix plan (half-day focused work, ~4-6 hours total):**
- **Server changes** (main.py:3777 `/api/search`, ~50 LOC):
  - Accept `cursor` (opaque base64-JSON `{last_date, last_pdf_id}`) + `sort` query params
  - Support sorts: `date_desc` (default), `date_asc`, `amount_desc`, `amount_asc`, `account_asc`, `vendor_asc`, `property_asc`
  - Sort entries at query time (in-memory; 30-100K entries sorts in ms)
  - Filter entries where `(sort_key, pdf_id)` tuple comes after cursor
  - Return next 500 + `next_cursor` (empty string = end of results)
  - Remove `truncated` flag (cursor absence = done)
- **Client changes** (`templates/search.html`, ~100 LOC):
  - IntersectionObserver on a sentinel element near bottom of results list
  - When visible, fetch next page with cursor, append results
  - Sort dropdown above results
  - Reset cursor + clear list when user changes query params
  - "Showing N of M total" hint (uses `index_size`)
- **Tests** (~1 hour):
  - Unit: cursor encode/decode round-trip
  - Integration: scroll through 1000+ results across pages, verify no dupes/gaps
  - Edge: cursor-past-end returns empty + no cursor
- **Effort:** ~4-6 hours focused; candidate for follow-up quick-win session
- **Update ISSUE-024 → planned-fix**

**[Q-17]** **Search smart matching:** Any specific pain points you or users have hit? I can propose normalization rules (strip punctuation, collapse whitespace, handle leading zeros on account numbers) if so.

**✅ ANSWERED (2026-04-16):** No pain on user's end, but likely worse for downstream users. Make it more robust.

**Fix plan (bundled with Q-16 search work, ~2 hours additional):**
- Add a `_normalize_for_search(s)` helper:
  - Strip all non-alphanumeric (spaces, dashes, underscores, parens, slashes, periods)
  - Lowercase
  - Collapse multi-whitespace if preserved
- At index-build time (`_index_one_day`), store BOTH the original and the normalized forms: `account_n`, `vendor_n`, `property_n` alongside `account_l`, `vendor_l`, `property_l`
- At search time, normalize the query terms the same way, match against `_n` fields
- For account numbers specifically: also try with leading zeros stripped (`int(acct)` if all-digit, then string back)
- Add a "smart match" toggle to search UI (opt-out if user wants exact)
- Test cases:
  - "ACC-123" query matches "ACC123" stored
  - "Con Edison" matches "CON_EDISON"
  - "00123" matches "123"
  - Numeric-only query tries both zero-stripped and zero-preserved
- Update ISSUE-025 → planned-fix (bundled with ISSUE-024 search work)

**[Q-18]** **Scraper CSV fallback:** Can we delete the CSV fallback entirely? Seems like a "belt-and-suspenders" that actually causes more harm than good during scraper-API outage.

**✅ ANSWERED (2026-04-16):** Option 1 — hard-fail with error banner. Delete CSVs.

**Fix plan (~1 hour):**
- Delete the CSV-fallback block in `_load_scraper_mappings()` (main.py:3978-4004)
- Delete `integration_uuid_provider_map.csv` and `account_uuid_provider_map.csv` from source tree (if they exist)
- When `_fetch_scraper_integrations()` fails, return empty list as before, but signal the error to callers
- `api_scraper_providers` returns `{ok: false, error: "Scraper API unavailable — try again later"}` with HTTP 503 when no integrations available
- Frontend (`input.html`) shows a red banner "Scraper temporarily unavailable" when scraper endpoints return error
- Update ISSUE-026 → planned-fix

**[Q-19]** **Stage 3:** What is `Bill_Parser_3_Parsed_Outputs/`? If dead, delete env var. If live, document.

**✅ ANSWERED (2026-04-16):** Already answered in Q-11 investigation. Stage 3 IS live — it's the parser's JSONL output, consumed by the enricher. Drift now corrected in CLAUDE.md and 04_data_architecture.md. User: "probably oversight, no idea" about why it was missing from old docs. No further archaeology needed.

---

## 12. Dead / Unused Code

- `jrk-presigned-upload` Lambda — 87 lines, not wired to any frontend flow (see Q-15)
- `integration_uuid_provider_map.csv` + `account_uuid_provider_map.csv` — CSV fallbacks for scraper mapping (see Q-18)
- Various legacy scraper bucket patterns that `_build_account_map_from_s3()` handles — suggests multiple generations of scraper data model

---

## 13. SSO Migration Concerns (feeds `project_sso_migration.md`)

Identity touchpoints in Module 2 that SSO migration must preserve:

| Touchpoint | Current source of identity | Post-SSO consideration |
|---|---|---|
| `/api/upload_input` | `user: str = Depends(require_user)` (session cookie) | Must resolve to SSO claim; user still needs write attribution (see ISSUE-023) |
| `/api/retrigger_pending_pdfs` | `require_user` + `ADMIN_USERS` set check | Replace `ADMIN_USERS` with capability `admin:retrigger_pdfs` or broader `admin:system` |
| `/api/search/rebuild-index` | `require_user` + `ADMIN_USERS` | Capability `admin:rebuild_search_index` |
| `/api/scraper/*` | `require_user` (no role check) | Decide: is scraper import allowed for all authenticated, or capability-gated? (AP clerk yes; viewer no?) |
| `/api/upload_input` user record in tracker/audit | Currently: not recorded anywhere | After SSO: write SSO claim's `email` + `sub` to S3 metadata; update audit log |
| `parse_dashboard` | `require_user` but doesn't use user identity for anything | Should it filter by "bills I uploaded"? User asked about pull-through attribution (memory `project_sso_migration.md`) |

**Per-user attribution stakes for METRICS module (user raised this in Q-9):**
Every upload-by-user metric currently doesn't exist because we don't record the uploader. METRICS module can't build "bills uploaded per user per day" unless we fix ISSUE-023. Flag this as a hard prerequisite for SSO scoping.

---

## 14. Service-Account Concerns (feeds `project_sso_migration.md`)

Automated callers that touch Module 2 now or could:

| Caller | Current auth | What it needs |
|---|---|---|
| `jrk-email-ingest` Lambda | None; writes directly to S3 | No app endpoint call; no auth needed |
| `jrk-bill-router` Lambda | None; S3-triggered | No app endpoint call |
| `jrk-bill-parser` Lambda | None; S3-triggered | No app endpoint call |
| Scheduled search index rebuild | Not currently external | Could move to Lambda-driven if cache-build pattern (per `feedback_ubi_cache_architecture.md`) applied |
| Automated retrigger (future) | Currently in-app background loop | Could move to scheduled Lambda |
| Automated test smoke (smoke_test_production.py) | Session cookie | Should be bearer token (TODO-AUTH-006) |

**Capabilities needed** (for capability registry):
- `upload:bill` (POST /api/upload_input)
- `scraper:list` (GET /api/scraper/*)
- `scraper:import` (POST /api/scraper/import)
- `scraper:extract_dates` (POST /api/scraper/extract-dates)
- `search:query` (GET /api/search)
- `search:rebuild_index` (POST /api/search/rebuild-index) — admin
- `admin:retrigger_pdfs` — admin
- `parse:view_dashboard` (GET /parse)

---

## Observations for Current-State Synthesis

1. **Clunkiness theme reinforced:** The uppercase-.PDF problem is the textbook example of "doesn't do a thing well or complete". Three mitigations, a 10-minute lag, a confusing admin button, and the root cause (case-sensitive S3 event filter) is one infrastructure config change away.

2. **Half-built features:** Presigned upload Lambda (unused), scraper CSV fallback (actively harmful), Stage 3 prefix (unclear existence).

3. **Per-user attribution is a pervasive gap** that will block the SSO migration's promise of "know who did what". Must be fixed early.

4. **Silent failures in many places:**
   - Pipeline tracker errors → swallowed
   - Failed parses → hidden from UI
   - Search cap → no feedback
   - Corrupt PDFs → journey to Failed Jobs without early rejection
   - Scraper API outage → silently serves stale CSV

   All of these are instances of the system preferring "silent keep going" to "loud fail fast". For a tool that's supposed to complete jobs, the opposite is generally better.

5. **Module 2 is tightly coupled** to Lambdas (5 of them), Module 3 (Review — uses `load_day`), Module 14 (Failed Jobs — handles the hidden failures). The "Parse & Input" module boundary is fuzzy.

---

## References

- `../02_endpoint_inventory.md` — endpoint table (Parse + Scraper sections)
- `../03_module_taxonomy.md` — Module 2 block
- `../04_data_architecture.md` — DDB + S3 (needs S1_Standard, S1_LargeFile, S3 stages added per ISSUE-021)
- `../ISSUES.md` — ISSUE-020 through ISSUE-036
- `../../current/ARCHITECTURE_BIG_BILL.md` — Big PDF handling architecture (router + large-parser + chunk-processor)
- `../../current/planning/PARSER_ACCURACY.md` — Parser improvement planning (relates to Lambda accuracy)
