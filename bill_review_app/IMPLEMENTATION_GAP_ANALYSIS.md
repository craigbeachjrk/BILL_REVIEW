# Implementation Gap Analysis - UBI Billback System

**Created:** 2025-11-17
**Status:** Backend Complete, Frontend Partially Complete

---

## What We Discussed

### 1. Independent Account Flags
**Requirement:** Two INDEPENDENT flags at account level
- `is_tracked` - for monitoring accounts
- `is_ubi` - for UBI program inclusion
- Must be able to add to tracker WITHOUT adding to UBI
- Must be able to add to UBI WITHOUT adding to tracker

### 2. Line Item Override Tracking
**Requirement:** Track ALL overrides with reasons
- Charge code overrides (flag + reason)
- Amount overrides (flag + reason)
- Period amount overrides (flag + reason per period)

### 3. Line Item Exclusions
**Requirement:** Exclude specific line items from UBI
- `is_excluded_from_ubi` flag (0 or 1) at LINE ITEM level
- Exclusion reason text field
- Show exclusion status in UI

### 4. Property-Aware GL Code Mappings
**Requirement:** GL code → charge code mapping includes property
- Composite key: property_id + gl_code → charge_code
- Same GL code at different properties = different charge codes
- Wildcard "*" for global mappings
- Auto-lookup charge codes when processing line items

### 5. Master Bills Aggregation
**Requirement:** Aggregate line items into master bills
- Aggregation key: property_id + charge_code + utility_name + period
- GL codes NOT in aggregation key (preserved for drill-down)
- Track source line items with GL codes
- Propagate override flags

### 6. Batch Management
**Requirement:** Group master bills into batches
- Filter by date range
- Batch-level memo (applies to ALL rows in export)
- Batch finalization (sets run_date)
- Status tracking (draft → finalized → exported)

### 7. Snowflake Export
**Requirement:** Generate SQL INSERT statements
- Match _Master_Bills schema exactly
- Apply batch memo to all rows
- Apply batch run_date to all rows
- Copy-to-clipboard functionality

---

## Backend Implementation Status

### ✅ FULLY IMPLEMENTED (15 Endpoints)

#### Account Management
✅ **POST `/api/ubi/add-to-tracker`** - IMPLEMENTED
- Creates/updates account with is_tracked=true, is_ubi=false
- Independent from UBI flag

✅ **POST `/api/ubi/add-to-ubi`** - IMPLEMENTED
- Sets is_ubi=true on account
- Independent from tracking flag

#### Line Item Management
✅ **POST `/api/billback/update-line-item`** - IMPLEMENTED
```python
# Supports ALL override tracking:
- charge_code (the actual charge code)
- charge_code_source ("mapping" or "override")
- charge_code_overridden (boolean)
- charge_code_override_reason (text)
- current_amount (the overridden amount)
- amount_overridden (boolean)
- amount_override_reason (text)
- is_excluded_from_ubi (0 or 1)
- exclusion_reason (text)
```

✅ **POST `/api/billback/assign-periods`** - IMPLEMENTED
```python
# Accepts JSON array with per-period overrides:
[
  {
    "billback_month_start": "2024-01-01",
    "billback_month_end": "2024-01-31",
    "utility_amount": 100.00,
    "amount_overridden": false,
    "amount_override_reason": ""
  }
]
```

#### GL Code Mapping
✅ **GET `/api/config/gl-charge-code-mapping`** - IMPLEMENTED
```python
# Returns property-aware mappings:
{
  "items": [
    {
      "property_id": "1296739",
      "gl_code": "5730-0000",
      "charge_code": "WATER-RES-001",
      ...
    }
  ]
}
```

✅ **POST `/api/config/gl-charge-code-mapping`** - IMPLEMENTED
- Saves property + GL code → charge code mappings
- Supports wildcard property_id = "*"

#### Master Bills
✅ **POST `/api/master-bills/generate`** - IMPLEMENTED
```python
# Aggregates by: property + charge code + utility + period
# Filters: is_ubi=true accounts, is_excluded_from_ubi=0 lines
# Tracks source line items with GL codes
# Propagates override flags
```

✅ **GET `/api/master-bills/list`** - IMPLEMENTED
✅ **GET `/api/master-bills/detail/{id}`** - IMPLEMENTED

#### UBI Batches
✅ **POST `/api/ubi-batch/create`** - IMPLEMENTED
✅ **POST `/api/ubi-batch/finalize`** - IMPLEMENTED
✅ **GET `/api/ubi-batch/list`** - IMPLEMENTED
✅ **GET `/api/ubi-batch/detail/{id}`** - IMPLEMENTED
✅ **POST `/api/ubi-batch/export-snowflake`** - IMPLEMENTED

---

## Frontend Implementation Status

### ✅ IMPLEMENTED - New Pages

#### master-bills.html (NEW)
✅ Generate master bills with date filtering
✅ View aggregated bills in table
✅ Drill-down modal showing source line items with GL codes
✅ Override badges
✅ Summary statistics

**Location:** `/master-bills`
**Status:** FULLY FUNCTIONAL

#### ubi-batch.html (NEW)
✅ Create batch modal (name, date range, memo)
✅ List all batches with status
✅ Batch detail modal
✅ Finalize batch button
✅ Export to Snowflake button
✅ SQL output with copy-to-clipboard

**Location:** `/ubi-batch`
**Status:** FULLY FUNCTIONAL

---

## ❌ NOT IMPLEMENTED - Missing Frontend Integration

### billback.html - NEEDS MAJOR UPDATES

The existing billback.html page has NO integration with the new UBI architecture. Here's what's MISSING:

#### ❌ MISSING: Override Tracking UI

