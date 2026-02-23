# WORKFLOW Enhancements Design Document

## Executive Summary

This document outlines 5 major enhancements to the Bill Review WORKFLOW system:

1. **Vendor Correction Utility** - Fix incorrectly mapped accounts from November setup
2. **Account Gap Analysis** - Compare external account list against tracked accounts
3. **Smart UBI Allocation** - Auto-suggest UBI period based on service dates
4. **Account Removal Tool** - Admin-only removal of tracked accounts (especially vacant duplicates)
5. **Outlier Detection Tab** - Flag bills with unusual amounts using statistical analysis

---

## 1. Vendor Correction Utility

### Problem Statement
During November account setup, many accounts were mapped to incorrect vendors. The `accounts_to_track` config has ~1976 entries, some with wrong `vendorId`/`vendorName` combinations. We need to:
- Identify accounts added in November
- Find actual bills with matching account numbers
- Use Gemini AI to compare vendor names and suggest corrections
- Update both the historical bill data and the `accounts_to_track` config

### Data Model

**Current accounts_to_track entry:**
```json
{
  "vendorId": "705870",
  "vendorName": "SoCalGas",
  "accountNumber": "12452413011",
  "propertyId": "1296739",
  "propertyName": "Arbors at California Oaks",
  "glAccountNumber": "5710-0000",
  "glAccountName": "Gas",
  "daysBetweenBills": 30,
  "is_ubi": true,
  "created_at": "2025-11-15T10:30:00Z"  // Need to add this field
}
```

**Bill record fields for matching:**
```json
{
  "Account Number": "12452413011",
  "EnrichedPropertyID": "1296739",
  "EnrichedVendorID": "705870",
  "EnrichedVendorName": "SoCalGas",
  "Vendor Name": "Southern California Gas Company"  // Raw parsed name
}
```

### Algorithm

```
PHASE 1: Identify November Accounts
----------------------------------------
1. Load all accounts_to_track
2. Filter to accounts where:
   - created_at is in November 2025 (if field exists)
   - OR vendorId appears in a "suspect list" (user-provided)
   - OR flag all for review if no created_at metadata

PHASE 2: Find Matching Bills
----------------------------------------
For each suspect account (propertyId, accountNumber):
1. Scan Stage 4, 6, 7, Archive for bills where:
   - Account Number == account.accountNumber
   - EnrichedPropertyID == account.propertyId (or close match)
2. Collect unique (Vendor Name, EnrichedVendorID, EnrichedVendorName) tuples
3. If multiple vendors found for same account, flag as conflict

PHASE 3: Gemini AI Vendor Comparison
----------------------------------------
For each account with potential mismatch:
1. Prepare comparison prompt:
   - accounts_to_track vendor: "SoCalGas" (ID: 705870)
   - Bill vendor(s) found: "Southern California Gas Company" (ID: 705870)
   - Bill vendor(s) found: "SoCal Gas" (ID: 705871)

2. Ask Gemini:
   "Given these vendor names, which is the correct canonical vendor?
    Are these the same company or different companies?
    Return JSON: {same: bool, canonical_name: str, canonical_id: str, confidence: float}"

3. Store Gemini response with confidence score

PHASE 4: Apply Corrections
----------------------------------------
For corrections with confidence >= 0.9:
1. Update accounts_to_track entry with correct vendorId/vendorName
2. Mark old entry as "deprecated": true, "deprecated_date": "...", "replaced_by": "..."
3. Optionally: Update historical S3 files (EnrichedVendorID, EnrichedVendorName)
   - This is expensive; consider doing only for active stages (4, 6)

PHASE 5: Deprecate Old Accounts
----------------------------------------
1. Don't delete old accounts - mark as deprecated
2. Add "deprecated": true field
3. Add "deprecation_reason": "vendor_correction"
4. Add "correct_account_key": "(propertyId, vendorId, accountNumber)"
5. WORKFLOW will filter out deprecated accounts
```

### API Endpoints

