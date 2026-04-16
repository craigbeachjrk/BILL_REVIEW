# Issue Log — Current State Review

**Purpose:** Accumulate issues discovered during the line-by-line code review. Each issue has a stable ID and is cross-referenced from the module docs where it was found.

**Process:** During review I flag suspected issues inline in the module docs. User confirms/rejects/adds context. Confirmed issues are copied here with a status field.

## ⚠️ Top-Level Framing (user input 2026-04-16)

> "there's just a great deal of clunkiness in getting the tool to actually get people's jobs done. it doesn't do a single thing well or complete."

The primary issue class is **systemic workflow incompleteness** — the app has many features but jobs-to-be-done don't flow end-to-end. Individual bugs matter, but **architectural/workflow issues rank higher** in severity.

Issues in this log are tagged with a **Scope** field:
- `JTBD` — Job-to-be-done incomplete, clunky, or requires manual workaround
- `INTEGRATION` — Data/flow breaks between modules
- `BUG` — Incorrect behavior in isolation
- `PERF` — Timeouts, slowness, resource waste
- `SECURITY` — Auth, secrets, injection risks
- `DATA` — Data quality, integrity, corruption
- `UX` — User interface friction not otherwise classified
- `TECH-DEBT` — Code quality, maintainability
- `DEAD` — Dead code, unused features

## Status Values
- `flagged` — Found during review, not yet confirmed by user
- `confirmed` — User has agreed this is a real issue
- `rejected` — User has explained why it's intentional or a non-issue
- `in-progress` — Actively being fixed
- `fixed` — Resolved (link to PR/commit)
- `wontfix` — Known issue, accepted

## Severity
- `P0` — Data loss, security, production-down
- `P1` — Significant correctness problem users are hitting
- `P2` — Correctness problem, intermittent or edge case
- `P3` — Code quality, perf, tech debt without user-visible impact
- `P4` — Nit (style, naming, unused code)

## Issues

### Module 1 — Auth (reviewed 2026-04-16, awaiting user confirmation)

#### [ISSUE-001] Two admin systems that don't align
- **Severity:** P1
- **Scope:** INTEGRATION / JTBD
- **Status:** flagged
- **Module:** 01_auth
- **Location:** main.py:177 (hardcoded `ADMIN_USERS` set) vs. auth.py:16 (`ROLES["System_Admins"]`)
- **User job affected:** Grant admin access — must update BOTH systems to make a new admin whole
- **Problem:** `require_admin()` uses hardcoded set; `/api/users/*` endpoints use role check. A System_Admins role user can manage users but not perform other admin ops.
- **Proposed fix:** Unify — make role the source of truth; replace hardcoded set with role check; add single `require_system_admin` dependency used everywhere

#### [ISSUE-002] auth.py permission functions are dead code
- **Severity:** P2
- **Scope:** DEAD
- **Status:** flagged
- **Module:** 01_auth
- **Location:** auth.py:186-204 (`has_permission`), auth.py:207-218 (`can_access_page`)
- **Problem:** Defined but never called from main.py or elsewhere. Access control reduces to "authenticated or not" + "in hardcoded ADMIN_USERS set or not".
- **Proposed fix:** Either wire them up (see ISSUE-018) or delete

#### [ISSUE-003] Admin check duplicated in /api/users endpoints
- **Severity:** P3
- **Scope:** TECH-DEBT
- **Status:** flagged
- **Module:** 01_auth
- **Location:** main.py:3289, 3300, 3332, 3345, 3358, 3390
- **Problem:** Same 3-line admin check repeated 6x; new endpoint may forget
- **Proposed fix:** Create `require_system_admin` FastAPI dependency

#### [ISSUE-004] No password complexity beyond length-8
- **Severity:** P2
- **Scope:** SECURITY
- **Status:** flagged
- **Location:** main.py:3239, 3319, 3367

#### [ISSUE-005] No password history
- **Severity:** P3
- **Scope:** SECURITY
- **Status:** flagged
- **Location:** auth.py:126 (`update_password`)

#### [ISSUE-006] No failed-login lockout
- **Severity:** P2
- **Scope:** SECURITY
- **Status:** flagged
- **Location:** auth.py:155 (`authenticate`)
- **Problem:** Brute-force risk; unlimited password attempts

#### [ISSUE-007] No idle session timeout
- **Severity:** P2
- **Scope:** SECURITY
- **Status:** flagged
- **Location:** main.py:106 (`SESSION_MAX_AGE_SECONDS = 7 * 24 * 3600`)
- **Problem:** 7-day absolute; no idle revocation

#### [ISSUE-008] No 2FA for financial application
- **Severity:** P1
- **Scope:** SECURITY
- **Status:** flagged
- **Problem:** AP users can post invoices (move money) with single-factor auth

#### [ISSUE-009] No self-service forgot-password
- **Severity:** P2
- **Scope:** JTBD
- **Status:** flagged
- **User job affected:** "I forgot my password on a weekend" — must wait for admin

