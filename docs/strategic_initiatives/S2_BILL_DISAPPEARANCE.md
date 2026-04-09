# S2: Bill Disappearance Resolution

## Problem
Users report "bills I scanned in disappear." This is the #1 user complaint. Root causes identified from deep-dive audit (2026-04-09):

### Confirmed Root Causes (from code audit)
1. **[FIXED] Search only covered Stage 4** — bills vanished from search after submission. Fixed: search now indexes posted invoices from DDB.
2. **[FIXED] Submit deletes old S6 files BEFORE writing new one** — if write failed, bill vanished. Fixed: write-first-then-delete order.
3. **[FIXED] Delete parsed permanently destroys enrichment** — no archive. Fixed: copies to `Bill_Parser_Deleted_Archive/` before deleting.
4. **Email mirror failure is silent** — email ingest Lambda swallows S3 mirror errors (line 175). Bill archived but never enters pipeline. No alerting.
5. **[FIXED] Enricher crash leaves bill stuck in Stage 3** — added try/except + fallback copy of unenriched data to S4
6. **[FIXED] Partial chunk upload hangs large-file jobs forever** — job now marked FAILED on partial upload
7. **No dead-letter/timeout for stuck large-file jobs** — no mechanism to detect `chunks_completed < total_chunks` stalls.
8. **[FIXED] Aggregator trigger is a TODO** — chunk processor now directly invokes aggregator Lambda
9. **Rework deletes enrichment with no archive** — if re-parse fails, original enrichment is gone.
10. **No write verification before source deletion** — all stage-move operations trust _write_jsonl without head_object check.
11. **No user attribution** — can't tell who submitted which bill (blocks "My Bills" view).

## Objective
Zero bills lost. Every scanned bill either reaches its destination or surfaces a clear error explaining why not.

## Task Breakdown

### Phase 1: Diagnostic Deep Dive
- [ ] **1.1** Audit email ingest -> S1 (Pending) flow — verify every email attachment becomes an S3 object
- [ ] **1.2** Audit S1 -> Router flow — verify router moves ALL files, not just first
- [ ] **1.3** Audit Router -> Parser flow — verify parser picks up every routed file
- [ ] **1.4** Audit Parser -> S4 (Enriched) flow — verify failed parses are trackable
- [ ] **1.5** Audit S4 -> S6 (PreEntrata) submit flow — verify submit doesn't silently drop records
- [ ] **1.6** Audit S6 -> S7 (PostEntrata) post flow — verify post lock + Entrata response handling
- [ ] **1.7** Audit S7 -> S8 (UBI Assigned) flow — verify assignment doesn't lose files
- [ ] **1.8** Map ALL code paths where an S3 object is deleted/moved — ensure every delete has a corresponding copy
- [ ] **1.9** Audit duplicate detection — verify it doesn't false-positive on corrections/re-scans

### Phase 2: Safety Nets
- [ ] **2.1** Add "dead letter" stage (S99) for any bill that can't be processed — never delete, always move
- [ ] **2.2** Add reconciliation job: compare S3 object counts across stages, flag discrepancies
- [ ] **2.3** Add user notification on parse failure (in-app banner + optional email)
- [ ] **2.4** Add "My Scanned Bills" view — shows all bills attributed to current user with current status
- [ ] **2.5** Add S3 lifecycle rule to prevent accidental permanent deletion (versioning or soft-delete)

### Phase 3: Prevention
- [ ] **3.1** Implement copy-then-delete pattern everywhere (verify destination exists before deleting source)
- [ ] **3.2** Add idempotency keys to prevent duplicate processing
- [ ] **3.3** Add stage transition validation — a bill can only move forward, not skip stages
- [ ] **3.4** Add cross-instance consistency checks for in-memory caches

## Dependencies
- S1 (Observability) Phase 1 enables tracking where bills go
- S4 (Bill Attribution) enables "My Scanned Bills" view

## Success Criteria
- Zero user reports of "disappeared bills" for 30 consecutive days
- 100% of scanned bills are either processed or have a clear failure record
- Failed bills are visible in the UI within 5 minutes of failure
