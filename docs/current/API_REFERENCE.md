# UBI Billback API Reference

**Last Updated:** 2025-11-17
**Total Endpoints:** 15

---

## Account Management

### POST `/api/ubi/add-to-tracker`
**Purpose:** Add account to tracker (monitoring only)

**Form Data:**
- `vendor_id` (required)
- `vendor_name` (required)
- `account_number` (required)
- `property_id` (required)
- `property_name` (required)

**Response:**
```json
{
  "ok": true,
  "existed": false
}
```

**Behavior:**
- Creates account with `is_tracked=true`, `is_ubi=false`
- Updates existing account to set `is_tracked=true`

---

### POST `/api/ubi/add-to-ubi`
**Purpose:** Add account to UBI program

**Form Data:**
- `vendor_id` (required)
- `vendor_name` (required)
- `account_number` (required)
- `property_id` (required)
- `property_name` (required)

**Response:**
```json
{
  "ok": true,
  "message": "Account added to UBI program"
}
```

**Behavior:**
- Sets `is_ubi=true` on existing account
- Creates new account with `is_ubi=true` if doesn't exist

---

## Line Item Management

### POST `/api/billback/update-line-item`
**Purpose:** Update line item with overrides and exclusions

**Form Data:**
- `bill_id` (required)
- `line_index` (required) - integer
- `charge_code` (optional)
- `charge_code_source` (optional) - "mapping" or "override"
- `charge_code_overridden` (optional) - "true"/"false" or "1"/"0"
- `charge_code_override_reason` (optional)
- `current_amount` (optional) - float
- `amount_overridden` (optional) - "true"/"false" or "1"/"0"
- `amount_override_reason` (optional)
- `is_excluded_from_ubi` (optional) - "0" or "1"
- `exclusion_reason` (optional)

**Response:**
```json
{
  "ok": true,
  "line": { /* updated line item */ }
}
```

---

### POST `/api/billback/assign-periods`
**Purpose:** Assign billback periods to a line item

**Form Data:**
- `bill_id` (required)
- `line_index` (required) - integer
- `assignments` (required) - JSON array string

