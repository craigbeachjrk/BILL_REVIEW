# 01 — File Catalog

Complete inventory of every Python file and HTML template in the codebase. Status as of 2026-04-16.

**Categories:**
- `ENTRY` — production entrypoint (main.py, lambda handlers)
- `PROD-CORE` — production support code (auth.py, utils.py, lambda libraries)
- `PROD-TEMPLATE` — production Jinja2 template
- `PACKAGE` — active Python package file (`__init__.py`, models, config)
- `TEST` — pytest test file
- `SCRIPT-PROD` — production script (scheduled or manual)
- `SCRIPT-DEBUG` — ad-hoc diagnostic script
- `SCRIPT-ONEOFF` — migration/backfill/cleanup, probably not re-run
- `SCRIPT-UTILITY` — reusable utility script
- `DEAD` — appears unused; never imported/referenced
- `UNKNOWN` — needs investigation

**Status values:**
- `ACTIVE` — modified recently and clearly in use
- `STALE` — not touched recently but still referenced
- `ABANDONED` — left behind
- `GENERATED` — build output / vendored library

---

## Summary

| Category | File count | Lines (approx) |
|---|---|---|
| ENTRY (main.py + Lambda handlers) | 17 | ~42,000 |
| PROD-CORE (auth, utils, libs) | 20 | ~5,500 |
| PROD-TEMPLATE (Jinja2) | 56 | ~30,000 |
| PACKAGE (active sub-packages) | 8 | ~1,000 |
| TEST | 23 | ~5,143 |
| SCRIPT-PROD | 2 | ~800 |
| SCRIPT-DEBUG | 7 | ~1,500 |
| SCRIPT-ONEOFF | 18+ | ~7,000 |
| DEAD / ABANDONED | 20+ | ~8,000 |
| **TOTAL custom code** | **~170 files** | **~100,000 lines** |

---

## Root-level Python files

| Path | Lines | Category | Purpose | Status |
|---|---|---|---|---|
| `main.py` | 34,272 | ENTRY | FastAPI app — sole uvicorn entrypoint; 349 endpoints | ACTIVE |
| `auth.py` | 282 | PROD-CORE | Session auth, role-based access control | ACTIVE |
| `utils.py` | 379 | PROD-CORE | Shared utilities (formatting, validation, dates) | ACTIVE |
| `app.py` | 311 | DEAD | Streamlit stub; never imported | ABANDONED 🟥 |
| `test_post_validation.py` | 1,737 | TEST | Post-to-GL workflow, duplicate detection, error handling | ACTIVE |
| `test_chunk_processor.py` | 1,191 | TEST | PDF chunking, Gemini payloads, HTTP errors | ACTIVE |
| `test_perf_and_tracker.py` | 861 | TEST | Performance benchmarks, pipeline tracker | ACTIVE |
| `test_before_dec17.py` | 79 | TEST | Ad-hoc pre-cutover validation | STALE |
| `test_both_tables.py` | 92 | TEST | Migration verification | STALE |
| `test_date_ranges.py` | 53 | TEST | Date boundary tests | STALE |
| `test_exclusion.py` | 87 | TEST | Bill exclusion rules | STALE |
| `test_file_exclusion.py` | 71 | TEST | S3 file filtering | STALE |
| `test_line_level_before_dec17.py` | 86 | TEST | Pre-cutover line-level comparison | STALE |
| `test_master_bills.py` | 44 | TEST | Master bill aggregation | STALE |
| `test_master_bills_new.py` | 152 | TEST | Master bill new schema | STALE |
| `test_property_stats.py` | 117 | TEST | Property-level aggregation | STALE |
| `populate_debug_reports.py` | 705 | SCRIPT-PROD | Pipeline diagnostic report generator | STALE |
| `check_recent.py` | 43 | SCRIPT-DEBUG | Recent bill inspector | STALE |
| `debug_hashes.py` | 74 | SCRIPT-DEBUG | Hash mismatch investigation | STALE |
| `debug_hash_mismatch.py` | 80 | SCRIPT-DEBUG | Variant of hash debugging | STALE |
| `analyze_assignments.py` | 36 | SCRIPT-DEBUG | User/manager assignment analysis | STALE |
| `analyze_missing.py` | 223 | SCRIPT-DEBUG | Missing bill audit | STALE |
| `sum_missing.py` | 7 | SCRIPT-ONEOFF | Missing bill counter (minimal) | STALE |
| `backfill_account_history.py` | 267 | SCRIPT-ONEOFF | Historical account migration | ABANDONED |
| `migrate_to_stage8.py` | 215 | SCRIPT-ONEOFF | Schema migration to stage 8 | ABANDONED |
| `migration_compare.py` | 266 | SCRIPT-ONEOFF | Pre/post migration validation | ABANDONED |
| `verify_stage8.py` | 132 | SCRIPT-ONEOFF | Stage 8 migration verification | ABANDONED |
| `verify_multiperiod.py` | 85 | SCRIPT-ONEOFF | Multi-period verification | ABANDONED |
| `reconcile_counts.py` | 112 | SCRIPT-ONEOFF | Bill count reconciliation | ABANDONED |
| `setup_lambda_alarms.py` | 101 | SCRIPT-PROD | CloudWatch alarm setup for Lambdas | ACTIVE |
| `entrata_send_invoices_prototype.py` | 464 | SCRIPT-DEBUG | Invoice posting to Entrata (prototype) | STALE |
| `add_improve_to_templates.py` | 500 | SCRIPT-DEBUG | Template HTML enhancement utility | STALE |
| `count_lines.py` | 104 | SCRIPT-UTILITY | Codebase line counter | STALE |