```
GET /api/admin/vendor-corrections
  - Returns list of accounts needing review
  - Params: ?status=pending|reviewed|applied

POST /api/admin/vendor-corrections/analyze
  - Triggers Gemini analysis for a batch of accounts
  - Body: {account_keys: ["prop|vend|acct", ...]}
  - Returns: {results: [{key, suggestions, confidence}, ...]}

POST /api/admin/vendor-corrections/apply
  - Applies approved corrections
  - Body: {corrections: [{old_key, new_vendor_id, new_vendor_name}, ...]}
  - Returns: {applied: int, failed: int, errors: [...]}

GET /api/admin/vendor-corrections/preview
  - Preview what a correction would change
  - Params: ?old_key=...&new_vendor_id=...
  - Returns: {affected_bills: int, affected_stages: [...]}
```

### UI Component

**Location:** `/admin/vendor-corrections` (System_Admins only)

**Layout:**
```
+----------------------------------------------------------+
| VENDOR CORRECTION UTILITY                    [Run Analysis]|
+----------------------------------------------------------+
| Filter: [All] [Pending] [High Confidence] [Needs Review]  |
+----------------------------------------------------------+
| Account        | Current Vendor | Suggested | Confidence  |
|----------------|----------------|-----------|-------------|
| 1296739|12345  | WM (710432)   | Waste Mgmt| 95%  [Apply]|
| 1296740|67890  | SCE (704990)  | SoCalEdison| 88% [Review]|
+----------------------------------------------------------+
```

### Risk Mitigation
- **Backup before changes**: Create S3 snapshot of accounts_to_track before any modifications
- **Audit trail**: Log all changes with timestamp, user, before/after values
- **Dry run mode**: Preview changes before applying
- **Confidence threshold**: Only auto-apply >= 95% confidence; manual review for lower

---

## 2. Account Gap Analysis

### Problem Statement
User has an external list of ~1400 accounts that should be tracked. Need to compare against existing ~1976 accounts to find:
- Accounts in external list but NOT in accounts_to_track (missing)
- Accounts in accounts_to_track but NOT in external list (orphaned?)

### Input Format

User provides CSV/JSON with:
```
propertyId (or propertyName), vendorName, accountNumber
```

### Algorithm

```
PHASE 1: Normalize Input
----------------------------------------
1. Parse user's account list
2. For each entry, attempt to resolve:
   - propertyId from propertyName using property catalog
   - vendorId from vendorName using vendor catalog
3. Create normalized key: (propertyId, vendorId, accountNumber)

PHASE 2: Index Existing Accounts
----------------------------------------
1. Load accounts_to_track
2. Build index by:
   - Exact key: (propertyId, vendorId, accountNumber)
   - Account-only: accountNumber -> [list of tracked accounts]
   - Property+Account: (propertyId, accountNumber) -> [list]

PHASE 3: Exact Matching
----------------------------------------
For each input account:
1. Check exact key match -> Mark as "matched"
2. If no exact match, check property+account match:
   - If vendor differs, flag for Gemini name comparison
3. If no property+account match, check account-only:
   - Multiple properties may share account numbers (different utilities)

PHASE 4: Fuzzy Matching with Gemini
----------------------------------------
For unmatched accounts:
1. Find candidate matches by:
   - Same property, similar account number (typos)
   - Same account number, similar property name
   - Similar vendor name (abbreviations)

2. Batch send to Gemini:
   "Given these account pairs, are they the same utility account?
    Input: {propertyName: 'Arbors at Cal Oaks', vendorName: 'SCG', acct: '1234'}
    Candidate: {propertyName: 'Arbors at California Oaks', vendorName: 'SoCalGas', acct: '12345'}
    Return: {same: bool, confidence: float, reason: str}"

PHASE 5: Generate Report
----------------------------------------
Output categories:
1. MATCHED_EXACT - Account exists, all fields match
2. MATCHED_VENDOR_DIFF - Account exists but vendor name differs (possibly same)
3. MATCHED_FUZZY - Likely same account based on Gemini analysis
4. NOT_FOUND - Account not tracked, should be added
5. ORPHANED - In accounts_to_track but not in user's list (review for removal)
```

### API Endpoints

