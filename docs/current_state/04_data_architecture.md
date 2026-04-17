# 04 — Data Architecture

Inventory of all DynamoDB tables and S3 prefixes referenced in the codebase, plus data flow between stages.

---

## DynamoDB Tables

| Table | Purpose | Primary Key | Sort Key | Key Patterns |
|-------|---------|-------------|----------|--------------|
| jrk-bill-review | Line item review status (submitted/rejected) | pk (line_id) | sk (derived from pdf_id + index) | PDF_ID#INDEX |
| jrk-bill-drafts | Invoice edits (property, vendor, GL overrides) | pk (pdf_id#line_id) | — | PDF_ID#INDEX |
| jrk-bill-config | Global config: workflow reasons, notes, caches, metrics, autonomy, paired vendor-GL/property history, overrides, UBI mapping, charge codes, AP mappings | PK (config type) | SK (specific key) | WORKFLOW_REASONS, WORKFLOW_NOTES, POST_LOCK, POSTED_INVOICES, METRICS_*, AUTONOMY_*, PAIRED_VENDORS_*, EXCLUSION_HASHES, etc. |
| jrk-bill-knowledge-base | Vendor/property/GL rules learned from user corrections | pk (entity_type) | sk (entity_id) | VENDOR#ID, PROPERTY#ID, GL_ACCOUNT#ID |
| jrk-bill-ai-suggestions | AI-generated suggestions, accuracy tracking, late fee metadata | pk (pdf_id or invoice_id) | sk (suggestion_type or timestamp) | SUGGESTION#PDF_ID, ACCURACY#PDF_ID, LATE_FEE#INVOICE_ID |
| jrk-bill-pipeline-tracker | Daily event log and task tracking for autonomy simulator | pk (date or pdf_id) | sk (timestamp or event_type) | YYYY-MM-DD#PDF_ID, EVENT#TIMESTAMP |
| jrk-bill-ubi-assignments | UBI classifications (electricity, water, gas, waste, internet) | pk (invoice_id) | sk (assigned_date) | INVOICE_ID#YYYY-MM-DD |
| jrk-bill-ubi-archived | Archive of UBI-assigned items after processing | pk (invoice_id) | sk (archived_date) | INVOICE_ID#YYYY-MM-DD |
| jrk-check-slips | Check payment slips (metadata: vendor, date, total, PDF status) | pk (check_slip_id) | sk (created_at) | CHECK_SLIP_ID#TIMESTAMP |
| jrk-check-slip-invoices | Line items in check slips | pk (check_slip_id) | sk (invoice_id) | CHECK_SLIP_ID#INVOICE_ID |
| jrk-bill-manual-entries | Manual/accrual entries (true-ups, adjustments) | pk (property_id) | sk (period or entry_id) | PROPERTY_ID#YYYY-MM or ENTRY_ID |
| jrk-manual-billback-entries | Manual billback overrides (amounts, GL codes) | pk (pdf_id or invoice_id) | sk (line_index or timestamp) | INVOICE_ID#LINE_INDEX |
| jrk-bill-parser-errors | Parser error tracking for failed jobs | pk (pdf_id or job_id) | sk (error_type or timestamp) | PDF_ID#ERROR_TYPE |
| jrk-bill-review-debug | Debug activity logs, reports, screenshots | pk (report_id or user) | sk (timestamp or report_date) | REPORT_ID#TIMESTAMP, USER#YYYY-MM-DD |
| jrk-url-short | URL shortener cache for long S3 URLs | pk (code) | — | SHORT_CODE |
| **jrk-bill-review-users** | **User accounts + role assignments for auth** | **user_id (email)** | **—** | **Attrs: password_hash, role, full_name, enabled, must_change_password, last_login_utc, created_utc, created_by, password_changed_utc. GSI: `role-index` (PK=role). Added 2026-04-16 during Module 1 review.** |

---

**Notes:**
- `jrk-bill-config` is used as a catch-all config store with heavy partition-key-based segmentation (`WORKFLOW_REASONS`, `POST_LOCK`, `POSTED_INVOICES`, `METRICS_*`, etc.). This is a smell to discuss in review — consider splitting into domain-specific tables.
- `jrk-bill-ubi-assignments` and `jrk-bill-ubi-archived` are large enough that scans are slow (per memory: "DynamoDB scans on large tables are slow — always cache").
- Several tables' exact keys/sort-keys are inferred from usage; to be verified during module review.

### Addendum (Module 2 review 2026-04-16)

**Additional DDB table found:**

| Table | Purpose | Primary Key | Sort Key | Notes |
|-------|---------|-------------|----------|-------|
| `jrk-bill-router-log` | Router decisions log (page count, file size, route, reason) | `pk` = `ROUTE#{filename}` | `timestamp` | Written by `jrk-bill-router` Lambda for every routing decision |

**Corrected S3 Stage Map** (per confirmed Q-11 2026-04-16):

The original 9-stage simplification was incomplete. Stage 3 was missing entirely; there are sub-stages within Stage 1 for the routing/chunking path. Updated map:

| Stage | Prefix | Role | Trigger into next |
|---|---|---|---|
| 1 | `Bill_Parser_1_Pending_Parsing/` | Ingest (web upload, email, scraper) | S3 event → router |
| 1a | `Bill_Parser_1_Standard/` | Router output: PDFs ≤10 pages, ≤10MB | S3 event → parser |
| 1b | `Bill_Parser_1_LargeFile/` | Router output: larger PDFs | S3 event → large-parser |
| 1c | `Bill_Parser_1_LargeFile_Chunks/` | Large-parser splits pages; chunks here | S3 event → chunk-processor |
| 1d | `Bill_Parser_1_LargeFile_Results/` | Aggregator reassembles chunk results | S3 event (.json) → aggregator |
| 2 | `Bill_Parser_2_Parsed_Inputs/` | PDF + metadata copy (archive of parser input). **Role unclear vs. 1a/1b — TODO** | No trigger (archive only?) |
| 3 | `Bill_Parser_3_Parsed_Outputs/` | Parser JSONL output (pre-enrichment) | S3 event (.jsonl) → enricher |
| 4 | `Bill_Parser_4_Enriched_Outputs/` | Enriched (vendor/property/GL) | Manual review in app |
| 5 | `Bill_Parser_5_Overrides/` | User overrides (property/vendor/GL) | Manual via /post |
| 6 | `Bill_Parser_6_PreEntrata_Submission/` | Merged for Entrata POST | Manual via /api/post_to_entrata |
| 7 | `Bill_Parser_7_PostEntrata_Submission/` | Posted to Entrata | Manual via /ubi or /billback |
| 8 | `Bill_Parser_8_UBI_Assigned/` | UBI-assigned | Manual via /billback submit |
| 9 | `Bill_Parser_9_Flagged_Review/` | Flagged for manual QC | Manual unflag |
| 99 | `Bill_Parser_99_Historical Archive/` | End-of-lifecycle archive | — |

**Parallel/off-main prefixes:** `Bill_Parser_Rework_Input/`, `Bill_Parser_Failed_Jobs/`, `Bill_Parser_Meter_Data/`, `Bill_Parser_Config/`, `Bill_Parser_Deleted_Archive/`, `Bill_Parser_Rework_Archive/`

**S3 Event Triggers (7 rules):**

| Rule Id | Source Prefix | Suffix | Target Lambda |
|---|---|---|---|
| BillRouterTrigger | Bill_Parser_1_Pending_Parsing/ | .pdf | jrk-bill-router |
| BillParserStandardTrigger | Bill_Parser_1_Standard/ | .pdf | jrk-bill-parser |
| BillParserLargeFileTrigger | Bill_Parser_1_LargeFile/ | .pdf | jrk-bill-large-parser |
| ChunkProcessorTrigger | Bill_Parser_1_LargeFile_Chunks/ | .pdf | jrk-bill-chunk-processor |
| AggregatorTrigger | Bill_Parser_1_LargeFile_Results/ | .json | jrk-bill-aggregator |
| InvokeBillEnricherOnStage3 | Bill_Parser_3_Parsed_Outputs/ | .jsonl | jrk-bill-enricher |
| ReworkPdfCreate | Bill_Parser_Rework_Input/ | .pdf | jrk-bill-parser-rework |

All suffix filters are case-sensitive (lowercase only). See ISSUE-020 for uppercase `.PDF` handling.

**Open question:** What exactly is the role of `Bill_Parser_2_Parsed_Inputs/`? Parser Lambda writes to BOTH S2 and S3. S2 may be redundant with S1_Standard/S1_LargeFile. Needs investigation in future session.

## S3 Prefixes (in `jrk-analytics-billing` bucket)

| Prefix | Purpose | Stage | Read/Write |
|--------|---------|-------|-----------|
| Bill_Parser_1_Pending_Parsing/ | Input PDFs awaiting parser Lambda | Stage 1 | W (app), R (parser) |
| Bill_Parser_2_Parsed_Inputs/ | Output JSONLs from parser Lambda (raw parsed invoices) | Stage 2 | W (parser), R (app) |
| Bill_Parser_4_Enriched_Outputs/ | Enriched invoices with GL/vendor lookups | Stage 4 | R (app review dashboard) |
| Bill_Parser_5_Overrides/ | User overrides (property, vendor, GL code changes) | Stage 5 | RW (app) |
| Bill_Parser_6_PreEntrata_Submission/ | Merged files ready to validate and post to Entrata | Stage 6 | RW (app validation/post) |
| Bill_Parser_7_PostEntrata_Submission/ | Files successfully posted to Entrata, awaiting UBI classification | Stage 7 | RW (app, UBI assignment) |
| Bill_Parser_8_UBI_Assigned/ | UBI-classified invoices, ready for billback/master bills | Stage 8 | RW (app) |
| Bill_Parser_9_Flagged_Review/ | Records flagged by user for manual intervention | Stage 9 | RW (app) |
| Bill_Parser_99_Historical Archive/ | Long-term archival of all processed bills | Archive | W (app), R (historical queries) |
| Bill_Parser_Rework_Input/ | PDFs needing re-parse (document errors, updates) | Rework | RW (app) |
| Bill_Parser_Failed_Jobs/ | Parser Lambda failures and diagnostics | Failed | R (app failure analysis), W (parser) |
| Bill_Parser_Meter_Data/ | Meter readings and consolidation data | Config | RW (app) |
| Bill_Parser_Config/ | Configuration JSON files (vendor mappings, UBI mappings, charge codes, accounts-to-track, workflow cache, completion tracker, bill index, outliers, gap analysis uploads) | Config | RW (app) |
| Bill_Parser_Deleted_Archive/ | Deleted invoice archive (tombstone) | Archive | W (app) |
| Bill_Parser_Rework_Archive/ | Archive of reworked PDFs | Archive | W (app) |
| EXPORTS_ROOT/dim_vendor/ | Vendor dimension table (daily snapshot) | Dimension | R (app catalog) |
| EXPORTS_ROOT/dim_property/ | Property dimension table (daily snapshot) | Dimension | R (app catalog) |
| EXPORTS_ROOT/dim_gl_account/ | GL account dimension table (daily snapshot) | Dimension | R (app catalog) |
| EXPORTS_ROOT/dim_uom_mapping/ | Unit of measure mapping (latest or dated) | Dimension | RW (app UOM config) |
| improve-screenshots/ | Debug report screenshots | Debug | RW (app) |
| jrk-utility-pdfs (SCRAPER_BUCKET) | Utility scraper PDFs (provider/account hierarchy) | External | R (app scraper import) |
| api-vendor (external) | Vendor master list from external API | External | R (app vendor catalog) |

---

---

## Data flow through the stages

```
                             External systems        BILL_REVIEW APPLICATION
                             ─────────────────       ─────────────────────────

                             Email ─► SES ─► jrk-email-ingest Lambda
                                                    │
                             Web form ──(POST)──────┼─► S1 Pending_Parsing
                                                    │           │
                             Scraper ─► /api/scraper/import ────┘
                                                                │
                                                                ▼
                                                    jrk-bill-router Lambda
                                                                │
                                       ┌────────────────────────┴─────────────┐
                                       │ (small)                              │ (large)
                                       ▼                                      ▼
                              jrk-bill-parser                    jrk-bill-large-parser
                                       │                                      │
                                       ▼                                      ▼
                              S2 Parsed_Inputs                jrk-bill-chunk-processor
                                       │                                      │
                                       ▼                                      └─► S2
                              jrk-bill-enricher
                                       │
                                       ▼
                              S4 Enriched_Outputs
                                       │
                                       ▼
                             [HUMAN REVIEW: /day, /review, edits stored in S5 Overrides + DDB drafts]
                                       │
                                       ▼
                             /post validation → S6 PreEntrata_Submission
                                       │
                                       ▼
                             /api/post_to_entrata (with POST_LOCK nonce)
                                       │
                                       ▼
                             S7 PostEntrata_Submission ───► (Entrata API: invoice created)
                                       │
                                       ▼
                             [UBI ASSIGNMENT: /ubi, /billback ─► S8 UBI_Assigned]
                                       │
                             ┌─────────┼──────────────┐
                             ▼         ▼              ▼
                    /master-bills   /billback      /print-checks
                          │          (AR post)      check slips
                          ▼                              │
                    Snowflake                     /review-checks
                    _Master_Bills_Prod             (approval)

                             [Anywhere] ─► flag for review ─► S9 Flagged_Review
                             [End of lifecycle] ────────────► S99 Historical_Archive
```

---

## External integrations

| System | Direction | Purpose |
|---|---|---|
| Google Gemini API | Out | PDF parsing, date extraction, invoice analysis |
| Entrata API | Out | Post invoices, query status, post AR billbacks |
| Snowflake | Out | Master bills warehousing; dim_vendor/property/gl/uom exports |
| SES / IMAP | In | Inbound bills via `jrk-email-ingest` Lambda |
| Utility Scraper API | In | Pull PDFs from utility company websites |
| Google Workspace APIs | Both | Vendor validation (via `jrk-vendor-validator` Lambda) |
| AWS SES | Out | Vendor notifications (via `jrk-vendor-notifier` Lambda) |

---

## Notable findings from code scan

## Appendix: Notable Findings

- **Module scatter:** Helper functions for caches (_perf_*, _ubi_*) are grouped by operation, not module, making module boundaries somewhat fuzzy. Recommend future refactor into dedicated cache.py.
  
- **DynamoDB size limit workaround:** accounts_to_track exceeded 400KB; now S3-primary with optional DynamoDB fallback (18048–18140). Pattern should apply to other large configs.

- **Entrata response parsing is complex (lines 1–52):** Handles duplicate detection separately from error status because some APIs embed "duplicate invoice" in HTTP 200 responses. Good defensive programming but fragile to API changes.

- **Search index is gzipped:** Bill_Parser_Config/bill_index_cache.json.gz is loaded on startup for full-text search. Cache invalidation uses per-day TTLs (749–769) to balance freshness vs. frequent rebuilds.

- **UBI assignment has multiple deletion paths:** Three separate cleanup methods (`api_billback_ubi_cleanup_exclusions`, `api_billback_ubi_unassign`, `api_billback_ubi_unassign_account`) with slightly different semantics. Consolidation would reduce bugs.

- **Distributed post lock uses nonce:** POST_LOCK entries in DynamoDB include nonce to prevent duplicate Entrata submissions across AppRunner instances. Verify and update in atomic conditional writes (2335–2367). Good pattern.

- **Performance monitoring is comprehensive:** Middleware (720–748) samples all requests; hourly rollups persist to DynamoDB (645–720); 7-day history loads on startup (682–720). Overhead is minimal (~1% of request time).

- **Vacant Electric module is decoupled:** Imported from bill_review_app.vacant_electric (87–93). Likely a future module to spin out.

- **Four routes return 351 @app decorators:** Massive endpoint surface. Consider API versioning (v1/v2) and endpoint grouping by feature area (currently ad-hoc).

- **S3 key validation is strict:** _validate_s3_key() (424–447) whitelists allowed prefixes to prevent directory traversal or accidental deletes. Good security practice.

- **Cascading S3 moves on workflow:** Bills flow through 9 stages (1 → 2 → 4 → 5/6 → 7 → 8 → 9 → Archive). Each move copies to dest, deletes source. No atomic rename; consider S3 Object Lambda for transparent renaming.

- **Timezone handling via ZoneInfo:** Uses zoneinfo.ZoneInfo for proper TZ handling (80). Better than pytz.

- **Search index precompilation is optional:** If BILL_INDEX_CACHE_KEY not found on startup, searches fall back to slow S3 scan (3618–3650). Should fail loudly in prod.

- **Scraper integrations config not exported:** _fetch_scraper_integrations() (3947–3970) reads from scraper API; no version control of mappings. Risk of orphaned accounts if scraper API changes.

- **Check slip PDF generation uses background task:** Not shown in endpoint code but hinted at (30853). Ensure no race on status check.

- **Meter merge is complex:** Consolidates readings across duplicate meters; no audit trail of which meters were merged. Recommendation: log to DDB for undo capability.

- **Autonomy simulator is independent:** `api_autonomy_sim_run()` is a parallel evaluation path; doesn't mutate actual autonomy config. Good for what-if analysis, but keep simulation data separate from prod.

---

Next: [10_doc_audit.md](./10_doc_audit.md)
