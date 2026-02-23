# Claude Code Instructions

## Deployment Rules
- **DO NOT deploy automatically** - Always ask the user before running deploy_app.ps1
- Wait for explicit user approval before deploying any changes
- Summarize what changes will be deployed and get confirmation first

## Project Overview
Bill Review App - A FastAPI web application for reviewing and processing utility bills at JRK Residential.

### Key Files
- `main.py` - FastAPI backend (~20,000 lines) with all API endpoints
- `templates/` - Jinja2 HTML templates for each module
- `deploy_app.ps1` - Deployment script (CodeBuild + AppRunner)
- `README.md` - Comprehensive technical documentation

### Data Pipeline (S3 Stages)
```
Stage 1: Bill_Parser_1_Pending_Parsing/    - Raw PDFs for parsing
Stage 2: Bill_Parser_2_Parsed_Inputs/      - Parsed data + original PDFs
Stage 4: Bill_Parser_4_Enriched_Outputs/   - Enriched with vendor/property
Stage 6: Bill_Parser_6_PreEntrata/         - Ready for Entrata posting
Stage 7: Bill_Parser_7_PostEntrata/        - Posted (BILLBACK unassigned)
Stage 8: Bill_Parser_8_UBI_Assigned/       - UBI period assigned
Stage 9: Bill_Parser_9_Flagged_Review/     - Flagged for QC
```

### Key Architecture Concepts
- **pdf_id**: SHA1 hash of S3 key, used to identify invoices across stages
- **Header drafts**: DynamoDB records (`draft#{pdf_id}#__header__#{user}`) storing user overrides
- **S3 JSONL files**: Each invoice is a JSONL file with one JSON record per line item
- **UBI**: Utility Bill Imaging - resident billback system with periods in MM/YYYY format

### Critical Pattern: Dual Updates
When updating vendor/property via bulk operations, you MUST update BOTH:
1. S3 JSONL files (source of truth for pipeline)
2. DynamoDB header drafts (override source for UI display)

If only one is updated, the UI will show inconsistent data.

## AWS Resources
- **S3 Bucket**: jrk-analytics-billing
- **Region**: us-east-1
- **Profile**: jrk-analytics-admin
- **AppRunner ARN**: arn:aws:apprunner:us-east-1:789814232318:service/jrk-bill-review/...

