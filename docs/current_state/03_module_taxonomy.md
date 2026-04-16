# 03 — main.py Module Taxonomy

main.py is a ~34K-line monolith. It divides naturally into **14 logical modules** based on feature area and line groupings. Some modules scatter across multiple line ranges because helper functions are grouped by operation (e.g., all `_perf_*` functions together) rather than by module.

This taxonomy is the basis for Phase 2 per-module deep-dives under `modules/`.

---

## Module Definitions

The codebase divides naturally by operational stage and feature area. The 34K lines span **14 logical modules**, some scattered across multiple sections due to related helper functions.

### Module 1: Auth
**Lines:** 1562–1605, 3169–3416  
**Purpose:** User authentication, role-based access control (RBAC), session management, and user admin. Manages login, password reset, and user enable/disable via `auth` module. Multi-user system with System Admin, Supervisor, Team Lead, and Reviewer roles.  
**Key Functions:**
- `set_session()` (1565), `get_current_user()` (1569), `require_user()` (1591), `require_admin()` (1605)
- `login_form()` (3170), `login()` (3174), `change_password()` (3226), `users_page()` (3267)
- User CRUD: `api_list_users()` (3286), `api_create_user()` (3297), `api_disable_user()` (3329), `api_enable_user()` (3342), `api_reset_password()` (3355), `api_change_role()` (3387)