**Observations:**
- `app.py` is safe to delete (Streamlit stub, not referenced anywhere) — confirm during review
- 10 stale `test_*.py` in root are mostly pre-cutover validation; production tests live in `tests/`
- 6 migration/verification scripts are one-offs that probably should move to `scripts/archive/`
- `setup_lambda_alarms.py` is currently listed as ACTIVE but should be confirmed — it's dated Apr 13, 2026

---

## AWS Lambda handlers (production pipeline)

All under `aws_lambdas/us-east-1/{function-name}/code/`. Status all ACTIVE unless noted.

| Function | File | Lines | Purpose |
|---|---|---|---|
| jrk-bill-router | `lambda_bill_router.py` | 206 | Routes uploaded bills to parser or large-file handler |
| jrk-bill-parser | `lambda_bill_parser.py` | 1,067 | Bill text extraction via Gemini; writes enriched JSON |
| jrk-bill-parser | `error_tracker.py` | 195 | Error logging for parser failures (Gemini API, timeouts) |
| jrk-bill-large-parser | `lambda_bill_large_parser.py` | 243 | Handles >10MB PDFs via chunking before parsing |
| jrk-bill-chunk-processor | `lambda_chunk_processor.py` | 970 | Splits large bills; sends chunks to parser |
| jrk-bill-enricher | `lambda_bill_enricher.py` | 1,112 | Enriches parsed data: GL mapping, account validation |
| jrk-bill-aggregator | `lambda_aggregator.py` | 423 | Aggregates enriched bills to Snowflake master tables |
| jrk-bill-index-builder | `lambda_bill_index.py` | 323 | Builds search indexes for bill retrieval |
| jrk-bill-parser-failure-router | `lambda_failure_router.py` | 197 | Routes parser failures to DLQ or retry queue |
| jrk-email-ingest | `lambda_email_ingest.py` | 249 | Receives bills from email; uploads to S3 |
| jrk-enrichment-retry | `lambda_enrichment_retry.py` | 111 | Retries failed enrichment operations |
| jrk-meter-cleaner | `lambda_meter_cleaner.py` | 1,542 | Deduplicates/standardizes meter readings |
| jrk-presigned-upload | `lambda_presigned_upload.py` | 87 | Generates presigned S3 URLs for web uploads |
| jrk-bw-lookup | `lambda_bw_lookup.py` | 177 | Bandwidth/consumption data lookup service |
| jrk-vendor-notifier | `lambda_function.py` | 363 | Notifies vendors of bill posting status |
| jrk-vendor-property-mapper | `lambda_vendor_property_map.py` | 153 | Maps vendor accounts to property IDs |
| jrk-vendor-validator | `lambda_function.py` | 290 | Validates vendor data via Google API |
| vendor-cache-builder | `build_vendor_cache.py` | 217 | Pre-builds vendor cache (offloads from web app) |

**Lambda totals:** 18 files, ~7,500 lines across 13 distinct functions (some functions have multiple .py files).

**Observations:**
- `aws_lambdas/us-east-1/jrk-vendor-validator/code/googleapiclient/` appears to contain vendored `googleapiclient` library — should be in `.gitignore` or lambda layer
- `jrk-meter-cleaner` at 1,542 lines is the heaviest Lambda — complex matching logic

---

## `bill_review_app/` package (Vacant Electric subsystem)

Active subsystem for vacant-unit electric cost allocation. Production-ready.