**assignments format:**
```json
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

**Response:**
```json
{
  "ok": true
}
```

---

## GL Code Mapping

### GET `/api/config/gl-charge-code-mapping`
**Purpose:** Get property-aware GL code mappings

**Response:**
```json
{
  "items": [
    {
      "property_id": "1296739",
      "property_name": "Arbors at CA",
      "gl_code": "5730-0000",
      "gl_code_name": "Water",
      "charge_code": "WATER-RES-001",
      "utility_name": "Water",
      "is_billable": true,
      "notes": ""
    }
  ]
}
```

---

### POST `/api/config/gl-charge-code-mapping`
**Purpose:** Save GL code mappings

**JSON Body:**
```json
{
  "items": [
    {
      "property_id": "1296739",
      "property_name": "Arbors at CA",
      "gl_code": "5730-0000",
      "gl_code_name": "Water",
      "charge_code": "WATER-RES-001",
      "utility_name": "Water",
      "is_billable": true,
      "notes": ""
    }
  ]
}
```

**Response:**
```json
{
  "ok": true
}
```

**Notes:**
- Use `property_id: "*"` for wildcard (applies to all properties)
- Lookup order: exact property match first, then wildcard

---

## Master Bills

### POST `/api/master-bills/generate`
**Purpose:** Generate master bills by aggregating line items

**Form Data:**
- `start_date` (optional) - filter by period start
- `end_date` (optional) - filter by period end

**Response:**
```json
{
  "ok": true,
  "count": 45,
  "total_amount": 12500.00
}
```

**Behavior:**
- Scans all drafts
- Filters: `is_ubi=true` accounts, `is_excluded_from_ubi=0` lines
- Aggregates by: property_id + charge_code + utility_name + period
- Stores in config: "master-bills-latest"

---

### GET `/api/master-bills/list`
**Purpose:** List all generated master bills

**Response:**
```json
{
  "items": [
    {
      "master_bill_id": "1296739|WATER-001|Water|2024-01-01|2024-01-31",
      "property_id": "1296739",
      "property_name": "Arbors at CA",
      "ar_code_mapping": "WATER-001",
      "utility_name": "Water",
      "billback_month_start": "2024-01-01",
      "billback_month_end": "2024-01-31",
      "utility_amount": 225.00,
      "source_line_items": [
        {
          "bill_id": "ABC123",
          "line_index": 0,
          "gl_code": "5730-0000",
          "gl_code_name": "Water",
          "description": "Water usage",
          "amount": 100.00,
          "overridden": false,
          "override_reason": ""
        }
      ],
      "created_utc": "2024-01-15T10:00:00Z",
      "created_by": "user@example.com",
      "status": "draft"
    }
  ],
  "count": 45
}
```

---

### GET `/api/master-bills/detail/{master_bill_id}`
**Purpose:** Get drill-down detail of a specific master bill

**URL Parameters:**
- `master_bill_id` - URL-encoded master bill ID

**Response:**
```json
{
  "master_bill_id": "...",
  "property_id": "1296739",
  "ar_code_mapping": "WATER-001",
  "utility_amount": 225.00,
  "source_line_items": [ /* array of source line items */ ],
  /* ... other fields ... */
}
```

---

## UBI Batches

### POST `/api/ubi-batch/create`
**Purpose:** Create a new UBI billback batch

**Form Data:**
- `batch_name` (required)
- `period_start` (required) - YYYY-MM-DD
- `period_end` (required) - YYYY-MM-DD
- `memo` (required) - batch-level memo for all rows

**Response:**
```json
{
  "ok": true,
  "batch": {
    "batch_id": "UBI-2024-01-01-2024-03-31",
    "batch_name": "Q1 2024 UBI Billback",
    "period_start": "2024-01-01",
    "period_end": "2024-03-31",
    "memo": "Q1 2024 utilities billback...",
    "master_bill_ids": [ /* array of IDs */ ],
    "status": "draft",
    "total_master_bills": 45,
    "total_amount": 12500.00,
    "properties_count": 5,
    /* ... timestamps ... */
  }
}
```

**Behavior:**
- Filters master bills by date range
- Calculates totals and property counts
- Status: "draft"

---

### POST `/api/ubi-batch/finalize`
**Purpose:** Finalize a batch (mark as reviewed)

**Form Data:**
- `batch_id` (required)

**Response:**
```json
{
  "ok": true
}
```

**Behavior:**
- Sets `status: "finalized"`
- Sets `reviewed_utc` and `reviewed_by`
- Sets `run_date` (ISO8601 datetime string)

---

### GET `/api/ubi-batch/list`
**Purpose:** List all UBI batches

**Response:**
```json
{
  "items": [
    {
      "batch_id": "UBI-2024-01-01-2024-03-31",
      "batch_name": "Q1 2024 UBI Billback",
      "status": "finalized",
      "total_master_bills": 45,
      "total_amount": 12500.00,
      /* ... other fields ... */
    }
  ],
  "count": 3
}
```

---

### GET `/api/ubi-batch/detail/{batch_id}`
**Purpose:** Get detail of a specific batch

**URL Parameters:**
- `batch_id` - URL-encoded batch ID

**Response:**
```json
{
  "batch_id": "UBI-2024-01-01-2024-03-31",
  "batch_name": "Q1 2024 UBI Billback",
  "memo": "Q1 2024 utilities billback...",
  "master_bills": [ /* array of master bills in this batch */ ],
  /* ... other fields ... */
}
```

**Notes:**
- Includes full master bill objects for the batch
- Use for review before export

---

### POST `/api/ubi-batch/export-snowflake`
**Purpose:** Export batch to Snowflake SQL format

**Form Data:**
- `batch_id` (required)

**Response:**
```json
{
  "ok": true,
  "sql": "-- UBI Billback Export\n-- Batch: Q1 2024...\n\nINSERT INTO \"_Master_Bills\"...",
  "rows_exported": 45,
  "batch": { /* updated batch with status=exported */ }
}
```

**Behavior:**
- Validates batch is finalized
- Generates SQL INSERT statements
- Applies batch memo to all rows
- Applies batch run_date to all rows
- Marks batch as `status: "exported"`

**SQL Format:**
```sql
INSERT INTO "_Master_Bills"
("Property_ID", "AR_Code_Mapping", "Utility_Name", "Utility_Amount",
 "Billback_Month_Start", "Billback_Month_End", "RunDate", "Memo")
VALUES
('1296739', 'WATER-001', 'Water', '225.00', '2024-01-01', '2024-01-31', '2024-04-01T10:30:00Z', 'Q1 2024 billback...'),
('1296739', 'ELEC-001', 'Electricity', '450.00', '2024-01-01', '2024-01-31', '2024-04-01T10:30:00Z', 'Q1 2024 billback...');
```

---

## Updated Endpoint

### GET `/api/config/accounts-to-track`
**Purpose:** Get all tracked accounts

**Response:**
```json
{
  "items": [
    {
      "vendorId": "705870",
      "vendorName": "SMUD",
      "accountNumber": "6390640",
      "propertyId": "1296739",
      "propertyName": "Arbors at CA",
      "glAccountNumber": "",
      "glAccountName": "",
      "daysBetweenBills": 30,
      "is_tracked": true,
      "is_ubi": true,
      "notes": ""
    }
  ]
}
```

**Notes:**
- Now includes `is_tracked` and `is_ubi` flags
- Defaults: `is_tracked=true`, `is_ubi=false` if not present

---

## Data Flow Summary

### Complete Workflow:

```
1. Line Items
   ↓
   [POST /api/billback/update-line-item]
   [POST /api/billback/assign-periods]
   ↓
