# Bill Review Application - Technical Documentation

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Data Pipeline Stages](#data-pipeline-stages)
3. [Key Concepts](#key-concepts)
4. [Application Modules](#application-modules)
5. [Database Tables](#database-tables)
6. [S3 Structure](#s3-structure)
7. [API Reference](#api-reference)
8. [Configuration](#configuration)
9. [Troubleshooting Guide](#troubleshooting-guide)
10. [Common Issues & Solutions](#common-issues--solutions)

---

## Architecture Overview

### Tech Stack
- **Backend**: FastAPI (Python)
- **Frontend**: Jinja2 templates with vanilla JavaScript
- **Database**: AWS DynamoDB
- **Storage**: AWS S3
- **Hosting**: AWS AppRunner
- **Build**: AWS CodeBuild

### Key Files
| File | Purpose |
|------|---------|
| `main.py` | FastAPI backend (~20,000 lines) - all API endpoints |
| `templates/` | Jinja2 HTML templates for each module |
| `deploy_app.ps1` | Deployment script (CodeBuild + AppRunner) |
| `CLAUDE.md` | AI assistant instructions |

### AWS Resources
- **S3 Bucket**: `jrk-analytics-billing`
- **Region**: `us-east-1`
- **Profile**: `jrk-analytics-admin`
- **AppRunner ARN**: `arn:aws:apprunner:us-east-1:789814232318:service/jrk-bill-review/...`

### Local Development
```powershell
python -m venv .venv
. .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```
Visit `http://localhost:8000`

---

## Data Pipeline Stages

Bills flow through numbered stages in S3. Each stage represents a step in the processing pipeline.

```
Stage 1: Pending Parsing     --> Raw PDFs uploaded for processing
Stage 2: Parsed Inputs       --> Lambda extracts text/data from PDFs
Stage 4: Enriched Outputs    --> Vendor/Property/GL enrichment added
Stage 5: Overrides           --> User edits stored (header drafts)
Stage 6: PreEntrata          --> Ready for Entrata posting
Stage 7: PostEntrata         --> Successfully posted to Entrata
Stage 8: UBI Assigned        --> UBI period assigned for billback
Stage 9: Flagged Review      --> Bills flagged for quality control
Stage 99: Historical Archive --> Archived/completed bills
```

### S3 Prefixes
```
Bill_Parser_1_Pending_Parsing/       - INPUT: Raw PDFs awaiting parsing
Bill_Parser_2_Parsed_Inputs/         - Parsed invoice data (also stores original PDFs)
Bill_Parser_4_Enriched_Outputs/      - Enriched with vendor/property/GL data
Bill_Parser_5_Overrides/             - User override data
Bill_Parser_6_PreEntrata_Submission/ - Ready for Entrata posting
Bill_Parser_7_PostEntrata_Submission/- Posted to Entrata (BILLBACK unassigned)
Bill_Parser_8_UBI_Assigned/          - UBI period assigned (BILLBACK assigned)
Bill_Parser_9_Flagged_Review/        - Flagged for quality review
Bill_Parser_99_Historical_Archive/   - Archived completed bills
Bill_Parser_Config/                  - Configuration files (JSON)
Bill_Parser_Rework_Input/            - Bills sent back for re-parsing
Bill_Parser_Failed_Jobs/             - Failed parsing jobs
Bill_Parser_Meter_Data/              - Meter consumption data
entrata_exports/                     - Dimension tables from Entrata
```

### Stage Transitions
```
INPUT --> PARSE:    Upload PDF --> Lambda parses --> Stage 4
PARSE --> POST:     User reviews --> Submits --> Stage 6
POST --> BILLBACK:  Posts to Entrata --> Stage 7 (Unassigned)
BILLBACK --> UBI:   Assigns UBI period --> Stage 8 (Assigned)
BILLBACK --> FLAG:  Flag for review --> Stage 9
```

---

## Key Concepts

### pdf_id (Invoice Identifier)
Every invoice has a unique `pdf_id` which is a SHA1 hash of the S3 key:
```python
import hashlib
pdf_id = hashlib.sha1(s3_key.encode()).hexdigest()
```

This ID is used to:
- Track invoices across all stages
- Store user edits (header drafts) in DynamoDB
- Link back to original PDF file

### Header Drafts
User edits are stored in DynamoDB as "header drafts" with this key format:
```
pk: draft#{pdf_id}#__header__#{user_email}
```

Header drafts contain overrides for:
- `vendor_id`, `vendor_name`
- `property_id`, `property_name`
- `gl_account_id`, `gl_account_number`
- `total_amount`
- Custom line items added by user

**CRITICAL**: When updating vendor/property via bulk operations, you MUST update BOTH:
1. S3 JSONL files (source of truth for the pipeline)
2. DynamoDB header drafts (override source for UI display)

If only one is updated, the UI will show inconsistent data.

### JSONL File Format
Each invoice is stored as a JSONL (JSON Lines) file with one JSON record per line item:
```jsonl
{"Line Number": 1, "Amount": 150.00, "GL Account Number": "6110", "Description": "Electric", ...}
{"Line Number": 2, "Amount": 50.00, "GL Account Number": "6120", "Description": "Late Fee", ...}
```

Common fields in each line:
- `EnrichedPropertyID`, `EnrichedPropertyName` - Property info
- `EnrichedVendorID`, `EnrichedVendorName` - Vendor info
- `EnrichedGLAccountID`, `EnrichedGLAccountNumber` - GL info
- `Amount`, `Total Amount` - Dollar amounts
- `Account Number` - Utility account number
- `Bill Period Start`, `Bill Period End` - Service dates
- `source_input_key` - Path to original PDF

### UBI (Utility Bill Imaging)
UBI is JRK's system for allocating utility costs to residents. Key concepts:

| Term | Description |
|------|-------------|
| **UBI Account** | An account marked for resident billback |
| **UBI Period** | Month/year for billback (format: `MM/YYYY`) |
| **Charge Code** | Category for allocation (ELEC, WATER, GAS, SEWER, TRASH, etc.) |
| **Master Bill** | Aggregated bill data by property/period for Snowflake export |

### Accounts to Track
The "tracker" is a configuration that determines which utility accounts should be:
- Monitored for incoming bills
- Flagged as UBI accounts (for resident billback)
- Assigned to specific AP team members
- Alerted when bills are overdue

Tracker data is stored in `jrk-bill-config` with key `accounts_to_track`.

---

## Application Modules

### INPUT (`/input`)
**Purpose**: Upload PDFs for parsing

**Features**:
- Drag-and-drop PDF upload
- Progress tracking
- Scraper integration for automated downloads from utility websites

**How it works**:
1. User uploads PDF
2. File is saved to `Bill_Parser_1_Pending_Parsing/`
3. Lambda trigger parses the PDF
4. Parsed data appears in PARSE module

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/upload_input` | POST | Upload PDF to S3 |
| `/api/scraper/providers` | GET | List available scrapers |
| `/api/scraper/import` | POST | Import from scraper |

---

### PARSE (`/parse`, `/invoices`, `/review`)
**Purpose**: Review and edit parsed invoices

**Features**:
- Date picker to select parse date
- Invoice list with sorting/filtering
- Detailed line item editing
- Bulk operations (assign property, vendor, rework)
- Select All toggle button

**Workflow**:
1. Select date from calendar
2. Review invoice list
3. Click invoice to edit details
4. Fix vendor/property/GL as needed
5. Submit to POST stage

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/invoices` | GET | List invoices for date |
| `/api/drafts` | GET | Get user's draft edits |
| `/api/drafts` | PUT | Save draft edits |
| `/api/submit` | POST | Submit invoice to next stage |
| `/api/bulk_assign_property` | POST | Bulk property assignment |
| `/api/bulk_assign_vendor` | POST | Bulk vendor assignment |
| `/api/bulk_rework` | POST | Send back for re-parsing |
| `/api/delete_parsed` | POST | Delete parsed invoice |

**Troubleshooting**:
- If invoice doesn't appear: Check S3 path `Bill_Parser_4_Enriched_Outputs/yyyy={year}/mm={month}/dd={day}/`
- If edits don't save: Check DynamoDB `jrk-bill-drafts` table
- If vendor shows wrong: Update both S3 file AND header draft

---

### POST (`/post`)
**Purpose**: Post invoices to Entrata accounting system

**Features**:
- View invoices ready for posting (Stage 6)
- Post individual or batch
- Track posting status
- View posting errors

**Workflow**:
1. Invoices submitted from PARSE appear here
2. Review and verify data
3. Click POST to send to Entrata
4. Successfully posted invoices move to Stage 7

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/post/total` | GET | Get posting queue count |
| `/api/post_to_entrata` | POST | Post invoice to Entrata |
| `/api/advance_to_post_stage` | POST | Move to post stage |

---

### BILLBACK (`/billback`)
**Purpose**: Assign UBI periods for resident billback

**Features**:
- Two tabs: Unassigned (Stage 7) and Assigned (Stage 8)
- Filter by property, vendor, GL code, utility type, tracker status
- Assign UBI periods to line items
- Track UBI vs non-UBI accounts
- Refresh GL mappings for charge codes
- Flag bills for review

**Filter Behavior** (Important!):
- Filters load on page init from `/api/billback/ubi/filter-options`
- Checking filter boxes applies **instantly** (client-side, no server reload)
- "Refresh Lists" button reloads filter options from server
- "Refresh GL Mappings" only processes **filtered bills**, not all loaded bills
- Data is cached locally - changing filters doesn't reload from server

**Utility Type Filter**:
Filters bills by utility type (Electric, Gas, Water, Sewer, Trash, Storm, Other).
Checks both `Utility Type` field and charge code patterns.

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/billback/ubi/filter-options` | GET | Get filter dropdown options |
| `/api/billback/ubi/unassigned` | GET | Get unassigned bills (Stage 7) |
| `/api/billback/ubi/assign` | POST | Assign UBI period |
| `/api/billback/ubi/assigned` | GET | Get assigned bills (Stage 8) |
| `/api/billback/ubi/unassign` | POST | Remove UBI assignment |
| `/api/billback/update-line-item` | POST | Update charge code |
| `/api/billback/flag` | POST | Flag bill for review |

**Troubleshooting**:
- Property not in filter: Check `/api/billback/ubi/filter-options` - now includes all properties from dimension table
- GL mapping not applying: Check if `charge_code_overridden = true` on line item
- Filter changes reload data: They shouldn't - filters are client-side only

---

### MASTER BILLS (`/master-bills`)
**Purpose**: View aggregated UBI bills by property/period for Snowflake export

**Features**:
- Generate master bills from Stage 8 data
- View by period and property
- Manual data upload (CSV) for external systems (Yardi, RealPage)
- Download CSV template
- Export to Snowflake via UBI Batch

**Manual Upload**:
1. Click "Template CSV" to download format
2. Fill in: property_name, charge_code, amount, ubi_period (MM/YYYY), utility_type, etc.
3. Click "Upload Manual Data"
4. Entries display with "MANUAL" badge
5. Can delete individual entries or entire batches

**Date Format**:
- `ubi_period`: `MM/YYYY` (e.g., `02/2026`)
- `billback_month_start`: `MM/01/YYYY` (first day of month)
- `billback_month_end`: `MM/DD/YYYY` (last day of month)

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/master-bills/generate` | POST | Generate master bills from Stage 8 |
| `/api/master-bills/list` | GET | List master bills |
| `/api/master-bills/upload-manual` | POST | Upload manual CSV |
| `/api/master-bills/manual-template` | GET | Download CSV template |
| `/api/master-bills/manual-entries` | GET | List manual entries |
| `/api/master-bills/manual-entry/{id}` | DELETE | Delete single entry |
| `/api/master-bills/manual-batch/{id}` | DELETE | Delete entire batch |

---

### FLAGGED REVIEW (`/flagged`)
**Purpose**: Quality control for bills flagged during BILLBACK

**Features**:
- View bills flagged for review (Stage 9)
- Add notes and comments
- Generate emails for vendor issues
- Confirm reviewed or unflag to return to BILLBACK

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/flagged` | GET | List flagged bills |
| `/api/billback/flag` | POST | Flag a bill |
| `/api/flagged/unflag` | POST | Remove flag, return to Stage 7 |
| `/api/flagged/confirm` | POST | Confirm reviewed |
| `/api/flagged/generate-email` | POST | Generate issue email |

---

### PRINT CHECKS (`/print-checks`)
**Purpose**: AP reps create check slips for Treasury review

**Features**:
- Load posted invoices (Stage 7) grouped by vendor
- Select invoices to include in check slip
- Create check slip with total
- Generate PDF with merged invoice PDFs

**Workflow**:
1. Select date range and load invoices
2. Expand vendor accordion
3. Select invoices to include
4. Click "Create Check Slip"
5. Download PDF for Treasury

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/print-checks/posted-invoices` | GET | Get posted invoices |
| `/api/print-checks/create-slip` | POST | Create check slip |
| `/api/print-checks/my-slips` | GET | List user's slips |
| `/api/print-checks/slip/{id}` | GET | Get slip details |
| `/api/print-checks/slip/{id}` | DELETE | Delete slip |
| `/api/print-checks/slip/{id}/pdf` | GET | Generate PDF |

---

### REVIEW CHECKS (`/review-checks`)
**Purpose**: Treasury approval of check slips

**Features**:
- View pending check slips by date
- Two-panel layout: list on left, details on right
- Review individual invoices within slip
- Approve or reject slips
- Checkbox to mark invoices as reviewed

**Workflow**:
1. Select date to view slips
2. Click slip to see details
3. Review each invoice (click to expand)
4. Check checkbox when invoice reviewed
5. Approve or reject entire slip

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/review-checks/pending` | GET | List pending slips for date |
| `/api/review-checks/slip/{id}` | GET | Get slip details |
| `/api/review-checks/slip/{id}/invoice/{idx}/pdf` | GET | Get invoice PDF |
| `/api/review-checks/approve/{id}` | POST | Approve slip |
| `/api/review-checks/reject/{id}` | POST | Reject slip |

---

### WORKFLOW (`/workflow`)
**Purpose**: Track account status and AP team assignments

**Features**:
- Prioritized account list
- Overdue bill alerts
- AP team assignments
- Vacant account tracking
- Status notes and comments

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/workflow` | GET | Get workflow data |
| `/api/workflow/ap-priority` | GET | AP priority list |
| `/api/workflow/accounts/update` | POST | Update account status |
| `/api/workflow/vacant-accounts` | GET | Get vacant accounts |

---

### TRACK (`/track`)
**Purpose**: Monitor utility account status

**Features**:
- Account status overview
- Missing bill alerts
- UBI tracking status
- Last bill received dates

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/track` | GET | Get tracking data |

---

### METRICS (`/metrics`)
**Purpose**: Processing statistics and productivity tracking

**Features**:
- User timing data (processing speed)
- Parsing volume by date
- Override tracking (manual edits)
- Outlier detection (unusual amounts)
- Submitter statistics
- Login history

**API Endpoints**:
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/metrics/user-timing` | GET | User processing times |
| `/api/metrics/parsing-volume` | GET | Volume by date |
| `/api/metrics/submitter-stats` | GET | Submitter statistics |
| `/api/metrics/overrides` | GET | Override history |
| `/api/metrics/outliers` | GET | Unusual amounts |
| `/api/metrics/logins` | GET | Login history |

---

### CONFIG (`/config`)
**Purpose**: System configuration

**Subpages**:
| Page | Purpose |
|------|---------|
| `/config/gl-code-mapping` | GL to charge code mappings |
| `/config/account-tracking` | Account tracker settings |
| `/config/ap-team` | AP team member list |
| `/config/ubi-mapping` | UBI configuration |
| `/config/charge-codes` | Charge code definitions |
| `/config/uom-mapping` | Unit of measure mappings |
| `/config/workflow-reasons` | Workflow status reasons |

---

### Additional Modules

| Module | Path | Purpose |
|--------|------|---------|
| SEARCH | `/search` | Search across all parse dates |
| HISTORY | `/history` | View archived bills |
| CHART BY METER | `/chart-by-meter` | Visualize meter consumption |
| UBI BATCH | `/ubi-batch` | Manage Snowflake exports |
| VENDOR CORRECTIONS | `/vendor-corrections` | Fix vendor mismatches |
| GAP ANALYSIS | `/account-gap-analysis` | Compare tracked vs external accounts |
| FAILED JOBS | `/failed` | Review parsing failures |
| DEBUG | `/debug` | Bug reports and enhancement requests |

---

## Database Tables

### DynamoDB Tables

| Table | Purpose | Primary Key |
|-------|---------|-------------|
| `jrk-bill-drafts` | User draft edits | `pk` (draft#{pdf_id}#...) |
| `jrk-bill-config` | Configuration data | `config_key` |
| `jrk-bill-review` | Review metadata | `pdf_id` |
| `jrk-bill-review-debug` | Bug reports | `report_id` |
| `jrk-bill-parser-errors` | Parsing errors | `job_id` |
| `jrk-check-slips` | Check slip records | `check_slip_id` |
| `jrk-check-slip-invoices` | Invoice-to-slip mapping | `pdf_id` |
| `jrk-manual-billback-entries` | Manual billback data | `entry_id` |
| `jrk-url-short` | URL shortener | `short_code` |

### Config Items (jrk-bill-config)
| config_key | Purpose |
|------------|---------|
| `accounts_to_track` | Account tracking configuration |
| `ubi_mapping` | UBI period mappings |
| `ap_team` | AP team member list |
| `charge_codes` | Charge code definitions |
| `gl_charge_code_mapping` | GL to charge code mappings |
| `workflow_reasons` | Workflow status reasons |
| `uom_mapping` | Unit of measure mappings |

---

## S3 Structure

### Date-Partitioned Data
Most stage data is partitioned by parse date:
```
Bill_Parser_X_Stage/yyyy={year}/mm={month}/dd={day}/{pdf_id}.jsonl
```

Example:
```
Bill_Parser_4_Enriched_Outputs/yyyy=2026/mm=01/dd=27/abc123def456.jsonl
```

### Dimension Tables (from Entrata)
```
entrata_exports/dim_vendor/latest.json.gz     - Vendor list
entrata_exports/dim_property/latest.json.gz   - Property list
entrata_exports/dim_gl_account/latest.json.gz - GL accounts
entrata_exports/dim_uom_mapping/latest.json.gz - Unit of measure
```

### Configuration Files
```
Bill_Parser_Config/accounts_to_track.json
Bill_Parser_Config/ubi_mapping.json
Bill_Parser_Config/ap_team.json
Bill_Parser_Config/gl_charge_code_mapping.json
Bill_Parser_Config/charge_codes.json
```

---

## Troubleshooting Guide

### Invoice Not Showing in PARSE
1. **Check S3**: Look for file in `Bill_Parser_4_Enriched_Outputs/yyyy={year}/mm={month}/dd={day}/`
2. **Verify date**: Make sure you selected the correct parse date
3. **Check Lambda**: Review Lambda logs for parsing errors
4. **Check FAILED JOBS**: Look for the file in `/failed` module

### Vendor/Property Not Displaying Correctly
1. **Check header draft**: Query DynamoDB `jrk-bill-drafts` for `draft#{pdf_id}#__header__#*`
2. **Check S3 JSONL**: Verify `EnrichedVendorName`/`EnrichedPropertyName` in file
3. **Update both**: If fixing, update BOTH S3 file AND DynamoDB header draft

### BILLBACK Filters Not Showing All Properties
1. Filter options come from `/api/billback/ubi/filter-options`
2. Scans last 60 days of Stage 7 data by default
3. **Now also includes** all properties from dimension table
4. Click "Refresh Lists" to reload from server

### GL Mappings Not Applying
1. Check config at `/config/gl-code-mapping`
2. Verify property ID and GL account ID match **exactly**
3. Use "Refresh GL Mappings" button (applies to filtered bills only)
4. Check if line item has `charge_code_overridden = true` (manual override takes precedence)

### UBI Period Not Assigned
1. Bill must be in Stage 7 (PostEntrata)
2. Account must be marked as UBI in tracker
3. Check API response from `/api/billback/ubi/unassigned`
4. Verify account key format: `{propertyId}|{vendorId}|{accountNumber}`

### Check Slip PDF Missing Invoice PDFs
1. Verify original PDFs exist in S3 `Bill_Parser_2_Parsed_Inputs/`
2. Check `source_input_key` field in invoice JSONL data
3. Original PDF path should be valid

### Cache Issues
The app uses in-memory caching (5-15 minute TTL). To clear:
1. **Restart AppRunner** - full cache clear
2. **Use `?refresh=1`** - on specific API calls
3. **Wait for TTL** - caches auto-expire

### AppRunner Deployment Stuck
1. Check service status: `aws apprunner describe-service --service-arn <ARN>`
2. If `OPERATION_IN_PROGRESS`, wait for it to complete
3. If stuck, may need to manually stop/start service

---

## Common Issues & Solutions

### Issue: "Invoice shows wrong vendor after bulk assign"
**Cause**: Only S3 was updated, not DynamoDB header draft
**Solution**: Bulk assign now updates both. For old data, manually fix header draft.

### Issue: "BILLBACK filters don't show my property"
**Cause**: Property had no Stage 7 data in last 60 days
**Solution**: Fixed - now includes all properties from dimension table.

### Issue: "Refresh GL Mappings processes everything"
**Cause**: Was processing ALL loaded bills
**Solution**: Fixed - now only processes bills matching current filters.

### Issue: "Filter changes trigger server reload"
**Cause**: User clicking "Apply & Reload" button
**Solution**: Just check filter boxes - they apply instantly. "Done" button closes drawer.

### Issue: "Manual billback entries show wrong dates"
**Cause**: Period format mismatch (MM/YYYY vs date format)
**Solution**: Fixed - converts `ubi_period` to proper `billback_month_start/end` format.

### Issue: "Check slips table error"
**Cause**: DynamoDB table doesn't exist
**Solution**: Create `jrk-check-slips` and `jrk-check-slip-invoices` tables with IAM permissions for AppRunner role.

### Issue: "Can't click checkboxes in REVIEW CHECKS"
**Cause**: Checkbox too small and not obvious
**Solution**: Fixed - larger checkbox (32x32px) with ☐ symbol and instruction banner.

---

## Deployment

### Deploy to AppRunner
```powershell
.\deploy_app.ps1
```

This script:
1. Creates a lean zip archive (~1MB)
2. Uploads to S3 `tmp/jrk-bill-review/source.zip`
3. Triggers CodeBuild
4. Waits for build completion
5. Starts AppRunner deployment

### Check Deployment Status
```bash
aws apprunner describe-service \
  --service-arn "arn:aws:apprunner:us-east-1:789814232318:service/jrk-bill-review/..." \
  --query "Service.Status" \
  --profile jrk-analytics-admin
```

### Manual Deployment Steps
```powershell
# 1. Create zip (automated by deploy script)
# 2. Upload to S3
aws s3 cp bill_review_app_src.zip s3://jrk-analytics-billing/tmp/jrk-bill-review/source.zip

# 3. Start CodeBuild
aws codebuild start-build --project-name jrk-bill-review-build --profile jrk-analytics-admin

# 4. Trigger AppRunner (after build completes)
aws apprunner start-deployment --service-arn <ARN> --profile jrk-analytics-admin
```

---

## Development Notes

### Adding a New Module
1. Add route in `main.py`:
```python
@app.get("/new-module", response_class=HTMLResponse)
def page_new_module(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse("new_module.html", {"request": request, "user": user})
```

2. Create template `templates/new_module.html`
3. Add tile to `templates/landing.html`
4. Add API endpoints as needed

### Adding a New API Endpoint
```python
@app.get("/api/new-endpoint")
def api_new_endpoint(user: str = Depends(require_user)):
    try:
        # Implementation
        return {"data": result}
    except Exception as e:
        return JSONResponse({"error": _sanitize_error(e, "request")}, status_code=500)
```

### Frontend Patterns
```javascript
// Toast notifications
showToast('Success message', 'ok');
showToast('Error message', 'err');

// Loading spinner
showLoading('Processing...');
hideLoading();

// API calls
const response = await fetch('/api/endpoint', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
});
if (!response.ok) throw new Error('Request failed');
const result = await response.json();
```

### Caching Pattern
```python
cache_key = ("cache_name", param1, param2)
cached = _CACHE.get(cache_key)
if cached and (time.time() - cached.get("ts", 0) < CACHE_TTL_SECONDS):
    return cached.get("data")
# ... fetch data ...
_CACHE[cache_key] = {"ts": time.time(), "data": result}
```

---

## Contact & Support

- **Report bugs**: Click IMPROVE button on landing page
- **View reports**: Check DEBUG module (`/debug`)
- **Bug tracking**: DynamoDB table `jrk-bill-review-debug`
