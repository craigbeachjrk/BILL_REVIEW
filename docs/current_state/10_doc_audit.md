# 10 — Documentation Audit & Reorganization Plan

Classification of all 35 existing documentation files, with recommended action for each and a reorganization plan.

**Classification values:**
- `CURRENT` — Accurate and should be kept as canonical
- `NEEDS-UPDATE` — Good structure but content partially stale
- `SUPERSEDED` — Replaced by a newer doc
- `PLAN-COMPLETED` — Was a plan for work now finished
- `PLAN-IN-PROGRESS` — Plan for work partially done
- `PLAN-ABANDONED` — Plan for work never done
- `HISTORICAL` — Point-in-time record (audits, changelogs, session notes)
- `DUPLICATE` — Duplicates another doc

**Action values:**
- `KEEP-AT-ROOT` — Leave at repo root (CLAUDE.md, etc.)
- `MOVE-TO-CURRENT` — Move to `docs/current/` (being replaced by `docs/current_state/` during review)
- `MOVE-TO-ARCHIVE` — Move to `docs/archive/` (historical)
- `MOVE-TO-SUPERSEDED` — Move to `docs/superseded/` (replaced but referenced)
- `UPDATE-IN-PLACE` — Leave in place but refresh content
- `MERGE-INTO-X` — Merge into named doc
- `DELETE` — Remove (with user approval)

---

## Classification Table

### Root-level docs (mostly older, mostly November 2025 vintage)

| File | Date | Size | Classification | Action | Rationale |
|---|---|---|---|---|---|
| `CLAUDE.md` | 2026-03-10 | 11K | CURRENT | KEEP-AT-ROOT | Claude Code project instructions; actively used; do not move |
| `README.md` | 2026-02-23 | 25K | NEEDS-UPDATE | UPDATE-IN-PLACE (root) | Canonical user-facing docs; refresh with recent features |
| `OVERVIEW.md` | 2026-02-23 | 2K | CURRENT | SUPERSEDED by `docs/current_state/00_overview.md` | Good exec summary; our new overview is more comprehensive |
| `AUTHENTICATION_SETUP.md` | 2025-11-22 | 7K | HISTORICAL | MOVE-TO-ARCHIVE/2025-11 | Accurate record of 2025-11-09 auth implementation |
| `DEPLOYMENT_RECORD.md` | 2025-11-22 | 4.5K | HISTORICAL | MOVE-TO-ARCHIVE/2025-11 | Point-in-time deployment record |
| `CHANGELOG_2025-11-22.txt` | 2025-11-22 | 12K | HISTORICAL | MOVE-TO-ARCHIVE/2025-11 | Old changelog; use git history going forward |
| `CHANGES_2025-01-18.md` | 2025-11-22 | 5K | HISTORICAL | MOVE-TO-ARCHIVE/2025-01 | Very old change notes |
| `CODEBASE_ANALYSIS_REPORT.md` | 2025-11-22 | 16K | SUPERSEDED | MOVE-TO-SUPERSEDED | Replaced by CODE_AUDIT_2026_04.md / CODE_AUDIT_2026_04_14.md |
| `SESSION_SUMMARY.md` | 2025-11-22 | 19K | HISTORICAL | MOVE-TO-ARCHIVE/2025-11 | Session notes from 2025-11-17 UBI backend implementation |
| `WORK_COMPLETED_SUMMARY.md` | 2025-11-22 | 11K | SUPERSEDED | MOVE-TO-SUPERSEDED | Nov 2025 work summary; keep for ref |
| `IMPLEMENTATION_PROGRESS.md` | 2025-11-22 | 9K | HISTORICAL | MOVE-TO-ARCHIVE/2025-11 | UBI billback backend progress (all completed) |
| `IMPLEMENTATION_GAP_ANALYSIS.md` | 2025-11-22 | 11K | PLAN-COMPLETED | MOVE-TO-ARCHIVE/2025-11 | Gaps identified in Nov 2025 are now filled |
| `BILLBACK_INTEGRATION_COMPLETE.md` | 2025-11-22 | 9.4K | HISTORICAL | MOVE-TO-ARCHIVE/2025-11 | Frontend integration completion record |
| `BILLBACK_UPDATE_PLAN.md` | 2025-11-22 | 6.2K | PLAN-COMPLETED | MOVE-TO-ARCHIVE/2025-11 | Plan that was executed |
| `BILLBACK_ARCHITECTURE_ANALYSIS.md` | 2025-11-22 | 16K | HISTORICAL | MERGE-INTO-ARCHITECTURE_UBI, then ARCHIVE | Architecture analysis now implemented |
| `BILLBACK_CHARGE_CODE_ANALYSIS.md` | 2025-11-22 | 15K | PLAN-COMPLETED | MERGE-INTO-ARCHITECTURE_UBI, then ARCHIVE | Analysis identified gaps that are now fixed |
| `BILL_REVIEW_INTEGRATION.md` | 2026-03-03 | 11K | CURRENT | MOVE-TO-CURRENT | VE integration guide; still accurate |
| `INFRASTRUCTURE_RECOMMENDATIONS.md` | 2025-11-22 | 16K | NEEDS-UPDATE | MOVE-TO-CURRENT + refresh status | Recommendations guide; track which have been applied |
| `SNOWFLAKE_SETUP_GUIDE.md` | 2025-11-22 | 5.7K | CURRENT | MOVE-TO-CURRENT | Snowflake export setup guide |
| `STORAGE_ARCHITECTURE.md` | 2025-11-22 | 4.7K | CURRENT | MERGE-INTO-04_data_architecture, then ARCHIVE | Covered by our new doc |
| `UBI_API_REFERENCE.md` | 2025-11-22 | 13K | CURRENT | MOVE-TO-CURRENT | Canonical API ref for UBI endpoints |
| `UBI_BILLBACK_COMPLETE_ARCHITECTURE.md` | 2025-11-22 | 44K | HISTORICAL | MOVE-TO-CURRENT as ARCHITECTURE_UBI_DETAILED.md | Comprehensive ref; rename for clarity |
| `WORKFLOW_ENHANCEMENTS_DESIGN.md` | 2025-11-22 | 29K | PLAN-ABANDONED | MOVE-TO-SUPERSEDED | Design for 5 enhancements, status unknown — check against code |