```
POST /api/admin/account-gap-analysis/upload
  - Upload external account list (CSV or JSON)
  - Returns: {upload_id: str, row_count: int}

POST /api/admin/account-gap-analysis/run
  - Run analysis on uploaded file
  - Body: {upload_id: str, use_gemini: bool}
  - Returns: {job_id: str, status: "processing"}

GET /api/admin/account-gap-analysis/status/{job_id}
  - Check analysis progress
  - Returns: {status, progress_pct, results_ready: bool}

GET /api/admin/account-gap-analysis/results/{job_id}
  - Get analysis results
  - Returns: {matched: [...], missing: [...], orphaned: [...], needs_review: [...]}

POST /api/admin/account-gap-analysis/add-missing
  - Add missing accounts to accounts_to_track
  - Body: {accounts: [{propertyId, vendorId, accountNumber, ...}, ...]}
```

### UI Component

**Location:** `/admin/account-gap-analysis` (System_Admins only)

```
+----------------------------------------------------------+
| ACCOUNT GAP ANALYSIS                                      |
+----------------------------------------------------------+
| Step 1: Upload your account list                          |
| [Choose File] accounts.csv          [Upload]              |
+----------------------------------------------------------+
| Step 2: Run Analysis                                      |
| [x] Use Gemini AI for fuzzy matching  [Run Analysis]      |
+----------------------------------------------------------+
| Results:                                                  |
| - 1,312 Matched (exact)                                   |
| -    45 Matched (vendor name differs)                     |
| -    23 Likely matches (Gemini 90%+)                      |
| -    20 NOT FOUND - Need to add                           |
| -   596 In tracker but not in your list                   |
+----------------------------------------------------------+
| [Export Results CSV]  [Add 20 Missing Accounts]           |
+----------------------------------------------------------+
```

---

## 3. Smart UBI Allocation

### Problem Statement
User spends significant time manually assigning bills to UBI periods. The system should auto-suggest the correct period based on:
- Bill's service period dates (Bill Period Start, Bill Period End)
- Previous bills' service periods for the same account
- UBI period boundaries (typically 1st of month)

### UBI Period Logic

UBI periods are monthly buckets for utility billing back to residents:
- Period "2026-01" = Jan 1 - Jan 31, 2026
- A bill with service period Dec 15 - Jan 14 might span two UBI periods
- Bills can be split across multiple periods based on service days

### Algorithm

```
SMART ALLOCATION LOGIC
----------------------------------------
For a bill with:
- Bill Period Start: 2025-12-15
- Bill Period End: 2026-01-14
- Bill Amount: $100.00

Step 1: Determine which UBI periods the service spans
- Dec 15-31: 17 days in 2025-12 period
- Jan 1-14: 14 days in 2026-01 period
- Total: 31 service days

Step 2: Calculate suggested allocation
- 2025-12: 17/31 = 54.8% = $54.84
- 2026-01: 14/31 = 45.2% = $45.16

Step 3: Check historical patterns for this account
- Load last 6 bills for (propertyId, vendorId, accountNumber)
- If consistent monthly billing, confirm suggestion
- If irregular, flag for manual review

Step 4: Present suggestion with confidence
- High confidence: Service dates clearly in one period
- Medium confidence: Spans periods, allocation calculated
- Low confidence: Missing dates, irregular history

PREVIOUS BILL TRACKING
----------------------------------------
For each tracked account, maintain:
{
  "accountKey": "prop|vend|acct",
  "lastBillDate": "2026-01-05",
  "lastServiceStart": "2025-12-01",
  "lastServiceEnd": "2025-12-31",
  "lastUbiPeriod": "2025-12",
  "avgServiceDays": 30,
  "billingPattern": "monthly|bi-monthly|quarterly"
}

When new bill arrives:
1. Compare new service start vs last service end
2. If gap > 7 days, flag as potential skip
3. If overlap, flag as potential duplicate
4. Suggest next UBI period = lastServiceEnd + 1 day's month
```

### Data Model Changes

