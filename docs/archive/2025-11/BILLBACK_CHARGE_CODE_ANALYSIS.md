# Billback Charge Code Lookup Analysis

## Overview
This document provides a detailed analysis of how charge codes are currently being looked up and matched against accounts in the billback.html interface, including what fields are used for matching and what data is available in the line item rendering logic.

---

## 1. Function: getChargeCodeForAccount()

**Location:** `templates/billback.html` lines 534-546

### Implementation:
```javascript
function getChargeCodeForAccount(accountNumber, vendorName) {
  if (!accountNumber) return null;
  const acctStr = String(accountNumber).trim();
  const vendorStr = String(vendorName || '').trim();

  const mapping = ubiMapping.find(m => {
    const configAcct = String(m.accountNumber || m.account_number || '').trim();
    const configVendor = String(m.vendorName || m.vendor_name || '').trim();
    return configAcct === acctStr && configVendor === vendorStr;
  });

  return mapping ? (mapping.chargeCode || mapping.charge_code || null) : null;
}
```

### Matching Fields:
- **accountNumber** (camelCase) or **account_number** (snake_case)
- **vendorName** (camelCase) or **vendor_name** (snake_case)

### Return Value:
- Returns **chargeCode** or **charge_code** field from the matching UBI mapping entry
- Returns `null` if no matching account/vendor combination found

### Data Source:
- The `ubiMapping` array is populated by the `/api/config/ubi-mapping` endpoint
- This data is loaded in the `loadUbiConfig()` function at line 459

---

## 2. UBI Mapping Data Structure

### Backend API: `/api/config/ubi-mapping`
**Location:** `main.py` lines 2703-2749

The API returns items with these fields:
```javascript
{
  vendorId: string,
  vendorName: string,
  accountNumber: string,
  propertyId: string,
  propertyName: string,
  glAccountNumber: string,
  glAccountName: string,
  daysBetweenBills: number,
  isUbi: boolean,
  chargeCode: string,
  notes: string
}
```

### Backend Storage: DynamoDB Config
**Location:** `main.py` lines 2560-2584

When adding a new UBI mapping via `/api/config/add-to-ubi`:
```javascript
{
  account_number: string,
  vendor_name: string,
  charge_code: string,
  utility_name: string
}
```

**Critical Issue:** The stored format uses `account_number` and `vendor_name` (snake_case), but the API endpoint merges this with accounts-to-track data that uses camelCase fields (`accountNumber`, `vendorName`).

---

## 3. Line Item Data Available

### Source Data:
Each line item in the unassigned bills has a `line_data` object containing these fields:
- **EnrichedPropertyName** or **Property Name** - Enriched property identifier
- **Vendor Name** - Vendor name
- **Account Number** - Account identifier
- **Bill Period Start** - Start date of bill period
- **Bill Period End** - End date of bill period
- **Charge Code** - May be present from original bill data
- **Line Item Description** - Description of the line item
- **Utility Type** - Type of utility
- **Line Item Charge** - Amount charged for this line
- **Notes** - Any existing notes

### Rendering Code:
**Location:** `templates/billback.html` lines 867-912

```javascript
// Get charge code from line data, or lookup from UBI mapping if available
let chargeCode = ld['Charge Code'] || null;

// If no charge code or it's 'N/A', try to lookup from UBI mapping
if (!chargeCode || chargeCode === 'N/A' || chargeCode.trim() === '') {
  const mappedChargeCode = getChargeCodeForAccount(account, vendor);
  console.log('Looked up charge code:', mappedChargeCode, 'for', account, vendor);
  chargeCode = mappedChargeCode || 'N/A';
}

const utilityName = ld['Utility Type'] || '';
const lineDesc = ld['Line Item Description'] || 'N/A';
```

### What's Displayed:
The line item renders with:
- **Charge Code** (from line data or UBI mapping lookup)
- **Utility Name** (from line data)
- **Line Item Description** (from line data)
- **Amount** (editable input)
- **Notes** (editable input)

---

## 4. Add to UBI Modal: Fields Passed

**Location:** `templates/billback.html` lines 573-593

### Modal Opening:
```javascript
function openAddToUbiModal(account, vendor, property) {
  currentModalAccount = account;
  currentModalVendor = vendor;
  currentModalProperty = property;

  document.getElementById('ubiAccount').textContent = account;
  document.getElementById('ubiVendor').textContent = vendor;
  document.getElementById('ubiProperty').textContent = property;
  // ... populate charge codes dropdown
}
```

### Fields Passed to Modal:
1. **account** - Account Number from line data
2. **vendor** - Vendor Name from line data
3. **property** - Property Name from line data (NOT currently used in backend)

### Form Submission:
**Location:** `templates/billback.html` lines 649-690

