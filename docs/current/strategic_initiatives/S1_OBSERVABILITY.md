# S1: Observability & Transaction Visibility

## Problem
Users report bills "disappearing" and there's no way to trace a bill's journey through the pipeline. No unified view of where a bill is, where it's been, and what happened to it.

## Objective
Complete visibility into every bill's lifecycle — from email/scan intake through parsing, enrichment, review, posting, and UBI assignment. Every state transition logged, queryable, and surfaced in the UI.

## Task Breakdown

### Phase 1: Pipeline Event Log (foundation)
- [x] **1.1** Using existing `jrk-bill-pipeline-tracker` table (PK: `BILL#{hash}`, SK: `EVENT#{timestamp}`)
- [x] **1.2** Schema defined: `{pk, sk, event_type, stage, source, s3_key, timestamp_epoch, filename, metadata, ttl}`
- [ ] **1.3** Add event emission to email ingest Lambda (RECEIVED, CLASSIFIED)
- [ ] **1.4** Add event emission to bill router Lambda (ROUTED_STANDARD, ROUTED_LARGE)
- [ ] **1.5** Add event emission to parser Lambda (PARSE_STARTED, PARSE_COMPLETED, PARSE_FAILED)
- [ ] **1.6** Add event emission to enricher Lambda (ENRICHED, ENRICHMENT_FAILED)
- [x] **1.7** Add event emission to main.py submit flow (SUBMITTED, POSTED, POST_FAILED, ADVANCED_TO_POST)
- [x] **1.8** Add event emission to UBI assign/unassign (UBI_ASSIGNED, UBI_UNASSIGNED)
- [x] **1.9** Add event emission to rework/delete flows (REWORKED, DELETED, ARCHIVED)

### Phase 2: Bill Lifecycle UI
- [x] **2.1** Create `/bill/{pdf_id}/timeline` page showing full event history (dark Mission Control theme)
- [x] **2.2** Add "View Timeline" button to review page header + search results + home page tile
- [x] **2.3** Create `/api/bill/{pdf_id}/events` endpoint querying the events table
- [x] **2.4** Add visual stage progression indicator (connected pipeline nodes with glow effects)

### Phase 3: Transaction Dashboard
- [x] **3.1** Create `/transactions` page with real-time pipeline flow visualization
- [x] **3.2** Show bills in-flight per stage with counts and age
- [ ] **3.3** Alert on bills stuck > threshold (integrate with existing `/api/pipeline/stuck`)
- [x] **3.4** Daily transaction summary: received, parsed, posted, failed — by user and by hour

### Phase 4: Alerting
- [ ] **4.1** SNS topic for critical events (parse failures, stuck bills, duplicate posts)
- [ ] **4.2** Slack/email integration for alerts
- [ ] **4.3** Anomaly detection: sudden drop in parsing volume, spike in failures

## Dependencies
- Existing pipeline tracker table (`jrk-bill-pipeline-events`) partially covers this but lacks coverage for email ingest, UBI flows, and user actions
- S2 (Bill Disappearance) depends on Phase 1 being complete

## Success Criteria
- Any bill can be located within 30 seconds by pdf_id, account number, or date range
- Every state transition is logged with timestamp, actor, and before/after state
- Dashboard shows real-time pipeline health at a glance

---

## Known architecture issue: per-stage `pdf_id` fragmentation (2026-05-05)

### The problem
Every Lambda that writes to `jrk-bill-pipeline-tracker` computes
`pdf_id = SHA1(its_current_s3_key)` and uses that as the table's PK
(`BILL#{pdf_id}`). When a bill moves between S3 prefixes — Pending →
Standard → Parsed_Outputs (jsonl) → Enriched_Outputs (jsonl) →
PreEntrata_Submission (jsonl) — its s3_key changes, so its `pdf_id`
changes. **One logical bill produces 4-6 different pks**, each with
its own disconnected event history that ends abruptly when the file
moves stages.

This silently broke every consumer of the tracker that aggregates
per-bill:

- The "X bills stuck >60 min" banner on `/pipeline-tracker` showed
  ~108-2,800 bills stuck at any time, but most were the same logical
  bills counted multiple times. Verified by tracing one
  `Inv_54668_from_National_WiFi` bill: **4 pks, 6 events, end-to-end
  completion in 4 hours** — yet showed up as 3-4 separate "stuck"
  entries.
- Each pk's "latest event" is whatever happened in that stage before
  the file moved on. The verification query "is the bill actually
  still in this stage?" returns true (the latest event in this pk
  IS in this stage), so the bill stays flagged as stuck forever even
  after successful end-to-end processing.
- Real stuck-bill count was inflated **~5-10x** for months.

### The two fixes

**Quick fix (shipped 2026-05-05, commits `d75f9cd`, `4ee5308`,
`5e73e96`):** Group events by **filename stem** (basename minus
extension) on the read side, since `filename` is preserved across all
stage tracker writes (only the path changes). Helper:
`_canonical_bill_key(filename, s3_key)` in `main.py` strips the
trailing extension and disambiguates chunk events
(`chunk_NNN.pdf`) by S3 parent path. The "stuck" metric now walks
**all** stages in the time window, takes the LATEST event per
filename across all pks/stages, and only flags pre-submission
filenames whose newest-anywhere event is in `{S1, S1_Std, S1_Lg,
S1_largefile, S3}`. Chunks whose parent job (looked up in
`jrk-bill-parser-jobs`) is `completed`/`failed` are also dropped
since they're historical artifacts of finished jobs, not live work.

Required IAM addition: `jrk-bill-review-instance-role` needs
`dynamodb:GetItem`/`BatchGetItem`/`Query` on `jrk-bill-parser-jobs`
(added as inline policy `ParserJobsRead` 2026-05-05). After the IAM
change, force an AppRunner redeploy to refresh the instance
credential cache (otherwise the new permission doesn't take effect
until normal boto3 credential refresh, ~1 hour).

Result: 48h stuck count dropped from **562 → 2** unique bills.

**Proper fix (NOT yet implemented — TODO):** Pick a single canonical
`pdf_id` (probably `SHA1` of the original Pending key) and thread it
through every Lambda's tracker writes. The Pending key is the only
key guaranteed to exist for every bill at every stage in some form
(via metadata or sidecar). Once all Lambdas use this pdf_id, the
tracker becomes one continuous lineage per logical bill, and the
filename-grouping workaround can be removed. Estimated scope: every
Lambda that writes to the tracker (parser, large-parser,
chunk-processor, aggregator, enricher, rework_handler) needs the
pdf_id propagated via S3 metadata or sidecar JSON.

### Endpoints affected
- `GET /api/pipeline/stuck-count` — paginated `Select=COUNT` per stage,
  cheap, drives the banner number on every poll
- `GET /api/pipeline/stuck` — the verified, displayable list (lazy-
  loaded when user expands the banner)
- `GET /api/pipeline/stuck-legacy` — old per-pk logic preserved for
  diagnostic comparison (returns inflated count). Will be removed once
  the proper fix lands.

### Cross-references
- `docs/current_state/ISSUES.md` — issue entry
- `memory/feedback_pipeline_tracker_pdf_id_fragmentation.md` (in
  Claude memory) — terse rule for future sessions