**Add to bill record (Stage 6/7):**
```json
{
  "ubi_suggested_period": "2026-01",
  "ubi_suggested_allocation": [
    {"period": "2025-12", "amount": 54.84, "days": 17},
    {"period": "2026-01", "amount": 45.16, "days": 14}
  ],
  "ubi_suggestion_confidence": "high|medium|low",
  "ubi_suggestion_reason": "Service period Dec 15 - Jan 14 spans two periods"
}
```

**New config file: `ubi_account_history.json`:**
```json
{
  "accounts": {
    "1296739|705870|12345": {
      "lastBillDate": "2026-01-05",
      "lastServiceEnd": "2025-12-31",
      "lastUbiPeriods": ["2025-12"],
      "avgServiceDays": 30,
      "billHistory": [
        {"billDate": "2025-12-05", "serviceEnd": "2025-11-30", "ubiPeriod": "2025-11"},
        {"billDate": "2026-01-05", "serviceEnd": "2025-12-31", "ubiPeriod": "2025-12"}
      ]
    }
  },
  "last_updated": "2026-01-12T20:00:00Z"
}
```

### API Endpoints

```
GET /api/billback/ubi/suggestions
  - Get UBI allocation suggestions for unassigned bills
  - Params: ?page=1&per_page=50
  - Returns: {bills: [{...bill, ubi_suggestion: {...}}, ...]}

POST /api/billback/ubi/accept-suggestion
  - Accept suggested allocation
  - Body: {pdf_id: str, accept_suggestion: bool}
  - If accepted, applies suggested periods
  - If rejected, marks for manual assignment

GET /api/billback/ubi/account-history/{accountKey}
  - Get billing history for an account
  - Returns: {history: [...], pattern: "monthly", avg_days: 30}

POST /api/billback/ubi/calculate-suggestion
  - Calculate suggestion for a specific bill
  - Body: {pdf_id: str, service_start: str, service_end: str, amount: float}
  - Returns: {suggestion: {...}}
```

### UI Changes to BILLBACK Page

```
+----------------------------------------------------------+
| UBI PERIOD ASSIGNMENT                                     |
+----------------------------------------------------------+
| Account: 12345 | Vendor: SoCalGas | Property: Arbors     |
+----------------------------------------------------------+
| This Bill:                                                |
| Service Period: Dec 15, 2025 - Jan 14, 2026 (31 days)     |
| Amount: $100.00                                           |
+----------------------------------------------------------+
| Previous Bill:                                            |
| Service Period: Nov 15 - Dec 14, 2025                     |
| Assigned to: 2025-11 and 2025-12                          |
+----------------------------------------------------------+
| SUGGESTED ALLOCATION:                     [HIGH CONFIDENCE]|
| +--------------------------------------------------+      |
| | Period    | Days | Amount | [x] Accept            |     |
| |-----------|------|--------|                       |     |
| | 2025-12   |  17  | $54.84 |                       |     |
| | 2026-01   |  14  | $45.16 |                       |     |
| +--------------------------------------------------+      |
|                                                           |
| [Accept Suggestion]  [Assign Manually]  [Skip]            |
+----------------------------------------------------------+
```

### Batch Mode

For high-confidence suggestions, allow bulk acceptance:
```
+----------------------------------------------------------+
| SMART ALLOCATE (Batch Mode)                  [Refresh]    |
+----------------------------------------------------------+
| 47 bills ready for auto-allocation (90%+ confidence)      |
|                                                           |
| Preview:                                                  |
| - 32 bills -> single period assignment                    |
| - 15 bills -> split across 2 periods                      |
|                                                           |
| [Review All] [Accept All High-Confidence] [Export Preview]|
+----------------------------------------------------------+
```

---

## 4. Account Removal from WORKFLOW

### Problem Statement
User added many vacant accounts to tracker that shouldn't be there. Need ability to:
- Identify accounts with multiple VACANT line items
- Allow admin to remove/archive accounts from tracking
- Prevent non-admins from removing accounts

### Detection Logic

```
VACANT ACCOUNT DETECTION
----------------------------------------
For each tracked account:
1. Count bills in last 90 days
2. Count line items with House Or Vacant == "Vacant"
3. Flag if:
   - > 50% of line items are Vacant
   - Multiple invoices, ALL are Vacant
   - Account has "Vacant" in property/account name

FLAGGING CATEGORIES:
- RED: 100% Vacant (3+ bills) - Strong candidate for removal
- YELLOW: 50-99% Vacant - Review needed
- GREEN: < 50% Vacant or mixed - Keep tracking
```

