# Billback.html Update Plan

## Current State
The existing billback.html focuses on UBI period assignment for unassigned line items. It has basic modals for "Add to Tracker" and "Add to UBI" but doesn't integrate with the new architecture.

## Required Changes

### 1. GL Code Mapping Auto-Lookup (CRITICAL)
**Where:** When rendering line items in `renderUnassignedBills()` (line ~782)
**Changes:**
- Load GL code mappings on page load
- For each line item, attempt to lookup charge code from property_id + gl_code
- Display "MAPPED" badge when charge code came from mapping
- Display "OVERRIDE" badge when user manually set it
- Auto-populate charge code field if mapping exists

### 2. Charge Code Override Tracking (CRITICAL)
**Where:** Add new modal after line 270
**Changes:**
- Create `chargeCodeOverrideModal`
- When user clicks to change charge code on a line item, open modal
- Modal shows current charge code and asks for:
  - New charge code (dropdown from charge codes)
  - Override reason (textarea, required)
- On submit, call `POST /api/billback/update-line-item` with:
  ```javascript
  charge_code: newChargeCode,
  charge_code_source: 'override',
  charge_code_overridden: true,
  charge_code_override_reason: reason
  ```

### 3. Amount Override Tracking (CRITICAL)
**Where:** Add new modal after charge code override modal
**Changes:**
- Create `amountOverrideModal`
- When user changes amount field on a line item, intercept and open modal
- Modal shows current amount and asks for:
  - New amount (number input)
  - Override reason (textarea, required)
- On submit, call `POST /api/billback/update-line-item` with:
  ```javascript
  current_amount: newAmount,
  amount_overridden: true,
  amount_override_reason: reason
  ```

### 4. Line Item Exclusions (CRITICAL)
**Where:** In line item rendering (line ~943)
**Changes:**
- Add checkbox column: "Exclude from UBI"
- When checkbox clicked, open modal asking for exclusion reason
- On submit, call `POST /api/billback/update-line-item` with:
  ```javascript
  is_excluded_from_ubi: 1,
  exclusion_reason: reason
  ```
- Show excluded lines with visual indicator (red badge, strikethrough, etc.)

### 5. Update Tracker/UBI Buttons (MEDIUM)
**Where:** Bill header rendering and modal handlers
**Changes:**
- Update "Add to Tracker" button to call `POST /api/ubi/add-to-tracker`
- Update "Add to UBI" button to call `POST /api/ubi/add-to-ubi`
- Make it clear these are INDEPENDENT operations
- Show account status badges (TRACKED, UBI, BOTH, NEITHER)

### 6. Visual Indicators (MEDIUM)
**Where:** Throughout line item rendering
**Changes:**
- Badge for "MAPPED" (charge code from GL mapping) - blue
- Badge for "OVERRIDDEN" (user changed charge code/amount) - yellow
- Badge for "EXCLUDED" (line excluded from UBI) - red
- Show override reasons on hover or in detail view

## Implementation Steps

1. Add new global variables for GL mappings
2. Add GL mapping load function
3. Create charge code override modal HTML
4. Create amount override modal HTML
5. Create exclusion reason modal HTML
6. Update line item rendering to:
   - Show checkboxes for exclusion
   - Auto-lookup charge codes from GL mappings
   - Show badges for mapped/overridden/excluded status
7. Add click handlers for override modals
8. Add submit functions that call proper endpoints
9. Update account button handlers to use new endpoints

## Code Locations

### Modals to Add (after line 270)
```html
<!-- Charge Code Override Modal -->
<div class="improve-modal-overlay" id="chargeCodeOverrideModal">
  ...
</div>

<!-- Amount Override Modal -->
<div class="improve-modal-overlay" id="amountOverrideModal">
  ...
</div>

<!-- Exclusion Reason Modal -->
<div class="improve-modal-overlay" id="exclusionModal">
  ...
</div>
```

### JavaScript Functions to Add
```javascript
let glCodeMappings = [];

async function loadGLCodeMappings() {
  const response = await fetch('/api/config/gl-charge-code-mapping');
  const data = await response.json();
  glCodeMappings = data.items || [];
}

function lookupChargeCode(propertyId, glCode) {
  // Try property-specific first
  let mapping = glCodeMappings.find(m =>
    m.property_id === propertyId && m.gl_code === glCode
  );
  // Fall back to wildcard
  if (!mapping) {
    mapping = glCodeMappings.find(m =>
      m.property_id === '*' && m.gl_code === glCode
    );
  }
  return mapping;
}

async function openChargeCodeOverrideModal(billId, lineIndex, currentChargeCode) {
  // Show modal, populate current value
  // On submit, call updateLineItemOverride
}

async function openAmountOverrideModal(billId, lineIndex, currentAmount) {
  // Show modal, populate current value
  // On submit, call updateLineItemOverride
}

async function openExclusionModal(billId, lineIndex) {
  // Show modal asking for reason
  // On submit, call updateLineItemExclusion
}

async function updateLineItemOverride(billId, lineIndex, overrideData) {
  const fd = new FormData();
  fd.append('bill_id', billId);
  fd.append('line_index', lineIndex);
  for (const [key, value] of Object.entries(overrideData)) {
    fd.append(key, value);
  }

  await fetch('/api/billback/update-line-item', {
    method: 'POST',
    body: fd
  });
}
```

## Testing Checklist

After implementation:
- [ ] GL code mappings load correctly
- [ ] Charge codes auto-populate from property+GL lookup
- [ ] "MAPPED" badge shows when from mapping
- [ ] Clicking charge code opens override modal
- [ ] Override modal saves reason and updates line item
- [ ] "OVERRIDDEN" badge shows after override
- [ ] Clicking amount opens override modal
- [ ] Amount override saves reason and updates line item
- [ ] Exclusion checkbox opens reason modal
- [ ] Exclusion saves and shows "EXCLUDED" badge
- [ ] Excluded lines visually distinct
- [ ] "Add to Tracker" calls correct endpoint
- [ ] "Add to UBI" calls correct endpoint
- [ ] Account status badges show correctly

## Estimated Lines of Code
- HTML (modals): ~200 lines
- JavaScript (functions): ~300 lines
- Updated rendering logic: ~200 lines
- Total: ~700 lines added/modified
