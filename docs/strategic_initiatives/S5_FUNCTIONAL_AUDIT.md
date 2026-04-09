# S5: Functional Validation Audit

## Problem
No systematic verification that every feature works as intended. Bugs accumulate because there's no automated check that "clicking Submit actually submits" or "assigning a UBI period actually moves the file."

## Objective
Every user-facing feature has a documented expected behavior and a test that verifies it. The audit produces a feature map with pass/fail status.

## Task Breakdown

### Phase 1: Feature Inventory
- [ ] **1.1** Enumerate all user-facing pages and their functions
- [ ] **1.2** Enumerate all API endpoints with expected behavior
- [ ] **1.3** Map data flows: which endpoints read/write which S3 keys and DDB tables
- [ ] **1.4** Document expected behavior for each workflow (happy path + error cases)

### Phase 2: Code vs. Intent Audit
- [ ] **2.1** PARSE module: verify date filtering, bulk ops, submit flow
- [ ] **2.2** REVIEW module: verify draft save, GL mapping, line add/delete, submit
- [ ] **2.3** POST module: verify Entrata posting, lock mechanism, status tracking
- [ ] **2.4** BILLBACK module: verify UBI assignment, unassign, reassign, suggestions
- [ ] **2.5** MASTER BILLS module: verify generation, manual upload, Snowflake export
- [ ] **2.6** CHECK SLIPS module: verify creation, PDF merge, approval/rejection
- [ ] **2.7** COMPLETION TRACKER: verify bill index, skip reasons, account management
- [ ] **2.8** WORKFLOW module: verify directed plan, task assignment, notes
- [ ] **2.9** CONFIG modules: verify GL mapping, UOM mapping, portfolio, account manager
- [ ] **2.10** SEARCH: verify search index, cross-stage search

### Phase 3: Edge Case Audit
- [ ] **3.1** Multi-account bills (split flow)
- [ ] **3.2** Bills with special characters in vendor/property names
- [ ] **3.3** Concurrent operations (2 users editing same bill)
- [ ] **3.4** Large bills (50+ line items)
- [ ] **3.5** Bills spanning multiple months (quarterly, annual)
- [ ] **3.6** Rework flow (send back to parser, re-parse, re-submit)

### Phase 4: Documentation
- [ ] **4.1** Create feature matrix with test status
- [ ] **4.2** Document all known limitations and workarounds
- [ ] **4.3** Create user-facing FAQ for common issues

## Dependencies
- S6 (Automated Testing) builds on the feature inventory from Phase 1

## Success Criteria
- Every feature has documented expected behavior
- Every code path is traced from UI click to data persistence
- All discrepancies between intent and implementation are logged as bugs
