# Bill Review — Current State Documentation

**Status:** Phase 0 (Setup & Inventory) — In Progress
**Started:** 2026-04-16
**Scope:** Comprehensive line-by-line review of the entire codebase, with business requirements, drift reconciliation against existing docs, and docs reorganization.

## What this directory is

This is the canonical, actively-maintained snapshot of the Bill Review system as it exists today. Everything here is built from direct reading of the code, cross-referenced against the claimed state in existing docs. Drift (code vs. doc disagreement) is flagged explicitly.

Older docs that describe design intent, completed plans, or historical analyses remain available under `docs/archive/` and `docs/superseded/` — but only `docs/current_state/` (and `docs/current/` when we migrate) should be trusted as describing what the system actually does today.

## Audience

This documentation is written for four overlapping audiences:

1. **The developer (Craig)** — source of truth for day-to-day work
2. **Future AI assistants** — self-contained context for cold-start sessions
3. **Future engineers** — onboarding-quality explanations of domain and architecture
4. **Stakeholders / operations** — business-process framing of what each module does

Each module doc leads with a non-technical "What this module does" summary, then the technical detail.

## Phases

| Phase | Deliverables | Status |
|---|---|---|
| 0. Setup & Inventory | Directory structure, file catalog, endpoint inventory, module taxonomy, data arch, doc audit | ⏳ In progress |
| 1. Doc Reorganization | Execute reorg per 10_doc_audit.md proposal (move to archive/superseded) | ⏳ Awaiting approval |
| 2. Line-by-Line Module Review | Per-module deep-dive docs in `modules/`. Collaborative — user answers business-intent questions as code is reviewed. | ⏳ Awaiting Phase 0 completion |
| 3. Issue Prioritization | Consolidated issue log with severity, triaged | ⏳ Rolling |
| 4. Synthesis | Final current-state snapshot; architecture recommendations; forward plan | ⏳ At end |

## Contents

### Phase 0 Artifacts (this phase)
- [00_overview.md](./00_overview.md) — Multi-audience system overview, architecture, glossary
- [01_file_catalog.md](./01_file_catalog.md) — Every .py and template: role, size, status
- [02_endpoint_inventory.md](./02_endpoint_inventory.md) — All 349 FastAPI routes in main.py
- [03_module_taxonomy.md](./03_module_taxonomy.md) — main.py's 14 logical sub-modules with line ranges
- [04_data_architecture.md](./04_data_architecture.md) — DDB tables, S3 prefixes, data flow stages
- [10_doc_audit.md](./10_doc_audit.md) — Classification of ~35 existing docs + reorganization plan
- [ISSUES.md](./ISSUES.md) — Rolling issue log (populated during review)

### Phase 2 Artifacts (per-module; not yet created)
- `modules/README.md` — Index of module deep-dives
- `modules/01_auth.md`
- `modules/02_parse.md`
- `modules/03_review.md`
- `modules/04_post_entrata.md`
- `modules/05_ubi.md`
- `modules/06_billback.md`
- `modules/07_master_bills.md`
- `modules/08_print_checks.md`
- `modules/09_workflow_directed.md`
- `modules/10_metrics_perf.md`
- `modules/11_config_catalog.md`
- `modules/12_account_manager.md`
- `modules/13_knowledge_ai.md`
- `modules/14_admin_debug_meters.md`
- `modules/lambdas.md` — All 13 AWS Lambda functions
- `modules/vacant_electric.md` — bill_review_app/vacant_electric/ subsystem
- `modules/scripts.md` — Root-level scripts (production, debug, one-offs)
- `modules/tests.md` — Test suites
- `modules/templates.md` — Jinja2 templates + inline JS inventory

## How to use this during review

1. **Orientation** — Start with `00_overview.md` for architecture context
2. **"Where is X?"** — Consult `01_file_catalog.md` (files) or `02_endpoint_inventory.md` (routes)
3. **"How does module Y work?"** — Consult the relevant `modules/NN_xxx.md` when Phase 2 is underway
4. **"What's broken?"** — Consult `ISSUES.md`
5. **"What did the old docs claim?"** — Consult `10_doc_audit.md` for drift analysis per doc

## Conventions

- **File paths** are always absolute from repo root, e.g., `main.py:3430` (file:line_number)
- **Drift flags** use `🔴 DRIFT` callout blocks when code disagrees with an existing doc
- **Business requirements** captured as "What it does (business)" + "What it does (technical)" + "Why (motivation)" + "Users" subsections
- **Issues** tagged `[ISSUE-NNN]` with numbering from ISSUES.md
- **Open questions** tagged `[Q-NNN]` for user to answer in collaborative review

## Review lens (locked in 2026-04-16)

User identified the top-level issue as **systemic workflow incompleteness** — jobs don't flow end-to-end, features are clunky, nothing works completely. Every module review must therefore lead with **Job-to-Be-Done analysis** before bug hunting.

See `C:\Users\cbeach\.claude\...\memory\feedback_jtbd_review_lens.md` for the full protocol. Key sections each module doc must include (in order):

1. **Module Purpose (Business)** — what jobs this module supports
2. **User Personas & Roles** — who uses it
3. **End-to-End Workflow Walkthrough** — happy path, click-by-click
4. **🚨 Clunkiness / Workflow Gaps** — where jobs break, require manual work, or don't complete
5. **Integration Gaps** — where data/flow breaks between this module and adjacent ones
6. **Feature Inventory** — what the UI shows vs. what works
7. **Technical Implementation** — code-level review (line-by-line)
8. **Data Touchpoints** — DDB/S3 reads and writes
9. **Drift vs. Existing Docs** — where code disagrees with claimed behavior
10. **Issues Flagged** — list with IDs linking to ISSUES.md
11. **Open Questions for User** — for collaborative answer
12. **Dead / Unused Code** — within this module