**What We Discussed:**
- When user updates a charge code → prompt for override reason
- When user updates an amount → prompt for override reason
- Show override badges/indicators
- Track override reasons in the UI

**What's Actually There:**
- Nothing. The old billback.html doesn't have ANY override tracking UI
- No prompts for override reasons
- No visual indicators for overridden values

**Where It Should Be:**
- In the line item editing interface
- Modals/prompts when changing charge codes or amounts

#### ❌ MISSING: Line-Level Exclusion UI

**What We Discussed:**
- Checkbox to exclude individual line items from UBI
- Prompt for exclusion reason when checking the box
- Visual indicator for excluded lines

**What's Actually There:**
- Nothing. No exclusion checkboxes exist

**Where It Should Be:**
- Each line item row should have an "Exclude from UBI" checkbox
- Clicking it should prompt for a reason
- Excluded lines should be visually distinct (red badge, strikethrough, etc.)

#### ❌ MISSING: Property-Aware GL Code Mapping Integration

**What We Discussed:**
- Auto-lookup charge codes from GL code + property mapping
- Display mapped charge code in UI
- Show when charge code came from mapping vs override
- Allow user to override mapped charge code

**What's Actually There:**
- Old UBI mapping system (4-field composite key with vendor)
- No property-aware GL code lookup
- No visual distinction between mapped vs overridden charge codes

**Where It Should Be:**
- When line items load, auto-populate charge codes from property+GL lookup
- Show "Mapped" badge when from mapping
- Show "Overridden" badge when user changed it

#### ❌ MISSING: Account Management Buttons

**What We Discussed:**
- "Add to Tracker" button (separate from UBI)
- "Add to UBI" button (separate from Tracker)
- Two INDEPENDENT operations

**What's Actually There:**
- Old system with mixed "Add to Tracker" and "Add to UBI" modals
- Not clearly separated

**Where It Should Be:**
- Bill header should have TWO distinct buttons:
  - [Add to Tracker] - calls POST /api/ubi/add-to-tracker
  - [Add to UBI] - calls POST /api/ubi/add-to-ubi
- Badges showing account status (TRACKED, UBI, BOTH, NEITHER)

---

## Specific Code Locations

### Backend (COMPLETE)
**File:** `main.py`
**Lines:** 2654-3380

All 15 endpoints are implemented and working.

### Frontend - New Pages (COMPLETE)
**File:** `templates/master-bills.html`
**Lines:** 1-400
**Status:** Fully functional

**File:** `templates/ubi-batch.html`
**Lines:** 1-400
**Status:** Fully functional

### Frontend - Missing Integration (NOT DONE)
**File:** `templates/billback.html`
**Current Lines:** 1-1408
**What's There:** Old UBI period assignment system
**What's Missing:**
- Override tracking UI (lines to add: ~200)
- Exclusion checkboxes (lines to add: ~50)
- Property-aware GL mapping integration (lines to add: ~100)
- Separate Tracker/UBI buttons (lines to modify: ~50)

---

## The Gap

### What Works Right Now:

1. ✅ **Backend is 100% complete** - All 15 endpoints work
2. ✅ **Master Bills page works** - Can generate and view aggregated bills
3. ✅ **UBI Batch page works** - Can create batches and export SQL

### What DOESN'T Work:

1. ❌ **No way to set overrides in the UI** - Backend accepts them, but no UI to set them
2. ❌ **No way to exclude lines in the UI** - Backend accepts exclusions, but no checkboxes
3. ❌ **No property-aware GL mapping in UI** - Backend does the lookup, but UI doesn't use it
4. ❌ **No separate Tracker/UBI buttons** - Backend has independent endpoints, but UI is confusing

### The Result:

**You CAN:**
- Go to /master-bills and see aggregated data
- Go to /ubi-batch and create batches
- Export SQL to Snowflake

**You CANNOT:**
- Actually SET the override reasons from the UI
- Actually EXCLUDE line items from the UI
- See which charge codes came from mapping vs override
- Clearly distinguish Tracker vs UBI operations

---

## What Needs to Be Done

### billback.html Updates Required:

1. **Override Tracking (HIGH PRIORITY)**
   - Add modal for charge code override with reason input
   - Add modal for amount override with reason input
   - Show override badges on line items
   - Call POST /api/billback/update-line-item with override fields

2. **Exclusion Checkboxes (HIGH PRIORITY)**
   - Add checkbox column to line items table
   - Add modal for exclusion reason when checking
   - Show excluded items with visual indicator
   - Call POST /api/billback/update-line-item with exclusion fields

3. **Property-Aware GL Mapping (MEDIUM PRIORITY)**
   - Load GL mappings on page load
   - Auto-populate charge codes from property+GL lookup
   - Show "Mapped" vs "Overridden" badges
   - Allow override with reason prompt

4. **Separate Tracker/UBI Buttons (MEDIUM PRIORITY)**
   - Replace confusing modals with clear buttons
   - [Add to Tracker] → POST /api/ubi/add-to-tracker
   - [Add to UBI] → POST /api/ubi/add-to-ubi
   - Show account status badges

---

## Summary

**Backend:** ✅ 100% COMPLETE (15 endpoints, all tested)
**Frontend - New Pages:** ✅ 100% COMPLETE (master-bills.html, ubi-batch.html)
**Frontend - Integration:** ❌ 0% COMPLETE (billback.html has no integration)

**You have the ENGINE but not the STEERING WHEEL.**

The backend can:
- Accept override reasons
- Accept exclusions
- Accept property-aware GL mappings
- Generate master bills
- Create batches
- Export SQL

But the UI doesn't LET YOU SET any of that data except through manual API calls.

---

**Next Step:** Update billback.html to integrate with all the new backend endpoints.