| Path | Lines | Category | Purpose | Status |
|---|---|---|---|---|
| `vacant_electric/__init__.py` | 42 | PACKAGE | Module exports | ACTIVE |
| `vacant_electric/property_maps.py` | 843 | PROD-CORE | Tenant-to-meter mapping rules; allocation logic | ACTIVE |
| `vacant_electric/entrata_ar.py` | 594 | PROD-CORE | Entrata accounting records API integration | ACTIVE |
| `vacant_electric/batch_runner.py` | 553 | PROD-CORE | Batch processing orchestrator | ACTIVE |
| `vacant_electric/web_models.py` | 472 | PACKAGE | Pydantic models for web requests | ACTIVE |
| `vacant_electric/pipeline.py` | 399 | PROD-CORE | Allocation pipeline + state management | ACTIVE |
| `vacant_electric/s3_bills.py` | 392 | PROD-CORE | S3 bill locator/fetcher | ACTIVE |
| `vacant_electric/lease_clauses.py` | 269 | PROD-CORE | Lease parsing for allocation terms | ACTIVE |
| `vacant_electric/matcher.py` | 235 | PROD-CORE | Meter-to-tenant matching | ACTIVE |
| `vacant_electric/reports.py` | 213 | PROD-CORE | Allocation report generation | ACTIVE |
| `vacant_electric/classifier.py` | 209 | PROD-CORE | Meter/account classification | ACTIVE |
| `vacant_electric/models.py` | 81 | PACKAGE | Data models | ACTIVE |
| `vacant_electric/config.py` | 88 | PACKAGE | Configuration/constants | ACTIVE |
| `vacant_electric/queries.py` | 115 | PROD-CORE | Database queries | ACTIVE |
| `vacant_electric/parser.py` | 46 | PROD-CORE | Invoice parser | ACTIVE |
| `vacant_electric/corrections.py` | 73 | PROD-CORE | Manual correction handler | ACTIVE |
| `vacant_electric/test_e2e.py` | 297 | TEST | E2E pipeline tests | ACTIVE |
| `utils.py` (package root) | — | PROD-CORE | Package-level utils | ACTIVE |
| `aws_lambdas/shared/pipeline_tracker.py` | — | PROD-CORE | Shared Lambda utility | ACTIVE |
| `infra/rework_lambda/rework_handler.py` | — | ENTRY | Rework Lambda handler | ACTIVE |
| `infra/urlshort/urlshort_index.py` | — | ENTRY | URL shortener Lambda | ACTIVE |
| `scripts/build_vendor_cache.py` | — | SCRIPT-PROD | Vendor cache builder script | ACTIVE |
| `scripts/entrata_get_vendors_example.py` | — | SCRIPT-DEBUG | Entrata vendor fetch example | STALE |
| `entrata_send_invoices_prototype.py` | — | SCRIPT-DEBUG | Duplicate of root-level prototype? Verify during review | UNKNOWN ⚠️ |

**Note:** `bill_review_app/main.py` was deleted 2026-04-08 per memory (`MEMORY.md`).

---

## Tests

Under `tests/`. Pytest-based, 23 files, 5,143 lines total. All ACTIVE.

| Path | Lines | Purpose |
|---|---|---|
| `conftest.py` | ~100 | Pytest fixtures |
| `integration/test_api_ubi.py` | 774 | UBI batch API tests |
| `integration/test_api_config.py` | 646 | Configuration API tests |
| `integration/test_api_invoices.py` | 513 | Invoice API tests |
| `integration/test_api_bulk_ops.py` | 449 | Bulk operations tests |
| `integration/test_api_users.py` | 421 | User management tests |
| `integration/test_api_submit.py` | 406 | Bill submission tests |
| `integration/test_api_metrics.py` | 396 | Metrics/reporting tests |
| `integration/test_api_auth.py` | 293 | Auth/authorization tests |
| `unit/test_helpers.py` | 476 | Utility function tests |
| `unit/test_auth.py` | 466 | Auth unit tests |
| `unit/test_security.py` | 301 | Security tests |
| `unit/lambdas/test_bill_enricher.py` | — | Enricher Lambda tests |
| `unit/lambdas/test_bill_parser.py` | — | Parser Lambda tests |
| `unit/lambdas/test_bill_router.py` | — | Router Lambda tests |
| `smoke_test_production.py` | ~50 | Post-deploy smoke test |
| `test_api_integration.py` | — | Top-level integration tests |

---

## Templates (Jinja2)

`templates/` — 56 files, ~30,000 lines total (including inline JS/CSS). All PROD-TEMPLATE / ACTIVE unless noted.

**Largest (> 1000 lines, heavy inline JS):**

| Template | Lines | Purpose |
|---|---|---|
| `billback.html` | 4,118 | Billback management with UBI classification |
| `review.html` | 3,754 | Daily invoice review editor |
| `workflow.html` | 2,571 | Workflow dashboard |
| `master-bills.html` | 2,315 | Master bills management |
| `metrics.html` | 2,067 | Metrics dashboard with charts |
| `input.html` | 1,361 | Input/upload view |
| `invoices.html` | 1,237 | Invoice list |
| `chart-by-meter.html` | 1,174 | Meter-level charts |
| `post.html` | 1,126 | POST validation page |
| `debug.html` | 1,062 | Debug triage dashboard |