### docs/ folder (more recent, some current)

| File | Date | Size | Classification | Action | Rationale |
|---|---|---|---|---|---|
| `AI_NATIVE_PLATFORM_PLAN.md` | 2026-02-23 | 38K | PLAN-COMPLETED | MOVE-TO-CURRENT (+note status) | Phase 1 complete per commit bda6e79 |
| `AUTONOMOUS_PIPELINE_PLAN.md` | 2026-03-24 | 14K | PLAN-IN-PROGRESS | MOVE-TO-CURRENT | 3-phase plan, phase 2-3 future; active |
| `BIG_BILL_DEPLOYMENT.md` | 2026-02-23 | 23K | CURRENT | MOVE-TO-CURRENT | Architecture ref for big-bill handling |
| `CODEBASE_REVIEW_FINDINGS.md` | 2026-02-23 | 5.2K | SUPERSEDED | MOVE-TO-ARCHIVE | Jan 2025 review, superseded by 2026-04 audits |
| `CODE_AUDIT_2026_04.md` | 2026-04-09 | 28K | SUPERSEDED | MOVE-TO-ARCHIVE | 134+ issues; older than 2026_04_14 version |
| `CODE_AUDIT_2026_04_14.md` | 2026-04-14 | 11K | CURRENT | MOVE-TO-CURRENT | Most recent audit; critical findings |
| `FLAG_FOR_REVIEW_BUG_REPORT.md` | 2026-02-23 | 11K | HISTORICAL | MOVE-TO-ARCHIVE | Jan 2025 bug analysis, now fixed |
| `HEALTH_AUDIT_2026_04_10.md` | 2026-04-13 | 12K | CURRENT | MOVE-TO-CURRENT | Prod health check; open issues |
| `IMPROVE_BUTTON_TEMPLATE.md` | 2026-03-05 | 42K | CURRENT | MOVE-TO-CURRENT | Feature docs for IMPROVE system |
| `MASTER_BILLS_DATA_QUALITY.md` | 2026-04-14 | 4.3K | CURRENT | MOVE-TO-CURRENT | Recent data quality audit |
| `PARSER_ACCURACY_IMPROVEMENT_PLAN.md` | 2026-03-19 | 44K | PLAN-IN-PROGRESS | MOVE-TO-CURRENT | Parser improvement plan; needs status update |
| `PERFORMANCE_ARCHITECTURE.md` | 2026-03-18 | 12K | PLAN-IN-PROGRESS | MOVE-TO-CURRENT | Caching/perf strategies; needs status update |
| `UBI_CACHE_FIX_SESSION_NOTES.md` | 2026-04-06 | 2.9K | HISTORICAL | MOVE-TO-ARCHIVE | Brief session notes; keep as record |

