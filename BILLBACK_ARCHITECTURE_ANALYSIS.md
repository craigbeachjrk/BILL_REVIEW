# BILLBACK Architecture Analysis & Recommendations

## Problem Statement

You identified the core issues:
1. **Account 6390640 SMUD not showing up** - matching logic is broken
2. **Wrong join key** - currently joining on Account + Vendor, should join on **GL Account**
3. **Missing granularity** - single invoice can have multiple charge codes for different line items
4. **Config interfaces are unusable** - not matching correctly, missing data, poor UX

## Current (Broken) Architecture

### Current Data Model

**accounts-to-track:**
```
Vendor ID | Vendor Name | Account Number | Property ID | Property Name | GL Account | GL Account Name | Days | ubi_tracking
```

**ubi-mapping:**
```
Account Number | Vendor Name | Charge Code | Utility Name
```

### Current Matching Logic

**Line item → Charge Code lookup:**
```javascript
// WRONG: Matches only on Account + Vendor
getChargeCodeForAccount(accountNumber, vendorName) {
  return ubiMapping.find(m =>
    m.account_number === accountNumber &&
    m.vendor_name === vendorName
  ).charge_code
}
```

**Why this is broken:**
- Single invoice can have multiple line items with different GL accounts
- Each GL account might need a different charge code
- Example: Same SMUD account might have:
  - Line 1: Electricity Usage → GL 5710-0000 (Electricity) → Charge Code "ELEC-001"
  - Line 2: Gas Charges → GL 5720-0000 (Gas) → Charge Code "GAS-001"
  - Line 3: Water Service → GL 5730-0000 (Water) → Charge Code "WATER-001"

## Recommended Architecture

### New Data Model

**accounts-to-track:** (unchanged - this is fine)
```
Vendor ID | Vendor Name | Account Number | Property ID | Property Name | GL Account | GL Account Name | Days | IS UBI
```
- `IS UBI` flag indicates whether this account is subject to UBI billback
- One row per tracked account configuration
- GL Account here represents the PRIMARY/DEFAULT GL for this account