**Key Endpoints:**
- /login, /logout, /change-password, /config/users, /api/users/*

**DDB Tables:** `jrk-bill-config` (role storage)

**S3 Prefixes:** None

---

### Module 2: Parse & Input
**Lines:** 1618–3165, 3439–3938  
**Purpose:** Handle bill PDF upload to Stage 1, dashboard showing parsed/pending bills, search index, and Gemini-powered service date extraction from scraper PDFs. Auto-detects and retriggers uppercase .PDF files missed by S3 event rules.  
**Key Functions:**
- `list_dates()` (1618), `load_day()` (1668), `_fetch_s3_file()` (1646)
- `parse_dashboard()` (3440) — pagination over last 8 days with load-more
- `_index_one_day()` (3545), `_build_search_index()` (3671), `_save_search_index_to_s3()` (3596)
- `api_upload_input()` (3879), `api_retrigger_pending_pdfs()` (3908)
- Scraper: `_fetch_scraper_integrations()` (3947), `_list_unlinked_account_folders()` (4022)
- Date extraction: `_extract_dates_from_pdf()` (4610), `_cache_pdf_dates()` (4585)
- `api_scraper_providers()` (4085), `api_scraper_accounts()` (4171), `api_scraper_pdfs()` (4256), `api_scraper_import()` (4411), `api_scraper_extract_dates()` (4690)

**Key Endpoints:**
- /parse, /input, /search, /api/search, /api/upload_input, /api/retrigger_pending_pdfs
- /api/scraper/providers, /api/scraper/accounts/*, /api/scraper/pdfs/*, /api/scraper/import, /api/scraper/extract-dates

**DDB Tables:**
- `jrk-bill-config` (search index cache)
- `jrk-bill-ai-suggestions` (cached PDF dates)

**S3 Prefixes:**
- Bill_Parser_1_Pending_Parsing/ (INPUT_PREFIX) — RW
- Bill_Parser_2_Parsed_Inputs/ (PARSED_INPUTS_PREFIX) — R
- Bill_Parser_4_Enriched_Outputs/ (ENRICH_PREFIX) — R
- Bill_Parser_Config/ (CONFIG_PREFIX) — search index cache
- jrk-utility-pdfs (SCRAPER_BUCKET) — R

---

### Module 3: Review & Invoice Editor
**Lines:** 25716–27804, 26261–26966  
**Purpose:** Daily invoice review interface (per-day workflow), line-item editing, override management, property/vendor bulk assignment. Core user-facing feature. Includes timing analytics (how long users spend per invoice). Drafts stored in DynamoDB, overrides to S3.  
**Key Functions:**
- `review_view()` (25716) — main review page
- `api_day()` (26449), `api_invoices()` (26464), `api_invoices_status()` (26511)
- `get_draft()` (1813), `put_draft()` (3115), `get_header_drafts_batch()` (1828)
- `api_drafts()` (26633), `api_drafts/new-lines()` (26651), `api_drafts/batch()` (26704)
- `_calc_invoice_total()` (3522)
- Timing: `api_timing/*` endpoints (26863–26966) for user workload tracking
- Bulk ops: `api_bulk_assign_property()` (24780), `api_bulk_assign_vendor()` (24883), `api_bulk_rework()` (24989), `api_split_bill()` (25192), `api_rework()` (25290)
- `api_overrides()` (27018), `api_status()` (27032), `api_submit()` (27038)

**Key Endpoints:**
- /day, /invoices, /review, /api/day, /api/invoices, /api/drafts, /api/timing/*, /api/submit

**DDB Tables:**
- `jrk-bill-review` (line item statuses)
- `jrk-bill-drafts` (property/vendor/GL edits)

**S3 Prefixes:**
- Bill_Parser_4_Enriched_Outputs/ (ENRICH_PREFIX) — R
- Bill_Parser_5_Overrides/ (OVERRIDE_PREFIX) — RW
- Bill_Parser_6_PreEntrata_Submission/ (PRE_ENTRATA_PREFIX) — RW
- Bill_Parser_Rework_Input/ (REWORK_PREFIX) — RW

---

### Module 4: Post & Entrata Integration
**Lines:** 2088–3159, 4853–4885  
**Purpose:** Merge parsed files, validate against Entrata GL/vendor rules, post invoices to Entrata API, manage distributed post locks to prevent duplicate submissions. Multi-step workflow: validate → acquire lock → post → mark posted.  
**Key Functions:**
- `_entrata_post_succeeded()` (line 1) — heuristic parse of Entrata responses (handles duplicate detection)
- Validation: `api_post_validate()` (2089) — checks vendor-property and vendor-GL pairs
- Lock management: `_acquire_post_lock()` (2183), `_update_post_lock()` (2238), `_clear_post_locks()` (2360)
- Posting: `api_post_to_entrata()` (2648) — main posting loop with retry logic, nonce verification
- `api_verify_entrata_sync()` (2500) — sync check
- `api_advance_to_post_stage()` (3002), `api_archive_parsed()` (3048)
- `api_post_total()` (4853) — lazy-load total for merged files

**Key Endpoints:**
- /post, /api/post/validate, /api/post_to_entrata, /api/clear_post_locks, /api/test_post_lock, /api/verify_entrata_sync, /api/advance_to_post_stage, /api/archive_parsed

**DDB Tables:**
- `jrk-bill-config` (POST_LOCK entries with nonce for idempotency)
- `jrk-bill-ai-suggestions` (posted invoice metadata cache)

**S3 Prefixes:**
- Bill_Parser_6_PreEntrata_Submission/ (PRE_ENTRATA_PREFIX) — RW
- Bill_Parser_7_PostEntrata_Submission/ (POST_ENTRATA_PREFIX) — RW
- Bill_Parser_99_Historical Archive/ (HIST_ARCHIVE_PREFIX) — W

---

### Module 5: UBI (Utility Billing Index)
**Lines:** 4887–7430  
**Purpose:** Classify posted invoices into utility billing index (UBI) categories (electricity, water, gas, waste, etc.). Supports manual assignment, AI suggestions, account-level unassignment, exclusion hash caching. Critical for billback reporting by utility type.  
**Key Functions:**
- `ubi_view()` (4888) — page to classify with selectable post date
- Cache: `_load_ubi_cache_from_s3()` (909), `_get_cached_exclusion_hashes()` (867)
- Unassigned: `_get_ubi_unassigned_cached()` (5373)
- AI suggestions: `api_billback_ubi_suggestions()` (5821) — ML-based recommendations
- Assignment: `api_billback_ubi_assign()` (5536), `api_billback_ubi_unassign()` (6568)
- Account-level: `api_billback_ubi_unassign_account()` (6749), `api_billback_ubi_reassign_account()` (7024)
- Archive: `api_billback_ubi_archive()` (7303)
- History: `api_billback_ubi_account_history()` (6291)

**Key Endpoints:**
- /ubi, /api/billback/ubi/*, /api/billback/ubi-batch/*

**DDB Tables:**
- `jrk-bill-ubi-assignments` (UBI classifications)
- `jrk-bill-ubi-archived` (archived UBI records)
- `jrk-bill-config` (UBI mapping, exclusion hashes cache)
- `jrk-bill-knowledge-base` (UBI suggestions refinement)

**S3 Prefixes:**
- Bill_Parser_7_PostEntrata_Submission/ (POST_ENTRATA_PREFIX) — R
- Bill_Parser_8_UBI_Assigned/ (UBI_ASSIGNED_PREFIX) — RW
- Bill_Parser_99_Historical Archive/ (HIST_ARCHIVE_PREFIX) — RW
- Bill_Parser_Config/ (CONFIG_PREFIX) — ubi_mapping.json, ubi_account_history.json

---

### Module 6: Billback
**Lines:** 4900–7430  
**Purpose:** After UBI assignment, create billback invoices for tenants. Generate master billback documents, manage line items (GL code mapping, amount overrides), and submit to AP. Accrual tracking for true-ups.  
**Key Functions:**
- `billback_view()` (4901), `billback_summary_view()` (4907)
- `api_billback_posted()` (5111) — list posted invoices by period
- Saving: `api_billback_save()` (5185), `api_billback_submit()` (5241)
- Archive: `api_billback_archive()` (5013)
- Summary: `api_billback_summary()` (5270) — aggregation by property, vendor, charge code, month
- Line item: `api_billback_update_line_item()` (19516), `api_billback_assign_periods()` (19594), `api_billback_send_to_post()` (19645)
- Report: `api_billback_report/*` (29157–30007) — data, PDF export, period selection

**Key Endpoints:**
- /billback, /billback/summary, /billback/charts, /api/billback/*, /api/billback/report/*

**DDB Tables:**
- `jrk-manual-billback-entries` (manual overrides, true-ups)
- `jrk-bill-config` (charge code mappings)

**S3 Prefixes:**
- Bill_Parser_8_UBI_Assigned/ (UBI_ASSIGNED_PREFIX) — R
- Bill_Parser_Config/ (CONFIG_PREFIX) — charge code mappings

---

### Module 7: Master Bills & Accrual
**Lines:** 20202–22206  
**Purpose:** Generate consolidated master bills by property/month, manage GL code classification, handle accruals (monthly true-ups) and manual entries. Works with Stage 8 (UBI_ASSIGNED) data.  
**Key Functions:**
- Generation: `api_master_bills_generate()` (19734) — create master bills from UBI-assigned data
- List: `api_master_bills_list()` (20202)
- Detail: `api_master_bills_detail()` (20482)
- Edits: `api_master_bills_exclude_line()` (20755), `api_master_bills_reclassify()` (20811), `api_master_bills_override_amount()` (20912)
- Manual: `api_master_bills_upload_manual()` (21038), `api_master_bills_manual_entries()` (21232)
- Accrual: `api_accrual_create()` (22118), `api_accrual_entries()` (22206), `api_accrual/calculate()` (21955)
- Completion tracker: `api_master_bills_completion_tracker()` (21338)

**Key Endpoints:**
- /master-bills, /api/master-bills/*, /api/accrual/*

**DDB Tables:**
- `jrk-bill-manual-entries` (accrual entries)
- `jrk-bill-config` (GL mappings, charge codes)

**S3 Prefixes:**
- Bill_Parser_8_UBI_Assigned/ (UBI_ASSIGNED_PREFIX) — R
- Bill_Parser_Config/ (CONFIG_PREFIX) — master bills cache

---

### Module 8: Print Checks & Check Review
**Lines:** 30439–31500  
**Purpose:** Generate check slips (payments) for billback invoices, create PDF check docs, and review before mailing. Fast DynamoDB metadata lookup for invoice details (vendor, amount, GL, account).  
**Key Functions:**
- `print_checks_view()` (30439), `review_checks_view()` (30447)
- `_get_cached_invoices_in_slips()` (993) — invoice metadata cache
- `_write_posted_invoice_metadata()` (30308) — cache at post time for fast CHECK REVIEW
- Slip creation: `api_print_checks_create_slip()` (30700)
- Slip list: `api_print_checks_my_slips()` (30773)
- PDF: `api_print_checks_slip_pdf()` (30853), `api_print_checks_bulk_pdf()` (31107)
- Review: `api_review_checks_pending()` (31347), `api_review_checks_slip()` (31372)
- Approve/Reject: `api_review_checks_approve()` (31462), `api_review_checks_reject()` (31500)

**Key Endpoints:**
- /print-checks, /review-checks, /api/print-checks/*, /api/review-checks/*

**DDB Tables:**
- `jrk-check-slips` (check slip metadata)
- `jrk-check-slip-invoices` (line items in slip)
- `jrk-bill-config` (posted invoice metadata — fast lookup)

**S3 Prefixes:**
- Bill_Parser_7_PostEntrata_Submission/ (POST_ENTRATA_PREFIX) — R (for invoice metadata)

---

### Module 9: Workflow & Directed Tasks
**Lines:** 8355–12055  
**Purpose:** Account-level workflow status (which accounts are ready to review, stuck, complete). Directed work feature assigns tasks to users with AI suggestions. Completion tracker monitors progress.  
**Key Functions:**
- `_s3_get_workflow_reasons()` (8357), `_s3_put_workflow_reasons()` (8370)
- `_s3_get_workflow_notes()` (8385), `_s3_put_workflow_notes()` (8473)
- Workflow dashboard: `workflow_view()` (8602), `api_workflow()` (10449)
- Directed: `directed_view()` (8962)
- `api_directed_generate()` (11551) — create daily task plan
- Completion: `api_directed_complete()` (11598), `api_directed_incomplete()` (11679)
- `api_workflow_completion_tracker()` (11009)
- Weekly objectives: `api_workflow_weekly_objectives()` (11446)

**Key Endpoints:**
- /workflow, /directed, /api/workflow/*, /api/directed/*

**DDB Tables:**
- `jrk-bill-config` (workflow notes, reasons, completion cache)
- `jrk-bill-pipeline-tracker` (daily events, tasks)

**S3 Prefixes:**
- Bill_Parser_Config/ (CONFIG_PREFIX) — workflow_cache.json, directed_plan_*.json, completion_tracker_cache.json.gz

---

### Module 10: Metrics & Analytics
**Lines:** 12055–16507  
**Purpose:** Performance metrics, user timing, parsing volume, late fees, outlier detection, submitter stats, week-over-week trends. Autonomy simulator evaluates vendor auto-posting eligibility.  
**Key Functions:**
- Dashboard: `metrics_view()` (12076), `perf_view()` (16615)
- User timing: `api_metrics_user_timing()` (12082)
- Parsing: `api_metrics_parsing_volume()` (12148)
- Pipeline: `api_metrics_pipeline_summary()` (12185), `api_parser_throughput()` (12343), `api_parser_queue_depth()` (12424)
- Bill events: `api_bill_events()` (12481)
- Submitter: `api_metrics_submitter_stats()` (13046)
- Week-over-week: `api_metrics_week_over_week()` (13450)
- Late fees: `api_metrics_late_fees()` (13751)
- Activity: `api_metrics_activity_detail()` (13929)
- Outliers: `api_metrics_outliers()` (16113), `api_metrics_outliers_scan()` (16216)
- Autonomy: `api_autonomy_sim()` (12939), `api_autonomy_sim_run()` (12958)
- Perf live: `api_perf_live()` (16631), `api_perf_rollups()` (16669)

**Key Endpoints:**
- /metrics, /perf, /autonomy-sim, /api/metrics/*, /api/parser/*, /api/autonomy/*

**DDB Tables:**
- `jrk-bill-config` (metrics cache, performance rollups, autonomy config)
- `jrk-bill-pipeline-tracker` (event history)
- `jrk-bill-ai-suggestions` (late fee metadata)

**S3 Prefixes:**
- Bill_Parser_Config/ (CONFIG_PREFIX) — metrics_cache_*.json.gz, autonomy_sim_results.json.gz, outlier_records.json

---

### Module 11: Configuration & Catalog
**Lines:** 8250–23269, 18611–23269  
**Purpose:** CRUD for business data: vendors, properties, GL accounts, charge code mappings, UBI mappings, UOM mappings, AP team assignments, account tracking, vendor corrections, gap analysis.  
**Key Functions:**
- Catalog: `api_catalog_vendors()` (18611), `api_catalog_properties()` (18664), `api_catalog_gl_accounts()` (18699)
- Accounts to track: `api_config_accounts_to_track()` (18745)
- GL charge code: `api_config_gl_charge_code_mapping()` (19682)
- UBI mapping: `api_config_ubi_mapping()` (22642)
- UOM mapping: `api_config_uom_mapping()` (22721)
- AP team/mapping: `api_config_ap_team()` (22601), `api_config_ap_mapping()` (22803)
- Overrides: vendor-property (22846), vendor-GL (22892)
- Vendor corrections: `api_vendor_corrections_suspects()` (9371), `api_vendor_corrections_apply()` (9615)
- Gap analysis: `api_account_gap_analysis_upload()` (9737), `api_account_gap_analysis_run()` (9851)

**Key Endpoints:**
- /config/*, /api/config/*, /api/catalog/*, /vendor-corrections, /account-gap-analysis

**DDB Tables:**
- `jrk-bill-config` (S3-primary for most, DynamoDB fallback for small items)

**S3 Prefixes:**
- Bill_Parser_Config/ (CONFIG_PREFIX) — accounts_to_track.json, ubi_mapping.json, vendor_corrections.json, gap_analysis/*, etc.

---

### Module 12: Account Manager & Vacant Properties
**Lines:** 8642–9722  
**Purpose:** Account rename/alias management, duplicate bill detection, closed account cleanup. Directed workflow for vacant property accounts (Vacant Electric integration).  
**Key Functions:**
- Account manager: `account_manager_view()` (8642)
- Rename: `api_account_manager_rename_account()` (8681), `api_account_manager_rename_history()` (8761)
- Duplicates: `api_account_manager_duplicate_bills()` (8798)
- Closed: `api_account_manager_closed_accounts()` (8876), `api_account_manager_remove_closed_accounts()` (8919)
- Vacant: `api_workflow_vacant_accounts()` (8968)
- Account lifecycle: `api_workflow_accounts_archive()` (9127), `api_workflow_accounts_restore()` (9174), `api_workflow_accounts_update()` (9245)

**Key Endpoints:**
- /account-manager, /api/account-manager/*, /api/workflow/vacant-accounts, /api/workflow/accounts/*

**DDB Tables:**
- `jrk-bill-config` (account metadata, vacant property config)

**S3 Prefixes:**
- Bill_Parser_Config/ (CONFIG_PREFIX) — account skip reasons, comments

---

### Module 13: Knowledge Base & AI Review
**Lines:** 31541–33928  
**Purpose:** ML-driven invoice review suggestions, pattern learning from user corrections, knowledge base for vendors/properties/GL codes. Quarantine bad patterns. Learning stats and accuracy tracking.  
**Key Functions:**
- `knowledge_base_view()` (31541)
- CRUD: `api_knowledge()` (31547–31847)
- AI review: `api_ai_review_analyze()` (32170), `api_ai_review_suggestion()` (32406), `api_ai_review_stats()` (33182)
- Learning: `api_ai_learning_stats()` (33265), `api_ai_learning_quarantined()` (33350), `api_ai_learning_review_pattern()` (33386)
- Autonomy: `api_autonomy_config()` (33619), `api_autonomy_promote()` (33652), `api_autonomy_demote()` (33729)

**Key Endpoints:**
- /knowledge-base, /ai-review-dashboard, /api/knowledge/*, /api/ai-review/*, /api/ai-learning/*, /api/autonomy/*

**DDB Tables:**
- `jrk-bill-knowledge-base` (vendor/property/GL rules)
- `jrk-bill-ai-suggestions` (suggestions, learning patterns)
- `jrk-bill-config` (autonomy config)

**S3 Prefixes:**
- Bill_Parser_Config/ (CONFIG_PREFIX)

---

### Module 14: Admin, Debug, Failure Analysis, Meters, Portfolio
**Lines:** 12055–34247  
**Purpose:** Admin tools (backfill, audit), debug/troubleshooting (reports, logs, orphaned data), failed job retry, meter consolidation/analytics, portfolio management, submeter rates.  
**Key Functions:**
- Admin: `admin_view()` (16623), `api_admin_backfill_posted_metadata()` (16697), `api_admin_backfill_late_fees()` (16753)
- Debug: `debug_view()` (12060), `api_debug_reports()` (23149), `api_debug_upload_screenshot()` (23937)
- Failure: `failed_view()` (12068), `api_failed_jobs()` (16881), `api_failed_retry()` (17079)
- Meters: `api_meters_scan()` (28477), `api_meters_analytics()` (28716), `api_meters_merge()` (28871), `api_meters_ai_clean()` (28988)
- Portfolio: `portfolio_config_view()` (30094), `api_portfolio_upload()` (30145)
- Submeter rates: `submeter_rates_view()` (33894), `api_submeter_rates_generate()` (34179)

**Key Endpoints:**
- /admin, /debug, /failed, /api/metrics/*, /api/failed/*, /api/meters/*, /portfolio-config, /submeter-rates

**DDB Tables:**
- `jrk-bill-debug` (debug reports, screenshots, activity logs)
- `jrk-bill-parser-errors` (error tracking)
- `jrk-bill-config` (debug cache)

**S3 Prefixes:**
- Bill_Parser_Failed_Jobs/ (FAILED_JOBS_PREFIX) — R
- Bill_Parser_Meter_Data/ (METER_DATA_PREFIX) — RW
- Bill_Parser_Config/ (CONFIG_PREFIX) — portfolio_master.json
- improve-screenshots/ — debug screenshots

---

---

## Notes on module boundaries

- **Module scatter** is real. E.g., Module 14 (Admin/Debug/Failure/Meters/Portfolio) spans `12055–34247` because related functionality is not contiguous. Phase 2 review will map tighter ranges for each sub-topic.
- Helper functions grouped by concern (e.g., all cache helpers `_perf_*`, `_ubi_*`) rather than by module. Recommend future refactor into dedicated helper modules.
- The **14-module count** is the upper bound; some pairs could be merged (e.g., UBI + Billback are tightly coupled) or split (e.g., Module 14 is 5 concerns).

## Phase 2 review order (proposed)

Order to minimize cognitive switching and maximize coverage of production-critical paths:

1. **Module 1 — Auth** (small, self-contained, sets baseline for security review)
2. **Module 2 — Parse & Input** (entry point into the pipeline)
3. **Module 4 — Post & Entrata** (highest-risk: money movement)
4. **Module 5 — UBI** (complex state machine; multiple reported issues)
5. **Module 6 — Billback** (closely coupled to UBI)
6. **Module 7 — Master Bills & Accrual** (downstream of UBI; data-quality known issues)
7. **Module 8 — Print Checks** (financial output)
8. **Module 3 — Review & Invoice Editor** (big, but foundational)
9. **Module 9 — Workflow & Directed** (state tracking)
10. **Module 10 — Metrics & Analytics** (reporting)
11. **Module 11 — Config & Catalog** (CRUD)
12. **Module 12 — Account Manager & Vacant** (specialized)
13. **Module 13 — Knowledge Base & AI Review** (newer layer)
14. **Module 14 — Admin, Debug, Failure, Meters, Portfolio** (grab bag; last)

**Non-main.py modules** (parallel tracks):
- `modules/lambdas.md` — all 13 AWS Lambdas (covered after Module 2)
- `modules/vacant_electric.md` — `bill_review_app/vacant_electric/` (late stage)
- `modules/templates.md` — Jinja2 templates (reviewed alongside corresponding main.py module)
- `modules/tests.md` — Test suite (interleaved to verify coverage)
- `modules/scripts.md` — Root scripts (last — most are STALE or ABANDONED)

User should confirm or override this order in Phase 2 kickoff.

---

Next: [04_data_architecture.md](./04_data_architecture.md)
