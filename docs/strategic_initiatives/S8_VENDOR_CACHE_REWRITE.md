# S8: Vendor Cache Builder Rewrite

## Status: NOT STARTED
## Priority: HIGH — broken since March 10, blocking new vendor onboarding

## Problem

The `vendor-cache-builder` Lambda has been OOM-crashing on every run since ~March 10, 2026.
- Memory: was 512 MB (bumped to 2048 MB as emergency fix on 2026-04-10)
- The Entrata `getVendorLocations` API returns ALL vendors + locations in a single response
- `r.text[:2000]` in the debug logging forces the entire response into memory as a string
- Then `r.json()` parses it again — two copies of a massive payload
- Result: every hourly run OOMs, vendor cache stale for a month

### Additional issue: disconnected data paths
- The vendor-cache-builder writes to `api-vendor` S3 bucket
- The enricher Lambda reads from `jrk-analytics-billing/Bill_Parser_Enrichment/exports/dim_vendor/`
- These are completely separate — the cache builder never updates what the enricher reads
- The dim_vendor file was being updated by a Snowflake export (legacy), now synced manually

## Emergency Fixes Applied (2026-04-10)
1. Bumped Lambda memory from 512 MB → 2048 MB
2. Manually synced API vendor cache → dim_vendor enrichment file
3. Confirmed Astound (vendor ID 736344, code a736344) now in both locations

## Rewrite Tasks

### Phase 1: Make it reliable
- [ ] Stream the API response using `r.iter_content()` + `ijson` instead of `r.json()`
- [ ] Remove `r.text[:2000]` debug logging that forces full response into memory
- [ ] Write dim_vendor enrichment file directly (not just api-vendor bucket)
- [ ] Add CloudWatch alarm for Lambda errors so OOM failures don't go unnoticed for a month
- [ ] Add a health check that compares vendor count against previous run (alert if >10% drop)

### Phase 2: Make it efficient
- [ ] Paginate Entrata API calls instead of fetching all vendors at once
- [ ] Use `getVendors` with batched vendor IDs instead of `getVendorLocations` for everything
- [ ] Write results incrementally to S3 instead of accumulating all in memory
- [ ] Reduce memory back to 512 MB or 1024 MB after streaming is implemented

### Phase 3: Close the loop
- [ ] Make vendor-cache-builder the SINGLE source of truth for dim_vendor
- [ ] Remove any remaining Snowflake export dependency
- [ ] Add vendor count + last-updated timestamp to the app's health dashboard
- [ ] Alert if vendor cache is >24h stale
