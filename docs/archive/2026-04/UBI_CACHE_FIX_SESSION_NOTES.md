# UBI Assignment Cache Bug — Session Notes (2026-04-06)

## The Problem

Assigned ~200 invoices to UBI periods in BILLBACK, refreshed the page, and all 200 came back as unassigned. Fear of double-assigning and creating duplicate billing records.

## Root Cause

**AppRunner runs 2 instances, but the UBI unassigned cache is in-memory (per-instance).**

1. User assigns 200 bills → request hits Instance A
2. Instance A writes assignments to DynamoDB → invalidates its own in-memory cache
3. User refreshes the page → request hits Instance B
4. Instance B still has its old cache (5-minute TTL) → serves stale data
5. All 200 bills appear as unassigned again

Secondary issue: No dedup guard on the assign endpoint. If you re-assigned a bill that reappeared from the stale cache, it would create a duplicate record in `jrk-bill-ubi-assignments` (each assignment gets a new UUID primary key).

## The Fix

### Fix 1: Cross-Instance Cache Invalidation (DDB Generation Counter)

**File:** `bill_review_app/main.py`

- `_invalidate_ubi_cache()` now writes a generation timestamp to DynamoDB (`CONFIG#ubi-cache-gen` in `jrk-bill-config`) in addition to clearing local cache
- `_get_ubi_unassigned_cached()` checks this DDB timestamp on every cached request (single GetItem, ~5ms)
- If stale (another instance invalidated after our cache was built), calls new `_patch_ubi_cache_from_ddb()`
- **`_patch_ubi_cache_from_ddb()`** — scans DDB for all assigned/archived line hashes and filters them out of the existing cached list. **No S3 reads at all.** Does NOT trigger the full expensive recompute. Just DDB scans + in-memory filtering (seconds, not minutes).
- Full recompute still only happens on TTL expiry (5 min) or explicit `refresh=1`

### Fix 2: Dedup Guard on Assignment Endpoint

- `/api/billback/ubi/assign` now scans `jrk-bill-ubi-assignments` for existing `line_hash` values before writing
- Skips any hash that's already assigned, logs `SKIP duplicate`
- Response now includes `skipped_duplicates` count
- Safe to re-assign bills that reappeared from stale cache — they'll just be skipped

## Flow After Fix

```
Instance A: assign 200 bills
  → write 200 records to DDB
  → clear local cache
  → write generation timestamp to DDB (CONFIG#ubi-cache-gen)

Instance B: user refreshes
  → local cache looks valid (within 5-min TTL)
  → check DDB generation timestamp
  → generation > cache timestamp → STALE
  → call _patch_ubi_cache_from_ddb()
  → scan DDB for assigned hashes (fast, no S3)
  → filter assigned bills out of cached list
  → return patched list (200 bills removed)
```

## Deployment Status

- **Committed:** `b143484` on `main`
- **NOT YET PUSHED** — `git push origin main` was blocked by SSL cert issue
- After push, auto-deploys via CodeBuild + AppRunner
- Need to run `git push origin main` manually from terminal
