# 00 — System Overview

Multi-audience introduction to the Bill Review system. Each section targets a different reader.

---

## For Stakeholders / Operations (non-technical)

### What this system is

The **Bill Review application** is JRK Residential's internal tool for processing utility bills (electric, gas, water, sewer, trash, stormwater). Each month, JRK receives thousands of utility bills as PDFs from hundreds of properties. Rather than keying these into the accounting system by hand, this application:

1. **Ingests** PDFs from email, a web upload form, or a scraper that pulls them from utility company websites
2. **Parses** the PDFs using AI (Gemini) to extract line items, dates, amounts, meter readings
3. **Enriches** the data by matching vendors, properties, and GL codes from master data
4. **Lets AP staff review** the extracted data, correct errors, and approve for posting
5. **Posts** approved invoices to Entrata (the property management system / accounting)
6. **Classifies** posted invoices into utility categories (called "UBI" — Utility Bill Imaging) so residents can be billed back their share
7. **Generates master bills** that aggregate utility cost by property/month for financial reporting
8. **Prints checks** — AP creates check slips from posted invoices, Treasury reviews and approves before mailing

### Users

| Role | What they do |
|---|---|
| **AP Clerk** | Reviews parsed invoices, corrects errors, approves for posting. Uses REVIEW, POST, PRINT CHECKS modules. |
| **Treasury** | Approves check slips before payment. Uses REVIEW CHECKS module. |
| **Property Manager / Ops** | Monitors aging accounts, resolves vendor/property issues. Uses WORKFLOW, ACCOUNT MANAGER modules. |
| **Resident Billback Admin** | Assigns UBI periods for tenant billback, generates master bills. Uses BILLBACK, MASTER BILLS, UBI modules. |
| **System Admin** | Manages users, configures GL/vendor/property mappings. Uses ADMIN, CONFIG modules. |

### Why this exists (business motivation)

- **Scale:** JRK has hundreds of properties and thousands of utility accounts. Manual keying is error-prone and slow.
- **Billback obligation:** Many leases require tenants to pay their share of utilities. Accurate, timely bill-backs depend on accurate classification.
- **Compliance & accrual accounting:** Financial reporting needs monthly utility cost by property; accruals required when a bill hasn't arrived yet.
- **Audit trail:** Every edit, override, approval, and post action is logged for compliance.
- **Fraud / duplicate prevention:** Duplicate bills from vendors are detected and blocked before payment.

### Current health (as of 2026-04-14)

Per `HEALTH_AUDIT_2026_04_10.md` and `CODE_AUDIT_2026_04_14.md`:
- **Core flows are working** — ~100 endpoints responding correctly in production
- **~13 real failures** in production: 5 endpoints return HTTP 500s (IAM/import issues), 8 endpoints time out
- **Data quality issues** in master bills: 5 charge-code-to-utility-type mismatches out of 756 bills (< 1%, but needs cleanup)
- **Known tech debt:** `main.py` is 34K lines (monolithic), 349 endpoints in a single module
- **Recent security fix:** hardcoded API keys removed in commit `9071511`

---

## For New Engineers (onboarding)

### Tech stack

- **Web framework:** FastAPI + Uvicorn
- **Templates:** Jinja2 with inline JS (no SPA; server-rendered pages with fetch() for API calls)
- **Storage:**
  - **S3** (`jrk-analytics-billing` bucket) — all invoice PDFs, parsed JSONL, config JSON
  - **DynamoDB** (15 tables) — user drafts, config state, AI suggestions, check slips, etc.
  - **Snowflake** — analytical data warehouse for dim tables and reporting
- **AI:** Google Gemini (Pro for parsing, Flash for fast lookups)
- **Cloud:** AWS us-east-1 — AppRunner (web app), Lambdas (pipeline), S3, DynamoDB
- **Deployment:** Auto-deploys on push to `main` via CodeBuild + AppRunner (2 instances)
- **Auth:** Session-based, stored in DDB (`jrk-bill-config` table)

### Architecture (high-level)

```
┌─────────────────────────────────────────────────────────────────────┐
│  INGESTION (async, via AWS Lambdas)                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   Email ─────┐                                                      │
│              ▼                                                      │
│   Web upload ─► S1 Pending ─► Router ─► Parser (or Large Parser)   │
│              ▲                               │                      │
│   Scraper ───┘                               ▼                      │
│                                         S2 Parsed                   │
│                                              │                      │
│                                              ▼                      │
│                                         Enricher                    │
│                                              │                      │
│                                              ▼                      │
│                                         S4 Enriched                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  HUMAN REVIEW (synchronous, via web app main.py + templates/)       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   S4 Enriched ─► REVIEW (/day, /review) ─► Drafts (DDB)             │
│                                              │                      │
│                                              ▼                      │
│                                         POST validation             │
│                                              │                      │
│                                              ▼                      │
│                                         Merge into S6 PreEntrata    │
│                                              │                      │
│                                              ▼                      │
│                                         POST to Entrata API         │
│                                              │                      │
│                                              ▼                      │
│                                         S7 PostEntrata              │
└─────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  BILLBACK & REPORTING (synchronous, via web app)                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   S7 PostEntrata ─► UBI assignment ─► S8 UBI Assigned               │
│                                              │                      │
│                                              ▼                      │
│                                         Master Bills generate       │
│                                              │                      │
│                                              ▼                      │
│                                         Snowflake _Master_Bills_Prod│
│                                                                     │
│   S8 UBI Assigned ─► BILLBACK submission to tenants (Entrata AR)    │
│                                                                     │
│   S7 PostEntrata ─► Check slips (AP) ─► Review (Treasury) ─► Pay   │
│                                                                     │
│   Any stage ─► S9 Flagged Review (on user flag)                    │
│   Any stage ─► S99 Historical Archive (end of lifecycle)           │
└─────────────────────────────────────────────────────────────────────┘
```