### Data Model

**Add to accounts_to_track:**
```json
{
  "vendorId": "...",
  "accountNumber": "...",
  "status": "active|archived|pending_removal",
  "archived_at": "2026-01-12T...",
  "archived_by": "cbeach",
  "archive_reason": "vacant_account",
  "vacant_stats": {
    "total_bills": 5,
    "vacant_lines": 15,
    "house_lines": 0,
    "vacant_pct": 100
  }
}
```

### API Endpoints

```
GET /api/workflow/vacant-accounts
  - List accounts flagged as mostly vacant
  - Params: ?min_vacant_pct=50
  - Returns: {accounts: [{...account, vacant_stats: {...}}, ...]}

POST /api/workflow/accounts/archive
  - Archive (soft-delete) accounts from tracking
  - Body: {account_keys: ["prop|vend|acct", ...], reason: str}
  - Requires: System_Admins role
  - Returns: {archived: int, failed: int}

POST /api/workflow/accounts/restore
  - Restore archived accounts
  - Body: {account_keys: [...]}
  - Returns: {restored: int}

GET /api/workflow/accounts/archived
  - List archived accounts (for restore option)
  - Returns: {accounts: [...]}
```

### UI Component

**Location:** WORKFLOW page, new "MANAGE" sub-tab (admin only)

```
+----------------------------------------------------------+
| WORKFLOW > MANAGE TRACKED ACCOUNTS          [Admin Only]  |
+----------------------------------------------------------+
| VACANT ACCOUNT CLEANUP                                    |
+----------------------------------------------------------+
| Filter: [100% Vacant] [50%+ Vacant] [All Flagged]         |
+----------------------------------------------------------+
| [x] | Property        | Vendor   | Account | Vacant % |   |
|-----|-----------------|----------|---------|----------|   |
| [x] | Arbors Unit 101 | SCE      | 12345   | 100%     |   |
| [x] | Arbors Unit 102 | SCE      | 12346   | 100%     |   |
| [ ] | Falcon North #5 | WM       | 67890   | 60%      |   |
+----------------------------------------------------------+
| Selected: 2 accounts                                      |
| [Archive Selected] [View Bills for Selected]              |
+----------------------------------------------------------+
```

### Permission Check

```python
ADMIN_USERS = {"cbeach", "admin"}  # Or from config

@app.post("/api/workflow/accounts/archive")
async def archive_accounts(request: Request, user: str = Depends(require_user)):
    if user not in ADMIN_USERS:
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    # ... proceed with archive
```

---

## 5. Outlier Detection Tab

### Problem Statement
Bills arrive monthly for most accounts. Need to detect anomalies:
- Bill amount significantly higher or lower than historical average
- Use statistical methods (standard deviation) when sufficient history
- Fall back to percentage thresholds when limited data

### Algorithm

```
OUTLIER DETECTION ALGORITHM
----------------------------------------
For each new bill for account (propertyId, vendorId, accountNumber):

Step 1: Load Historical Data
- Get last 12 bills for this account
- Extract amounts (Line Item Charge or total)
- Handle multi-line bills: use invoice total

Step 2: Calculate Statistics
If N >= 6 bills (enough for std dev):
  - mean = average(amounts)
  - std = standard_deviation(amounts)
  - z_score = (new_amount - mean) / std
  - OUTLIER if |z_score| > 3

If N >= 3 but < 6:
  - median = median(amounts)
  - pct_change = abs(new_amount - median) / median * 100
  - OUTLIER if pct_change > 50%

If N < 3:
  - Not enough history
  - Compare to property average for similar utility type
  - Flag if > 100% different from property average

Step 3: Categorize Outlier
- SPIKE: Amount significantly higher (z > 3 or +50%)
- DROP: Amount significantly lower (z < -3 or -50%)
- NORMAL: Within expected range

Step 4: Store Results
{
  "accountKey": "...",
  "billDate": "2026-01-05",
  "amount": 450.00,
  "historical_mean": 100.00,
  "historical_std": 15.00,
  "z_score": 23.33,
  "outlier_type": "SPIKE",
  "confidence": "high",  // based on N
  "reason": "Amount $450 is 23 std devs above mean $100"
}
```