**ubi-charge-code-mapping:** (NEW - replaces ubi-mapping)
```
Vendor ID | Vendor Name | Account Number | Property ID | Property Name | GL Account | GL Account Name | Charge Code | Utility Name | Notes
```
- **Join/Match fields:** Vendor ID, Account Number, Property ID, GL Account (composite key - all 4 required for matching)
- **Display fields:** Vendor Name, Property Name, GL Account Name, Utility Name (for legibility on BILLBACK page)
- **Assignment field:** Charge Code (the value we're looking up)
- **Optional:** Notes (for context/documentation)

### Key Changes

1. **Composite Join Key:** Vendor ID + Account Number + Property ID + **GL Account** (all 4 fields)
2. **Use IDs not names:** Join on Vendor ID and Property ID (stable), not names (can change)
3. **Granular mapping:** Each GL account at each property gets its own charge code assignment
4. **Complete context:** Include all identifying fields for debugging/auditing
5. **Display-ready data:** Store human-readable names (GL Account Name, Utility Name) so BILLBACK page can display them without additional lookups
6. **Property-specific codes:** Same account at different properties can have different charge codes

### Example Data

**ubi-charge-code-mapping entries for SMUD account 6390640:**

| Vendor ID | Vendor Name | Account Number | Property ID | Property Name | GL Account | GL Account Name | Charge Code | Utility Name | Notes |
|-----------|-------------|----------------|-------------|---------------|------------|-----------------|-------------|--------------|-------|
| 705870 | SMUD | 6390640 | 1296739 | Arbors at California Oaks | 5710-0000 | Electricity | ELEC-RES-001 | Electricity | Residential electricity |
| 705870 | SMUD | 6390640 | 1296739 | Arbors at California Oaks | 5720-0000 | Gas | GAS-RES-001 | Gas | Residential gas |

**When BILLBACK page displays a line item:**
- Line has: Account 6390640, Vendor SMUD (705870), Property 1296739, GL 5710-0000
- System looks up: `find(vendor_id=705870 AND account=6390640 AND property_id=1296739 AND gl=5710-0000)`
- Returns mapping with: Charge Code "ELEC-RES-001", Utility Name "Electricity", GL Account Name "Electricity"
- Display shows: **"ELEC-RES-001 - Electricity - Electricity"** (fully legible)

### New Matching Logic

```javascript
// CORRECT: Match on Vendor ID + Account + Property ID + GL Account
function getChargeCodeForLineItem(vendorId, accountNumber, propertyId, glAccount) {
  const mapping = ubiChargeCodeMapping.find(m => {
    return String(m.vendor_id) === String(vendorId) &&
           String(m.account_number) === String(accountNumber) &&
           String(m.property_id) === String(propertyId) &&
           String(m.gl_account) === String(glAccount);
  });
  return mapping ? mapping.charge_code : null;
}

// Returns full mapping object with charge_code, utility_name, gl_account_name for display
function getChargeCodeMappingForLineItem(vendorId, accountNumber, propertyId, glAccount) {
  const mapping = ubiChargeCodeMapping.find(m => {
    return String(m.vendor_id) === String(vendorId) &&
           String(m.account_number) === String(accountNumber) &&
           String(m.property_id) === String(propertyId) &&
           String(m.gl_account) === String(glAccount);
  });
  return mapping; // Returns { charge_code, utility_name, gl_account_name, ... } or undefined
}
```

### How It Works

**Scenario:** Processing a SMUD invoice for account 6390640 at property "Arbors at California Oaks" (1296739)

**Line items on invoice:**
```
Line 1: Electricity Usage | Property 1296739 | GL 5710-0000 | $500
Line 2: Gas Charges       | Property 1296739 | GL 5720-0000 | $200
Line 3: Delivery Fee      | Property 1296739 | GL 5710-0000 | $50
```

**Charge code lookups:**
```
Line 1: getChargeCode(vendor=705870, account=6390640, property=1296739, gl=5710-0000) → "ELEC-RES-001"
Line 2: getChargeCode(vendor=705870, account=6390640, property=1296739, gl=5720-0000) → "GAS-RES-001"
Line 3: getChargeCode(vendor=705870, account=6390640, property=1296739, gl=5710-0000) → "ELEC-RES-001"
```

**Why Property ID matters:**
If the same account 6390640 has bills for a different property (e.g., "Oak Ridge Apartments" - 1234567), those could have different charge codes even for the same GL accounts.

## Implementation Changes Required

### 1. Database Schema (DynamoDB + S3)

**Create new config:** `ubi-charge-code-mapping`
- Migrate existing ubi-mapping data (will need to backfill GL accounts)
- Add indexes for fast lookups

### 2. Backend API Changes (main.py)

**New endpoints:**
```python
# Get charge code mappings (returns full data including display names)
GET /api/config/ubi-charge-code-mapping
  Returns: {
    items: [
      {
        vendor_id: "705870",
        vendor_name: "SMUD",               # For display
        account_number: "6390640",
        property_id: "1296739",
        property_name: "Arbors at CA",     # For display
        gl_account: "5710-0000",
        gl_account_name: "Electricity",    # REQUIRED for legibility
        charge_code: "ELEC-RES-001",
        utility_name: "Electricity",       # REQUIRED for legibility
        notes: "Residential electricity"
      },
      ...
    ]
  }

# Save charge code mappings
POST /api/config/ubi-charge-code-mapping
  Body: { items: [...] }

# Add single mapping (called from Add to UBI modal)
POST /api/config/add-charge-code-mapping
  Form data:
    - vendor_id              # Join key
    - vendor_name            # Display
    - account_number         # Join key
    - property_id            # Join key
    - property_name          # Display
    - gl_account             # Join key
    - gl_account_name        # Display - REQUIRED for legibility on BILLBACK page
    - charge_code            # Assignment value
    - utility_name           # Display - REQUIRED for legibility on BILLBACK page
    - notes                  # Optional
```

**Updated endpoint:**
```python
# This should JOIN tracker + charge code mapping
GET /api/billback/ubi-config-display
  Returns complete joined view for CONFIG page
```

### 3. Frontend Changes (templates/billback.html)

**Update charge code lookup:**
```javascript
// Line item has: vendorId, accountNumber, propertyId, glAccount
function getChargeCodeMappingForLineItem(ld) {
  const vendorId = ld['Vendor ID'] || ld.vendorId;
  const account = ld['Account Number'];
  const propertyId = ld['Property ID'] || ld.propertyId;  // NEW: property in join
  const glAccount = ld['GL Account Number'];              // NEW: use line item's GL

  const mapping = ubiChargeCodeMapping.find(m => {
    return String(m.vendor_id) === String(vendorId) &&
           String(m.account_number) === String(account) &&
           String(m.property_id) === String(propertyId) &&  // NEW: property in join
           String(m.gl_account) === String(glAccount);
  });

  return mapping; // Returns full object with charge_code, utility_name, gl_account_name
}

// When rendering line items, get full mapping for display
const mapping = getChargeCodeMappingForLineItem(lineData);
if (mapping) {
  // Display: "ELEC-RES-001 - Electricity - Electricity"
  displayText = `${mapping.charge_code} - ${mapping.utility_name} - ${mapping.gl_account_name}`;
} else {
  displayText = 'N/A';
}
```

**Update Add to UBI modal:**
```javascript
// Capture GL Account from line item
function openAddToUbiModal(line) {
  currentModalAccount = line['Account Number'];
  currentModalVendor = line['Vendor Name'];
  currentModalVendorId = line['Vendor ID'];
  currentModalProperty = line['Property Name'];
  currentModalPropertyId = line['Property ID'];
  currentModalGlAccount = line['GL Account Number'];      // NEW
  currentModalGlAccountName = line['GL Account Name'];    // NEW

  // Show all context in modal
  document.getElementById('ubiAccountDisplay').textContent =
    `${currentModalVendor} - ${currentModalAccount} - ${currentModalProperty} - ${currentModalGlAccount} (${currentModalGlAccountName})`;
}

// When submitting, send all fields including display names
async function submitAddToUbi() {
  const chargeCode = document.getElementById('ubiChargeCode').value;
  const selectedCc = chargeCodes.find(cc => cc.chargeCode === chargeCode);
  const utilityName = selectedCc ? selectedCc.utilityName : '';

  const fd = new FormData();
  fd.append('vendor_id', currentModalVendorId);           // Join key
  fd.append('vendor_name', currentModalVendor);           // Display
  fd.append('account_number', currentModalAccount);       // Join key
  fd.append('property_id', currentModalPropertyId);       // Join key
  fd.append('property_name', currentModalProperty);       // Display
  fd.append('gl_account', currentModalGlAccount);         // Join key
  fd.append('gl_account_name', currentModalGlAccountName); // Display - REQUIRED for legibility
  fd.append('charge_code', chargeCode);                   // Assignment value
  fd.append('utility_name', utilityName);                 // Display - REQUIRED for legibility

  // Send to /api/config/add-charge-code-mapping
  // ...
}
```

### 4. Config Page Redesign (templates/config.html)

**Current problems:**
- Two separate tables (tracker vs UBI mapping) with no visual connection
- Can't see which accounts have charge codes assigned
- No way to see/edit charge codes alongside tracker config

**Recommended: Single unified table with expandable charge code section**

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Vendor  │ Account │ Property      │ GL Acct   │ GL Name     │ Days │ IS UBI │ … │
├─────────────────────────────────────────────────────────────────────────────────┤
│ SMUD    │ 6390640 │ Arbors at CA  │ 5710-0000 │ Electricity │ 30   │ ☑      │ ▼ │
│   └─ Charge Codes:                                                              │
│       • 5710-0000 (Electricity) → ELEC-RES-001                         [Edit]   │
│       • 5720-0000 (Gas)        → GAS-RES-001                           [Edit]   │
│       • Add charge code mapping...                                     [+ Add]  │
├─────────────────────────────────────────────────────────────────────────────────┤
│ SoCalGas│ 1245241 │ Arbors at CA  │ 5720-0000 │ Gas         │ 30   │ ☐      │ ▼ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Benefits:**
- See all charge code mappings for each account in one place
- Visual indication of which accounts have UBI configured
- Easy to spot missing mappings
- Can expand/collapse charge code details

## Migration Path

### Phase 1: Data Migration (No UI changes)
1. Create new `ubi-charge-code-mapping` config in DynamoDB + S3
2. Migrate existing `ubi-mapping` data:
   - For each mapping in ubi-mapping
   - Look up the account in accounts-to-track to get GL account
   - Create new mapping with full fields
3. Keep both configs during transition

### Phase 2: Update BILLBACK page
1. Load new ubi-charge-code-mapping config
2. Update getChargeCodeForLineItem() to use GL account in match
3. Update Add to UBI modal to capture GL account
4. Test charge code lookups are working correctly

### Phase 3: Update CONFIG page
1. Create new unified table view
2. Add expandable charge code section
3. Add inline editing for charge codes
4. Remove old separate UBI mapping table

### Phase 4: Cleanup
1. Deprecate old ubi-mapping config
2. Remove debug logging
3. Update documentation

## Why This Fixes Your Issues

### Issue: "Account 6390640 SMUD not showing up"
**Fix:** With vendor ID + account + GL as composite key, matching will be precise and stable. No more name-based matching that breaks on typos or case differences.

### Issue: "Different charge codes on same invoice"
**Fix:** Each line item's GL account determines charge code. Multiple GL accounts = multiple charge codes, as needed.

### Issue: "Config interfaces suck ass"
**Fix:** Unified table shows complete picture. Expandable charge code section provides context without clutter. No more hunting across multiple pages.

### Issue: "Not matching, missing granularity"
**Fix:** Granularity increased from account-level to account+GL-level. All join keys are stable IDs, not names.

## Next Steps

1. **Review this design** - Does this solve your core problems?
2. **Prioritize phases** - Which phase should we tackle first?
3. **Data audit** - Check existing line items to see which GL accounts appear most frequently
4. **Confirm GL account availability** - Verify all line items have GL Account Number in the data

Let me know if this architecture makes sense or if you want changes before we start implementing.