### DynamoDB Tables
| Table | Purpose |
|-------|---------|
| `jrk-bill-drafts` | User draft edits (pk: draft#{pdf_id}#...) |
| `jrk-bill-config` | Configuration (config_key) |
| `jrk-check-slips` | Check slip records |
| `jrk-check-slip-invoices` | Invoice-to-slip mapping |
| `jrk-manual-billback-entries` | Manual billback CSV uploads |

## Module Quick Reference

### PARSE (/parse, /invoices)
- Reviews parsed invoices by date
- Bulk operations: property, vendor, rework
- Submits to POST stage

### BILLBACK (/billback)
- Assigns UBI periods for resident billback
- **Filters are client-side** - no server reload when changing filters
- "Refresh GL Mappings" only processes FILTERED bills
- Utility type filter: Electric, Gas, Water, Sewer, Trash, Storm

### MASTER BILLS (/master-bills)
- Aggregated UBI data by property/period
- Manual CSV upload for external systems (Yardi, RealPage)
- Date format: ubi_period=MM/YYYY, billback_month_start=MM/01/YYYY

### PRINT CHECKS / REVIEW CHECKS
- AP creates check slips from posted invoices
- Treasury reviews and approves
- PDFs merge original invoice PDFs

## Common Troubleshooting

### Invoice not appearing
1. Check S3: `Bill_Parser_4_Enriched_Outputs/yyyy={year}/mm={month}/dd={day}/`
2. Check FAILED JOBS module

### Vendor/property wrong after edit
1. Check DynamoDB header draft
2. Check S3 JSONL file
3. Update BOTH if fixing

### BILLBACK filter missing property
- Filter options include ALL properties from dimension table
- Click "Refresh Lists" to reload

### GL mapping not applying
- Check `/config/gl-code-mapping`
- Check if `charge_code_overridden = true` (manual override wins)

## Known Issues / Planned Fixes

### BILLBACK Multi-Period Suggestion (NOT YET IMPLEMENTED)
**Problem:** When a bill covers multiple months (e.g., quarterly bill), the "SUGGESTED ASSIGN TO PERIOD" only suggests a single month instead of splitting across the actual service period.

**Root cause:** `_calculate_ubi_suggestion()` in main.py:8632 ignores service dates and always returns a single period.

**Workaround:** User must manually change the "Months" input field before clicking Accept.

**Fix plan:** See `docs/MULTI_PERIOD_SUGGESTION_FIX.md` for complete implementation plan.

## Recent Fixes (2026-01)

### Performance Monitoring System (main.py ~231-404, templates/perf.html)
Server-side request timing middleware that logs every API request's duration.

**Architecture:**
- `@app.middleware("http")` wraps every request, records path/method/status/ms/user
- **Ring buffer**: `collections.deque(maxlen=50_000)` for raw request records (~24h)
- **Hourly rollups**: Per-endpoint stats (count, avg, min, max, p50, p95, p99, errors)
- **DynamoDB persistence**: Rollups saved to `jrk-bill-config` (PK=`CONFIG#perf-rollup`, SK=hour key)
- **Thread safety**: `_PERF_LOG_LOCK` and `_PERF_ROLLUPS_LOCK` protect concurrent access
- **Path normalization**: Dynamic URL segments collapsed (e.g. `/api/timing/abc123` → `/api/timing/{id}`)
- Skips: `/static/`, `/favicon`, `/login`, `/logout`
- Adds `Server-Timing` response header (visible in browser DevTools)

**Endpoints:**
| Endpoint | Purpose |
|----------|---------|
| `GET /perf` | Dashboard page (admin-only) with Chart.js charts |
| `GET /api/perf/live?minutes=60` | Raw recent requests + summary stats |
| `GET /api/perf/rollups?days=7` | Hourly rollup data for charting |
| `GET /api/perf/slow?threshold_ms=3000&minutes=60` | Requests above threshold |

**Key functions:** `_perf_record()`, `_perf_compute_rollup()`, `_perf_percentile()`, `_perf_maybe_persist_hour()`, `_perf_update_current_hour()`, `_perf_load_historical_rollups()`

**Tests:** `test_perf_and_tracker.py` — 62 tests covering path normalization, percentile edge cases, rollup aggregation, recording/skipping, hour transitions, DDB persistence/pagination, thread safety, and all is_tracked fixes.

### UBI Tracker `is_tracked` Bug Fixes
**Problem:** Double-click to remove accounts from UBI Master Bills completion tracker didn't persist — accounts reappeared on refresh.

**Root cause:** Completion tracker filtered by `is_ubi` only, ignoring `is_tracked` flag. Additionally, 5 other code paths had the same gap.

**Fixes applied (6 locations in main.py):**
1. **Completion tracker** (~line 13495): Added `and acc.get("is_tracked", True)` filter + dedup with `seen_accounts` set
2. **UBI unassigned bills** (3 endpoints ~lines 3387, 3562, 4085): Added `is_tracked` check
3. **Gap analysis** (~line 7078): Added `is_tracked` filter to `active_tracked`
4. **Vacant detection** (~line 6235): Added `continue` for untracked accounts

### UBI Tracker Duplicate Entry Fixes
**Problem:** Same bill appeared twice in tracker for a single period.

**Root causes & fixes:**
1. **Remove-from-tracker used `break`** (~line 12120): Removed `break` so ALL matching duplicates get `is_tracked = False`
2. **Remove-from-ubi same bug** (~line 11907): Same fix
3. **Add-to-tracker mismatched dedup keys**: Now matches by name OR ID to prevent duplicates from different code paths

### CHECK SLIP PDF Error Handling
- PDF generation errors tracked per-invoice in DynamoDB
- Error badges shown on check slips with failures
- "Regenerate PDF" button to retry failed PDFs
- Non-blocking - process continues even if some PDFs fail

### BILLBACK Improvements
- Filter options now include all properties from dimension table
- Added Utility Type filter (Electric, Gas, Water, etc.)
- "Refresh GL Mappings" only processes filtered subset
- Removed "Apply & Reload" - filters apply instantly (client-side)
- Added "Refresh Lists" button to reload filter options

### MASTER BILLS Manual Upload
- CSV upload for external system data
- Proper date conversion: ubi_period (MM/YYYY) -> billback dates (MM/DD/YYYY)
- "MANUAL" badge for uploaded entries

### REVIEW CHECKS
- Fixed checkbox visibility (32x32px, ☐ symbol)
- Added instruction banner for pending invoices

## Code Patterns

### API Endpoint
```python
@app.get("/api/endpoint")
def api_endpoint(user: str = Depends(require_user)):
    try:
        # implementation
        return {"data": result}
    except Exception as e:
        return JSONResponse({"error": _sanitize_error(e, "request")}, status_code=500)
```

### Frontend Filter Pattern
```javascript
// Client-side filtering - no server reload
function applyFilters() {
    // Read checkbox states into filter object
    // Call renderBills() which filters locally
}

// Helper to get filtered data
function getFilteredBills() {
    return allBills.filter(bill => {
        // Apply all filter criteria
        return true;
    });
}
```

### Caching Pattern
```python
cache_key = ("name", param1, param2)
cached = _CACHE.get(cache_key)
if cached and (time.time() - cached.get("ts", 0) < CACHE_TTL_SECONDS):
    return cached.get("data")
# ... fetch data ...
_CACHE[cache_key] = {"ts": time.time(), "data": result}
```