2. Generate Master Bills
   ↓
   [POST /api/master-bills/generate]
   ↓
3. Review Master Bills
   ↓
   [GET /api/master-bills/list]
   [GET /api/master-bills/detail/{id}]
   ↓
4. Create Batch
   ↓
   [POST /api/ubi-batch/create]
   ↓
5. Finalize Batch
   ↓
   [POST /api/ubi-batch/finalize]
   ↓
6. Export to Snowflake
   ↓
   [POST /api/ubi-batch/export-snowflake]
```

---

## Key Concepts

### Aggregation Key
Master bills aggregate by:
- `property_id`
- `ar_code_mapping` (charge code)
- `utility_name`
- `billback_month_start` + `billback_month_end`

**GL codes are NOT in the aggregation key** - they're preserved in `source_line_items` for drill-down.

### Export Logic
A line item is exported if:
1. Account has `is_ubi = true`
2. Line has `is_excluded_from_ubi = 0`
3. Line has valid charge code
4. Line has billback_assignments

### Batch Memo
- Applied at batch level (not per master bill or line item)
- Same memo used for all rows in the export
- Stored in batch object

### RunDate
- Set when batch is finalized
- ISO8601 datetime string (e.g., "2024-04-01T10:30:00Z")
- Stored as VARCHAR in Snowflake
- Applied to all rows in the export

---

## Error Handling

All endpoints return standard error format:

```json
{
  "error": "error message here"
}
```

HTTP Status Codes:
- `400` - Bad request (missing required fields, validation error)
- `404` - Not found (batch, master bill, draft not found)
- `500` - Server error (exception during processing)

---

## Frontend Usage Examples

### Example 1: Add Account to UBI

```javascript
async function addAccountToUbi(vendorId, accountNumber, propertyId) {
  const fd = new FormData();
  fd.append('vendor_id', vendorId);
  fd.append('vendor_name', 'SMUD');
  fd.append('account_number', accountNumber);
  fd.append('property_id', propertyId);
  fd.append('property_name', 'Arbors at CA');

  const response = await fetch('/api/ubi/add-to-ubi', {
    method: 'POST',
    body: fd
  });

  const result = await response.json();
  if (result.ok) {
    console.log('Added to UBI');
  }
}
```

### Example 2: Override Line Item Amount

```javascript
async function overrideAmount(billId, lineIndex, newAmount, reason) {
  const fd = new FormData();
  fd.append('bill_id', billId);
  fd.append('line_index', lineIndex);
  fd.append('current_amount', newAmount);
  fd.append('amount_overridden', 'true');
  fd.append('amount_override_reason', reason);

  await fetch('/api/billback/update-line-item', {
    method: 'POST',
    body: fd
  });
}
```

### Example 3: Generate and Export

```javascript
async function generateAndExportBatch() {
  // 1. Generate master bills
  await fetch('/api/master-bills/generate', {
    method: 'POST',
    body: new FormData()
  });

  // 2. Create batch
  const batchFd = new FormData();
  batchFd.append('batch_name', 'Q1 2024 UBI');
  batchFd.append('period_start', '2024-01-01');
  batchFd.append('period_end', '2024-03-31');
  batchFd.append('memo', 'Q1 2024 utilities billback');

  const batchResp = await fetch('/api/ubi-batch/create', {
    method: 'POST',
    body: batchFd
  });
  const batch = await batchResp.json();
  const batchId = batch.batch.batch_id;

  // 3. Finalize batch
  const finalizeFd = new FormData();
  finalizeFd.append('batch_id', batchId);
  await fetch('/api/ubi-batch/finalize', {
    method: 'POST',
    body: finalizeFd
  });

  // 4. Export
  const exportFd = new FormData();
  exportFd.append('batch_id', batchId);
  const exportResp = await fetch('/api/ubi-batch/export-snowflake', {
    method: 'POST',
    body: exportFd
  });
  const exportResult = await exportResp.json();

  console.log('SQL:', exportResult.sql);
}
```

---

**END OF API REFERENCE**
