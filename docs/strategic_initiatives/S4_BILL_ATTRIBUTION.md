# S4: Bill Attribution & User Tracking

## Problem
Users can't see which bills they scanned. When a bill fails or gets stuck, there's no way to know who submitted it. The team wants to know "which bill did I just scan in?"

## Objective
Every bill is attributed to the user who submitted it. Users can see their own bills and their status. Two intake paths (email and web upload) both capture user identity.

## Architecture

### Email Path
```
dgonzalez@jrkanalytics.com -> SES -> Lambda (email ingest)
  -> Extract sender -> Map to user -> Tag S3 object metadata: submitted_by=dgonzalez
```

### Web Upload Path
```
User clicks "Upload" on INPUT tab -> Presigned URL Lambda
  -> Include user in S3 metadata: submitted_by={authenticated_user}
```

## Task Breakdown

### Phase 1: Email-Based Attribution
- [x] **1.1** Audit email ingest Lambda — sender extraction implemented
- [ ] **1.2** Build sender -> user mapping (SES verified identities or config table)
- [ ] **1.3** Create per-user email endpoints: `{username}@jrkanalytics.com` routes to same SES inbox
- [x] **1.4** Tag S3 objects with `submitted_by` metadata on email ingest
- [ ] **1.5** Propagate `submitted_by` through router -> parser -> enricher chain

### Phase 2: Web Upload Attribution
- [ ] **2.1** Update presigned upload Lambda to accept `submitted_by` parameter
- [ ] **2.2** Update INPUT tab upload flow to pass authenticated user
- [ ] **2.3** Tag uploaded S3 objects with `submitted_by` metadata
- [ ] **2.4** Ensure `submitted_by` propagates through pipeline stages

### Phase 3: User Dashboard
- [ ] **3.1** Create "My Bills" page — shows all bills submitted by current user
- [ ] **3.2** Show status per bill: Parsing, Ready for Review, Posted, Failed
- [ ] **3.3** Real-time updates (poll or WebSocket) so user sees bill appear after scan
- [ ] **3.4** Add "submitted_by" column to existing invoice/review pages
- [ ] **3.5** Filter options: "Show only my bills" toggle on PARSE and REVIEW pages

### Phase 4: SES Configuration
- [ ] **4.1** Set up SES domain for jrkanalytics.com (or subdomain like scan.jrkanalytics.com)
- [ ] **4.2** Configure catch-all or per-user addresses
- [ ] **4.3** Route rules: `{username}@` -> same S3 bucket, tagged with username
- [ ] **4.4** Document user setup: "Email your bills to dgonzalez@scan.jrkanalytics.com"

## Dependencies
- S1 (Observability) event log captures attribution data
- S7 (CloudFormation) for SES infrastructure

## Success Criteria
- Every bill has a `submitted_by` field within 30 seconds of intake
- Users can see all their submitted bills on a "My Bills" page
- Users can email bills to their personal address and see them appear in the app
