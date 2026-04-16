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