### Where the code lives

| Path | Purpose |
|---|---|
| `main.py` | The FastAPI app — ALL routes, business logic, and orchestration. 34,272 lines, 349 endpoints, ~492 functions. See `02_endpoint_inventory.md` + `03_module_taxonomy.md`. |
| `auth.py` | User authentication (session management, password hashing, role checks). 282 lines. |
| `utils.py` | Shared helper functions (formatting, validation, date parsing). 379 lines. |
| `templates/` | Jinja2 HTML templates. Most have heavy inline JS (the "frontend" lives here). 56 files, ~30K lines. |
| `aws_lambdas/us-east-1/` | Serverless pipeline handlers: router, parser, large-parser, chunk-processor, enricher, aggregator, email-ingest, etc. 13+ Lambda functions. |
| `bill_review_app/vacant_electric/` | Standalone subsystem for vacant-unit electric allocation. Mounted under main FastAPI app. |
| `tests/` | Pytest suites (unit + integration). 5,143 lines. |
| `docs/` | Documentation (being reorganized as part of this review). |

### Deployment flow

1. Developer pushes to `main`
2. CodeBuild project `jrk-bill-review-build` triggers
3. Docker image built from `Dockerfile`, pushed to ECR
4. AppRunner service (2 instances) auto-deploys from ECR
5. In-memory caches on each instance wipe at deploy — S3-backed caches persist (see `feedback_cache_resets.md` memory)

Lambdas deploy separately (each has its own CodeBuild or manual update).

### Key architectural patterns

- **pdf_id** = SHA1 hash of S3 key. Identifies a PDF invoice across stages.
- **Header drafts** = DynamoDB records (`draft#{pdf_id}#__header__#{user}`) storing user-supplied overrides (vendor, property, GL). Overrides stack on top of enricher output.
- **S3 JSONL** = each parsed invoice is a JSONL file, one JSON record per line item.
- **Dual updates** = when a user edits vendor/property via bulk ops, BOTH the S3 JSONL AND the DynamoDB draft must be updated.
- **S3-primary config** = configs larger than DDB's 400KB item limit (e.g., accounts_to_track) are stored as JSON in S3, with DDB as optional fallback.
- **Cache pattern** = `_metrics_serve(name, compute_fn)` → in-memory → S3 fallback → async rebuild → 60min TTL.
- **External cache builders** = expensive caches built by Lambda (vendor cache) so app doesn't do cold-rebuild on startup. See `feedback_ubi_cache_architecture.md` memory.

---

## For Future AI Assistants (cold-start context)

### Quick orientation

- Project directory: `H:/Business_Intelligence/1. COMPLETED_PROJECTS/BILL_REVIEW/`
- Sole production entrypoint: `main.py` (ignore `bill_review_app/main.py` — deleted 2026-04-08 audit)
- `CLAUDE.md` has load-bearing conventions — read first
- Memory at `C:\Users\cbeach\.claude\projects\H--Business-Intelligence-1--COMPLETED-PROJECTS-BILL-REVIEW\memory\MEMORY.md`
- Deployment: push to `main` auto-deploys; do not run `deploy_app.ps1` unless asked
- Verify in production after any deploy via `python tests/smoke_test_production.py`

### Vocabulary you'll hit immediately

| Term | Meaning |
|---|---|
| **UBI** | Utility Bill Imaging — residential billback system. Each bill belongs to a UBI period (MM/YYYY). Not to be confused with "Utility Billing Index" (the term Section 2 of Agent output used — verify during review which is canonical). |
| **Billback** | Charging a tenant for their share of a utility cost. |
| **GL** | General Ledger. GL codes (e.g., `61000`) classify expenses by type. GL Name and GL Number are paired. |
| **Charge code** | The vendor/utility-specific identifier for what the line item is (e.g., `ELECTRIC`, `WATER`, `LATE FEE`). Maps to a GL. |
| **AP** | Accounts Payable. |
| **Entrata** | External property management / accounting platform JRK uses. The Bill Review app posts to and queries Entrata. |
| **Accrual** | Booking an estimated expense before the actual bill arrives (for month-end close). |
| **Rework** | Sending a PDF back for re-parse (e.g., if the parser got it wrong). |
| **Flagged** | Marked for manual review; moved to Stage 9. |
| **Master Bill** | Aggregated utility cost by property + month. Generated after UBI assignment. |
| **Check Slip** | A bundle of posted invoices printed together for one check payment. |
| **Pipeline tracker** | The completion tracker showing which accounts have bills for which months. |
| **Directed work** | Daily AI-generated task plan for AP clerks. |
| **Autonomy** | Vendor-level setting for auto-posting without human review (promoted/demoted based on accuracy). |

### What to reach for when

- "How does X work?" → `03_module_taxonomy.md` to find which module owns it, then `modules/NN_xxx.md` (Phase 2)
- "Where's endpoint /foo?" → `02_endpoint_inventory.md`
- "Which DDB table for Y?" → `04_data_architecture.md`
- "What's broken?" → `ISSUES.md`
- "What did the docs used to say?" → `10_doc_audit.md`
- "Is this file still used?" → `01_file_catalog.md`

---

## Glossary (full)

See vocabulary tables above. Canonical definitions will be expanded in Phase 2 as terms surface during module review.

## Next: go to [01_file_catalog.md](./01_file_catalog.md)
