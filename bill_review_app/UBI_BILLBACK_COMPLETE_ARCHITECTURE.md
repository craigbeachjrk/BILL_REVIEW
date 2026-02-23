# UBI Billback System - Complete Architecture Documentation

**Last Updated:** 2025-11-17
**Status:** Implementation Ready

---

## Table of Contents
1. [Overview](#overview)
2. [Complete Workflow](#complete-workflow)
3. [Data Architecture](#data-architecture)
4. [Flag System](#flag-system)
5. [Override Mechanisms](#override-mechanisms)
6. [Four-Stage Process](#four-stage-process)
7. [Implementation Plan](#implementation-plan)
8. [Examples](#examples)

---

## Overview

The UBI Billback system processes utility bills and aggregates them into master bills for export to Snowflake. The system supports:

- **Account-level UBI inclusion/exclusion** (is_ubi flag)
- **Line-item-level exclusions** (is_excluded_from_ubi flag)
- **Property-aware GL code → Charge code mappings**
- **Multi-month bill splitting** with period assignments
- **Amount and charge code overrides** with reason tracking
- **Aggregation** of line items into master bills
- **Batch management** with memos for export

---

## Complete Workflow

### Bill Processing Pipeline

```
1. INPUT
   ├─ Email or upload
   ├─ Large or regular bill
   └─ Parser extracts line items

2. REVIEW & CORRECT
   └─ User reviews in REVIEW page

3. ROUTING
   ├─ Option A: Post to Entrata Core (or NOT)
   ├─ Option B: UBI Billback (or NOT)
   └─ Option C: Archive (ALL eventually go here)

4. UBI BILLBACK PROCESSING (if routed to UBI)
   ├─ Stage 1: Line Item Processing
   │   ├─ Add accounts to tracker
   │   ├─ Mark accounts as UBI
   │   ├─ Assign charge codes
   │   ├─ Split into billback periods
   │   └─ Handle overrides & exclusions
   │
   ├─ Stage 2: Master Bill Aggregation
   │   └─ Group by Property + Charge Code + Utility + Period
   │
   ├─ Stage 3: Batch Creation
   │   ├─ Select master bills for period
   │   └─ Add batch memo
   │
   └─ Stage 4: Export to Snowflake
       └─ Generate SQL inserts for _Master_Bills table
```

---

## Data Architecture

### 1. accounts-to-track (Account-Level Configuration)

**Purpose:** Track which accounts to monitor and which are in UBI program

**Schema:**
```javascript
{
  // Identity
  vendor_id: "705870",              // Join key (stable ID)
  vendor_name: "SMUD",              // Display
  account_number: "6390640",        // Join key
  property_id: "1296739",           // Join key (stable ID)
  property_name: "Arbors at CA",    // Display

  // Tracking flags (INDEPENDENT)
  is_tracked: true,                 // Monitor this account for bills?
  is_ubi: true,                     // Include in UBI billback program?

  // Metadata
  days_between_bills: 30,
  notes: "Added by user@example.com"
}
```

**Key Points:**
- `is_tracked` and `is_ubi` are **INDEPENDENT** flags
- An account can be tracked but NOT UBI
- An account can be UBI but not tracked (rare)
- Most accounts are BOTH tracked AND UBI

---

### 2. gl-charge-code-mapping (Property-Aware GL → Charge Code)

**Purpose:** Map GL codes to charge codes, with property-specific overrides

**Schema:**
```javascript
{
  // Composite key: property_id + gl_code
  property_id: "1296739",           // "*" for wildcard (all properties)
  property_name: "Arbors at CA",    // Display
  gl_code: "5730-0000",             // Join key
  gl_code_name: "Water",            // Display

  // Assignment
  charge_code: "WATER-RES-001",     // Target charge code
  utility_name: "Water",            // Display

  // Configuration
  is_billable: true,                // Can this be billedback?
  notes: "Residential water"
}
```

**Example Data:**
```javascript
[
  // Property-specific mapping
  {
    property_id: "1296739",
    property_name: "Arbors at CA",
    gl_code: "5730-0000",
    gl_code_name: "Water",
    charge_code: "WATER-RES-001",
    utility_name: "Water",
    is_billable: true
  },

  // Different property, same GL code, DIFFERENT charge code
  {
    property_id: "1234567",
    property_name: "Oak Ridge Apts",
    gl_code: "5730-0000",
    gl_code_name: "Water",
    charge_code: "WATER-COMM-002",    // Different!
    utility_name: "Water",
    is_billable: true
  },

  // Wildcard mapping (applies to ALL properties)
  {
    property_id: "*",
    property_name: "All Properties",
    gl_code: "6510-0000",
    gl_code_name: "Late Fees",
    charge_code: null,                 // No charge code
    utility_name: "Late Fee",
    is_billable: false                 // Cannot billback!
  }
]
```

**Lookup Logic:**
1. Try exact match: `property_id + gl_code`
2. Fallback to wildcard: `"*" + gl_code`
3. If no match: charge code = null

---

### 3. Line Items (in drafts table - DETAIL LEVEL)

**Purpose:** Individual line items from parsed bills with all detail

**Schema:**
```javascript
{
  // Identity
  bill_id: "ABC123",
  line_index: 0,

  // Account info
  vendor_id: "705870",
  vendor_name: "SMUD",
  account_number: "6390640",
  property_id: "1296739",
  property_name: "Arbors at CA",

  // GL Code (used to lookup charge code)
  gl_code: "5730-0000",
  gl_code_name: "Water",

  // Description
  description: "Water usage Jan-Mar 2024",

  // Bill period (original from bill)
  bill_period_start: "2024-01-01",
  bill_period_end: "2024-03-31",

  // ===== AMOUNT TRACKING =====
  original_amount: 300.00,            // Original amount from bill
  current_amount: 300.00,             // Current amount (may be overridden)
  amount_overridden: false,           // Flag: was amount changed?
  amount_override_reason: "",         // WHY was it overridden?

  // ===== CHARGE CODE ASSIGNMENT =====
  charge_code: "WATER-RES-001",       // Assigned charge code
  charge_code_source: "mapping",      // "mapping" or "override"
  charge_code_overridden: false,      // Flag: was charge code changed from mapping?
  charge_code_override_reason: "",    // WHY was it overridden?
  utility_name: "Water",

  // ===== LINE-LEVEL EXCLUSION =====
  is_excluded_from_ubi: 0,            // 0 = include, 1 = exclude
  exclusion_reason: "",               // WHY excluded? (e.g., "Late fee")

  // ===== BILLBACK PERIOD ASSIGNMENTS =====
  // One line item can be split into MULTIPLE billback periods
  billback_assignments: [
    {
      billback_month_start: "2024-01-01",
      billback_month_end: "2024-01-31",
      utility_amount: 100.00,
      amount_overridden: false,       // Was THIS period's amount overridden?
      amount_override_reason: ""      // Why this period overridden?
    },
    {
      billback_month_start: "2024-02-01",
      billback_month_end: "2024-02-29",
      utility_amount: 100.00,
      amount_overridden: false,
      amount_override_reason: ""
    },
    {
      billback_month_start: "2024-03-01",
      billback_month_end: "2024-03-31",
      utility_amount: 100.00,
      amount_overridden: false,
      amount_override_reason: ""
    }
  ],

  // Metadata
  updated_by: "user@example.com",
  updated_utc: "2024-01-15T10:00:00Z"
}
```

---

### 4. Master Bills (AGGREGATED LEVEL - NEW!)

**Purpose:** Aggregated line items rolled up by Property + Charge Code + Utility + Period

**Schema:**
```javascript
{
  // Composite key
  master_bill_id: "1296739|WATER-001|Water|2024-01-01|2024-01-31",

  // ===== AGGREGATION DIMENSIONS =====
  // (These form the grouping key)
  property_id: "1296739",
  property_name: "Arbors at CA",           // For display
  ar_code_mapping: "WATER-001",            // Charge code
  utility_name: "Water",
  billback_month_start: "2024-01-01",
  billback_month_end: "2024-01-31",

  // ===== AGGREGATED AMOUNT =====
  utility_amount: 225.00,                  // SUM of all constituent line items

  // ===== CONSTITUENT LINE ITEMS (for drill-down) =====
  source_line_items: [
    {
      bill_id: "ABC123",
      line_index: 0,
      gl_code: "5730-0000",               // GL code exists here for drill-down
      gl_code_name: "Water",
      description: "Water usage",
      amount: 100.00,
      overridden: false,
      override_reason: ""
    },
    {
      bill_id: "ABC123",
      line_index: 1,
      gl_code: "5731-0000",
      gl_code_name: "Sewer",
      description: "Sewer charges",
      amount: 50.00,
      overridden: false,
      override_reason: ""
    },
    {
      bill_id: "DEF456",
      line_index: 0,
      gl_code: "5732-0000",
      gl_code_name: "Water Base Fee",
      description: "Water base fee",
      amount: 75.00,
      overridden: true,                   // This line was overridden
      override_reason: "Prorated for partial month"
    }
  ],

  // ===== METADATA =====
  created_utc: "2024-01-15T10:00:00Z",
  created_by: "user@example.com",
  status: "draft"                         // "draft", "reviewed", "finalized", "exported"
}
```

**Key Points:**
- **GL codes are NOT in the aggregation key** - they only exist in source_line_items for drill-down
- **One master bill = One row in Snowflake** (eventually)
- Master bills can be drilled down to see constituent line items
- If ANY constituent line item has overrides, master bill shows warning flag

---

### 5. UBI Billback Batches (BATCH LEVEL - NEW!)

**Purpose:** Group master bills into batches for export with shared memo

**Schema:**
```javascript
{
  // Identity
  batch_id: "UBI-2024-Q1",
  batch_name: "Q1 2024 UBI Billback",

  // Time period covered by this batch
  period_start: "2024-01-01",
  period_end: "2024-03-31",

  // ===== MEMO (applies to ENTIRE batch) =====
  memo: "Q1 2024 utilities billback to residents. Includes Jan-Mar water, electric, gas. Late fees excluded per policy.",

  // Master bills included in this batch
  master_bill_ids: [
    "1296739|WATER-001|Water|2024-01-01|2024-01-31",
    "1296739|WATER-001|Water|2024-02-01|2024-02-29",
    "1296739|WATER-001|Water|2024-03-01|2024-03-31",
    "1296739|ELEC-001|Electricity|2024-01-01|2024-01-31",
    // ... more master bills
  ],

  // ===== BATCH METADATA =====
  status: "draft",                        // "draft", "reviewed", "finalized", "exported"
  created_utc: "2024-01-15T10:00:00Z",
  created_by: "user@example.com",
  reviewed_utc: null,
  reviewed_by: null,
  exported_utc: null,
  exported_by: null,
  run_date: "2024-04-01T10:30:00Z",       // When exported to Snowflake (DATETIME/ISO8601)

  // ===== AGGREGATED STATS =====
  total_master_bills: 45,
  total_amount: 12500.00,
  properties_count: 5,
  line_items_count: 150                   // Total source line items
}
```

**Key Points:**
- **MEMO is batch-level**, not per master bill or line item
- All master bills in a batch share the same memo when exported
- Batches can be in various states: draft → reviewed → finalized → exported

---

### 6. Snowflake _Master_Bills Table (EXPORT TARGET)

**Purpose:** Final destination for UBI billback data

**Schema:**
```sql
create or replace TABLE "_Master_Bills" (
    "Property_ID" VARCHAR(16777216),
    "AR_Code_Mapping" VARCHAR(16777216),
    "Utility_Name" VARCHAR(16777216),
    "Utility_Amount" VARCHAR(16777216),
    "Billback_Month_Start" VARCHAR(16777216),
    "Billback_Month_End" VARCHAR(16777216),
    "RunDate" VARCHAR(16777216),          -- Value inserted is DATETIME string (ISO8601)
    "Memo" VARCHAR(16777216)
);
```

**Mapping from Master Bills:**
```javascript
{
  "Property_ID": master_bill.property_id,
  "AR_Code_Mapping": master_bill.ar_code_mapping,      // Charge code
  "Utility_Name": master_bill.utility_name,
  "Utility_Amount": master_bill.utility_amount,        // Aggregated amount
  "Billback_Month_Start": master_bill.billback_month_start,
  "Billback_Month_End": master_bill.billback_month_end,
  "RunDate": batch.run_date,                           // From batch (DATETIME)
  "Memo": batch.memo                                   // FROM BATCH (same for all)
}
```

**Example Data:**
```sql
INSERT INTO "_Master_Bills" VALUES
('1296739', 'WATER-001', 'Water', '225.00', '2024-01-01', '2024-01-31', '2024-04-01', 'Q1 2024 utilities billback to residents. Includes Jan-Mar water, electric, gas. Late fees excluded per policy.'),
('1296739', 'ELEC-001', 'Electricity', '450.00', '2024-01-01', '2024-01-31', '2024-04-01', 'Q1 2024 utilities billback to residents. Includes Jan-Mar water, electric, gas. Late fees excluded per policy.'),
('1296739', 'GAS-001', 'Gas', '150.00', '2024-01-01', '2024-01-31', '2024-04-01', 'Q1 2024 utilities billback to residents. Includes Jan-Mar water, electric, gas. Late fees excluded per policy.');
```

**Key Points:**
- **NO GL codes** in final export (they disappear during aggregation)
- **NO line item detail** in final export (rolled up)
- **MEMO is the same for all rows in a batch**
- One master bill = One row

---

## Flag System

### Account-Level Flags

Located in: `accounts-to-track`

#### `is_tracked` (boolean)
- **Purpose:** Are we monitoring this account for bill receipt?
- **Values:**
  - `true` = Track this account (alert if bills missing)
  - `false` = Don't track this account
- **Use case:** Bill tracking and alerting

#### `is_ubi` (boolean)
- **Purpose:** Is this account included in UBI billback program?
- **Values:**
  - `true` = Include in UBI (export to Snowflake)
  - `false` = Exclude from UBI (don't export)
- **Use case:** Account-level inclusion/exclusion
- **NOTE:** This is THE account-level exclusion mechanism (no separate `is_excluded_from_ubi` at account level!)

### Line-Item-Level Flags

Located in: `line_data` in drafts table

#### `is_excluded_from_ubi` (integer)
- **Purpose:** Should this specific line item be excluded from UBI even though the account is UBI?
- **Values:**
  - `0` = Include this line item
  - `1` = Exclude this line item
- **Use case:** Excluding late fees, penalties, credits that can't be billedback
- **Always paired with:** `exclusion_reason` (text field explaining why)

### Export Logic

**A line item is exported to UBI Master Bills if and only if:**

```javascript
function shouldExportToUbi(account, lineItem) {
  // Account must be in UBI program
  if (account.is_ubi !== true) {
    return false;
  }

  // Line item must not be excluded
  if (lineItem.is_excluded_from_ubi === 1) {
    return false;
  }

  // Must have charge code assigned
  if (!lineItem.charge_code) {
    return false;
  }

  // Must have billback period assignments
  if (!lineItem.billback_assignments || lineItem.billback_assignments.length === 0) {
    return false;
  }

  return true;
}
```

**Truth Table:**

| account.is_ubi | lineItem.is_excluded_from_ubi | Result |
|----------------|-------------------------------|--------|
| TRUE | 0 | ✅ Export |
| TRUE | 1 | ❌ Don't export (line excluded) |
| FALSE | 0 | ❌ Don't export (account not UBI) |
| FALSE | 1 | ❌ Don't export (account not UBI) |

---

## Override Mechanisms

### 1. Amount Override (Line Item Level)

**Fields:**
- `original_amount` - Original amount from bill (never changes)
- `current_amount` - Current amount (may be overridden)
- `amount_overridden` - Flag indicating if amount was changed
- `amount_override_reason` - Text explaining why

**Workflow:**
1. User changes amount in input field
2. System detects change: `current_amount !== original_amount`
3. Prompt: "Why are you overriding this amount?"
4. User enters reason: "Prorated for partial month"
5. Save: `amount_overridden = true`, `amount_override_reason = "Prorated..."`

**UI Indicator:**
- Show ⚠️ badge next to amount if overridden
- Hover shows reason

---

### 2. Charge Code Override (Line Item Level)

**Fields:**
- `charge_code` - Assigned charge code
- `charge_code_source` - "mapping" or "override"
- `charge_code_overridden` - Flag indicating if charge code was changed from mapping
- `charge_code_override_reason` - Text explaining why

**Workflow:**
1. System looks up charge code: `gl_code + property_id → charge_code`
2. Default charge code populated in dropdown
3. User changes dropdown to different charge code
4. System detects: selected charge code !== mapped charge code
5. Prompt: "Why are you overriding this charge code?"
6. User enters reason: "Should be commercial rate, not residential"
7. Save: `charge_code_source = "override"`, `charge_code_overridden = true`, `charge_code_override_reason = "Should be..."`

**UI Indicator:**
- Show "Override" badge next to dropdown if overridden
- Hover shows reason

---

### 3. Billback Period Amount Override

**Fields (per period in billback_assignments):**
- `utility_amount` - Amount for this period
- `amount_overridden` - Flag for THIS period
- `amount_override_reason` - Why THIS period overridden

**Workflow:**
1. User clicks "Auto-Split by Months"
2. System divides total amount evenly: $300 / 3 months = $100 each
3. User wants to adjust February: changes $100 to $120
4. Prompt: "Why override February amount?"
5. User enters: "Higher usage in Feb due to cold weather"
6. Save period-specific override

**Example:**
```javascript
billback_assignments: [
  {
    billback_month_start: "2024-01-01",
    billback_month_end: "2024-01-31",
    utility_amount: 100.00,
    amount_overridden: false,
    amount_override_reason: ""
  },
  {
    billback_month_start: "2024-02-01",
    billback_month_end: "2024-02-29",
    utility_amount: 120.00,              // Overridden!
    amount_overridden: true,
    amount_override_reason: "Higher usage in Feb due to cold weather"
  },
  {
    billback_month_start: "2024-03-01",
    billback_month_end: "2024-03-31",
    utility_amount: 80.00,               // Adjusted to balance (300 - 100 - 120 = 80)
    amount_overridden: true,
    amount_override_reason: "Adjusted to maintain total"
  }
]
```

---

## Four-Stage Process

### Stage 1: Line Item Processing (billback.html)

**Purpose:** Process individual line items from parsed bills

**User Actions:**

1. **Add Accounts to Tracker**
   - Button: "Add to Tracker"
   - Creates account with `is_tracked = true`, `is_ubi = false`

2. **Add Accounts to UBI**
   - Button: "Add to UBI"
   - Sets `is_ubi = true` on account (creates account if doesn't exist)

3. **Assign Charge Codes**
   - Dropdown populated from property + GL code mapping
   - Override triggers reason prompt

4. **Override Amounts**
   - Change amount in input field
   - Triggers reason prompt
   - Sets `amount_overridden = true`

5. **Split into Billback Periods**
   - Button: "Assign Billback Periods"
   - Opens modal
   - "Auto-Split by Months" divides amount evenly
   - User can manually add/remove/edit periods
   - Override period amounts triggers reason prompt

6. **Exclude Line Items**
   - Checkbox: "Exclude from UBI"
   - Triggers reason prompt
   - Sets `is_excluded_from_ubi = 1`

**Result:** All line items have:
- ✅ Charge codes assigned
- ✅ Billback periods assigned
- ✅ Amounts finalized (with override tracking)
- ✅ Exclusions marked

**Filters:**
- ☑️ "Show UBI Accounts Only" - Filter to `is_ubi = true`
- ☑️ "Hide Excluded" - Hide lines where `is_excluded_from_ubi = 1`

---

### Stage 2: Master Bill Aggregation (master-bills.html - NEW PAGE!)

**Purpose:** Aggregate line items into master bills for review

**Process:**
1. User clicks "Generate Master Bills"
2. System scans all line items where:
   - Account `is_ubi = true`
   - Line `is_excluded_from_ubi = 0`
   - Has billback_assignments
3. Groups by:
   - `property_id`
   - `charge_code` (ar_code_mapping)
   - `utility_name`
   - `billback_month_start` + `billback_month_end`
4. Sums amounts within each group
5. Creates master_bill record with constituent line items

**UI - List View:**
```
Master Bills - January 2024

Property: Arbors at California Oaks (1296739)

┌──────────────────────────────────────────────────────────────────┐
│ Charge Code  │ Utility      │ Amount    │ Line Items │ Flags    │
├──────────────────────────────────────────────────────────────────┤
│ WATER-001    │ Water        │ $225.00   │ 3 items    │ ⚠️       │ ← Click to drill down
│ ELEC-001     │ Electricity  │ $450.00   │ 2 items    │          │
│ GAS-001      │ Gas          │ $150.00   │ 1 item     │          │
└──────────────────────────────────────────────────────────────────┘

⚠️ = Contains overridden amounts or charge codes

Total: $825.00
```

**UI - Drill-Down View:**
```
Master Bill Detail
Property: Arbors at California Oaks
Charge Code: WATER-001 - Water
Period: January 1-31, 2024
Total: $225.00

Constituent Line Items:
┌──────────────────────────────────────────────────────────────────────────┐
│ Bill ID │ GL Code    │ Description         │ Amount   │ Override?      │
├──────────────────────────────────────────────────────────────────────────┤
│ ABC123  │ 5730-0000  │ Water usage         │ $100.00  │                │
│ ABC123  │ 5731-0000  │ Sewer charges       │ $50.00   │                │
│ DEF456  │ 5732-0000  │ Water base fee      │ $75.00   │ ⚠️ Prorated    │
└──────────────────────────────────────────────────────────────────────────┘

[Back to Master Bills]
```

**Note:** GL codes are visible in drill-down but NOT in the master bill summary

---

### Stage 3: Batch Creation (ubi-batch.html - NEW PAGE!)

**Purpose:** Group master bills into batches for export with memo

**Process:**
1. User clicks "Create New Batch"
2. Enters:
   - Batch name: "Q1 2024 UBI Billback"
   - Date range: Jan 1 - Mar 31, 2024
3. System shows all master bills in that range
4. User reviews summary:
   - Total master bills: 45
   - Total properties: 5
   - Total amount: $12,500.00
5. User enters **MEMO**:
   ```
   Q1 2024 utilities billback to residents.
   Includes Jan-Mar water, electric, gas for all UBI properties.
   Late fees excluded per policy.
   ```
6. User clicks "Finalize Batch"
7. Status changes: draft → reviewed
8. Batch is ready for export

**UI:**
```
Create UBI Billback Batch

Batch Name: [Q1 2024 UBI Billback]
Period: [2024-01-01] to [2024-03-31]

┌─────────────────────────────────────────────────────────────────┐
│ MEMO (Batch Description - applies to all master bills)         │
├─────────────────────────────────────────────────────────────────┤
│ Q1 2024 utilities billback to residents. Includes Jan-Mar      │
│ water, electric, gas for all UBI properties. Late fees         │
│ excluded per policy.                                            │
└─────────────────────────────────────────────────────────────────┘

Master Bills Summary:

Property            │ Jan    │ Feb    │ Mar    │ Total
────────────────────┼────────┼────────┼────────┼─────────
Arbors at CA        │ $825   │ $850   │ $800   │ $2,475
Oak Ridge Apts      │ $1,200 │ $1,150 │ $1,100 │ $3,450
Sunset Villas       │ $950   │ $980   │ $940   │ $2,870
Canyon Creek        │ $1,100 │ $1,080 │ $1,120 │ $3,300
Riverside Manor     │ $210   │ $205   │ $190   │ $605
────────────────────┼────────┼────────┼────────┼─────────
TOTAL               │        │        │        │ $12,500

Total Master Bills: 45
Total Properties: 5

[Finalize Batch] [Preview Export] [Cancel]
```

---

### Stage 4: Export to Snowflake (from ubi-batch.html)

**Purpose:** Generate SQL inserts for _Master_Bills table

**Process:**
1. User clicks "Export to Snowflake" on finalized batch
2. System generates SQL INSERT statements:
   - One row per master bill
   - MEMO from batch applied to all rows
3. User can preview SQL before export
4. User clicks "Confirm Export"
5. System executes SQL (or provides download)
6. Batch status → exported

**SQL Generation Example:**
```sql
-- Batch: Q1 2024 UBI Billback
-- Run Date: 2024-04-01
-- Total Rows: 45

INSERT INTO "_Master_Bills"
("Property_ID", "AR_Code_Mapping", "Utility_Name", "Utility_Amount",
 "Billback_Month_Start", "Billback_Month_End", "RunDate", "Memo")
VALUES
-- Arbors at CA - January 2024
('1296739', 'WATER-001', 'Water', '225.00', '2024-01-01', '2024-01-31', '2024-04-01',
 'Q1 2024 utilities billback to residents. Includes Jan-Mar water, electric, gas for all UBI properties. Late fees excluded per policy.'),
('1296739', 'ELEC-001', 'Electricity', '450.00', '2024-01-01', '2024-01-31', '2024-04-01',
 'Q1 2024 utilities billback to residents. Includes Jan-Mar water, electric, gas for all UBI properties. Late fees excluded per policy.'),
('1296739', 'GAS-001', 'Gas', '150.00', '2024-01-01', '2024-01-31', '2024-04-01',
 'Q1 2024 utilities billback to residents. Includes Jan-Mar water, electric, gas for all UBI properties. Late fees excluded per policy.'),

-- Arbors at CA - February 2024
('1296739', 'WATER-001', 'Water', '230.00', '2024-02-01', '2024-02-29', '2024-04-01',
 'Q1 2024 utilities billback to residents. Includes Jan-Mar water, electric, gas for all UBI properties. Late fees excluded per policy.'),
-- ... more rows ...
;
```

**Key Points:**
- **MEMO is identical for all rows in batch** (batch-level, not row-level)
- **RunDate is the export date** (from batch)
- **NO GL codes in export** (aggregated away)
- **NO line item detail in export** (aggregated away)

---

## Implementation Plan

### Phase 1: Backend - Account & Flag Management ✅

**Files:** `main.py`

**Tasks:**
- [ ] Update `accounts-to-track` schema to include `is_tracked` and `is_ubi` as separate boolean flags
- [ ] Create endpoint: `POST /api/ubi/add-to-tracker`
  - Adds account with `is_tracked=true`, `is_ubi=false`
- [ ] Create endpoint: `POST /api/ubi/add-to-ubi`
  - Sets `is_ubi=true` (creates account if doesn't exist)
- [ ] Update `GET /api/config/accounts-to-track` to return all flags

---

### Phase 2: Backend - Override Tracking ✅

**Files:** `main.py`

**Tasks:**
- [ ] Add amount override fields to line item schema:
  - `original_amount`
  - `current_amount`
  - `amount_overridden`
  - `amount_override_reason`
- [ ] Add charge code override fields to line item schema:
  - `charge_code_overridden`
  - `charge_code_override_reason`
- [ ] Add period override fields to `billback_assignments`:
  - `amount_overridden` (per period)
  - `amount_override_reason` (per period)
- [ ] Create endpoint: `POST /api/billback/update-line-item`
  - Accepts all override fields
  - Saves to drafts table

---

### Phase 3: Backend - Master Bills & Aggregation 🆕

**Files:** `main.py`

**Tasks:**
- [ ] Create DynamoDB table: `master_bills`
- [ ] Create endpoint: `POST /api/master-bills/generate`
  - Scan all line items where `account.is_ubi=true` and `line.is_excluded_from_ubi=0`
  - Group by: property_id + charge_code + utility_name + period
  - SUM amounts
  - Create master_bill records with source_line_items array
- [ ] Create endpoint: `GET /api/master-bills/list`
  - Returns master bills with aggregated data
- [ ] Create endpoint: `GET /api/master-bills/detail/{master_bill_id}`
  - Returns master bill with full drill-down to constituent line items

---

### Phase 4: Backend - Batch Management 🆕

**Files:** `main.py`

**Tasks:**
- [ ] Create DynamoDB table: `ubi_batches`
- [ ] Create endpoint: `POST /api/ubi-batch/create`
  - Creates new batch with memo
  - Links master bills by date range
- [ ] Create endpoint: `POST /api/ubi-batch/finalize`
  - Changes status: draft → finalized
- [ ] Create endpoint: `GET /api/ubi-batch/list`
  - Returns all batches with summary stats
- [ ] Create endpoint: `GET /api/ubi-batch/detail/{batch_id}`
  - Returns batch with all master bills

---

### Phase 5: Backend - Snowflake Export 🆕

**Files:** `main.py`

**Tasks:**
- [ ] Create endpoint: `POST /api/ubi-batch/export-snowflake`
  - Generates SQL INSERT statements
  - Format matches _Master_Bills schema exactly
  - Applies batch.memo to all rows
  - Applies batch.run_date to all rows
  - Returns SQL as text or executes directly

---

### Phase 6: Frontend - billback.html Updates ✅

**Files:** `templates/billback.html`

**Tasks:**
- [ ] Add TWO separate buttons:
  - "Add to Tracker" (when not tracked)
  - "Add to UBI" (when not UBI)
- [ ] Add amount override with reason prompt:
  - Detect changes to amount input
  - Prompt for reason
  - Show ⚠️ badge if overridden
- [ ] Add charge code override with reason prompt:
  - Detect changes to charge code dropdown
  - Compare to mapped charge code
  - Prompt for reason if different
  - Show "Override" badge
- [ ] Update billback period modal:
  - Add reason prompts for period amount overrides
  - Show override flags per period
- [ ] Add filters:
  - "Show UBI Accounts Only"
  - "Hide Excluded Items"
- [ ] Update status badges to show:
  - Tracked/Not Tracked
  - UBI/Not UBI
  - Excluded (account or line level)

---

### Phase 7: Frontend - master-bills.html (NEW PAGE!) 🆕

**Files:** `templates/master-bills.html`

**Tasks:**
- [ ] Create new page: Master Bills
- [ ] Add "Generate Master Bills" button
- [ ] Show master bills grouped by property and period
- [ ] Display aggregated amounts (NO GL codes)
- [ ] Show warning flags if constituent items overridden
- [ ] Add drill-down view:
  - Click master bill → see constituent line items
  - Show GL codes in drill-down
  - Show override flags and reasons
- [ ] Add filters:
  - By property
  - By period
  - By status

---

### Phase 8: Frontend - ubi-batch.html (NEW PAGE!) 🆕

**Files:** `templates/ubi-batch.html`

**Tasks:**
- [ ] Create new page: UBI Batches
- [ ] Add "Create New Batch" button
- [ ] Batch creation form:
  - Batch name
  - Date range selector
  - **MEMO text area** (large, multi-line)
- [ ] Show master bills in selected range
- [ ] Display summary stats:
  - Total master bills
  - Total properties
  - Total amount
  - Breakdown by property
- [ ] Add "Finalize Batch" button
- [ ] Add "Export to Snowflake" button:
  - Preview SQL
  - Confirm export
  - Download SQL or execute directly
- [ ] Show batch list with statuses

---

### Phase 9: Data Migration 🔄

**Tasks:**
- [ ] Add `is_tracked` and `is_ubi` flags to existing accounts in accounts-to-track
  - Default: `is_tracked=true`, `is_ubi=true` (assume most are both)
- [ ] Add override fields to existing line items in drafts:
  - `original_amount = current_amount` (no overrides yet)
  - `amount_overridden = false`
  - All override reasons = ""
- [ ] Create property-aware gl-charge-code-mapping from existing ubi-mapping
- [ ] Create initial wildcard mappings for non-billable GL codes (late fees, etc.)

---

### Phase 10: Testing 🧪

**Test Cases:**

**Account Management:**
- [ ] Add account to tracker (not UBI) → `is_tracked=true`, `is_ubi=false`
- [ ] Add account to UBI (not tracked) → `is_tracked=false`, `is_ubi=true`
- [ ] Add to tracker, then add to UBI → both true
- [ ] Filter "Show UBI Only" shows only `is_ubi=true`

**Override Tracking:**
- [ ] Override line item amount → prompts for reason → saves with flag
- [ ] Override charge code → prompts for reason → saves with flag
- [ ] Override period amount → prompts for reason → saves per-period flag
- [ ] Badges show ⚠️ for overridden items

**Master Bills:**
- [ ] Generate master bills groups by property + charge code + utility + period
- [ ] GL codes NOT in aggregation key (only in drill-down)
- [ ] Amounts sum correctly
- [ ] Override flags propagate to master bill
- [ ] Drill-down shows constituent line items with GL codes

**Batches:**
- [ ] Create batch with memo
- [ ] Memo applies to all master bills in batch
- [ ] Summary stats calculate correctly
- [ ] Finalize batch changes status

**Snowflake Export:**
- [ ] SQL format matches _Master_Bills schema exactly
- [ ] RunDate from batch
- [ ] Memo from batch (same for all rows)
- [ ] NO GL codes in export
- [ ] One row per master bill

---

## Examples

### Example 1: Simple Water Bill (Single Month)

**Input Line Item:**
```javascript
{
  bill_id: "WATER-001",
  vendor_name: "Water District",
  account_number: "123456",
  property_id: "1296739",
  property_name: "Arbors at CA",
  gl_code: "5730-0000",
  gl_code_name: "Water",
  bill_period_start: "2024-01-01",
  bill_period_end: "2024-01-31",
  original_amount: 100.00
}
```

**After Processing:**
```javascript
{
  // ... same fields ...
  current_amount: 100.00,
  amount_overridden: false,
  charge_code: "WATER-RES-001",        // Looked up from property + GL code
  charge_code_source: "mapping",
  charge_code_overridden: false,
  utility_name: "Water",
  is_excluded_from_ubi: 0,
  billback_assignments: [
    {
      billback_month_start: "2024-01-01",
      billback_month_end: "2024-01-31",
      utility_amount: 100.00,
      amount_overridden: false
    }
  ]
}
```

**Master Bill (after aggregation):**
```javascript
{
  property_id: "1296739",
  ar_code_mapping: "WATER-RES-001",
  utility_name: "Water",
  billback_month_start: "2024-01-01",
  billback_month_end: "2024-01-31",
  utility_amount: 100.00,
  source_line_items: [
    {
      bill_id: "WATER-001",
      gl_code: "5730-0000",
      amount: 100.00
    }
  ]
}
```

**Snowflake Export:**
```sql
('1296739', 'WATER-RES-001', 'Water', '100.00', '2024-01-01', '2024-01-31', '2024-04-01', 'Q1 2024 billback')
```

---

### Example 2: Multi-Month Electric Bill with Split

**Input Line Item:**
```javascript
{
  bill_id: "ELEC-456",
  vendor_name: "Power Company",
  account_number: "789012",
  property_id: "1296739",
  gl_code: "5710-0000",
  gl_code_name: "Electricity",
  bill_period_start: "2024-01-01",
  bill_period_end: "2024-03-31",      // 3 months!
  original_amount: 600.00
}
```

**After Auto-Split:**
```javascript
{
  // ... same fields ...
  current_amount: 600.00,
  charge_code: "ELEC-RES-001",
  billback_assignments: [
    {
      billback_month_start: "2024-01-01",
      billback_month_end: "2024-01-31",
      utility_amount: 200.00,          // 600 / 3 = 200
      amount_overridden: false
    },
    {
      billback_month_start: "2024-02-01",
      billback_month_end: "2024-02-29",
      utility_amount: 200.00,
      amount_overridden: false
    },
    {
      billback_month_start: "2024-03-01",
      billback_month_end: "2024-03-31",
      utility_amount: 200.00,
      amount_overridden: false
    }
  ]
}
```

**Master Bills (3 separate master bills!):**
```javascript
[
  {
    property_id: "1296739",
    ar_code_mapping: "ELEC-RES-001",
    utility_name: "Electricity",
    billback_month_start: "2024-01-01",
    billback_month_end: "2024-01-31",
    utility_amount: 200.00
  },
  {
    property_id: "1296739",
    ar_code_mapping: "ELEC-RES-001",
    utility_name: "Electricity",
    billback_month_start: "2024-02-01",
    billback_month_end: "2024-02-29",
    utility_amount: 200.00
  },
  {
    property_id: "1296739",
    ar_code_mapping: "ELEC-RES-001",
    utility_name: "Electricity",
    billback_month_start: "2024-03-01",
    billback_month_end: "2024-03-31",
    utility_amount: 200.00
  }
]
```

**Snowflake Export (3 rows):**
```sql
('1296739', 'ELEC-RES-001', 'Electricity', '200.00', '2024-01-01', '2024-01-31', '2024-04-01', 'Q1 2024 billback'),
('1296739', 'ELEC-RES-001', 'Electricity', '200.00', '2024-02-01', '2024-02-29', '2024-04-01', 'Q1 2024 billback'),
('1296739', 'ELEC-RES-001', 'Electricity', '200.00', '2024-03-01', '2024-03-31', '2024-04-01', 'Q1 2024 billback')
```

---

### Example 3: Multiple GL Codes Rolling Up

**Input Line Items (3 separate line items, same bill):**
```javascript
[
  {
    bill_id: "WATER-789",
    property_id: "1296739",
    gl_code: "5730-0000",              // Water usage
    gl_code_name: "Water",
    original_amount: 100.00,
    charge_code: "WATER-RES-001"
  },
  {
    bill_id: "WATER-789",
    property_id: "1296739",
    gl_code: "5731-0000",              // Sewer
    gl_code_name: "Sewer",
    original_amount: 50.00,
    charge_code: "WATER-RES-001"       // Same charge code!
  },
  {
    bill_id: "WATER-789",
    property_id: "1296739",
    gl_code: "5732-0000",              // Water base fee
    gl_code_name: "Water Base Fee",
    original_amount: 25.00,
    charge_code: "WATER-RES-001"       // Same charge code!
  }
]
```

**Master Bill (aggregated - GL codes disappear!):**
```javascript
{
  property_id: "1296739",
  ar_code_mapping: "WATER-RES-001",    // All three rolled into one!
  utility_name: "Water",
  billback_month_start: "2024-01-01",
  billback_month_end: "2024-01-31",
  utility_amount: 175.00,              // 100 + 50 + 25 = 175
  source_line_items: [
    { gl_code: "5730-0000", amount: 100.00 },
    { gl_code: "5731-0000", amount: 50.00 },
    { gl_code: "5732-0000", amount: 25.00 }
  ]
}
```

**Snowflake Export (single row!):**
```sql
('1296739', 'WATER-RES-001', 'Water', '175.00', '2024-01-01', '2024-01-31', '2024-04-01', 'Q1 2024 billback')
```

**Note:** GL codes `5730-0000`, `5731-0000`, `5732-0000` do NOT appear in export! They're only visible in drill-down.

---

### Example 4: Override Scenarios

**Scenario A: Amount Override**
```javascript
{
  bill_id: "GAS-123",
  original_amount: 150.00,
  current_amount: 120.00,              // User changed!
  amount_overridden: true,
  amount_override_reason: "Prorated for partial month occupancy"
}
```

**Scenario B: Charge Code Override**
```javascript
{
  gl_code: "5720-0000",
  // Mapped charge code would be "GAS-RES-001"
  charge_code: "GAS-COMM-002",         // User overrode to commercial!
  charge_code_source: "override",
  charge_code_overridden: true,
  charge_code_override_reason: "This unit is commercial space, not residential"
}
```

**Scenario C: Period Amount Override**
```javascript
{
  billback_assignments: [
    {
      billback_month_start: "2024-01-01",
      billback_month_end: "2024-01-31",
      utility_amount: 80.00,           // Would be 100 if evenly split
      amount_overridden: true,
      amount_override_reason: "Lower usage in January (vacant days)"
    },
    {
      billback_month_start: "2024-02-01",
      billback_month_end: "2024-02-29",
      utility_amount: 120.00,          // Higher!
      amount_overridden: true,
      amount_override_reason: "Higher usage in February (cold weather)"
    },
    {
      billback_month_start: "2024-03-01",
      billback_month_end: "2024-03-31",
      utility_amount: 100.00,
      amount_overridden: false
    }
  ]
}
```

---

### Example 5: Exclusion Scenarios

**Scenario A: Account-Level Exclusion**
```javascript
// In accounts-to-track
{
  vendor_id: "705870",
  account_number: "999999",
  property_id: "1296739",
  is_tracked: true,
  is_ubi: false,                       // ← Excluded from UBI!
  notes: "Corporate account - not billable to residents"
}

// Result: ALL line items from this account are excluded from UBI export
```

**Scenario B: Line-Level Exclusion**
```javascript
// Account IS in UBI program
{
  vendor_id: "705870",
  account_number: "123456",
  is_ubi: true                         // ← Account is UBI
}

// But this specific line item is excluded
{
  bill_id: "ELEC-999",
  account_number: "123456",
  gl_code: "6510-0000",
  gl_code_name: "Late Fee",
  amount: 25.00,
  is_excluded_from_ubi: 1,             // ← Line excluded!
  exclusion_reason: "Late fees cannot be billedback to residents per policy"
}

// Result: This line item is NOT included in master bills, even though account is UBI
```

---

### Example 6: Complete Batch Export

**Batch:**
```javascript
{
  batch_id: "UBI-2024-Q1",
  batch_name: "Q1 2024 UBI Billback",
  period_start: "2024-01-01",
  period_end: "2024-03-31",
  memo: "Q1 2024 utilities billback. Includes water, electric, gas. Late fees excluded.",
  run_date: "2024-04-01",
  master_bill_ids: [/* ... 45 master bills ... */]
}
```

**Snowflake Export (all rows have same memo and run_date):**
```sql
INSERT INTO "_Master_Bills" VALUES
-- January
('1296739', 'WATER-001', 'Water', '225.00', '2024-01-01', '2024-01-31', '2024-04-01',
 'Q1 2024 utilities billback. Includes water, electric, gas. Late fees excluded.'),
('1296739', 'ELEC-001', 'Electricity', '450.00', '2024-01-01', '2024-01-31', '2024-04-01',
 'Q1 2024 utilities billback. Includes water, electric, gas. Late fees excluded.'),
('1296739', 'GAS-001', 'Gas', '150.00', '2024-01-01', '2024-01-31', '2024-04-01',
 'Q1 2024 utilities billback. Includes water, electric, gas. Late fees excluded.'),

-- February
('1296739', 'WATER-001', 'Water', '230.00', '2024-02-01', '2024-02-29', '2024-04-01',
 'Q1 2024 utilities billback. Includes water, electric, gas. Late fees excluded.'),
('1296739', 'ELEC-001', 'Electricity', '470.00', '2024-02-01', '2024-02-29', '2024-04-01',
 'Q1 2024 utilities billback. Includes water, electric, gas. Late fees excluded.'),
('1296739', 'GAS-001', 'Gas', '180.00', '2024-02-01', '2024-02-29', '2024-04-01',
 'Q1 2024 utilities billback. Includes water, electric, gas. Late fees excluded.'),

-- March
('1296739', 'WATER-001', 'Water', '210.00', '2024-03-01', '2024-03-31', '2024-04-01',
 'Q1 2024 utilities billback. Includes water, electric, gas. Late fees excluded.'),
('1296739', 'ELEC-001', 'Electricity', '420.00', '2024-03-01', '2024-03-31', '2024-04-01',
 'Q1 2024 utilities billback. Includes water, electric, gas. Late fees excluded.'),
('1296739', 'GAS-001', 'Gas', '140.00', '2024-03-01', '2024-03-31', '2024-04-01',
 'Q1 2024 utilities billback. Includes water, electric, gas. Late fees excluded.');

-- Total: 9 rows for one property across 3 months
-- Full batch: 45 rows across 5 properties
```

---

## Summary

This UBI Billback system provides:

✅ **Clear separation** of tracking vs UBI inclusion at account level
✅ **Granular exclusions** at line item level
✅ **Property-aware** GL code → charge code mappings
✅ **Multi-month bill splitting** with period-level overrides
✅ **Complete override tracking** with reasons for audit trail
✅ **Aggregation workflow** from line items → master bills → batches
✅ **Batch-level memos** for export documentation
✅ **Clean Snowflake export** matching _Master_Bills schema

The four-stage workflow (Line Items → Master Bills → Batches → Snowflake) ensures data quality and provides multiple review points before final export.

---

**END OF DOCUMENTATION**