### docs/strategic_initiatives/

| File | Status | Action |
|---|---|---|
| `README.md` | Index of S1-S9 initiatives | MOVE-TO-CURRENT |
| `S1_OBSERVABILITY.md` | COMPLETE | MOVE-TO-CURRENT |
| `S2_BILL_DISAPPEARANCE.md` | ~COMPLETE | MOVE-TO-CURRENT |
| `S3_PIPELINE_CHAINS.md` | PLANNING | MOVE-TO-CURRENT |
| `S4_BILL_ATTRIBUTION.md` | COMPLETE | MOVE-TO-CURRENT |
| `S5_FUNCTIONAL_AUDIT.md` | COMPLETE | MOVE-TO-CURRENT |
| `S6_AUTOMATED_TESTING.md` | Phase 1 done | MOVE-TO-CURRENT + update |
| `S7_CLOUDFORMATION.md` | Inventory in progress | MOVE-TO-CURRENT |
| `S7_RESOURCE_INVENTORY.md` | Reference | MOVE-TO-CURRENT |
| `S8_VENDOR_CACHE_REWRITE.md` | Planning | MOVE-TO-CURRENT |
| `S9_AWS_HEALTH_AUDIT.md` | COMPLETE | MOVE-TO-CURRENT |

---

## Duplication / Overlap Identified

### UBI / Billback architecture (6 overlapping docs)
- `BILLBACK_ARCHITECTURE_ANALYSIS.md` (16K, Nov 2025)
- `BILLBACK_CHARGE_CODE_ANALYSIS.md` (15K, Nov 2025)
- `BILLBACK_UPDATE_PLAN.md` (6K, Nov 2025)
- `BILLBACK_INTEGRATION_COMPLETE.md` (9K, Nov 2025)
- `UBI_BILLBACK_COMPLETE_ARCHITECTURE.md` (44K, Nov 2025) ← most comprehensive
- `UBI_API_REFERENCE.md` (13K, Nov 2025)

**Action:** Consolidate into single `ARCHITECTURE_UBI_BILLBACK.md` in `docs/current/`, archive originals.

### Code audits (3 overlapping)
- `CODEBASE_ANALYSIS_REPORT.md` (16K, Nov 2025) — 50 issues
- `CODE_AUDIT_2026_04.md` (28K, Apr 9) — 134+ issues
- `CODE_AUDIT_2026_04_14.md` (11K, Apr 14) — critical findings

**Action:** Keep `CODE_AUDIT_2026_04_14.md` as current, archive others. Create `AUDIT_TRACKING.md` to monitor fix status.

### Implementation progress (3 overlapping)
- `SESSION_SUMMARY.md`
- `IMPLEMENTATION_PROGRESS.md`
- `WORK_COMPLETED_SUMMARY.md`

**Action:** Archive all three; use git history for ongoing tracking.

---

## Gaps — Docs That Should Exist But Don't

| Topic | Priority | Proposed Doc |
|---|---|---|
| Developer quickstart (15-min setup) | P0 | `docs/current/QUICKSTART.md` |
| Module taxonomy (how 349 endpoints map to modules) | P1 | Covered by `03_module_taxonomy.md` |
| Endpoint inventory with auth/tables/logs | P1 | Covered by `02_endpoint_inventory.md` |
| Data models / ER diagram | P2 | `docs/current/DATA_MODELS.md` (covered partially by `04_data_architecture.md`) |
| Lambda function reference | P2 | `docs/current_state/modules/lambdas.md` (Phase 2) |
| Troubleshooting guide | P2 | `docs/current/TROUBLESHOOTING.md` |
| Performance optimization guide | P3 | `PERFORMANCE_ARCHITECTURE.md` needs update |
| Security checklist (auth per endpoint, IAM roles, secrets) | P3 | `docs/current/SECURITY.md` |

---

## Proposed Final `docs/` Structure