### Data Model

**New config file: `account_statistics.json`:**
```json
{
  "accounts": {
    "1296739|705870|12345": {
      "history": [
        {"date": "2025-12-05", "amount": 95.00},
        {"date": "2025-11-05", "amount": 102.00},
        {"date": "2025-10-05", "amount": 98.50}
      ],
      "stats": {
        "mean": 98.50,
        "std": 3.50,
        "median": 98.50,
        "n": 3,
        "last_updated": "2026-01-05"
      }
    }
  }
}
```

**Outlier record:**
```json
{
  "pdf_id": "abc123",
  "accountKey": "1296739|705870|12345",
  "detected_at": "2026-01-12T...",
  "bill_date": "2026-01-05",
  "amount": 450.00,
  "historical_mean": 100.00,
  "z_score": 23.33,
  "outlier_type": "SPIKE",
  "status": "pending|reviewed|resolved|false_positive",
  "reviewed_by": null,
  "review_notes": null
}
```

### API Endpoints

```
GET /api/metrics/outliers
  - Get detected outliers
  - Params: ?status=pending&type=SPIKE&min_z=3
  - Returns: {outliers: [...], summary: {spikes: 5, drops: 2}}

POST /api/metrics/outliers/{pdf_id}/review
  - Mark outlier as reviewed
  - Body: {status: "resolved|false_positive", notes: str}
  - Returns: {success: true}

GET /api/metrics/account-stats/{accountKey}
  - Get historical stats for an account
  - Returns: {history: [...], stats: {...}, outliers: [...]}

POST /api/metrics/outliers/recalculate
  - Recalculate stats and detect outliers for recent bills
  - Background job
  - Returns: {job_id: str}
```

### UI Component

**Location:** METRICS page, new "OUTLIERS" tab

```
+----------------------------------------------------------+
| METRICS > OUTLIERS                          [Recalculate] |
+----------------------------------------------------------+
| Summary: 5 Spikes | 2 Drops | 7 Total Pending            |
+----------------------------------------------------------+
| Filter: [All] [Spikes Only] [Drops Only] [Pending Only]   |
+----------------------------------------------------------+
| Status | Property     | Vendor | Amount  | Expected | Z  |
|--------|--------------|--------|---------|----------|-----|
| SPIKE  | Arbors #101  | SCE    | $450.00 | ~$100    | 23  |
| DROP   | Falcon North | WM     | $12.00  | ~$85     | -21 |
+----------------------------------------------------------+
| Click row to view details and mark as reviewed            |
+----------------------------------------------------------+

[Detail Panel when row clicked]
+----------------------------------------------------------+
| OUTLIER DETAIL                                            |
+----------------------------------------------------------+
| Account: Arbors #101 | SCE | 12345                       |
| Bill Date: Jan 5, 2026 | Amount: $450.00                 |
+----------------------------------------------------------+
| Historical Pattern (6 months):                            |
| Oct: $95 | Nov: $102 | Dec: $98 | Jan: $450 (!!)         |
|                                                           |
| Mean: $98.33 | Std Dev: $3.51                            |
| Z-Score: 100.05 (EXTREME OUTLIER)                        |
+----------------------------------------------------------+
| [View Invoice PDF]  [Mark Resolved]  [False Positive]     |
+----------------------------------------------------------+
```

### Integration with WORKFLOW

Outliers can be surfaced on the main WORKFLOW page:
- Add "Outliers" column showing count of pending outliers per account
- Add filter: "Has Pending Outliers"
- Link to METRICS > OUTLIERS for the specific account

---

## Implementation Sequence

### Phase 1: Foundation (Week 1)
1. Add `created_at` field to new accounts_to_track entries
2. Add `status` field for soft-delete capability
3. Create `account_statistics.json` config structure
4. Create backup mechanism for accounts_to_track

