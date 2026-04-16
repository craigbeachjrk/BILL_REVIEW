# Documentation Structure

Docs are organized into four top-level directories. **Only `docs/current/` and `docs/current_state/` should be trusted as describing current system behavior.**

## Layout

```
docs/
├── current/                     ← canonical, trusted, actively maintained
│   ├── README.md
│   ├── VE_INTEGRATION.md        (Vacant Electric mount guide)
│   ├── INFRASTRUCTURE.md        (DDB, Lambda, AppRunner config)
│   ├── SNOWFLAKE_SETUP.md
│   ├── API_REFERENCE.md         (UBI endpoints)
│   ├── ARCHITECTURE_UBI_BILLBACK.md
│   ├── ARCHITECTURE_BIG_BILL.md
│   ├── ARCHITECTURE_AI_PARSING.md
│   ├── CODE_AUDIT_CURRENT.md
│   ├── HEALTH_STATUS.md
│   ├── IMPROVE_FEATURE.md
│   ├── DATA_QUALITY_MASTER_BILLS.md
│   ├── planning/
│   │   ├── AUTONOMOUS_PIPELINE.md
│   │   ├── PARSER_ACCURACY.md
│   │   └── PERFORMANCE.md
│   └── strategic_initiatives/
│       ├── README.md
│       ├── S1_OBSERVABILITY.md .. S9_AWS_HEALTH_AUDIT.md
│
├── current_state/               ← CURRENT REVIEW EFFORT (2026-04-16)
│   ├── INDEX.md                 (master plan + review conventions)
│   ├── 00_overview.md           (multi-audience system overview)
│   ├── 01_file_catalog.md       (every file with category/status)
│   ├── 02_endpoint_inventory.md (all 349 FastAPI routes)
│   ├── 03_module_taxonomy.md    (main.py's 14 logical modules)
│   ├── 04_data_architecture.md  (15 DDB tables, 15+ S3 prefixes, flow)
│   ├── 10_doc_audit.md          (how these docs got classified)
│   ├── ISSUES.md                (rolling issue log)
│   └── modules/                 (per-module deep dives — populated in Phase 2)
│
├── archive/                      ← historical, read-only, kept for reference
│   ├── 2025-01/                 (Jan 2025 notes)
│   ├── 2025-11/                 (Nov 2025 implementation wave)
│   ├── 2026-01/                 (Jan 2026 bug analyses)
│   └── 2026-04/                 (superseded Apr audits, session notes)
│
└── superseded/                   ← replaced by newer docs but kept for ref
    ├── CODEBASE_ANALYSIS_REPORT.md  (→ current/CODE_AUDIT_CURRENT.md)
    ├── OVERVIEW.md                   (→ current_state/00_overview.md)
    ├── STORAGE_ARCHITECTURE.md       (→ current_state/04_data_architecture.md)
    ├── WORKFLOW_ENHANCEMENTS_DESIGN.md
    └── WORK_COMPLETED_SUMMARY.md
```

## Where to start

- **New to the codebase?** → `current_state/00_overview.md`
- **Looking for a specific endpoint?** → `current_state/02_endpoint_inventory.md`
- **Understanding a module?** → `current_state/03_module_taxonomy.md`, then `current_state/modules/NN_xxx.md` once Phase 2 is underway
- **What's broken?** → `current_state/ISSUES.md` + `current/HEALTH_STATUS.md`
- **Canonical UBI/billback architecture?** → `current/ARCHITECTURE_UBI_BILLBACK.md`
- **Canonical API reference?** → `current/API_REFERENCE.md`

## What lives at the repo root (not in docs/)

- `CLAUDE.md` — Claude Code project instructions (load-bearing; do not move)
- `README.md` — High-level repo readme (needs refresh)

## Doc reorganization executed 2026-04-16

Part of the Current State Review effort (see `current_state/INDEX.md`). **Content merges are still pending** — specifically, 6 UBI/billback docs in `archive/2025-11/` should be consolidated into `current/ARCHITECTURE_UBI_BILLBACK.md`. Until that happens, `archive/2025-11/` remains load-bearing for some technical details.

## Pending (not yet done)

- Create `current/QUICKSTART.md` (dev onboarding)
- Create `current/TROUBLESHOOTING.md`
- Create `current/SECURITY.md`
- Refresh root-level `README.md` with the new structure
- Merge content of archive/2025-11/BILLBACK_*.md into current/ARCHITECTURE_UBI_BILLBACK.md
