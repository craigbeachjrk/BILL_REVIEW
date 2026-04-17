# Module 3 — Post & Entrata (⚠️ Money Movement)

**Scope of review:**
- `main.py:1-52` — `_entrata_post_succeeded` response parser (unusually at file top, before imports)
- `main.py:2076-2163` — `/api/post/validate` (pre-post warnings)
- `main.py:2165-2345` — distributed lock (`_acquire_post_lock`, `_update_post_lock`)
- `main.py:2347-2486` — `/api/clear_post_locks`, `/api/test_post_lock`
- `main.py:2488-2634` — `/api/verify_entrata_sync` (post-facto sync verification)
- `main.py:2636-2987` — `/api/post_to_entrata` (the main POST endpoint — ~350 lines)
- `main.py:2990-3078` — `/api/advance_to_post_stage`, `/api/archive_parsed`
- `main.py:4853-4885` — `/api/post/total` (lazy total)
- `templates/post.html` (1,126 lines) — POST UI with validate/post flow
- DDB: `jrk-bill-config` (POST_LOCK entries), `jrk-bill-ai-suggestions` (posted metadata cache)
- S3: `Bill_Parser_6_PreEntrata_Submission/` (source), `Bill_Parser_7_PostEntrata_Submission/` (dest), `Bill_Parser_99_Historical Archive/` (parallel PDF archive)

**This is the highest-risk module in the codebase.** It moves money into a production accounting system. Every issue here can have financial consequences.

---

## 1. Module Purpose (Business)

Turn a reviewed invoice into a posted Entrata AP invoice. Triggers Accounts Payable to cut a check. The user job: "I reviewed this bill, it's correct, post it." The system job: "Don't post duplicates. Don't post bad data. Don't lie about whether it posted. Don't lose the audit trail."

Secondary jobs:
- **Validate before posting** — warn if vendor-property or vendor-GL combinations look unusual
- **Verify after posting** — compare our S7 data with Entrata's GL report (catch sync drift)
- **Advance without posting** — move a manually-posted bill from S6 to S7 (bookkeeping path)
- **Archive** — move old pre-Entrata files to historical archive (cleanup)

## 2. User Personas & Roles

| Persona | What they do |
|---|---|
| **AP Clerk** | Selects reviewed bills at /post, clicks Validate, resolves warnings, clicks POST → triggers Entrata posting |
| **AP Clerk (advanced)** | Uses force-clear-locks if a post got stuck; uses Advance-without-posting for manually-handled invoices |
| **Supervisor / Treasury** | Runs Entrata sync verification to confirm everything posted matches Entrata's books |
| **Anyone authenticated** | Can clear locks (NO admin gate — issue) |

## 3. End-to-End Workflow Walkthrough

### 3a. Normal post (happy path)
1. User visits `/post` → renders `post.html` with pending bills grouped by day
2. User selects one or many bills via checkboxes → clicks "Validate"
3. Client POSTs `/api/post/validate` with selected keys
4. Server reads all selected S3 JSONL files in parallel, checks every (vendor_id, property_id) and (vendor_id, gl_code) against 1-year history
5. Server returns warnings; client shows warnings modal
6. User reviews warnings (can expand to see raw data); clicks "POST anyway" or cancels
7. Client POSTs `/api/post_to_entrata` with keys
8. For each key, server:
   - Validates S3 key is in expected prefix (`STAGE6_PREFIX` or `STAGE4_PREFIX`)
   - **Acquires distributed post lock** in DDB (conditional put on `PK=POST_LOCK, SK=sha1(key)`)
   - Reads JSONL rows from S3
   - Attaches PDF (best-effort via `_try_load_pdf_b64`)
   - Applies GL override from Accounts-To-Track config if (property, vendor, account) matches a tracked config
   - Rebuilds `GL DESC_NEW` with current format (avoids stale descs from older S6 files)
   - Reconciles user GL Name vs GL Number edits (name wins on conflict)
   - Resolves vendor location (if vendor has multiple Entrata locations, need override or prompt user)
   - Builds Entrata payload via `build_send_invoices_payload()` (not seen yet)
   - Calls `do_post(payload, dry_run=False)` → HTTP call to Entrata
   - Parses response via `_entrata_post_succeeded()` (classifies as ok/duplicate/error/unknown)
   - On success: updates lock to POSTED; archives PDF to `Bill_Parser_99_Historical Archive/`; writes new JSONL to S7 with `PostedBy`, `PostedAt`, `Status=Posted`; writes metadata to DDB for CHECK REVIEW; **deletes source from S6**
   - On failure: updates lock to FAILED (force); adds error entry with hint
9. Returns `{updated, errors, unresolved, results}` to client
10. Client refreshes, POST becomes S7

### 3b. Duplicate detection → repost
1. Entrata returns status=error, message="duplicate invoice" (or similar)
2. `_entrata_post_succeeded` classifies as `reason="duplicate"`
3. Response includes `err_entry.repostable = True` + account_number + bill_date + current_suffix
4. Frontend offers "Repost with suffix" (e.g., "INV-123 REV1") — user edits suffix, re-submits
5. Back-end records `DUPLICATE_REPOST` audit event (via `_record_audit_event`)
6. New post goes through with appended suffix → creates net-new Entrata invoice

### 3c. Multi-location vendor resolution
1. Vendor has multiple Entrata locations (e.g., PECO Commercial vs PECO Residential)
2. First pass can't resolve single location → `unresolved` list populated
3. Response: `{ok: false, message: "vendor_locations_needed", unresolved: [...]}`
4. Frontend shows modal: "Pick a location for vendor X"
5. User selects → client re-submits with `vendor_overrides = {vendorId: locationId}`
6. Server resolves locations from overrides on retry

### 3d. Advance-without-post (bookkeeping path)
1. Bill was manually posted in Entrata outside this system
2. User selects bill → clicks "Advance to Post Stage" (skips actual Entrata call)
3. Server moves JSONL S6 → S7 + records PostedBy/PostedAt (like a normal post, but no Entrata call)
4. Useful when Entrata was posted directly by AP team but the bill needs to catch up in our pipeline

### 3e. Force-clear stuck locks
1. A post hung, lock status stuck at `POSTING`
2. User clicks "Clear Locks" in UI → POSTs `/api/clear_post_locks`
3. Server unconditionally writes lock status = FAILED
4. Lock is now clearable by anyone — user can retry

### 3f. Entrata sync verification
1. User navigates (where? not sure — probably /post page)
2. Paste Entrata's GL report JSON (or upload) → POST `/api/verify_entrata_sync`
3. Server reads our S7 files for date range, compares against Entrata's data by invoice number
4. Returns `{matched, missing_in_entrata, amount_mismatch, extra_in_entrata}` report
5. Used for reconciliation