```
docs/
├── current/                          ← canonical, trusted
│   ├── README.md                     (updated from root OVERVIEW.md + README.md merge)
│   ├── QUICKSTART.md                 (NEW — 15-min dev setup)
│   ├── ARCHITECTURE_UBI_BILLBACK.md  (merged from 6 UBI/billback docs)
│   ├── ARCHITECTURE_BIG_BILL.md      (from BIG_BILL_DEPLOYMENT.md)
│   ├── ARCHITECTURE_AI_PARSING.md    (from AI_NATIVE_PLATFORM_PLAN.md)
│   ├── DATA_MODELS.md                (expand STORAGE_ARCHITECTURE.md)
│   ├── API_REFERENCE.md              (from UBI_API_REFERENCE.md, expand)
│   ├── INFRASTRUCTURE.md             (INFRASTRUCTURE_RECOMMENDATIONS.md updated)
│   ├── SNOWFLAKE_SETUP.md            (from SNOWFLAKE_SETUP_GUIDE.md)
│   ├── VE_INTEGRATION.md             (from BILL_REVIEW_INTEGRATION.md)
│   ├── HEALTH_STATUS.md              (track audit findings; updated weekly)
│   ├── CODE_AUDIT_CURRENT.md         (from CODE_AUDIT_2026_04_14.md)
│   ├── TROUBLESHOOTING.md            (NEW)
│   ├── SECURITY.md                   (NEW)
│   ├── planning/
│   │   ├── AUTONOMOUS_PIPELINE.md
│   │   ├── PARSER_ACCURACY.md
│   │   ├── PERFORMANCE.md
│   │   └── WORKFLOW_ENHANCEMENTS.md  (decide if abandoned)
│   └── strategic_initiatives/
│       ├── README.md, S1.md ... S9.md
│
├── current_state/                    ← THIS REVIEW's output
│   ├── INDEX.md
│   ├── 00_overview.md
│   ├── 01_file_catalog.md
│   ├── 02_endpoint_inventory.md
│   ├── 03_module_taxonomy.md
│   ├── 04_data_architecture.md
│   ├── 10_doc_audit.md               (this file)
│   ├── ISSUES.md
│   └── modules/                      ← populated in Phase 2
│       ├── README.md
│       ├── 01_auth.md
│       └── ...
│
├── archive/                          ← historical record, read-only
│   ├── 2025-01/
│   │   ├── CHANGES_2025-01-18.md
│   │   └── CODEBASE_REVIEW_FINDINGS.md (from docs/)
│   ├── 2025-11/
│   │   ├── AUTHENTICATION_SETUP.md
│   │   ├── DEPLOYMENT_RECORD.md
│   │   ├── CHANGELOG_2025-11-22.txt
│   │   ├── SESSION_SUMMARY.md
│   │   ├── IMPLEMENTATION_PROGRESS.md
│   │   ├── IMPLEMENTATION_GAP_ANALYSIS.md
│   │   ├── BILLBACK_INTEGRATION_COMPLETE.md
│   │   ├── BILLBACK_UPDATE_PLAN.md
│   │   ├── BILLBACK_ARCHITECTURE_ANALYSIS.md
│   │   ├── BILLBACK_CHARGE_CODE_ANALYSIS.md
│   │   └── UBI_BILLBACK_COMPLETE_ARCHITECTURE.md  (after content merged)
│   ├── 2026-01/
│   │   └── FLAG_FOR_REVIEW_BUG_REPORT.md
│   └── 2026-04/
│       ├── CODE_AUDIT_2026_04.md   (older Apr version)
│       └── UBI_CACHE_FIX_SESSION_NOTES.md
│
└── superseded/                        ← replaced but kept for ref
    ├── CODEBASE_ANALYSIS_REPORT.md   (→ current/CODE_AUDIT_CURRENT.md)
    ├── WORK_COMPLETED_SUMMARY.md      (→ distributed)
    └── WORKFLOW_ENHANCEMENTS_DESIGN.md (→ planning/ or abandoned)
```

---

## Execution Plan (Phase 1: Doc Reorg)

After user approval:

### Step 1: Create directory structure
```bash
mkdir -p docs/current/{planning,strategic_initiatives}
mkdir -p docs/archive/{2025-01,2025-11,2026-01,2026-04}
# docs/superseded/ already created
```

### Step 2: Move to archive
Files to move to `docs/archive/2025-11/`:
- AUTHENTICATION_SETUP.md, DEPLOYMENT_RECORD.md, CHANGELOG_2025-11-22.txt
- SESSION_SUMMARY.md, IMPLEMENTATION_PROGRESS.md, IMPLEMENTATION_GAP_ANALYSIS.md
- BILLBACK_INTEGRATION_COMPLETE.md, BILLBACK_UPDATE_PLAN.md
- BILLBACK_ARCHITECTURE_ANALYSIS.md, BILLBACK_CHARGE_CODE_ANALYSIS.md