When submitting the Add to UBI modal:
```javascript
const fd = new FormData();
fd.append('account_number', currentModalAccount);
fd.append('vendor_name', currentModalVendor);
fd.append('charge_code', chargeCode);
fd.append('utility_name', utilityName);

const response = await fetch('/api/config/add-to-ubi', {
  method: 'POST',
  body: fd
});
```

**Fields Sent to Backend:**
- `account_number` - Account identifier
- `vendor_name` - Vendor name
- `charge_code` - Selected charge code
- `utility_name` - Utility name from charge code

**NOTE:** Property is displayed in the modal but NOT sent to the backend!

---

## 5. How UBI Status is Determined

### Function: `isUbiTracked()`
**Location:** `templates/billback.html` lines 496-507

```javascript
function isUbiTracked(accountNumber, vendorName) {
  if (!accountNumber) return false;
  const acctStr = String(accountNumber).trim();
  const vendorStr = String(vendorName || '').trim();
  const acct = accountsToTrack.find(a => {
    const configAcct = String(a.accountNumber || a.account_number || '').trim();
    const configVendor = String(a.vendorName || a.vendor_name || '').trim();
    return configAcct === acctStr && configVendor === vendorStr;
  });
  return acct && acct.ubi_tracking === true;
}
```

**Matching Criteria:**
- Account AND Vendor must both match
- The account must have `ubi_tracking === true` flag in the accounts-to-track config

### Function: `hasUbiMapping()`
**Location:** `templates/billback.html` lines 520-531

```javascript
function hasUbiMapping(accountNumber, vendorName) {
  if (!accountNumber) return false;
  const acctStr = String(accountNumber).trim();
  const vendorStr = String(vendorName || '').trim();
  return ubiMapping.some(m => {
    const configAcct = String(m.accountNumber || m.account_number || '').trim();
    const configVendor = String(m.vendorName || m.vendor_name || '').trim();
    return configAcct === acctStr && configVendor === vendorStr;
  });
}
```

**Matching Criteria:**
- Both account AND vendor must match in the UBI mapping configuration

### Function: `isInTracker()`
**Location:** `templates/billback.html` lines 509-518

```javascript
function isInTracker(accountNumber) {
  if (!accountNumber) return false;
  const acctStr = String(accountNumber).trim();
  return accountsToTrack.some(a => {
    const configAcct = String(a.accountNumber || a.account_number || '').trim();
    return configAcct === acctStr;
  });
}
```

**Matching Criteria:**
- Only account number needs to match (vendor is NOT checked)

---

## 6. Data Flow for UBI Status Badges

**Location:** `templates/billback.html` lines 819-828

```javascript
const inTracker = isInTracker(account);
const hasUbi = isUbiTracked(account, vendor);

const trackerBadge = inTracker
  ? '<span class="badge" style="...">TRACKED</span>'
  : '<span class="badge" style="...">NOT TRACKED</span>';

const ubiBadge = hasUbi
  ? '<span class="badge" style="...">UBI</span>'
  : '<span class="badge" style="...">NOT UBI</span>';
```

---

## 7. Critical Issues & Missing Fields

### ISSUE #1: Inconsistent Field Naming (snake_case vs camelCase)
**Problem:**
- Line items use Account Number and Vendor Name from enriched bill data
- UBI mapping stores account_number and vendor_name (snake_case)
- Accounts-to-track uses accountNumber and vendorName (camelCase)
- The frontend code attempts to handle both, but the backend storage is inconsistent

**Impact:**
- When a new UBI mapping is added via the modal, it stores in snake_case
- When the API retrieves UBI mappings, it may merge with camelCase accounts-to-track data
- Matching may fail if field names don't align across data sources

**Location:**
- UBI mapping storage: `main.py` lines 2579-2583 (uses snake_case)
- Frontend lookup: `billback.html` line 540-541 (handles both)

### ISSUE #2: Property Not Included in UBI Mapping

**Problem:**
- Property Name is displayed in the "Add to UBI" modal
- Property Name is NOT sent to the backend
- UBI mapping is based only on Account + Vendor combination
- Multiple properties might share the same Account+Vendor, requiring different charge codes

**Impact:**
- Cannot have property-specific charge code mappings
- If Account 12345 + Vendor XYZ appears in multiple properties, only ONE charge code can be assigned
- All properties will share the same mapping