---

## 4. 🚨 Clunkiness / Workflow Gaps (and money-movement risks)

### 4a. Lock fail-open during DDB outage → **[ISSUE-037]** (P0 — duplicate-post risk)
`_acquire_post_lock` (main.py:2221-2223):
```python
except Exception as e:
    print(f"[POST LOCK] Error acquiring lock for {s3_key}: {e}")
    return True  # fail-open: if DDB is down, allow the post rather than blocking
```

**Intent:** don't block posting if DDB is briefly unavailable.
**Risk:** during a real DDB outage, all 2 AppRunner instances accept all post requests without coordination. Two users clicking Post at the same time = bill posted twice.

For a system that moves real money, fail-CLOSED is almost always the right default. The 30s stale-lock timeout already provides recovery from genuinely-dead holders; the fail-open exists only to handle a transient DDB issue, which is rare.

**User Job Affected:** "Post this bill once." System honors that 99.99% of the time, but silently duplicates during DDB hiccups — and you only find out in the Entrata sync verification later.

### 4b. 30-second stale-lock timeout → **[ISSUE-038]** (P1 — duplicate-post risk)
`_acquire_post_lock` releases a lock as stale if it's been `POSTING` for >30 seconds (main.py:2185, 2204).

**Reality:** Entrata API calls regularly take 10-60 seconds, especially with PDF uploads attached. Cold-start of parser Lambda 1-3s. If a post takes 35s, another AppRunner request can steal the lock (conditional on `locked_at < stale_cutoff`) and post the same bill.

This is the classic "liveness vs. safety" tradeoff. The current setting favors liveness (recovery from hung posts in 30s) over safety (preventing duplicates).

**Fix options:**
- Extend to 5-10 minutes (trades slower recovery for safer semantics)
- Add heartbeat: holder pings `locked_at` timestamp every 20s during the post; stale check uses last ping, not acquisition time
- Drop the stale-lock mechanism entirely; admin force-clear is always available