#### [ISSUE-010] Auth bypass env-var backdoor
- **Severity:** P1
- **Scope:** SECURITY
- **Status:** flagged
- **Location:** main.py:1570-1581
- **Problem:** `DISABLE_AUTH=1` + `DISABLE_AUTH_SECRET="I-UNDERSTAND-THIS-IS-INSECURE"` bypasses all auth. Secret is in source code.
- **Proposed fix:** Remove; replace with IAM-level credential rotation for break-glass

#### [ISSUE-011] change-password updates last_login_utc
- **Severity:** P4
- **Scope:** BUG
- **Status:** flagged
- **Location:** main.py:3247 → auth.py:171
- **Problem:** Current-password verification calls `authenticate()` which has side effect

#### [ISSUE-012] HR_Admins role is dead
- **Severity:** P4
- **Scope:** DEAD
- **Status:** flagged
- **Location:** auth.py:41-47

#### [ISSUE-013] /api/users/{id}/role only accepts 3 of 4 roles
- **Severity:** P3
- **Scope:** BUG
- **Status:** flagged
- **Location:** main.py:3400
- **Proposed fix:** Read from `auth.ROLES` keys as source of truth

#### [ISSUE-014] list_users uses DDB Scan by default
- **Severity:** P3
- **Scope:** PERF
- **Status:** flagged
- **Location:** auth.py:234

#### [ISSUE-015] Login events written to jrk-bill-drafts table
- **Severity:** P3
- **Scope:** TECH-DEBT
- **Status:** flagged
- **Location:** main.py:3189-3197
- **Problem:** Login records co-located with invoice drafts; wrong table

#### [ISSUE-016] No CSRF protection on state-changing POSTs
- **Severity:** P2
- **Scope:** SECURITY
- **Status:** flagged
- **Example:** POST /logout, POST /api/users/*
- **Note:** Broader concern to evaluate across all POST endpoints in modules 2-14

#### [ISSUE-017] No audit log of admin actions
- **Severity:** P2
- **Scope:** COMPLIANCE / SECURITY
- **Status:** flagged
- **Problem:** Only `print()` to CloudWatch logs; no immutable audit trail

#### [ISSUE-018] Pages have no role-based access control
- **Severity:** P1
- **Scope:** JTBD / INTEGRATION
- **Status:** flagged
- **User job affected:** "Restrict UBI team from seeing payroll-adjacent data" — currently all authenticated users see all pages
- **Problem:** `can_access_page()` exists but nothing calls it; pages are not role-gated
- **Proposed fix:** Middleware that maps request path → role check via `auth.can_access_page(user_role, request.url.path)`

Template for new entries:

```
### [ISSUE-001] Short title
- **Severity:** P1
- **Scope:** JTBD | INTEGRATION | BUG | PERF | SECURITY | DATA | UX | TECH-DEBT | DEAD
- **Status:** flagged
- **Module:** 05_ubi
- **Location:** main.py:5432-5500
- **Found during:** Phase 2 review of UBI module
- **User job affected:** (what the user is trying to accomplish that this blocks)
- **Problem:** (what's wrong)
- **Evidence:** (code excerpt, reproduction)
- **Conflict with docs:** (if applicable — e.g. "UBI_API_REFERENCE.md says X but code does Y")
- **Suspected blast radius:** (who/what is affected)
- **Proposed fix:** (if any)
- **User response:** (filled in when user reviews)
```

## Pre-Review Known Issues

These are issues already recorded in existing docs that should be verified/updated during review (not new discoveries):

- **[PRE-01] Hardcoded API keys** — `CODE_AUDIT_2026_04_14.md` flagged; commit `9071511` claims fix. Verify during review.
- **[PRE-02] 5 endpoints return 500s in production** — per `HEALTH_AUDIT_2026_04_10.md` (IAM gaps for `jrk-bill-ai-suggestions`, `jrk-bill-billback-master` tables; missing `import pytz`). Status unknown — check during review.
- **[PRE-03] 8 endpoints time out** — per `HEALTH_AUDIT_2026_04_10.md`. Should be addressed via caching per memory guidance.
- **[PRE-04] Master bills data quality** — `MASTER_BILLS_DATA_QUALITY.md`: 5 charge code/utility type mismatches in 756 bills (ENVF vs ENVFE, GASIN on water, etc.).
- **[PRE-05] BILLBACK multi-period suggestion not implemented** — `CLAUDE.md` notes `_calculate_ubi_suggestion()` always returns single period; user must manually change Months field.
- **[PRE-06] 349 endpoints in a single `main.py`** — not an issue per se but a major architectural concern to address in synthesis.
- **[PRE-07] `app.py` at root is dead Streamlit stub** — per file catalog audit. Deletable.
- **[PRE-08] UBI assignment has 3 separate deletion paths with slightly different semantics** — per main.py analysis. Consolidation candidate.
- **[PRE-09] 10+ abandoned iteration scripts in `GEMINI_PDF_PARSER/`** — superseded by production Lambdas. Safe to archive or delete.
- **[PRE-10] S3 migration flow is not atomic** — each stage transition is copy-then-delete. No rollback on partial failure.