### Phase 2: Outlier Detection (Week 2)
1. Implement stats calculation for existing bills
2. Create OUTLIERS tab UI
3. Add outlier detection to bill submission pipeline
4. Test with real data

### Phase 3: Account Management (Week 2-3)
1. Implement vacant account detection
2. Create MANAGE sub-tab on WORKFLOW
3. Add archive/restore functionality
4. Add admin permission checks

### Phase 4: Smart UBI (Week 3-4)
1. Create `ubi_account_history.json` structure
2. Implement suggestion algorithm
3. Add suggestion display to BILLBACK page
4. Create batch acceptance mode

### Phase 5: Vendor Correction (Week 4-5)
1. Build Gemini comparison utility
2. Create admin correction UI
3. Implement S3 update logic with backup
4. Test thoroughly with dry-run mode

### Phase 6: Gap Analysis (Week 5-6)
1. Build upload and parsing logic
2. Implement exact and fuzzy matching
3. Create results UI with export
4. Add "Add Missing" bulk action

---

## Gemini Integration Details

### Rate Limiting
- Gemini API has rate limits (~60 requests/minute)
- Batch requests where possible
- Implement exponential backoff

### Prompt Templates

**Vendor Comparison:**
```
You are comparing utility vendor names to determine if they refer to the same company.

Vendor A: "{vendor_a}"
Vendor B: "{vendor_b}"

Consider:
- Abbreviations (SCE = Southern California Edison)
- Common variations (WM = Waste Management)
- Parent/subsidiary relationships

Return ONLY valid JSON:
{"same": true/false, "confidence": 0.0-1.0, "reason": "brief explanation"}
```

**Account Matching:**
```
Determine if these utility accounts are the same:

Account 1:
- Property: "{prop1}"
- Vendor: "{vendor1}"
- Account Number: "{acct1}"

Account 2:
- Property: "{prop2}"
- Vendor: "{vendor2}"
- Account Number: "{acct2}"

Consider typos, abbreviations, and partial matches.

Return ONLY valid JSON:
{"same": true/false, "confidence": 0.0-1.0, "reason": "brief explanation"}
```

### Error Handling
```python
async def gemini_compare(prompt: str, max_retries: int = 3) -> dict:
    for attempt in range(max_retries):
        try:
            response = await call_gemini_api(prompt)
            return json.loads(response)
        except RateLimitError:
            await asyncio.sleep(2 ** attempt)
        except JSONDecodeError:
            # Retry with stricter prompt
            continue
    return {"same": None, "confidence": 0, "reason": "API error"}
```

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Wrong vendor correction applied | High | Require manual review for < 95% confidence |
| Account incorrectly archived | Medium | Soft-delete with restore capability |
| Gemini API unavailable | Medium | Fall back to exact matching only |
| Performance issues with large batches | Medium | Process in chunks, background jobs |
| Data loss during S3 updates | High | Always backup before modify |

---

## Success Metrics

1. **Vendor Correction**: 90%+ of November accounts corrected with 95%+ confidence
2. **Gap Analysis**: < 5% false positives in matching
3. **Smart UBI**: 80%+ of suggestions accepted without modification
4. **Account Removal**: Reduce tracked vacant accounts by 50%+
5. **Outlier Detection**: Catch 95%+ of true anomalies, < 10% false positive rate

---

## Appendix: File Locations

| Feature | Config/Data Location |
|---------|---------------------|
| Accounts to Track | `s3://jrk-analytics-billing/Bill_Parser_Config/accounts_to_track.json` |
| Account Statistics | `s3://jrk-analytics-billing/Bill_Parser_Config/account_statistics.json` |
| UBI Account History | `s3://jrk-analytics-billing/Bill_Parser_Config/ubi_account_history.json` |
| Outlier Records | `s3://jrk-analytics-billing/Bill_Parser_Config/outlier_records.json` |
| Vendor Corrections Log | `s3://jrk-analytics-billing/Bill_Parser_Config/vendor_corrections_log.json` |
| Gap Analysis Results | `s3://jrk-analytics-billing/Bill_Parser_Config/gap_analysis/` |
