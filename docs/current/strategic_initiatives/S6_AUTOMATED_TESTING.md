# S6: Automated Testing & Performance Benchmarks

## Problem
No automated tests verify that the app works end-to-end. No performance baselines. When something breaks, it's discovered by users, not tests.

## Objective
Automated test suite that simulates real user workflows, measures page load times, validates data integrity, and runs on every deploy. Claude-driven simulations that act as synthetic users.

## Task Breakdown

### Phase 1: Test Infrastructure
- [ ] **1.1** Set up pytest + httpx test client for FastAPI integration tests
- [ ] **1.2** Create test fixtures: sample PDFs, JSONL files, DDB records
- [ ] **1.3** Set up localstack or mock AWS services for isolated testing
- [ ] **1.4** Add test stage to CodeBuild buildspec (run tests before deploy)
- [ ] **1.5** Create test user account for automated testing

### Phase 2: API Integration Tests
- [ ] **2.1** Auth flow: login, session, logout, expired session
- [ ] **2.2** PARSE flow: list dates, load day, submit overrides
- [ ] **2.3** POST flow: advance to post, post to Entrata (mock), verify status
- [ ] **2.4** BILLBACK flow: load unassigned, assign period, verify Stage 8
- [ ] **2.5** MASTER BILLS flow: generate, override amount, export
- [ ] **2.6** Search: index build, search by account/vendor/property
- [ ] **2.7** Config endpoints: GL mapping CRUD, account manager CRUD

### Phase 3: Performance Benchmarks
- [ ] **3.1** Define SLOs: page loads < 2s, API calls < 500ms, pipeline < 5min
- [ ] **3.2** Build performance test harness: measure P50/P95/P99 for key endpoints
- [ ] **3.3** Load test: simulate 10 concurrent users doing typical workflows
- [ ] **3.4** Identify and document performance bottlenecks
- [ ] **3.5** Add performance regression detection to CI (fail if P95 regresses > 20%)

### Phase 4: Synthetic User Simulations
- [ ] **4.1** Build Claude-driven test agent: walks through full review workflow
- [ ] **4.2** Simulation: scan bill -> wait for parse -> review -> submit -> verify posted
- [ ] **4.3** Simulation: assign UBI period -> verify master bill generation
- [ ] **4.4** Simulation: rework bill -> re-parse -> re-submit
- [ ] **4.5** Schedule simulations to run daily, report results

### Phase 5: Regression Suite
- [ ] **5.1** Snapshot tests for API response shapes
- [ ] **5.2** Golden file tests for enrichment output
- [ ] **5.3** GL mapping regression tests (known input -> expected GL)
- [ ] **5.4** Date parsing regression tests (all known date formats)

## Dependencies
- S5 (Functional Audit) provides the feature map that tests must cover
- S7 (CloudFormation) enables isolated test environments

## Success Criteria
- 80%+ API endpoint coverage with integration tests
- Performance baselines established for all key user workflows
- Tests run automatically on every push to main
- No deploy goes out without passing tests