Files to move to `docs/archive/2025-01/`:
- CHANGES_2025-01-18.md
- (from docs/) CODEBASE_REVIEW_FINDINGS.md

Files to move to `docs/archive/2026-01/`:
- FLAG_FOR_REVIEW_BUG_REPORT.md (from docs/)

Files to move to `docs/archive/2026-04/`:
- CODE_AUDIT_2026_04.md (older), UBI_CACHE_FIX_SESSION_NOTES.md

### Step 3: Move to superseded
- CODEBASE_ANALYSIS_REPORT.md
- WORK_COMPLETED_SUMMARY.md
- WORKFLOW_ENHANCEMENTS_DESIGN.md

### Step 4: Move to docs/current
- BILL_REVIEW_INTEGRATION.md → docs/current/VE_INTEGRATION.md
- INFRASTRUCTURE_RECOMMENDATIONS.md → docs/current/INFRASTRUCTURE.md
- SNOWFLAKE_SETUP_GUIDE.md → docs/current/SNOWFLAKE_SETUP.md
- UBI_API_REFERENCE.md → docs/current/API_REFERENCE.md
- UBI_BILLBACK_COMPLETE_ARCHITECTURE.md → docs/current/ARCHITECTURE_UBI_BILLBACK.md
- (from docs/) AI_NATIVE_PLATFORM_PLAN.md → docs/current/ARCHITECTURE_AI_PARSING.md
- (from docs/) BIG_BILL_DEPLOYMENT.md → docs/current/ARCHITECTURE_BIG_BILL.md
- (from docs/) CODE_AUDIT_2026_04_14.md → docs/current/CODE_AUDIT_CURRENT.md
- (from docs/) HEALTH_AUDIT_2026_04_10.md → docs/current/HEALTH_STATUS.md
- (from docs/) IMPROVE_BUTTON_TEMPLATE.md → docs/current/IMPROVE_FEATURE.md
- (from docs/) MASTER_BILLS_DATA_QUALITY.md → docs/current/DATA_QUALITY_MASTER_BILLS.md
- (from docs/) AUTONOMOUS_PIPELINE_PLAN.md → docs/current/planning/AUTONOMOUS_PIPELINE.md
- (from docs/) PARSER_ACCURACY_IMPROVEMENT_PLAN.md → docs/current/planning/PARSER_ACCURACY.md
- (from docs/) PERFORMANCE_ARCHITECTURE.md → docs/current/planning/PERFORMANCE.md
- (from docs/strategic_initiatives/) all files → docs/current/strategic_initiatives/

### Step 5: Keep at root
- CLAUDE.md (do not move)
- README.md (to be updated in place — expand with recent features)

### Step 6: Delete with user confirmation
- OVERVIEW.md (superseded by `current_state/00_overview.md`)
- STORAGE_ARCHITECTURE.md (merged into `04_data_architecture.md`)
- BILLBACK_ARCHITECTURE_ANALYSIS.md, BILLBACK_CHARGE_CODE_ANALYSIS.md (after merge into ARCHITECTURE_UBI_BILLBACK.md)

### Step 7: Create new docs
- `docs/current/QUICKSTART.md` — dev onboarding
- `docs/current/TROUBLESHOOTING.md` — common errors
- `docs/current/SECURITY.md` — auth/IAM reference

### Step 8: Update README.md
Expand to reference `docs/current/` and `docs/current_state/`. Note state: review in progress.

---

## Estimated Effort

| Task | Effort |
|---|---|
| Directory setup + moves (Step 1-5) | 30 min |
| Content merges (UBI docs → ARCHITECTURE_UBI_BILLBACK) | 2-3 hours |
| New docs (QUICKSTART, TROUBLESHOOTING, SECURITY) | 3-4 hours |
| README.md refresh | 1 hour |
| Total Phase 1 | ~1 day |

---

## Awaiting user approval before executing Step 1-8

**Key question for user:**
1. Approve the proposed structure above? Any changes?
2. Execute moves now (this session), or after Phase 2 (module reviews) since those may further inform doc needs?
3. Which new docs should be priority: QUICKSTART, TROUBLESHOOTING, SECURITY — or defer?
4. Keep OVERVIEW.md and STORAGE_ARCHITECTURE.md or delete after merging?
