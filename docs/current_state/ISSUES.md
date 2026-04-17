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

#### [ISSUE-007] Session duration too long
- **Severity:** P2
- **Scope:** SECURITY
- **Status:** **fixed** 2026-04-16 (main.py:106 now `24 * 3600`)
- **Location:** main.py:106
- **Fix landed:** Reduced from `7 * 24 * 3600` to `24 * 3600` — rolling 24h from login

#### [ISSUE-008] No 2FA for financial application
- **Severity:** ~~P1~~ → closed
- **Scope:** SECURITY
- **Status:** **wontfix** (user decided 2026-04-16 — MFA not needed; if SSO IdP enforces MFA that's fine)
- **Problem:** AP users can post invoices (move money) with single-factor auth

#### [ISSUE-009] No self-service forgot-password
- **Severity:** P2
- **Scope:** JTBD
- **Status:** **deferred-to-sso** (user decided 2026-04-16)
- **User job affected:** "I forgot my password on a weekend" — must wait for admin
- **Resolution:** Will be handled by SSO IdP post-migration; no interim fix

#### [ISSUE-010] Auth bypass env-var backdoor
- **Severity:** P1
- **Scope:** SECURITY
- **Status:** **fixed** 2026-04-16 (bypass block deleted from main.py; apprunner env var removed; tests removed)
- **Location:** main.py (was 1570-1581), apprunner_config.json, tests/unit/test_security.py
- **Problem:** `DISABLE_AUTH=1` + `DISABLE_AUTH_SECRET="I-UNDERSTAND-THIS-IS-INSECURE"` bypassed all auth. Secret was in source code.
- **Fix landed:** Removed all three. Break-glass replacement will come with SSO migration.

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
- **Status:** **fixed** 2026-04-16 (main.py:3400 now uses `auth.ROLES.keys()`)
- **Location:** main.py:3400
- **Fix landed:** `valid_roles = set(auth.ROLES.keys())` — single source of truth

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
- **Status:** **confirmed — build immutable audit log** (user decided 2026-04-16, no compliance obligation but design principle)
- **Problem:** Only `print()` to CloudWatch logs; no immutable audit trail
- **Fix plan:** New DDB table `jrk-bill-audit-log` (append-only). Fields: timestamp / admin_user / action / target_user / details. Written from every admin endpoint.
- **Blocked on:** Infra approval (CLAUDE.md rule — DDB table create needs explicit go)
- **Follow-up open:** Broader system audit (e.g., "user X posted invoice Y") tabled for synthesis phase.

#### [ISSUE-018] Pages have no role-based access control
- **Severity:** P1
- **Scope:** JTBD / INTEGRATION
- **Status:** flagged
- **User job affected:** "Restrict UBI team from seeing payroll-adjacent data" — currently all authenticated users see all pages
- **Problem:** `can_access_page()` exists but nothing calls it; pages are not role-gated
- **Proposed fix:** Part of SSO migration (see `project_sso_migration.md`). Build role-to-capability registry; wire via middleware.

#### [ISSUE-019] 5 HR_Admins users share identical password_hash
- **Severity:** P1
- **Scope:** SECURITY
- **Status:** flagged (action TABLED pending coordination)
- **Module:** 01_auth
- **Users affected:** vnavarrete@jrk.com, msalazar@jrk.com, alemoine@jrk.com (has logged in once), jburtch@jrk.com, drico@jrk.com
- **Problem:** All 5 provisioned 2026-03-05 with same default temp password. None have changed. Same hash means whoever knows that password can log in as any of the 5.
- **Why tabled:** HR moved to employeereportingservices.com. Need to check how THAT app handles auth before disabling/deleting these accounts (could break a working flow). User instruction 2026-04-16.
- **Tabled items:** TODO-AUTH-001..004 in `modules/01_auth.md` (contact other team, audit dependency, then fix)

### 🕰 Tabled / Pending Coordination

- **TODO-AUTH-001..004** (HR_Admins) — coordinate with employeereportingservices.com team before removing accounts/role. See ISSUE-019 + modules/01_auth.md Q-3.
- **TODO-AUTH-005** (SSO scoping) — per-module inventory of SSO touchpoints (METRICS, PARSE per-user attribution, etc.) must be complete before SSO migration lands. Each module review must include a "SSO migration concerns" section.
- **TODO-AUTH-006** (Service accounts before SSO) — design + build service-account auth (Option 2 from Q-9) BEFORE SSO migration. First customer: `tests/smoke_test_production.py`.

### Module 2 — Parse & Input (reviewed 2026-04-16, awaiting user confirmation)

#### [ISSUE-020] Uppercase .PDF handling has 3 overlapping fixes + 10-min lag
- **Severity:** P2
- **Scope:** JTBD / INTEGRATION
- **Status:** **attempted & reverted** 2026-04-16 (needs retry with better plan)
- **Location:** main.py:1239-1274 (auto-loop), main.py:3881-3883 (upload normalize), main.py:3896-3924 (admin button)
- **Problem:** S3 event filter case-sensitive; 3 code paths mitigate; up to 10 min lag between upload and pipeline resume for non-web-upload sources
- **Attempt 1 (2026-04-16):**
  1. Added S3 event rule `BillRouterTrigger_UppercasePDF` (Suffix=.PDF, same Lambda). ✓
  2. Attempted to update router Lambda to normalize extension on copy. ✗
  3. Zip packaging broke the deployment — `No module named 'botocore.vendored'` import error. Production router was DOWN for ~3 minutes (22:14:22–22:17:17 UTC).
  4. Reverted router to original deployment zip. Production restored.
  5. 9 production PDFs stuck during outage window — recovered via direct Lambda invocation with synthetic S3 events.
  6. Reverted the S3 event rule addition to return to pre-session state. Rationale: the S3 rule alone (without router Lambda extension-normalization) risks `.PDF` files getting stuck in Standard/LargeFile (downstream triggers don't match `.PDF`).
- **Lessons for retry:**
  - Zip must preserve boto3's vendored structure. My `os.walk + zipfile.ZIP_DEFLATED` may have mangled pkg layout. Investigate before retry.
  - Test in a staging Lambda first, not production.
  - Simpler approach: add 4 twin `_UppercasePDF` rules for the 4 `.pdf`-filtered triggers (router, standard-parser, large-parser, chunk-processor, rework) — no Lambda code change needed. But adds rule sprawl.
  - Even simpler: change all 4 rules to NO suffix filter, and have each Lambda validate extension at entry. More Lambda invocations but no case-sensitivity issue.
- **Proposed retry plan:** Do the Lambda deploy in a staging environment first OR take the "no suffix filter + validate in code" approach.

#### [ISSUE-021] Router intermediate stages undocumented
- **Severity:** P2
- **Scope:** DATA / DRIFT
- **Problem:** `Bill_Parser_1_Standard/`, `Bill_Parser_1_LargeFile/`, `Bill_Parser_3_Parsed_Outputs/` exist as routing destinations but not in CLAUDE.md or 04_data_architecture.md
- **Location:** `aws_lambdas/us-east-1/jrk-bill-router/code/lambda_bill_router.py:44-46`, parser Lambda:56
- **Fix:** Document or confirm dead + delete

#### [ISSUE-022] `/parse` renders `index.html` not `parse.html`
- **Severity:** P4
- **Scope:** UX / TECH-DEBT
- **Location:** main.py:3487

#### [ISSUE-023] No user attribution on uploaded bills
- **Severity:** P1
- **Scope:** JTBD / INTEGRATION
- **Status:** **planned-fix** (user approved 2026-04-16 — build now, coordinate with SSO)
- **Location:** main.py:3867-3893 (api_upload_input), api_scraper_import, email-ingest Lambda, router Lambda, parser Lambda, enricher Lambda
- **Problem:** Uploads don't record who uploaded. Breaks SSO scoping goal "know who did what"; blocks per-user metrics; blocks audit log.
- **Fix:** S3 object metadata `uploader={email}` + `source={web|scraper|email}`. Propagate through router/parser/enricher chain. See modules/02_parse.md Q-13 for 8-point plan.
- **Prereq for:** Audit log (ISSUE-017), per-user metrics (METRICS module), SSO identity migration

#### [ISSUE-024] Search 500-result cap silent; no pagination
- **Severity:** P2
- **Scope:** UX
- **Status:** **planned-fix** (user decided 2026-04-16 — cursor pagination + infinite scroll + sort param)
- **Location:** main.py:3803, 3827, `templates/search.html`
- **Fix:** Cursor-based pagination (`{last_date, last_pdf_id}`), IntersectionObserver for scroll-to-load, server-side sort param. ~4-6 hours.

#### [ISSUE-025] Search is naive substring match
- **Severity:** P3
- **Scope:** UX
- **Status:** **planned-fix** (user decided 2026-04-16 — make more robust; bundle with ISSUE-024 search overhaul)
- **Location:** main.py:3812-3817
- **Fix:** Add `_normalize_for_search` helper (strip non-alphanumeric, lowercase, handle leading zeros on account numbers). Store normalized `_n` fields at index time. ~2 hours bundled with pagination work.

#### [ISSUE-026] Scraper CSV fallback silently serves stale data
- **Severity:** P3
- **Scope:** TECH-DEBT / DATA
- **Status:** **planned-fix** (user decided Option 1 2026-04-16 — hard-fail with banner)
- **Location:** main.py:3978-4004 + two CSVs in source tree
- **Fix:** Delete CSVs + fallback code. Return 503 + error banner on UI when scraper API down. ~1 hour.

#### [ISSUE-027] Pipeline tracker errors silently swallowed
- **Severity:** P2
- **Scope:** OBSERVABILITY
- **Location:** router Lambda:39, parser Lambda:49, email-ingest Lambda:35
- **Fix:** Emit CloudWatch metric on tracker write failure

#### [ISSUE-028] Router doesn't validate PDF integrity
- **Severity:** P2
- **Scope:** DATA
- **Location:** router Lambda:54-62, 129-131
- **Problem:** Corrupt PDFs routed to standard parser, fail downstream; could reject at router

#### [ISSUE-029] `list_dates()` scans entire enrich prefix
- **Severity:** P3
- **Scope:** PERF
- **Location:** main.py:1606-1631
- **Problem:** Linear with data growth; first request after cache expiry slow

#### [ISSUE-030] `load_day()` fires 50 concurrent S3 GETs
- **Severity:** P3
- **Scope:** PERF
- **Location:** main.py:1690

#### [ISSUE-031] Search rebuild without S3 cache is hours
- **Severity:** P2
- **Scope:** PERF / STARTUP
- **Location:** main.py:3375-3395 (`_search_index_backfill`)

#### [ISSUE-032] Failed parses hidden from /parse dashboard
- **Severity:** P1
- **Scope:** JTBD / OBSERVABILITY
- **Status:** **planned-fix** (user decided Option 1 2026-04-16)
- **Problem:** Failed bills disappear silently; user has to know about /failed module
- **Fix:** Add FAILED as 4th column on /parse day cards. Extend `day_status_counts()` to count Failed_Jobs; update template; add retry action.

#### [ISSUE-033] Rework gap: search invisibility between rework and next rebuild
- **Severity:** P2
- **Scope:** DATA INTEGRITY
- **Location:** main.py:3638-3656 (`_search_index_remove`)
- **Fix:** Scheduled incremental rebuild loop (not just at startup)

#### [ISSUE-034] Scraper "unlinked" sentinel is fragile
- **Severity:** P4
- **Scope:** TECH-DEBT
- **Location:** main.py:4006

#### [ISSUE-035] Scraper import is sequential; AppRunner timeout risk
- **Severity:** P2
- **Scope:** PERF / UX
- **Location:** main.py /api/scraper/import

#### [ISSUE-036] `jrk-presigned-upload` Lambda exists but unused
- **Severity:** P3 → upgraded to P2 (now load-bearing for large-file upload path)
- **Scope:** TECH-DEBT → UX / JTBD
- **Status:** **planned-fix** (user decided 2026-04-16 — wire into /input)
- **Location:** aws_lambdas/us-east-1/jrk-presigned-upload/ + `templates/input.html`
- **Fix:** Browser PUTs directly to S3 via presigned URL (metadata for Q-13 attribution); poll via job-queue (Q-14). Removes the ~30MB upload ceiling.

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
- **[PRE-11] Billback: comments added don't show up** — user-reported 2026-04-16. Adding a comment in BILLBACK module does not surface the comment after save/refresh. Flag for Module 6 review.
- **[PRE-12] Billback: "Add to Tracker" appears to work but reverts on refresh** — user-reported 2026-04-16. Click succeeds in UI, but on page refresh the account is no longer in tracker. Classic "save endpoint returned 200 but state didn't persist" or "client state out of sync with server state". Flag for Module 6 / Module 9 review.
- **[PRE-13] Lambda code in aws_lambdas/*/code/ is behind the deployed version** — found during Module 2 router fix 2026-04-16. Deployed router has `_pipeline_track` helper + sidecar file copy logic that isn't in the repo copy. Suggests direct `aws lambda update-function-code` was used without git commit. Drift risk: future refactors based on repo code would overwrite production-only changes. Need policy: all Lambda changes go through git.
