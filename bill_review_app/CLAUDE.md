# Claude Code Instructions

## Deployment Rules
- **DO NOT deploy automatically** - Always ask the user before running deploy_app.ps1
- Wait for explicit user approval before deploying any changes
- Summarize what changes will be deployed and get confirmation first

## Project Overview
Bill Review App - A FastAPI web application for reviewing and processing utility bills.

### Key Files
- `main.py` - FastAPI backend with all API endpoints
- `templates/` - Jinja2 HTML templates
- `deploy_app.ps1` - Deployment script (CodeBuild + AppRunner)

### Data Flow
1. Bills are parsed by Lambda functions and stored in S3 as JSONL files
2. The app reads from S3 Stage 4 (enriched) files
3. User edits are saved as "header drafts" in DynamoDB
4. On submit, bills move through stages (Review -> POST -> BILLBACK -> Track)

### Key Architecture Concepts
- **pdf_id**: SHA1 hash of S3 key, used to identify invoices
- **Header drafts**: DynamoDB records (`draft#{pdf_id}#__header__#{user}`) that store user overrides for vendor, property, etc.
- **S3 JSONL files**: Each invoice is a JSONL file with one JSON record per line item
- **Cache invalidation**: `invalidate_day_cache(y, m, d)` clears cached data after updates

## Recent Session Notes (2025-11-28)

### Bulk Operations Added to Invoices Page
Added three bulk operations for selected invoices:
1. **Bulk Assign Property** - `/api/bulk_assign_property`
2. **Bulk Assign Vendor** - `/api/bulk_assign_vendor`
3. **Bulk Send to Rework** - `/api/bulk_rework`

Implementation details:
- Modals with searchable lists for property/vendor selection
- Updates both S3 files AND DynamoDB header drafts (critical for vendor to display correctly)
- Page reloads after bulk assignment to ensure UI reflects changes

### Bug Fixes Applied
1. **Table sorting not working** - Fixed missing `<thead>` tag in invoices.html
2. **Sort state not persisting** - Added localStorage persistence for sort column/direction
3. **Line count not updating** - Now appends extra lines to Stage 4 file on submit
4. **Checkbox too small** - Made entire first cell clickable with event.stopPropagation()
5. **Bulk vendor not sticking** - Now updates DynamoDB header drafts, not just S3
6. **Duplicate vendors in list** - Added deduplication by name in `/api/catalog/vendors`

### Important Patterns
- When updating vendor/property via bulk ops, MUST update both:
  1. S3 JSONL files (source of truth)
  2. DynamoDB header drafts (override source for display)
- The invoices page reads header drafts and uses them to override S3 values for display
- Vendor deduplication: `/api/catalogs` deduplicates, `/api/catalog/vendors` now also deduplicates

### UI Fixes
- Added `z-index: 100` to sticky headers across all templates
- Added `cursor: pointer` and `z-index: 1` to home page tiles
- Checkbox cells have `event.stopPropagation()` to prevent row click

## AWS Resources
- **S3 Bucket**: jrk-analytics-billing
- **DynamoDB Table**: jrk-bill-drafts (drafts), jrk-bill-metadata (metadata)
- **AppRunner**: arn:aws:apprunner:us-east-1:789814232318:service/jrk-bill-review/...
- **Region**: us-east-1
- **Profile**: jrk-analytics-admin