**Location:**
- Modal display: `billback.html` line 259 (shows property but doesn't use it)
- Form submission: `billback.html` lines 667-671 (doesn't send property)
- Backend handler: `main.py` line 2548 (accepts account_number, vendor_name only)

### ISSUE #3: Charge Code Lookup Uses Only Account+Vendor

**Problem:**
- `getChargeCodeForAccount()` only looks at Account Number and Vendor Name
- Doesn't consider:
  - Property
  - GL Account
  - Bill Period / Date
  - Utility Type
  - Line Item Description

**Impact:**
- Cannot handle scenarios where the same Account+Vendor at different properties needs different codes
- Cannot use more granular matching rules
- Limited to global account/vendor mappings

**Location:** `billback.html` lines 534-546

### ISSUE #4: Missing Property-aware Matching Fields

**Problem:**
The line item has rich data but matching only uses 2 fields:

**Available but unused:**
- Property (could enable property-specific mappings)
- Utility Type (could enable utility-specific codes)
- GL Account (could enable GL-specific codes)
- Bill Period Start/End (could enable date-based routing)
- Vendor ID (using Vendor Name instead)
- Property ID (using Property Name instead)

### ISSUE #5: Accounts-to-track Uses Different Key Structure

**Problem:**
The accounts-to-track configuration uses a composite key:
```python
# Backend key composition (main.py line 2715-2719)
k = "|".join([
    str(r.get("vendorId") or "").strip(),
    str(r.get("accountNumber") or "").strip(),
    str(r.get("propertyId") or "").strip(),
    str(r.get("glAccountNumber") or "").strip(),
])
```

But UBI mapping lookup uses only Account+Vendor (not vendorId, propertyId, glAccountNumber)

**Impact:**
- Mismatch between how accounts-to-track identifies records vs how UBI mapping identifies them
- The composite key allows for GL-account-specific tracking but this isn't leveraged in UBI mapping

---

## 8. Data Configuration APIs

### GET `/api/config/accounts-to-track`
- Returns array of tracked accounts with fields: vendorId, vendorName, accountNumber, propertyId, propertyName, glAccountNumber, glAccountName, daysBetweenBills, **ubi_tracking**

### GET `/api/config/ubi-mapping`
- Merges accounts-to-track base with ubi-mapping overlay
- Uses composite key: vendorId|accountNumber|propertyId|glAccountNumber

### POST `/api/config/add-to-ubi`
- Input: account_number, vendor_name, charge_code, utility_name
- Creates mapping WITHOUT property or GL account information
- Automatically sets ubi_tracking=true on the tracker account

### GET `/api/config/charge-codes`
- Returns available charge codes with format: { chargeCode, utilityName }
- Used to populate the dropdown in the Add to UBI modal

---

## 9. Summary of Matching Logic

### Current Matching Strategy:
1. **Tracker Status:** Account number + Vendor name (both required)
2. **UBI Status:** Account number + Vendor name (both required)
3. **Charge Code Lookup:** Account number + Vendor name (both required)

### Matching Consistency:
- Frontend code handles both camelCase (accountNumber) and snake_case (account_number)
- Backend storage is inconsistent (some fields are camelCase, others snake_case)
- This creates brittle code that works but is difficult to maintain

### What's Missing for Better Matching:
1. Property-level mappings
2. GL Account level mappings
3. Utility Type specific codes
4. Date-based routing rules
5. Vendor ID (currently using name, which is less stable)
6. Property ID (currently using name, which is less stable)

---

## 10. Recommended Improvements

### Short-term (Low Risk):
1. **Normalize field names** - Standardize on either camelCase or snake_case consistently across frontend and backend
2. **Add property to UBI mapping** - Send property to backend in the Add to UBI modal
3. **Improve matching documentation** - Document which fields are used for matching at each stage

### Medium-term (Moderate Risk):
1. **Add GL Account to UBI mapping** - Enable GL-account-specific charge code assignments
2. **Use IDs instead of names** - Use vendorId and propertyId for more stable matching instead of names
3. **Add utility type filtering** - Allow charge code selection based on utility type

### Long-term (Higher Risk):
1. **Composite key matching** - Support Account+Vendor+Property+GL+UtilityType for granular matching
2. **Wildcard matching rules** - Allow patterns like "Account 12345 + any Vendor" or "Any Account + Vendor XYZ"
3. **Date-based routing** - Route different codes based on bill date ranges

---

## Files Involved

1. **Frontend:**
   - `/templates/billback.html` (lines 272-1350)
   - Main functions: getChargeCodeForAccount(), isUbiTracked(), hasUbiMapping(), isInTracker()

2. **Backend:**
   - `/main.py` (various sections)
   - API endpoints: /api/config/accounts-to-track, /api/config/ubi-mapping, /api/config/add-to-ubi, /api/config/charge-codes
   - Data functions: _ddb_get_config(), _ddb_put_config()

3. **Data Storage:**
   - DynamoDB table: `jrk-bill-config` (CONFIG_TABLE)
   - Config IDs: "accounts-to-track", "ubi-mapping", "charge-codes"
   - S3 fallback: BUCKET/CONFIG_PREFIX/accounts_to_track.json, ubi_mapping.json

