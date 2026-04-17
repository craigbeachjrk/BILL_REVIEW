# Module 4 — UBI (Utility Bill Imaging / Resident Billback)

**Scope of review:**
- `main.py:4887-7430` — ~2,543 lines of UBI + billback endpoints
- `templates/billback.html` (4,118 lines — the largest template)
- DDB: `jrk-bill-ubi-assignments`, `jrk-bill-ubi-archived`, `jrk-bill-config` (UBI_CACHE metadata), `jrk-bill-billback-master`, `jrk-bill-knowledge-base` (suggestion learning)
- S3: `Bill_Parser_7_PostEntrata_Submission/` (source), `Bill_Parser_8_UBI_Assigned/` (dest), `Bill_Parser_99_Historical Archive/` (end-state), `Bill_Parser_Config/ubi_unassigned_cache.json.gz` (Lambda-built cache)
- Lambda-built: `Bill_Parser_Config/ubi_unassigned_cache.json.gz` (per `feedback_ubi_cache_architecture.md` memory)
- User-reported pain points: **PRE-11** (comments don't show after save), **PRE-12** (Add to Tracker reverts on refresh)

**15 UBI endpoints reviewed.** This is the second-highest-risk module (billback errors create wrong tenant charges).

---

## 1. Module Purpose (Business)

Classify posted utility invoices (Stage 7) by **resident billback period** (UBI = Utility Bill Imaging). Each line item on a utility bill gets assigned to one or more months (`MM/YYYY`) so residents can be billed their proportional share.

Secondary jobs: AI-suggested assignments based on history, bulk account-level unassignment, archiving of completed assignments, exclusion hash caching (bills that shouldn't be billed back — e.g., vacant periods already credited).

## 2. User Personas & Roles

| Persona | What they do |
|---|---|
| **UBI Admin / Resident Billing** | Primary user. Sees unassigned bills, picks period(s), confirms AI suggestion, clicks Assign. Manages account tracker metadata (comments, skip reasons). |
| **AP / Supervisor** | Reviews billback summary per property/period before submitting to Entrata AR. |
| **Admin** | Force-unassigns, archives, cleans up exclusion hashes. |

## 3. End-to-End Workflow Walkthrough

### 3a. Assign a bill to UBI period (happy path)
1. User navigates to `/billback` → renders `billback.html`
2. Client fetches `/api/billback/ubi/unassigned?page=1&sort=amount_desc` (paginated, ~5-min Lambda-built cache)
3. User sees list of unassigned bills grouped by account+vendor
4. For each bill, client auto-fetches suggestion via `/api/billback/ubi/suggestions` (if AI is available)
5. User reviews suggested period (e.g., "Suggested: 09/2025") or edits
6. User clicks "Assign" → POST `/api/billback/ubi/assign` with line_hashes + ubi_periods (comma-separated for multi-period)
7. Server reads S3 JSONL from Stage 7, finds matching line hashes, adds `ubi_assignments` array + notes/amounts
8. Moves matching items: writes to Stage 8 (UBI_Assigned), writes remaining back to Stage 7 if any
9. Records assignment in `jrk-bill-ubi-assignments` DDB table (PK = `line_hash`)
10. Invalidates caches (`_CACHE.pop("ubi_unassigned")`, `_remove_bill_from_ubi_cache(s3_key)`)
11. Client refreshes list; bill moves to "Assigned" tab

### 3b. Unassign a bill
1. User navigates to "Assigned" tab → calls `/api/billback/ubi/assigned`
2. User selects assignments, clicks Unassign → POST `/api/billback/ubi/unassign` with `assignment_ids=s3_key||line_hash,...`
3. Server reads Stage 8 files, splits matching/remaining, moves matching back to Stage 7
4. Pops 7 UBI fields (`ubi_period`, `ubi_assigned_date`, `ubi_assigned_by`, `ubi_amount`, `ubi_months_total`, `ubi_notes`, `ubi_assignments`, `ubi_period_count`) — legacy + new schema both handled
5. Deletes `jrk-bill-ubi-assignments` records — tries new format first (PK=line_hash), then scans for old format (`line_hash[:32]-date`)
6. Cache invalidation

### 3c. Account-level unassign (tracker path)
1. User on UBI Completion Tracker clicks "Unassign account for period X"
2. POST `/api/billback/ubi/unassign-account` with account_number, period, optional s3_key
3. Server scans Stage 8 for matching account+period, unassigns all lines
4. If no s3_key provided, scans entire Stage 8 to find — **expensive**

### 3d. Reassign (move to different period)
1. User wants to change period of an already-assigned bill
2. POST `/api/billback/ubi/reassign` or `/api/billback/ubi/reassign-account`
3. Essentially unassign + assign in one call, but via separate code paths

### 3e. AI suggestion flow
1. User opens a bill without pre-assigned period
2. Client calls `/api/billback/ubi/suggestions?page=1&page_size=50&days_back=90`
3. Server uses `_calculate_ubi_suggestion()` (main.py:8632 per CLAUDE.md) — returns single-period suggestion (bug: doesn't handle multi-period bills — known issue)
4. User can Accept or modify → POST `/api/billback/ubi/accept-suggestion`
5. Server calls the same assign logic

### 3f. Archive (end-of-lifecycle)
1. Admin selects old assignments → POST `/api/billback/ubi/archive`
2. Moves Stage 8 → Stage 99 Historical Archive
3. Writes archive metadata to `jrk-bill-ubi-archived` DDB

### 3g. Add account comment (PRE-11 flow)
1. User clicks "Add Comment" on an account card
2. `openAccountCommentModal()` opens modal
3. User types comment, clicks Save
4. `saveAccountComment()` POSTs to `/api/config/account-comment` (main.py:18796)
5. Server finds account in config, writes comment, re-caches workflow_tracker
6. Client updates local `accountsToTrack.find(...)` entry + DOM badge

---

## 4. 🚨 Clunkiness / Workflow Gaps

### 4a. PRE-11 ROOT CAUSE IDENTIFIED — Comment save UI inconsistency → **[ISSUE-056]** (P1, user-reported)

**Server side is (mostly) correct:** `api_update_account_comment` at main.py:18796 has an **account-only fallback** (added in commit `686fae9`): if exact `(account, vendor)` match fails, match by account alone and use the matched record's actual vendor.

**Client side is NOT symmetric:** `saveAccountComment()` at billback.html:3063 does:
```javascript
const acct = accountsToTrack.find(a => configAcct === currentCommentAccount
                                     && configVendor === currentCommentVendor);
if (acct) { acct.comment = comment; }  // silently no-op if not found
```

**Bug:** when vendor name differs between the config's `vendorName` and the BILLBACK page's displayed `vendor`, the client's `find` returns `undefined`, `acct.comment = comment` is a no-op, and local state is never updated. Server saves successfully → client believes save "worked" (response.ok true) → but the local `accountsToTrack` array still has the OLD (missing) comment → on next render, the comment isn't shown → **user reports "comments don't show up."**

Additionally, the DOM patch uses a fragile selector: `group.querySelector('.bill-header div div:nth-child(2)')`. If the template's HTML changed (and this template has been heavily edited per memory), this selector may silently fail even when account lookup succeeds.

**Fix:**
1. Client-side: mirror the server's account-only fallback in `saveAccountComment`'s `.find()` lookup
2. Client-side: after successful save, refetch `/api/config/accounts-to-track` to refresh local cache (costly but correct)
3. DOM patch: replace fragile CSS selector with data-attribute lookup (e.g., `[data-account="${acct}"]` on bill groups)
4. Better yet: after save, trigger a targeted re-render of the affected bill group rather than DOM patching

**Status:** this is exactly the user's reported "PRE-11 comments don't show up" bug, fully traced.

### 4b. PRE-12 investigation pending — "Add to Tracker reverts on refresh"

Add-to-tracker endpoints are in Module 11 (Config) at main.py:19018 (`api_config_add_to_tracker`) and 19199 (`api_ubi_add_to_tracker`). **Duplicate endpoints** — two paths for the same operation, which is itself a smell. Not reviewed deeply in this module; flagged for Module 11 review.

**Hypothesis (needs verification in Module 11):** the add succeeds server-side (writes to `accounts_to_track` config) but either:
- The workflow_tracker cache isn't invalidated, so the next `/api/workflow/completion-tracker` call returns stale data without the new account
- The two add-to-tracker endpoints write to different locations (`api_config_*` vs `api_ubi_*` — different sources, different caches)
- On refresh, the BILLBACK page re-fetches fresh `accountsToTrack` but somehow the addition isn't there (write failed silently? written to wrong shape?)

**[ISSUE-057]** (P1, user-reported) — flagged, root cause TBD until Module 11 review.

### 4c. 5+ removal paths for UBI assignments → **[ISSUE-058]** (P2, TECH-DEBT)

The "3 deletion paths" noted in the original main.py analysis was undercounting. Counting actual endpoints:
1. `/api/billback/ubi/unassign` — specific line items
2. `/api/billback/ubi/unassign-account` — all lines for account+period
3. `/api/billback/ubi/cleanup-exclusions` — exclusion hash cleanup
4. `/api/billback/ubi/archive` — move to S99
5. `/api/billback/ubi/reassign` — unassign+assign in one (specific lines)
6. `/api/billback/ubi/reassign-account` — unassign+assign in one (account-level)
7. `/api/ubi/remove-from-ubi` (Config module, main.py:19470)
8. `/api/ubi/remove-from-tracker` (Config module, main.py:19423)

Each path has its own cleanup of DDB + cache + S3. Subtle differences per memory: the GL-swap bug had multiple vectors; this is the same smell in a different module. Bug fix in one path doesn't propagate.

**Fix:** consolidate into a single `_ubi_release_assignments(line_hashes, target="tracker"|"exclusion"|"reassign")` helper. Every endpoint is a thin wrapper around it.

### 4d. Fallback S3-key search is a complexity smoke signal → **[ISSUE-059]** (P2, TECH-DEBT)

`api_billback_ubi_assign` (main.py:5589-5627) has a fallback block: if the original S3 key doesn't exist, it scans the entire date partition for files matching the line hashes. This exists because **files get rewritten with new timestamps mid-flight** (e.g., if another user unassigns from the same source, the file is rewritten with remaining items and a new timestamp).

**Implication:** S3 keys aren't stable identifiers for bills. The real identifier is the line hash. This is a leak of the file-rewrite abstraction into every consumer. Every endpoint that takes an `s3_key` parameter has to either (a) handle the "key moved" case, or (b) be buggy when it doesn't.

**Fix:** treat line hash as the primary identifier; resolve to current S3 key at operation time via an index (DDB `jrk-bill-ubi-line-index` mapping `line_hash → current_s3_key`).

### 4e. Two DDB assignment-ID formats → **[ISSUE-060]** (P3, MIGRATION DEBT)

The unassign endpoint (main.py:6676-6714) handles two DDB formats:
- **New:** `assignment_id = line_hash` (direct delete)
- **Old:** `assignment_id = line_hash[:32]-date` (requires full scan to find)

Old records persist. Every unassign of an old record triggers a full scan. At 5K+ assignments, scans are expensive.

**Fix:** one-shot migration: scan all `jrk-bill-ubi-assignments`, for each old-format record: copy data to new-format, delete old. Run as a Lambda or one-off script. After, remove the scan fallback.

### 4f. Form data with mixed delimiters → **[ISSUE-061]** (P3, UX / TECH-DEBT)

`api_billback_ubi_assign` accepts:
- `line_hashes` as comma-separated
- `ubi_periods` as comma-separated
- `notes` as `|||`-separated
- `amounts` as comma-separated

Different delimiters + positional mapping between lists is fragile. A comma in a note breaks parsing. Should be a JSON body:
```json
{
  "s3_key": "...",
  "items": [
    {"line_hash": "...", "amount": 100, "notes": "..."},
    {"line_hash": "...", "amount": 50, "notes": "..."}
  ],
  "ubi_periods": ["09/2025", "10/2025"]
}
```

### 4g. Multi-period single-bill doesn't use multi-period suggestion → **[ISSUE-062]** (P2, JTBD — KNOWN)

Already in `CLAUDE.md` Known Issues: `_calculate_ubi_suggestion()` ignores service dates and returns one period. For quarterly bills, user must manually change Months input before Accept. Doc reference: `docs/MULTI_PERIOD_SUGGESTION_FIX.md` — but I haven't verified this doc exists. Flag for follow-up.

### 4h. Reads entire S3 file to find line matches → **[ISSUE-063]** (P3, PERF)

Every assign/unassign reads the full JSONL file into memory, parses line-by-line, rewrites. For a bill with 100 line items where user assigns 1, we read+parse+re-serialize all 100. Not terrible at current scale, but scales linearly.

### 4i. Notes field isn't searchable or indexed → **[ISSUE-064]** (P3, JTBD)

Notes on UBI assignments are stored inline in the JSONL (`ubi_notes` per line). No way to search "all assignments with note containing 'X'". If AP needs to find all assignments that had a specific note (e.g., "VACANT - 2026-Q2"), they'd have to scan all S8 files.

### 4j. Billback save writes to `jrk-bill-billback-master` but the endpoint is poorly named → **[ISSUE-065]** (P3, TECH-DEBT)

`/api/billback/save` at main.py:5173 writes to `jrk-bill-billback-master` DDB. But from the name you'd expect it saves "billback edits" somewhere related to the UBI assignment flow. It's actually saving BILLBACK MASTER records — which is a separate concept (per-property-period billable totals). Naming confusion.

Additionally: two distinct save paths for "billback":
- `/api/billback/save` → jrk-bill-billback-master (master-level)
- `/api/billback/ubi/assign` → S3 JSONL + jrk-bill-ubi-assignments (line-item-level)

Not clear from naming which is which. Worth renaming the former to `/api/billback/master/save`.

### 4k. `jrk-bill-billback-master` DDB table not in inventory → **[ISSUE-066]** (P3, DRIFT)

This table wasn't listed in `04_data_architecture.md` or CLAUDE.md. Found during this review. Needs to be added.

### 4l. The AI suggestion endpoint uses `_calculate_ubi_suggestion` which is in a different module → **[ISSUE-067]** (P3, TECH-DEBT)

Module boundary is fuzzy. `_calculate_ubi_suggestion` is at main.py:8632 (Module 11 / Workflow area) but consumed by UBI endpoints at main.py:5821. Cross-module dependency without clear interface.

### 4m. `_get_accounts_to_track` caching — PRE-11 extra risk → **[ISSUE-068]** (P2, DATA)

The server save invalidates `workflow_tracker` cache (line 18838-18853) but doesn't explicitly clear the accounts_to_track cache that `_get_accounts_to_track` uses. If BILLBACK page reads from the latter on refresh, it gets stale data. Depends on `_put_accounts_to_track` implementation — flag for verification.

---

## 5. Integration Gaps

### 5a. UBI → Master Bills
Assignments in S8 feed `_Master_Bills_Prod` in Snowflake (via Module 7 / Master Bills module). If UBI assignments are wrong (GL swap, wrong period), master bills are wrong. ISSUE-045 (post-verifier with line-level checks) partially addresses this at the post stage; downstream UBI-level verification would also be needed.

### 5b. UBI → Entrata AR (resident billback charges)
Eventually billback records translate into Entrata AR charges posted to tenants. That path (via `EntrataARClient`) is in Module 14 / VE or standalone — not reviewed yet. Drift risk: if our S8 record shows $50 but Entrata AR charge is $45, tenant is undercharged, compliance issue.

### 5c. UBI → Knowledge Base
Suggestions feed from `jrk-bill-knowledge-base` table. Learning patterns promoted/demoted via Autonomy module (Module 13). That loop is a cross-module concern.

### 5d. Cache invalidation web
UBI operations touch: `_CACHE["ubi_unassigned"]`, `_remove_bill_from_ubi_cache`, `_METRICS_CACHE["ubi_suggestions"]`, `_METRICS_CACHE["ubi_assigned"]`. These invalidations are scattered — an endpoint that forgets one leaves stale UI. Consolidation candidate.

---

## 6. Feature Inventory (UI vs. what works)

| Feature | UI exists? | Works? | Notes |
|---|---|---|---|
| View unassigned bills | ✅ | ✅ | Lambda-built cache; fast |
| Pagination | ✅ | ✅ | |
| Filtering (property, vendor, GL) | ✅ | ✅ | Server-side |
| Sorting (amount, date, vendor) | ✅ | ✅ | |
| AI suggestion per bill | ✅ | ⚠️ | Single-period only (ISSUE-062) |
| Assign single period | ✅ | ✅ | |
| Assign multi-period | ✅ | ⚠️ | Works, but AI doesn't suggest multi-period |
| Per-line notes + amounts | ✅ | ⚠️ | Fragile delimiter parsing (ISSUE-061) |
| View assigned bills | ✅ | ✅ | |
| Unassign single items | ✅ | ✅ | |
| Unassign entire account | ✅ | ✅ | Expensive if no s3_key (ISSUE-*) |
| Reassign period | ✅ | ✅ | Separate from unassign+assign |
| Account comments | ✅ | ❌ | PRE-11: save silently fails to update UI |
| Archive old | ✅ | ✅ | |
| Exclusion hashes | ✅ | ⚠️ | Cleanup endpoint exists; semantics opaque |
| Completion tracker integration | ✅ | ⚠️ | Add-to-tracker issue PRE-12 |
| Billback report PDF | ✅ | ✅ | Module 6 region |
| Billback summary aggregation | ✅ | ✅ | |

---

## 7. Technical Implementation

Key helpers I read:
- `_get_ubi_unassigned_cached` (main.py:5361) — pure reader of Lambda-built cache; good pattern
- `_lookup_charge_code` (main.py:5388) — 4-tier fallback: property-specific by GL code → by GL account ID → wildcard by GL code → wildcard by GL account ID
- `_compute_stable_line_hash` — used as primary line-item identifier (elsewhere in main.py)
- `_remove_bill_from_ubi_cache` — surgical cache patch
- `_load_ubi_cache_from_s3` — startup loader + ETag-polling (Module 2 startup found it)

Endpoints:
- 15 UBI endpoints reviewed structurally; 3 read in detail (unassigned, assign, unassign)
- Form-based I/O throughout; mixed delimiters
- Per-endpoint cache invalidation (ad hoc)

---

## 8. Data Touchpoints

### DDB
| Table | Usage |
|---|---|
| `jrk-bill-ubi-assignments` | PK=`assignment_id` (new: line_hash; old: `line_hash[:32]-date`). Per-line assignment records |
| `jrk-bill-ubi-archived` | Archive |
| `jrk-bill-config` | UBI cache metadata, mappings, exclusion hashes |
| `jrk-bill-knowledge-base` | Suggestion patterns |
| `jrk-bill-billback-master` | **Not in inventory** — billback master records per period. Flag ISSUE-066. |

### S3
| Prefix | Usage |
|---|---|
| `Bill_Parser_7_PostEntrata_Submission/` | Source for unassigned bills |
| `Bill_Parser_8_UBI_Assigned/` | Assigned bills |
| `Bill_Parser_99_Historical Archive/` | Archived assignments |
| `Bill_Parser_Config/ubi_unassigned_cache.json.gz` | Lambda-built cache |
| `Bill_Parser_Config/ubi_mapping.json` | UBI mapping config |
| `Bill_Parser_Config/ubi_account_history.json` | Account history for suggestions |

### Caches (in-memory)
- `_UBI_UNASSIGNED_CACHE` (loaded from S3, polled every 5 min by Lambda update)
- `_CACHE["ubi_unassigned"]` (legacy? redundant?)
- `_METRICS_CACHE["ubi_suggestions"]`, `_METRICS_CACHE["ubi_assigned"]`
- `_CACHE["workflow_tracker"]` (shared with tracker module)

---

## 9. Drift vs. Existing Docs

| Claim | Source | Reality | Verdict |
|---|---|---|---|
| "UBI assignment has 3 separate deletion paths" | Module taxonomy findings | Actually 5-8 depending on how you count (ISSUE-058) | 🔴 DRIFT (undercounted) |
| "UBI unassigned built externally by Lambda" | Memory `feedback_ubi_cache_architecture.md` | ✅ True — `_get_ubi_unassigned_cached` never computes locally | Accurate |
| "jrk-bill-billback-master" table | Not in docs | Exists, written to by `/api/billback/save` | 🔴 DRIFT (missing from inventory) |
| "Comments don't show up" | User report PRE-11 | Client-side lookup bug in `saveAccountComment()` (ISSUE-056) | Confirmed + diagnosed |

---

## 10. Issues Flagged (summary → ISSUES.md)

| ID | Severity | Scope | Title |
|---|---|---|---|
| ISSUE-056 | **P1** | BUG / JTBD | PRE-11 comments UI — client lookup doesn't mirror server's account-only fallback |
| ISSUE-057 | P1 | BUG / JTBD | PRE-12 Add to Tracker reverts — root cause in Module 11 |
| ISSUE-058 | P2 | TECH-DEBT | 5-8 UBI removal paths; consolidation candidate |
| ISSUE-059 | P2 | TECH-DEBT / DATA | S3-key fallback search reveals file-rewrite leakage |
| ISSUE-060 | P3 | MIGRATION DEBT | Two DDB assignment-ID formats; scan fallback expensive |
| ISSUE-061 | P3 | UX / TECH-DEBT | Mixed-delimiter form data (CSV + `|||`); fragile |
| ISSUE-062 | P2 | JTBD | Multi-period suggestion not implemented (known, in CLAUDE.md) |
| ISSUE-063 | P3 | PERF | Full-file read on every assign/unassign |
| ISSUE-064 | P3 | JTBD | Notes not searchable/indexed |
| ISSUE-065 | P3 | TECH-DEBT | `/api/billback/save` endpoint naming conflates with `/api/billback/ubi/*` |
| ISSUE-066 | P3 | DRIFT | `jrk-bill-billback-master` DDB table missing from inventory |
| ISSUE-067 | P3 | TECH-DEBT | `_calculate_ubi_suggestion` cross-module dependency (fuzzy boundary) |
| ISSUE-068 | P2 | DATA / CONSISTENCY | `_get_accounts_to_track` cache invalidation on comment save unclear |

**Top priority: ISSUE-056 (PRE-11 fix) — can be a 10-line client-side fix. Fastest user-visible win of the entire review.**

---

## 11. Open Questions for User

**[Q-31]** **Commit to fix PRE-11 (ISSUE-056) quickly?** The fix is ~10 lines of JS in `saveAccountComment`. Mirror the server's account-only fallback client-side + use data-attribute DOM patching. Can land today. Agree?

**✅ ANSWERED (2026-04-17) AND LANDED:** Client-side fallback added in `templates/billback.html:3094-3108`. Local state now updates correctly when vendor name differs between config and display. Remaining polish (DOM selector robustness, server-returns-matched-vendor) deferred — not blocking the core bug.

**[Q-32]** **PRE-12 investigation:** Should I chase PRE-12 root cause now (means dipping into Module 11 early) or defer to the Module 11 review?

**[Q-33]** **Consolidate UBI removal paths (ISSUE-058):** The 5-8 paths create subtle bug surfaces. Consolidation is ~200 LOC refactor. Worth doing, or accept as tech debt?

**[Q-34]** **S3-key-as-identifier replacement (ISSUE-059):** Build a `line_hash → current_s3_key` index? This is a foundational architectural change that unlocks many other fixes (deletion, re-parse, search staleness) but is a big lift.

**[Q-35]** **DDB assignment-ID migration (ISSUE-060):** Run the one-shot migration? Risk is low (old records get updated to new format), benefit is removing the scan fallback.

**[Q-36]** **JSON body for UBI endpoints (ISSUE-061):** Replace form-based input with JSON across all UBI endpoints? Cleaner, but requires frontend changes.

**[Q-37]** **Multi-period suggestion (ISSUE-062):** Land the fix described in `docs/MULTI_PERIOD_SUGGESTION_FIX.md` (if it exists) or treat as separate planning doc?

---

## 12. Dead / Unused Code

- `_CACHE["ubi_unassigned"]` might be legacy (replaced by `_UBI_UNASSIGNED_CACHE` Lambda-built) — verify
- Old DDB format records (ISSUE-060) — data, not code, but same concept

---

## 13. SSO Migration Concerns

| Touchpoint | Notes |
|---|---|
| `ubi_assigned_by` field in JSONL | Session user → must survive SSO transition (like PostedBy in Module 3) |
| `locked_by` on any UBI operation | Session user |
| Per-user metrics on assignments | Need capture for METRICS module |
| Admin-only ops (archive, cleanup-exclusions) | Currently no role check — capability `admin:ubi_cleanup` |

---

## 14. Service-Account Concerns

- Lambda that builds `ubi_unassigned_cache.json.gz` — already runs as Lambda with IAM role (no app auth needed)
- Future Lambda for line-level post-verifier (ISSUE-045) would need read access to Stage 8 + write access to verify_status — via IAM, not app auth

---

## Observations for Current-State Synthesis

1. **PRE-11 is a client/server asymmetry.** Server has defensive fallback logic; client doesn't. Classic pattern of "we knew about the vendor-name mismatch and fixed it on the backend, but forgot the frontend."

2. **The "5+ deletion paths" problem echoes the GL-swap bug pattern.** Once you have many similar endpoints, bugs show up in one that don't show up in others. Consolidation is overdue.

3. **S3-key-as-identifier is an unstable foundation.** Many modules store or pass S3 keys. The UBI module's fallback search is a symptom. Other modules silently suffer when files get rewritten.

4. **Cache pattern is GOOD here.** The Lambda-built cache + in-memory load + ETag polling is the cleanest pattern in the codebase. Should be the template for other expensive computations.

5. **Billback UI quirks (comments, tracker revert) are real user pain.** Every time the user has to refresh or re-do an action, trust erodes. Priority fixes.