### 4c. Same-user retry allows concurrent posts → **[ISSUE-039]** (P1 — duplicate-post risk)
Condition `(#st = :posting AND locked_by = :user)` (main.py:2205) allows the same user to re-acquire an in-progress lock. Intent: if a user retries after UI timeout, they can continue. But: if user clicks POST twice quickly (network was slow, UI didn't update), both requests acquire the lock concurrently and both post to Entrata.

**Fix:** remove the same-user exception, OR require a short cool-down (e.g., `locked_at < now - 5s`), OR use a request_id so the same POST attempt can retry but a new click cannot.

### 4d. `_POST_LOCK_NONCES` is process-local → **[ISSUE-040]** (P2 — consistency risk)
`_POST_LOCK_NONCES: dict` (main.py:2167) is a Python dict in process memory. 2 AppRunner instances each have their own. If instance A acquires a lock and is killed by autoscale/deploy, the nonce is lost. Instance B doesn't know who owns the lock. Status in DDB says POSTING until 30s stale.

**Fix:** drop the in-memory cache; read nonce from DDB every time (tiny cost). Or: write nonce to DDB and use `conditional expression` on the write only.

### 4e. `/api/clear_post_locks` has no admin check → **[ISSUE-041]** (P1 — integrity)
```python
@app.post("/api/clear_post_locks")
def api_clear_post_locks(keys: str = Form(...), user: str = Depends(require_user)):
```

**No role check!** Any authenticated user can force-clear any post lock. Combined with fail-open semantics, this creates a path where user A is posting, user B clears the lock, user A's post finishes, user B retries → duplicate.

**Fix:** add `require_admin` (or capability check). Audit-log every clear.

### 4f. S3 move is copy-then-delete → **[ISSUE-042]** (P2 — data integrity)
Post flow:
1. `_write_jsonl(POST_ENTRATA_PREFIX, ...)` writes new file to S7
2. `s3.delete_object(Bucket=BUCKET, Key=key)` deletes original from S6

If delete fails, bill appears in BOTH S6 and S7. If write succeeded but the response doesn't reach the client, user may click Post again → lock stale check triggers retry → re-posts.

**Fix:** use S3 object versioning + lifecycle or tombstone the source before the copy (so listing S6 doesn't show the bill post-post).

### 4g. `_archive_posted_pdf` silently swallows errors → **[ISSUE-043]** (P3 — audit gap)
```python
try:
    archive_result = _archive_posted_pdf(rows[0])
    if archive_result:
        print(f"[post_to_entrata] Archived PDF: {archive_result}")
except Exception as archive_err:
    print(f"[post_to_entrata] PDF archive failed (non-fatal): {archive_err}")
```

The PDF archive is for the file-server sync (audit trail, check-slip PDF assembly). If it silently fails, downstream processes can't find the PDF. Not a money risk but a compliance one (auditors want every posted invoice's PDF).

**Fix:** surface archive failures as a warning on the response, write to a `archive_failed` queue for retry, emit a CloudWatch metric.

### 4h. Entrata response parsing is a string-heuristic → **[ISSUE-044]** (P1 — trust)
`_entrata_post_succeeded` relies on string patterns ("duplicate", "invoice exists", "success", "created") within JSON or text bodies. If Entrata changes response format or localizes messages, this breaks silently.

**Known patterns it tries:**
- JSON status fields: ok, success, created, accepted, processed → success
- JSON status fields: error, fail, failed, rejected, denied, invalid → failure
- Message contains: duplicate, already exists, already posted, invoice exists → duplicate
- Keyword scan fallback on raw text

**Defensive correctness:** "unknown response" defaults to failure. Good.
**Risk:** a response format change from Entrata could cause either (a) real successes to be treated as failures (bill moves to FAILED status but actually posted → looks like user needs to retry → duplicate risk) or (b) real failures classified as success (bill marked POSTED but not in Entrata → discovered via sync verification, maybe weeks later).

**Fix:** add a "canary" monitoring check that posts a test invoice daily and asserts the response classification. Alert if response shape drifts.

### 4i. No post-hoc verification → **[ISSUE-045]** (P2 — trust)
After a POST to Entrata returns success, we trust the response and move the file to S7. We don't:
- Query Entrata to confirm the invoice appears
- Re-fetch the Entrata invoice by ID to compare totals
- Check that line items match

`/api/verify_entrata_sync` exists for this but requires manual trigger + pasted GL report. Should be automatic.

**Fix:** after every successful post, schedule a verify-after-N-minutes job that confirms the invoice is in Entrata. Alert on mismatch.

### 4j. Bulk post has no batching → **[ISSUE-046]** (P2 — PERF / timeout)
`api_post_to_entrata` iterates keys sequentially. Each key: S3 reads + PDF attach + Entrata API call. If a user selects 50 keys, total time could be 50 × 10s = 500s. AppRunner request timeout is ~120s. User sees timeout; some bills posted, some didn't, some are in `POSTING` lock stale state.

**Fix:** This is a textbook case for the async job-queue pattern (per `feedback_async_job_queue_pattern.md`). Client submits job, gets job_id, polls for progress.

### 4k. Post validation returns warnings but no server-side enforcement → **[ISSUE-047]** (P2 — process)
`/api/post/validate` returns warnings but nothing stops the user from ignoring them and posting anyway. Is there a category of warning that SHOULD block posting? E.g., "vendor not found in Entrata" should not be post-able, vs. "vendor first time at this property" is informational.

**Fix:** split into "blocking" vs "informational" warnings. Blocking requires explicit supervisor override.

### 4l. The "advance without posting" path duplicates code → **[ISSUE-048]** (P3 — TECH-DEBT)
`api_advance_to_post_stage` mirrors the S6→S7 move logic from the main post endpoint but without Entrata. Two places to update when file-move semantics change.

**Fix:** extract the "write to S7 + delete from S6" into a single helper used by both.

### 4m. `_entrata_post_succeeded` defined at file line 1 → **[ISSUE-049]** (P4 — TECH-DEBT / WEIRDNESS)
This function is the FIRST thing in main.py, defined before any imports. Bizarre code organization. Moving it to a sensible location (e.g., near `api_post_to_entrata`) is a one-diff cleanup.

### 4n. Hidden in-memory mutation on `rows` → **[ISSUE-050]** (P3 — TECH-DEBT)
Post flow heavily mutates `rows` in place (GL ID swaps, GL DESC rebuild, name/number reconciliation). If any step throws mid-flight, `rows` is in a half-modified state before we re-use it for error messages or the S7 write. Hard to audit, hard to test.

**Fix:** derive a `posted_rows` copy with all transformations applied atomically at the end, rather than step-by-step mutation.

### 4o. Error hints are fuzzy → **[ISSUE-051]** (P3 — UX)
Error hints (e.g., "Entrata did not respond in time. Check Entrata for this invoice before retrying.") are important — they direct the user to verify before retrying (to prevent duplicate). But they don't link to anything. User has to go to Entrata manually. Could link to an invoice-search URL with the bill's account + date pre-filled.

### 4p. `locked_at` is an ISO string comparison → **[ISSUE-052]** (P4 — BUG)
Stale check compares `locked_at < :stale` (both strings). Works correctly for ISO 8601 (lexicographic ordering matches chronological), but fragile. If the stored format ever varies (e.g., ends in Z vs. no Z), comparison misbehaves silently.

**Fix:** store epoch seconds (`ttl_epoch` already exists) and compare those instead.

### 4q. Posted metadata DDB write is best-effort → **[ISSUE-053]** (P3 — JTBD)
`_write_posted_invoice_metadata` in try/except pass. If it fails, CHECK REVIEW module (Module 8) has to fall back to S3 reads per-invoice — slow and AppRunner-expensive per memory.

**Fix:** either make it non-optional (and retry), or explicitly queue a background retry.

### 4r. No "I posted but the response didn't reach me" recovery path → **[ISSUE-054]** (P1 — data integrity)
User clicks Post → Entrata receives request → Entrata creates invoice → **network drops before response returns to app**. App marks lock FAILED. Bill stays in S6. User retries → Entrata says "duplicate" → app shows repost modal → but bill IS already posted in Entrata with original suffix. User thinks their first attempt failed.

Requires the duplicate-detection codepath to:
- Offer "verify" (check if the ORIGINAL is in Entrata; if so, mark POSTED here)
- Separate from "repost with suffix"

Current code conflates these.

---

## 5. Integration Gaps

### 5a. POST module and RESUBMIT/REWORK
When a post fails, bill stays in S6. User goes back to `/review` to fix data, then returns to `/post` to retry. No explicit "post retry" workflow — user has to manually re-select the bill.

### 5b. POST module → CHECK REVIEW (Module 8)
Post writes metadata to DDB for fast CHECK REVIEW loading. If that write fails, CHECK REVIEW still works but slowly. No alert.

### 5c. POST module → audit log (when ISSUE-017 lands)
Currently only DUPLICATE_REPOST is recorded via `_record_audit_event`. Regular posts aren't audited beyond `print()`. When audit log lands, every post should record: who, when, what, Entrata response, S3 key before, S3 key after, lock nonce.

### 5d. POST module → pipeline tracker
`_pipeline_track(key, "POSTED", ...)` is called but errors swallowed. Same issue as Module 2's tracker silence.

### 5e. Sync verification is manual + paste-in
`/api/verify_entrata_sync` requires the user to paste Entrata's GL report JSON. Should be automated (scheduled Lambda pulls from Entrata API + runs comparison + alerts on mismatch).

---

## 6. Feature Inventory (UI vs. what works)

| Feature | UI exists? | Works? | Notes |
|---|---|---|---|
| Pre-post validation warnings | ✅ (post.html) | ✅ | Warnings shown in modal |
| Post single bill | ✅ | ✅ | |
| Post bulk bills | ✅ | ⚠️ | Sequential; 120s timeout risk |
| Progress indicator during post | ⚠️ | ⚠️ | Spinner; no per-bill progress |
| Duplicate detection + repost | ✅ | ⚠️ | Classification fragile; "already posted" vs "duplicate" conflated (ISSUE-054) |
| Multi-location vendor resolution | ✅ | ✅ | 2-step flow |
| Clear stuck locks | ✅ | ⚠️ | No admin gate (ISSUE-041) |
| Advance without posting | ✅ | ✅ | |
| Archive old pre-Entrata | ✅ | ✅ | `/api/archive_parsed` |
| Entrata sync verification | ✅ | ⚠️ | Manual paste-in; should be automated |
| Post lock status visibility | ⚠️ | ⚠️ | Lock status only visible in BLOCKED error; no proactive UI |
| "Verify this invoice was posted" | ❌ | — | Auto-verify after post missing |
| Blocking vs informational warnings | ❌ | — | All warnings are advisory |
| Async post with job_id | ❌ | — | All posting is sync |
| Audit trail of who posted what | ⚠️ | ⚠️ | PostedBy/PostedAt in JSONL; no separate audit log |
| Deep-link to Entrata invoice on failure | ❌ | — | Error hint text only |

---

## 7. Technical Implementation

### 7a. `_entrata_post_succeeded` (main.py:1-52)
- Defined BEFORE imports (rename/move needed)
- JSON first, then keyword text scan
- "Unknown response" → failure (conservative — good)
- Returns `(bool, reason)` where reason is one of: `ok`, `duplicate`, `error`, `http_error`, `unknown_response`, `parse_error`, `unrecognized_status:...`

### 7b. `_acquire_post_lock` (main.py:2171-2223)
- DDB conditional put on `PK=POST_LOCK, SK=sha1(key)`
- Status field: `POSTING` / `POSTED` / `FAILED`
- Nonce: 12-char uuid4 hex, stored in in-memory dict + DDB
- Stale timeout: 30 seconds
- Same-user retry allowed
- TTL: 86400 seconds (24h)
- Fail-open on exception

### 7c. `_update_post_lock` (main.py:2226-2344)
- Three modes: normal (nonce-verified), force, fallback-no-nonce
- Handles condition failure: if status=FAILED, force anyway (prevents stuck locks)
- Otherwise logs mismatch cause for debugging
- Pops in-memory nonce on completion

### 7d. `api_post_to_entrata` (main.py:2636-2987)
- ~350 lines
- Per-key loop with lock acquire → build → post → parse → archive → move
- `_post_lock_held` tracker for safety-net release on crash
- Complex GL override logic (Accounts-To-Track config)
- Complex GL name/number reconciliation
- Complex multi-location vendor resolution
- Cache invalidation at end (`_TRACK_CACHE`, workflow_tracker)

### 7e. `api_verify_entrata_sync` (main.py:2488-2634)
- Pastes in Entrata GL report JSON
- Reads S7 JSONL files for date range
- Compares by invoice number (format `"AccountNumber MM/DD/YYYY"`)
- 1-cent tolerance for amount mismatches
- Returns matched/missing/mismatch/extra lists

### 7f. `api_advance_to_post_stage` (main.py:2990-3035)
- Simpler version of post without Entrata call
- Same S6→S7 move semantics
- Records PostedBy/PostedAt

### 7g. `api_archive_parsed` (main.py:3048)
Not read in this session; flagged for next-pass investigation.

---

## 8. Data Touchpoints

### DDB
| Table | Purpose |
|---|---|
| `jrk-bill-config` | `PK=POST_LOCK, SK=sha1(s3_key)` — lock state |
| `jrk-bill-config` | `PK=POSTED_INVOICES, ...` — posted invoice metadata cache for CHECK REVIEW |
| `jrk-bill-ai-suggestions` | `SUGGESTION#...` (not this module directly) |
| `jrk-bill-pipeline-tracker` | `POSTED` / `POST_ARCHIVE_FAILED` events |

### S3
| Prefix | Usage |
|---|---|
| `Bill_Parser_6_PreEntrata_Submission/` | Source (R, delete on success) |
| `Bill_Parser_7_PostEntrata_Submission/` | Destination (W, move source after success) |
| `Bill_Parser_99_Historical Archive/` | PDF archive (W, best-effort) |

### External
- **Entrata API** — `do_post(payload, dry_run=False)` (function not yet located; presumed in utils or inline)

---

## 9. Drift vs. Existing Docs

| Claim | Source | Reality | Verdict |
|---|---|---|---|
| "Distributed post lock uses nonce" | `_raw_main_analysis.md` appendix | ✅ True; nonce is 12-char uuid4 hex; stored both in-memory and DDB | Accurate |
| Lock timeout | Not documented | 30 seconds (implicit); not configurable | Gap |
| Fail-open semantics | Not documented | True (main.py:2222-2223) | 🔴 DRIFT (unstated behavior with security implications) |
| "CSRF protection on state-changing POSTs" | ISSUE-016 (Module 1) | `/api/post_to_entrata` accepts Form data, no CSRF | Confirmed gap |
| "advance_to_post_stage" exists | Module taxonomy | ✅ | Accurate |

---

## 10. Issues Flagged (summary → ISSUES.md)

| ID | Severity | Scope | Title |
|---|---|---|---|
| ISSUE-037 | **P0** | DATA / SAFETY | Lock fail-open during DDB outage → duplicate-post risk |
| ISSUE-038 | P1 | DATA / SAFETY | 30-second stale-lock timeout too short for real Entrata calls |
| ISSUE-039 | P1 | DATA / SAFETY | Same-user retry allows concurrent posts |
| ISSUE-040 | P2 | DATA / CONSISTENCY | `_POST_LOCK_NONCES` process-local; instance-death loses nonce |
| ISSUE-041 | P1 | SECURITY / INTEGRITY | `/api/clear_post_locks` has no admin check |
| ISSUE-042 | P2 | DATA INTEGRITY | S3 move is copy-then-delete; not atomic |
| ISSUE-043 | P3 | AUDIT | PDF archive errors silently swallowed |
| ISSUE-044 | P1 | TRUST / SAFETY | Entrata response parsing is string-heuristic; fragile to API changes |
| ISSUE-045 | P2 | TRUST | No post-hoc verification that Entrata actually has the invoice |
| ISSUE-046 | P2 | PERF / UX | Bulk post sequential; AppRunner timeout at ~120s = partial failures |
| ISSUE-047 | P2 | PROCESS | Warnings don't block; no "informational vs blocking" split |
| ISSUE-048 | P3 | TECH-DEBT | S6→S7 move duplicated between post and advance |
| ISSUE-049 | P4 | TECH-DEBT | `_entrata_post_succeeded` defined before imports |
| ISSUE-050 | P3 | TECH-DEBT | Heavy in-place mutation of `rows` during post flow |
| ISSUE-051 | P3 | UX | Error hints don't deep-link to Entrata |
| ISSUE-052 | P4 | BUG | `locked_at` string comparison is fragile |
| ISSUE-053 | P3 | JTBD | Posted metadata DDB write is best-effort |
| ISSUE-054 | **P1** | DATA INTEGRITY | No recovery path for "Entrata posted but response didn't arrive" |

**Top 4 priorities for Module 3:**
1. **ISSUE-037** (P0): remove fail-open on lock; fail-closed is correct for money movement
2. **ISSUE-041** (P1): admin-gate `/api/clear_post_locks`; audit every clear
3. **ISSUE-054** (P1): disambiguate "duplicate in Entrata because I actually posted" from "duplicate in Entrata because same bill was posted before"
4. **ISSUE-044** (P1): monitoring canary for Entrata response format drift

---

## 11. Open Questions for User

**[Q-20]** **Fail-open vs fail-closed for post lock (ISSUE-037):** Current behavior allows all posts if DDB is temporarily unavailable. For a money-movement system, I'd default to **fail-closed** — if we can't acquire a lock, refuse to post. DDB hiccups are rare; duplicate-post incidents are expensive. Agree to flip?

**✅ ANSWERED (2026-04-17):** Flip to fail-closed. "Fail closed makes way more sense."

**Fix landed in session plan (queued for end-of-Module-3 batch commit):**
- `main.py:2221-2223` — change `return True` to `return False` in `_acquire_post_lock` exception branch
- Add a CloudWatch metric `PostLockAcquireFailed` incremented on exception (so we see DDB issues immediately)
- Update user-facing error from `Already being posted by another request: {file}` to include this failure mode: `Lock unavailable (DDB error) — try again in 30 seconds`
- Update ISSUE-037 → status: planned-fix (small surgical change)

**[Q-21]** **Stale-lock timeout (ISSUE-038):** 30s is aggressive. Should I extend to 5 minutes + add a heartbeat mechanism (lock holder updates `locked_at` every 20s while working)? That way recovery from truly-dead holders still works, but a legitimate 60s Entrata call doesn't lose its lock.

**✅ ANSWERED (2026-04-17):** Option B — heartbeat mechanism.

**Fix plan (~30-50 LOC, planned-fix):**
- Add `_lock_heartbeat(s3_key, stop_event)` helper — background daemon thread. Every 20s (interruptible via `stop_event.wait(20)`), DDB `update_item` refreshes `locked_at` with current ISO timestamp. Uses same nonce verification so only the holder can ping.
- `api_post_to_entrata` per-key loop: start heartbeat thread before Entrata call; `stop_event.set()` in a `finally:` block after success/failure update; short `join(timeout=1)` cleanup
- Keep stale timeout at ~60s (slightly longer than heartbeat interval, so missed ping = stale)
- On heartbeat error: log + CloudWatch metric, but don't kill the post (Entrata call might still succeed; better to let it finish than abort mid-flight)
- Test scenarios: (1) normal post with heartbeat visible in DDB, (2) long post (60+s) doesn't go stale, (3) crash simulation (instance killed) → heartbeat stops → 60s later another request can steal.
- Update ISSUE-038 → planned-fix

**[Q-22]** **Same-user retry (ISSUE-039):** Should we KEEP the same-user-retry allowance (user convenience) or remove it (safety)? If keep, at minimum add a 5-second cool-down between re-acquires.

**✅ ANSWERED (2026-04-17):** Option C — request-id-based idempotency. Full safety, clean semantics, industry-standard pattern (Stripe, etc).

**Fix plan (~50 LOC backend + ~20 LOC frontend, bundled with ISSUE-037 + ISSUE-038):**
- **Client side** (`templates/post.html`):
  - On first "Post" click, generate `request_id = crypto.randomUUID()`; store in form state
  - Send `request_id` in the POST body for all retries of the same attempt
  - Reset `request_id` to a fresh UUID only on form-edit / cancel / navigation away
  - If UI-driven retry (network failed, user clicks Post again) → reuse same request_id → server allows
  - If user clicks Post twice before first completes → same request_id → server de-duplicates (rejects second as in-progress)
- **Server side** (main.py `api_post_to_entrata`):
  - Accept `request_id: str | None = Form(None)` or `Idempotency-Key` header
  - Validate format (UUID); reject malformed
  - Pass to `_acquire_post_lock(s3_key, user, request_id)`
- **`_acquire_post_lock`** (main.py:2171+):
  - Store `request_id` in DDB item
  - Update ConditionExpression: remove same-user clause, add `OR (status = POSTING AND request_id = :req_id)` — same request_id can retry, different can't
  - DDB item schema now: `{PK, SK, s3_key, status, locked_by, locked_at, nonce, request_id, ttl_epoch}`
- **Semantics:**
  - Double-click with same `request_id` → 2nd acquire no-ops (lock already held by us) → returns True but doesn't duplicate work
  - Need to handle: "same request_id arrives while we're already processing" — server should detect and return the in-progress status (or 409 Conflict with `{status: 'in_progress', original_request_id: req_id}`)
- **Tests:**
  - Double-click (same req_id) = single Entrata POST
  - User clicks Post, times out, clicks again with same req_id = reclaim lock, resume
  - User clicks Post, cancels form, clicks Post again = new req_id, must wait for stale or force-clear
- Updates ISSUE-039 → planned-fix; bundled with ISSUE-037 + ISSUE-038 as a lock mechanism refactor

**[Q-23]** **Admin-gate on clear_post_locks (ISSUE-041):** This should be admin-only or capability-gated. Agree?

**✅ ANSWERED (2026-04-17):** Yes — gate it admin-only.

**Fix plan:**
- main.py:2347 `api_clear_post_locks` → add admin check (pre-SSO: `if user not in ADMIN_USERS` pattern to match existing code; post-SSO: capability `admin:clear_post_locks`)
- Log every clear to the future audit table (ISSUE-017) with: clearer, keys cleared, reason (require a `reason` form field?), timestamp
- Client: hide "Clear Locks" button for non-admin users
- Consider: require `reason` free-text input before clearing, to discourage casual use
- Update ISSUE-041 → planned-fix

**[Q-24]** **Post-hoc verification (ISSUE-045):** Should we auto-verify every post 2-5 minutes later (schedule a job that confirms the invoice is in Entrata)? Or keep verification manual via `/api/verify_entrata_sync`?

**✅ ANSWERED (2026-04-17):** Option A — scheduled Lambda. Aligns with the "heavy compute builds externally" pattern.

**Fix plan (new Lambda + new DDB fields) — LINE-ITEM level verification (per user clarification 2026-04-17):**
- **New Lambda: `jrk-entrata-post-verifier`** — runs every 10 min via EventBridge schedule
  - Scans S7 Stage 7 files posted in the last 60 min with no `verified_at` field
  - For each: queries Entrata API for invoice by `AccountNumber + BillDate` (or invoice number), fetching the **full line-item breakdown**
  - Compares **line-by-line**:
    - Same count of line items?
    - Each line: amount match (1-cent tolerance), GL account match, service period match, description similarity
    - If line-level mismatch even when totals agree → flag as `line_drift` (catches GL-swap-type bugs like the 8-vector bug in memory)
  - Writes result back to S7 JSONL:
    - Header: `verified_at`, `verified_status` (match / total_mismatch / line_count_mismatch / line_drift / gl_swap_detected / missing / api_error)
    - Per-line: `verified_amount`, `verified_gl`, `line_diff` (what specifically doesn't match)
  - Also writes to `jrk-bill-pipeline-tracker` as `POST_VERIFIED` or `POST_VERIFY_MISMATCH` event with full diff payload
- **Alerting tiers:**
  - Silent match → green check, no alert
  - Total or line-count mismatch → P0 SNS alert (money moved wrong)
  - GL drift on a line (e.g., water bill line went to sewer GL) → P0 SNS alert (classification wrong, downstream billback/reporting broken)
  - API error (Entrata unreachable) → retry, no alert until 3 attempts
  - Missing in Entrata after 60 min → P1 alert (did we actually post?)
- **UI surface:** /post and /track pages show:
  - Green check: fully verified (header + all lines match)
  - Yellow warn: verified but one or more lines drifted (e.g., GL swap) — deep-link to line diff
  - Red X: missing or total mismatch — deep-link to Entrata
- **Why line-level matters:** a successful invoice total can hide a GL swap (e.g., electric charge posted to water GL, water to sewer — totals match, downstream billback wrong). This is the exact class of bug the 8-vector GL swap fix addressed last month. Line-level verify is how we catch it silently going forward.
- **Retry:** Entrata API unreachable → try again next cycle; cap retries at 3 then flag as "could not verify"
- **Verification timeout:** if not verified within 60 min, flag as "verify_timeout" for manual review
- **Infra needed:** new Lambda + IAM role + EventBridge rule + SNS topic (all require explicit user approval per CLAUDE.md infrastructure rules)
- **Dependency:** need to document Entrata's API for fetching invoice-with-line-items (separate spike before implementation)
- Update ISSUE-045 → planned-fix (requires infra approval + Entrata API spike)

**[Q-25]** **Entrata response parsing (ISSUE-044):** Want me to build a canary monitor that posts a test invoice daily + asserts expected response shape + alerts on drift? Low effort, high value.

**✅ ANSWERED (2026-04-17):** Yes — build the canary.

**Open sub-question:** Does Entrata have a sandbox / test environment we can post to? Or do we need to set up a "canary property" in production for test invoices? Flagged as prerequisite spike before implementation.

**Fix plan (planned-fix, depends on Entrata sandbox answer):**
- **New Lambda: `jrk-entrata-canary`** — runs daily at 3am via EventBridge
  - Posts a known test invoice (fixed vendor/property/amount, unique per run via timestamp in suffix) to Entrata
  - Captures full response (HTTP status + body)
  - Runs `_entrata_post_succeeded` on the response and asserts `(True, "ok"|"success"|"created"|"accepted"|"processed")`
  - Also asserts response structure baseline: top-level keys, status field location, message field location (compared against a stored schema in config)
  - On any drift: P0 SNS alert to `cbeach@jrk.com` with diff
- **Response schema baseline:** stored in DDB (`jrk-bill-config` PK=`ENTRATA_RESPONSE_SCHEMA`) so it's versioned and easy to update when Entrata legitimately evolves
- **Cleanup:** canary invoices need a way to be either (a) posted against a test/sandbox that doesn't affect real books, OR (b) auto-voided/deleted after the test via Entrata's API
- **Why both sandbox preferred:** production canary invoices would need manual accounting cleanup; sandbox avoids that
- **Infra needed:** new Lambda + IAM + EventBridge + SNS + DDB schema record (all require explicit user approval per CLAUDE.md)
- Update ISSUE-044 → planned-fix (blocked on sandbox availability spike)

**[Q-26]** **Duplicate disambiguation (ISSUE-054):** When Entrata says "duplicate", we need to know: is it a duplicate because the user's current post attempt went through and this is their second click, OR because the invoice was already posted on a previous day? Currently conflated. Fix requires a query-back-to-Entrata step. Build this?

**✅ ANSWERED (2026-04-17):** Yes — build the disambiguation flow.

**Fix plan (planned-fix, bundled with Q-24 Entrata query code):**
- **New helper: `_entrata_fetch_invoice(account_number, bill_date)`** — shared by duplicate-disambiguation flow AND post-verifier Lambda (Q-24)
  - Queries Entrata by account + bill date (natural uniqueness key)
  - Returns: `{exists, posted_by, posted_at, total, line_items, invoice_id}` or `None`
- **In `api_post_to_entrata`**, when response is `reason="duplicate"`:
  1. Fetch what Entrata has for (account, bill_date)
  2. If NOT found: this is a paradox (Entrata said duplicate but we can't find it) → flag as `entrata_inconsistent`, alert admin
  3. If found AND totals match AND posted_at is recent (last 10 min): classify as `same_attempt_succeeded` → mark local lock as POSTED, move S6→S7, show user "already posted (your earlier attempt)" instead of repost prompt
  4. If found AND totals differ OR posted_at is old: classify as `genuine_duplicate` → show enhanced repost modal with:
     - Existing invoice details (amount, post date, who posted)
     - Our intended amount
     - Explicit confirmation: "You are about to create a SECOND invoice with a different amount. Is this intentional?"
     - Require user to type reason (for audit)
- **Response schema additions:**
  - `err_entry.disambiguated = "same_attempt_succeeded" | "genuine_duplicate" | "entrata_inconsistent"`
  - For genuine duplicate: include `existing_invoice = {total, posted_at, posted_by, invoice_id}`
- **Client-side UI (post.html):**
  - "same_attempt_succeeded" → green banner, auto-refresh to show bill moved to S7
  - "genuine_duplicate" → enhanced modal with comparison table + required reason field
  - "entrata_inconsistent" → red alert, "contact admin — Entrata state is ambiguous"
- **Audit trail:** every `same_attempt_succeeded` and `genuine_duplicate` logged to audit table (when ISSUE-017 lands)
- Update ISSUE-054 → planned-fix; explicitly shares the Entrata-fetch helper with Q-24 verifier Lambda

**[Q-27]** **Async post (ISSUE-046):** Apply the async job-queue pattern (per our Q-14 preference) to `/api/post_to_entrata` for bulk posts? High-value since posting is THE critical path. Agree?

**✅ ANSWERED (2026-04-17):** Yes — async job-queue for `/api/post_to_entrata`.

**Fix plan (1-2 days focused work):**
- **Use the canonical jobs subsystem** (being built per `feedback_async_job_queue_pattern.md` memory) — new DDB `jrk-bill-jobs` table (or reuse `jrk-bill-config` with PK=`JOB#{id}`)
- **Endpoint refactor** (main.py `api_post_to_entrata`):
  - Remove inline loop; create JOB record with `{job_id, user, keys, total=len(keys), completed=0, failed=0, status="queued"}`; return 202 with `{job_id, poll_url}`
  - Spawn background worker (`threading.Thread(target=_post_job_worker, args=(job_id, ...))`)
- **Worker** (`_post_job_worker`):
  - Processes keys one-by-one, acquires lock per key (heartbeat-enabled per Q-21), posts, releases lock
  - After each key: update JOB record (increment `completed` or `failed`, append result to `results` list)
  - On completion: set JOB status=`done` (or `partial` if failures); write final summary
- **Polling endpoint** (`GET /api/jobs/{job_id}`):
  - Returns full JOB record including per-key status
  - Client polls every 2-3s
- **Client UI** (post.html):
  - Replace "POST" button with "POST (async)" that immediately shows progress bar + "5 of 50 posted" counter
  - Show per-bill status as results arrive
  - Allow cancel (marks JOB cancelled; worker checks flag between keys)
  - Final results surface in normal errors/results lists once JOB completes
- **Observability:** CloudWatch metric `PostJobDuration`, alert if p95 > 30 min or if job sits in `queued` > 60s
- **Failure handling:** if worker thread dies (instance killed), the JOB record stays in `in_progress`. A reaper Lambda (or 5-min cron in-app) marks stale jobs as `failed` with note "worker died, please retry"
- Update ISSUE-046 → planned-fix; biggest chunk of Module 3 work

**[Q-28]** **Blocking vs informational warnings (ISSUE-047):** Want to categorize validation warnings into "blocking" (must be resolved before post) and "informational" (shown, but can proceed)? Example split: "vendor not in Entrata" = blocking; "vendor first time at this property" = informational. If yes, I need a rule set from you.

**✅ ANSWERED (2026-04-17):** Yes, 3-tier split. **Only rework reposts require supervisor approval.** Everything else either blocks or is informational.

**Fix plan — warning taxonomy:**

**Blocking (post refused; must fix before proceeding):**
- Missing `EnrichedVendorID` / `EnrichedPropertyID` / `EnrichedGLAccountNumber`
- Vendor disabled/deleted in dim_vendor dimension table
- Property disabled/deleted in dim_property
- GL account doesn't exist in dim_gl_account
- Account number doesn't match any vendor+property combo in dimension data
- (others surface during Module 3 deep-dive — add as found)

**Informational (shown, can proceed with single click):**
- Vendor-property pair never seen in past year (current warning)
- Vendor-GL pair never seen in past year (current warning)
- Amount significantly higher than historical average for this account (anomaly detection)
- First time posting for this new vendor
- Amount > some threshold (e.g., $10,000) — **informational only per user, not supervisor-gated**

**Supervisor-override required (shown, requires admin role to click "approve"):**
- **Rework reposts only** — invoice was previously flagged for rework and is now being re-posted. The supervisor check is the integrity gate: was the rework justified? Is the corrected data right?
- (Detection: S3 key contains `_REWORK_` in the filename, or a `RewornFrom` field is present on any row, or the bill has a rework history entry in DDB)

**Implementation:**
- Server `/api/post/validate` returns `{warnings: [...]}` where each warning has `{level: "blocking" | "info" | "supervisor", type, message, details}`
- Client renders in 3 sections:
  - Blocking: red; "Fix these to post" with a "Cancel" button only
  - Informational: yellow; single "Post anyway" button
  - Supervisor: purple; "Awaiting supervisor approval" — if current user is admin, shows "Approve & Post" button; otherwise "Request approval" button that notifies supervisor
- Server `/api/post_to_entrata` re-validates and refuses if any blocking warnings remain; refuses if supervisor warnings exist and user isn't admin
- Audit log entry for supervisor approvals (who approved, what bill, original flag, timestamp)
- Update ISSUE-047 → planned-fix

**[Q-29]** **Archive failure handling (ISSUE-043):** Currently PDF archive failures silently log-and-continue. Want to surface them in UI (warning banner) + queue for retry?

**✅ ANSWERED (2026-04-17):** Option B — post then queue retry.

**Fix plan:**
- **New DDB table or PK in jrk-bill-config:** `ARCHIVE_RETRY_QUEUE` — pending-retry list with `{s3_key, pdf_source, attempt_count, last_error, first_failed_at}`
- **On archive failure** (main.py:2932+):
  - Add entry to retry queue
  - Include archive warning in POST response: `{archive_warnings: [{key, message}]}`
  - UI shows a small yellow badge on posted bills with archive failures
- **New background worker** (in-app thread or scheduled Lambda):
  - Every 5 minutes, reads retry queue
  - For each entry: attempts archive; on success removes from queue; on failure increments attempt_count
  - After 10 failed attempts: moves to permanent "archive_failed" list, alerts admin
- **Admin UI:** dashboard showing pending retries, permanent failures, manual "retry now" and "give up" buttons
- **CloudWatch metric:** `PDFArchiveRetryQueueDepth` — alert if > 10
- Update ISSUE-043 → planned-fix

**[Q-30]** **The `do_post` function + Entrata API details:** I didn't get to the Entrata API code in this pass. Is there documentation on how `do_post` works, Entrata credentials, retry logic, auth scheme? I may need to dive in later.

**✅ ANSWERED (2026-04-17):** User didn't know; deep-dived.

### Entrata API — deep-dive findings

**Endpoint:**
- Base: `https://apis.entrata.com/ext/orgs`
- Org: `jrkpropertyholdingsentratacore` (JRK's "Core" tenant)
- Full URL: `{base}/{org}/v1/vendors`
- Despite the `/vendors` path, the payload's `method.name = "sendInvoices"` dispatches to Entrata's invoice-creation handler. So this URL is a multi-method endpoint.

**Auth scheme:**
- Header: `X-Api-Key: <uuid>`
- Payload also has `"auth": {"type": "apikey"}` (belt-and-suspenders declaration)

**Payload shape:**
```json
{
  "auth": {"type": "apikey"},
  "requestId": "YYYYMMDDHHMMSS",
  "method": {
    "name": "sendInvoices",
    "version": "r1",
    "params": {
      "ApBatch": {
        "IsPaused": "0",
        "IsPosted": "0",
        "ApHeaders": {"ApHeader": [/* list of invoice header dicts */]}
      }
    }
  }
}
```

**Invoice header fields include:**
- `InvoiceNumber`, `InvoiceDate`, `VendorId`, `LocationId`, `PostMonth`, `Total`
- `Files.File.FileData` = base64 PDF; `Files.File.@attributes.FileName` = filename
- Optional `InvoiceImageUrl` = web-accessible URL
- Line items are in a sub-array (need to verify field names in next pass)

**Retry semantics in `do_post` (entrata_send_invoices_prototype.py:354-391):**
- `max_retries = 2`, `base_timeout = (10, 30)` (10s connect, 30s read)
- **Retry ONLY on `ConnectionError`** (TCP never connected → safe to retry, request wasn't received)
- **No retry on `Timeout`** (request sent but no response → retry would risk duplicate). Returns `"Timeout after 30s — verify in Entrata before retrying"`
- ConnectionError: exponential backoff (2^attempt), max 2 attempts
- Other exceptions: immediate failure
- Comment in code: _"Do NOT retry on Timeout — the request was sent and Entrata may have processed it"_ — suggests this was an incident lesson

**Known flaky behavior (from code tells):**
- Entrata returns **HTTP 200 even for errors and duplicates** (which is why `_entrata_post_succeeded` exists as a heuristic parser)
- Timeouts are evidently common enough to have dedicated code paths
- No rate-limit handling in `do_post` — not documented in code
- No 5xx retry logic

**Entrata integrations catalog (3 distinct):**
| Integration | Secret | Used by | Purpose |
|---|---|---|---|
| **Entrata Core — sendInvoices** | ❌ **HARDCODED** in `entrata_send_invoices_prototype.py:23` | main.py's `api_post_to_entrata` via `do_post()` | Posting invoices (this module) |
| Entrata Core — other | `jrk/entrata_core` Secrets Manager (has `org`, `base_url`, `api_key`) | `vendor-cache-builder` Lambda | Vendor cache refresh |
| Entrata AR (Accounts Receivable) | `jrk/entrata-ar-api-key` Secrets Manager | `EntrataARClient` in `vacant_electric/entrata_ar.py` | AR posting for resident billback |

### 🚨 SECURITY FINDING: Entrata Core API key still hardcoded → **[ISSUE-055]** (P0)

`entrata_send_invoices_prototype.py:23` contains a hardcoded UUID-format Entrata API key (commit history shows it was there before commit `9071511` "Remove hardcoded API keys from source code" — so it was **missed during that security fix**). The file is deployed to production per `deploy_app.ps1:24`.

**Impact:**
- Anyone with git read access to this repo can post to Entrata as JRK (create invoices, move money)
- The key is in git history — even rotating now leaves the old key exposed in all historical commits
- Related CODE_AUDIT item (archived) flagged `_ve_ar_client` hardcoded key as L4 low-priority, but THAT one is now in Secrets Manager. This Core one was missed.

**Fix plan:**
1. **Rotate the key at Entrata** (contact them to invalidate current key + get new one). This MUST happen first; nothing else matters until the current key is burned.
2. Store new key in Secrets Manager (`jrk/entrata_core` already exists; can extend or add a new secret `jrk/entrata_send_invoices`)
3. Replace hardcoded `ENTRATA_API_KEY_ENV = "..."` with `_get_api_key()` that reads from Secrets Manager (mirror the `EntrataARClient` pattern at main.py:535)
4. Remove `ENTRATA_BASE_URL` / `ENTRATA_ORG` from source; read from the secret or env too
5. Delete the hardcoded test file paths (DEFAULT_TEST_JSONL, DEFAULT_VENDOR_CACHE) — these are Craig's local paths that shouldn't ship in prod
6. Audit: when was the hardcoded key added? Who has had access to this repo since? Consider whether other credentials should be rotated preemptively.

### Sandbox / test environment: ❌ NONE DETECTED

No code references to `sandbox.entrata.com`, no `ENTRATA_SANDBOX_*` env vars, no test-org configuration. The only "fake" mode is `dry_run=True` in `do_post` which returns the payload JSON without sending.

**Implication for Q-25 canary:** we need to either
- Ask Entrata support if they offer a sandbox / test org
- Set up a dedicated "canary property" in production Entrata with a test vendor, manually reconciled
- Or: run the canary as dry-run only (tests payload build + response parsing is semi-stable, but doesn't test the actual API)

### Update to review plan

Given the Entrata API has significant fragility + gaps, I recommend adding a discrete "Entrata Integration" mini-module doc (`modules/03b_entrata_integration.md`) in a follow-up session that covers:
- All 3 Entrata integrations (Core sendInvoices, Core other, AR)
- Common auth/retry patterns across them
- Where Entrata quirks live in the codebase
- Recommended consolidation into an `entrata_client.py` module

Current doc stays focused on the /post workflow; that new doc would focus on the API adapter layer.

---

## 12. Dead / Unused Code

- `passlib_bcrypt` import (main.py:72) — comment says "legacy; avoid calling due to backend issues". Delete candidate.
- `_entrata_post_succeeded` before imports — not dead but misplaced (ISSUE-049).
- `advance_to_post_stage` + `post_to_entrata` share S6→S7 logic — DRY candidate (ISSUE-048).

---

## 13. SSO Migration Concerns

Identity touchpoints in Module 3:

| Touchpoint | Current | Post-SSO concern |
|---|---|---|
| `/api/post/validate` | session user (unused in body) | capability `post:validate` |
| `/api/post_to_entrata` | session user → written as `PostedBy` field | **Critical** — `PostedBy` is a core audit field; must survive SSO migration. SSO claim's email → PostedBy. |
| `/api/clear_post_locks` | session user (no role check today) | Add `admin:clear_locks` capability |
| `/api/verify_entrata_sync` | session user (unused) | capability `post:verify_sync` or admin |
| `/api/advance_to_post_stage` | session user → `PostedBy` | Same as post_to_entrata |
| Post lock `locked_by` field | session user email | SSO email claim |

**PostedBy is load-bearing for audit and metrics.** If SSO migration silently changes the format (e.g., includes full name, changes email case), downstream queries break. Test explicitly.

---

## 14. Service-Account Concerns

Automated callers that could interact with Module 3:

| Caller | Notes |
|---|---|
| Smoke test | Probably doesn't actually post — needs verification |
| Automated sync verification (future) | If we build auto-verify (Q-24), a Lambda needs to call `/api/verify_entrata_sync` with Entrata data — needs bearer token + capability `post:verify_sync` |
| Retry-failed-posts background worker (future) | Would need `post:retry` capability |

**None today.** All posting is human-initiated.

---

## Observations for Current-State Synthesis

1. **This module is the strongest argument for the "clunkiness → systemic risk" framing.** It has working-but-fragile safeguards (lock, nonce, response parsing) that were clearly added after incidents (the fail-open comment reads like a postmortem). But the next incident (e.g., DDB outage + 2 users clicking Post) is lurking.

2. **Money-movement modules should follow these principles (none fully met here):**
   - Fail-closed, not fail-open
   - Idempotency keys on external API calls
   - Verify after mutating external state
   - Explicit "uncertain" states (neither posted nor failed) with human review
   - Immutable audit log of every mutation + external call

3. **Code organization is a source of risk:** `_entrata_post_succeeded` at line 1 is a symptom — the money-path code is scattered, hard to review as a cohesive unit, hard to reason about.

4. **Recovery from partial failures is the weakest area.** The flow has ~7 sequential steps (lock → read → build → post → parse → archive → move); any of them can fail. Current error handling is "log + continue to next key". No real transaction semantics.

5. **Manual operations (force-clear, sync verification) should be auditable.** Currently only `print()` → CloudWatch. When ISSUE-017 audit log lands, these MUST be included.

---

## References

- `../02_endpoint_inventory.md` — Post module endpoint list
- `../03_module_taxonomy.md` — Module 4 block (note: numbered as Module 4 in taxonomy but reviewing as Module 3 in our review sequence)
- `../04_data_architecture.md` — DDB POST_LOCK table, S3 S6/S7 prefixes
- `../ISSUES.md` — ISSUE-037 through ISSUE-054
- `./01_auth.md` — ADMIN_USERS discussion (relevant to ISSUE-041 admin-gate)