**Mid-sized (500-1000 lines):** `account_manager.html`, `track.html`, `config.html`, `config_old.html` (DEAD candidate?), `print_checks.html`, `failed.html`, `directed.html`, `ubi_mapping.html`, `history.html`, `pipeline.html`, `submeter-rates.html`, `knowledge_base.html`, `autonomy_sim.html`, `review_checks.html`, `uom_mapping.html`, `flagged_review.html`, `vendor_corrections.html`, `billback_summary.html`.

**Other ~30 smaller templates** for login, error pages, config sub-pages, account gap analysis, portfolio config, etc.

**Observations:**
- Heavy inline JavaScript — no SPA framework, no build step for frontend. All JS is inline `<script>` in the template.
- `config_old.html` (832 lines) is likely superseded by `config.html` — verify during review; candidate for deletion.
- `review.html` is the hottest template; multiple recent bug fixes (GL swap bug, clone logic) touched it.

---

## Abandoned / Candidate-for-Deletion

These appear unused and are candidates for deletion after confirmation.

### Dead entry points
- `app.py` at root — Streamlit stub, not imported anywhere

### Abandoned parser iterations (archaeology)
`GEMINI_PDF_PARSER/` contains 10 iterative parsing scripts (0-8) superseded by the production `jrk-bill-parser` Lambda:
- `0_PDF_PARSER_OCR_PyMuPDF.py` through `8_PDF_PARSER_REMOVING_FAILURES.py`
- Also archival copies: `lambda_bill_parser.py`, `lambda_bill_enricher.py`, `lambda_presigned_upload.py`
- `upload_to_s3_pending_parser.py`, `ping.py`

### Abandoned UBI scripts
All Feb 23 timestamps, no recent imports:
- `UBI/Generate Entrata Core Formatted Excel Upload Documents of UBI Transactions.py`
- `UBI/Select File and Write PDF Invoice.py`
- `UBI/Select File and Write PDF Invoice - Updating to New Ref Format.py`
- `UBI/Union Meter Read Raw Read Extracts - Database Insert Added.py`
- `UBI/Write PDF to LeaseDocument _ New API Format.py`
- `UBI/ubi_bulk_reverse.py`, `UBI/ubi_cleanup_pass.py`, `UBI/ubi_past_lease_audit.py`
- `UBI/debug_entrata_response.py`

### Abandoned PDF parsers
- `PDF_PARSER/main.py`, `utility_bill_parser.py`, `text_extractor.py`, `download_model.py`

### Abandoned legal parser
- `LEGAL_BILL_PARSER/GEMINI_PDF_PARSER.py`

### Unknown purpose
- `CANDIDATE_SCRIPT_UPDATES/Candidate_Script_Final.py` — needs investigation

### Vendored deps that should not be in git
- `aws_lambdas/us-east-1/jrk-vendor-validator/code/googleapiclient/` — should be a Lambda layer
- `build/` — Python package build output (should be .gitignore'd)
- `coverage_html/` — pytest-cov output

---

## Proposed cleanup actions (pending user approval)

| Action | Target | Files | Rationale |
|---|---|---|---|
| Delete | `app.py` | 1 | Dead Streamlit stub |
| Archive | `GEMINI_PDF_PARSER/` | 14 | Parser iteration history, superseded by production Lambdas |
| Archive | `UBI/` (scripts) | 9 | One-off backfill scripts, done |
| Archive | `PDF_PARSER/` | 4 | Abandoned local-model prototype |
| Archive | `LEGAL_BILL_PARSER/` | 1 | Abandoned |
| Archive | `CANDIDATE_SCRIPT_UPDATES/` | 1 | Unclear; archive after verifying |
| Move | `backfill_*.py`, `migrate_*.py`, `verify_*.py`, `reconcile_counts.py` | 6 | Move to `scripts/migrations_archive/` |
| Delete | `templates/config_old.html` | 1 | Superseded by `config.html` (verify first) |
| Delete | `build/`, `coverage_html/` | many | Add to .gitignore |
| Verify + possibly archive | root-level `test_*.py` (10 stale files) | 10 | Pre-cutover validation; production tests are in `tests/` |
| Fix | `aws_lambdas/us-east-1/jrk-vendor-validator/code/googleapiclient/` | — | Move to Lambda layer |

**Total proposed: ~45 files archived, 2+ files deleted, ~8 files moved.** Code surface shrinks from ~170 files to ~120 files.

**⚠️ DO NOT EXECUTE without user approval.** User approval requested in the Phase 1 reorg plan (see `10_doc_audit.md`).
