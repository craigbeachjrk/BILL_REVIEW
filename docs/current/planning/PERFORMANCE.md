# Bill Review App — Performance & Timing Architecture

## Performance Monitoring System

**Location:** `main.py:542-721`, `templates/perf.html`

The app has a built-in request timing middleware that wraps every HTTP request:

- **Ring buffer** (`_PERF_LOG`): `deque(maxlen=50,000)` stores raw records (~24h of data) with path, method, status, ms, timestamp, user
- **Hourly rollups** (`_PERF_ROLLUPS`): Per-endpoint aggregated stats (count, avg, min, max, p50, p95, p99, errors)
- **DynamoDB persistence**: Rollups auto-persist on hour boundaries to `jrk-bill-config` (PK=`CONFIG#perf-rollup`, SK=hour key)
- **Server-Timing header**: Added to every response (visible in browser DevTools)
- **Path normalization**: Dynamic URL segments collapsed for aggregation (e.g., `/api/timing/abc123` → `/api/timing/{id}`)
- **Skips**: `/static/`, `/favicon`, `/login`, `/logout`

### Dashboard (`/perf`, admin-only)

| Tab | Data Source | Description |
|-----|-------------|-------------|
| Endpoints | `/api/perf/live?minutes=N` | Top 10 slowest endpoints by P95, color-coded badges |
| Timeline | `/api/perf/rollups?days=N` | Chart.js line (P50/P95) + bar (volume) charts over time |
| Slow Requests | `/api/perf/slow?threshold_ms=N` | Requests exceeding threshold, sorted by duration |
| By User | Client-side from `/api/perf/live` | Per-user request counts, avg/P50/P95/max |

Auto-refreshes every 30 seconds.

---

## System Bottlenecks

### 1. AppRunner S3 GET Latency (THE Critical Constraint)

**2-5 seconds per S3 GET** on AppRunner vs 50-200ms on EC2.

This single fact drove most of the caching architecture. Any feature that requires bulk S3 file reads from AppRunner will be unacceptably slow.

**Mitigations:**
- Never do bulk S3 reads in request handlers
- Offload expensive S3 work to Lambda functions that build compressed caches
- Parse metadata from S3 key filenames instead of reading file contents
- All S3 caches use gzip compression to minimize transfer size

### 2. Bill Index / Completion Tracker

Needs to scan 34K+ S3 files across stages S6, S7, S8, S9, S99 to know which accounts have bills in which months.

| Approach | Performance |
|----------|-------------|
| Old: Read each S3 file | Hung indefinitely on AppRunner |
| Current: Lambda parses filenames | ~80s first build, <5s incremental |

- **Lambda**: `jrk-bill-index-builder` builds `config/bill_index_cache.json.gz`
- **Filename format**: `Property-Vendor-Account-StartDate-EndDate-BillDate_timestamp.jsonl`
- **Incremental**: Tracks `keys_seen` set, only reads new keys on subsequent runs
- **Concurrency control**: `_TRACKER_REBUILDING` flag prevents concurrent rebuilds

### 3. UBI Unassigned Bills

Requires scanning Stage 7 S3 files + full DynamoDB scan of `jrk-bill-ubi-assignments` for exclusion hashes.

**Mitigations:**
- External Lambda (`jrk-ubi-cache-builder`) builds `Bill_Parser_Cache/ubi_unassigned_cache.json.gz`
- Lambda uses `ThreadPoolExecutor(max_workers=50)` for parallel S3 reads
- App polls S3 ETag every ~60s and reloads only when file changes
- Local removal tracking (`_UBI_REMOVED_KEYS` set) bridges gap between Lambda builds

### 4. DynamoDB Table Scans

`jrk-bill-ubi-assignments` and `jrk-bill-ubi-archived` are large tables. Full scans grow linearly.

**Mitigations:**
- Exclusion hash cache (`_EXCLUSION_HASH_CACHE`, 5-min TTL)
- Paginated scans with `ExclusiveStartKey`
- Lambda-side processing (scans happen in Lambda, not AppRunner)
- Key-based queries preferred over scans where possible

### 5. Gemini API Rate Limits (Lambda Side)

The parsing pipeline's throughput ceiling is the Gemini API.

| Strategy | Detail |
|----------|--------|
| Exponential backoff | 2^n seconds on 429 errors (base=2s) |
| Key rotation | 10 API keys, cycled per attempt |
| Chunk staggering | 1.5s delay between chunk starts |
| Deadline awareness | Stops retrying if <30s left in Lambda timeout |
| Max attempts | 10 per chunk |

### 6. Two-Instance Cache Coherency

AppRunner runs 2 instances. In-memory caches diverge between them.

**Mitigation:** S3-backed caches are the shared source of truth. In-memory is a fast local layer only.

---

## Caching Architecture

### Three-Layer Strategy

| Layer | Storage | TTL Range | Shared Across Instances? |
|-------|---------|-----------|--------------------------|
| In-memory | Dict / deque | 5 min – 2 hours | No (per-instance) |
| S3 gzip | `.json.gz` files | 60 min – 24 hours | Yes |
| DynamoDB | Config table rows | 7 days | Yes |

### Core Pattern: `_metrics_serve(name, compute_fn)`

**Location:** `main.py:11346-11406`

Non-blocking, stale-while-revalidate serving:

```
Request arrives
  │
  ├─ In-memory hit (< TTL) → return instantly (~1ms)
  │
  ├─ S3 hit (< TTL) → return (~200ms)
  │
  ├─ Stale data exists → return old data immediately
  │                       + spawn background thread to rebuild
  │
  └─ No cache at all → compute synchronously (first request only)
```

**Metrics using this pattern:** pipeline summary, throughput, queue depth, submitter stats, activity detail, week-over-week, overrides, outliers, logins

### All Caches

| Cache | Storage | TTL | Source | Thread-Safe? |
|-------|---------|-----|--------|-------------|
| `_CACHE` (daily invoices) | In-memory | 5 min (today) / 1 hr (past) | S3 Stage 4 | No (dict) |
| `_TRACK_CACHE` | In-memory | 1 hour | S3 scan | No (dict) |
| `_EXCLUSION_HASH_CACHE` | In-memory | 5 min | DynamoDB scan | Yes |
| `_UBI_UNASSIGNED_CACHE` | In-memory + S3 | Lambda-driven | Lambda build | Yes (ETag poll) |
| `_PRINT_CHECKS_CACHE` | In-memory | 10 min | S3 Stage 7 | No |
| `_CHECK_SLIP_INVOICES_CACHE` | In-memory | 5 min | DynamoDB scan | No |
| `_VENDOR_CODE_CACHE` | In-memory | 1 hour | S3 (api-vendor) | No |
| `_VENDOR_PAIR_CACHE` | In-memory | 1 hour | S3 Stage 7 + archive | Yes (lock) |
| `_INVOICE_HISTORY_CACHE` | In-memory | 2 hours | Snowflake | Yes (lock) |
| `_SEARCH_INDEX` | In-memory + S3 | Incremental (5 min) | S3 Stage 4 | Yes (lock) |
| `_METRICS_CACHE` | In-memory + S3 | 60 min | Various | Implicit |
| `_PERF_LOG` | In-memory (ring) | 50K records (~24h) | Middleware | Yes (lock) |
| `_PERF_ROLLUPS` | In-memory + DDB | Per-hour | Aggregated | Yes (lock) |
| `_WORKFLOW_DATA_CACHE` | In-memory + S3 | 5 min / 24 hr rebuild | Computed | Yes (flag) |
| `_WEEK_OVER_WEEK_CACHE` | In-memory + DDB | 10 min | Computed | No |
| Completion Tracker | In-memory + S3 | 24 hours | Lambda index | Yes (flag) |
| Bill Index | S3 only | Lambda-driven | Lambda build | N/A |

### S3 Cache Keys

| Cache | S3 Key | Compressed? |
|-------|--------|-------------|
| Metrics (various) | `Bill_Parser_Config/metrics_cache_{name}.json.gz` | gzip |
| Search Index | `Bill_Parser_Config/search_index.json.gz` | gzip |
| Completion Tracker | `Bill_Parser_Config/completion_tracker_cache.json.gz` | gzip |
| Bill Index | `Bill_Parser_Config/bill_index_cache.json.gz` | gzip |
| Workflow Data | `Bill_Parser_Config/workflow_cache.json` | No |
| UBI Unassigned | `Bill_Parser_Cache/ubi_unassigned_cache.json.gz` | gzip |

### Cache Invalidation

| Cache | Method | Trigger |
|-------|--------|---------|
| `_CACHE` (daily) | `invalidate_day_cache(y,m,d)` | After parsing changes |
| `_TRACK_CACHE` | `.clear()` | After POST operations |
| `_UBI_UNASSIGNED_CACHE` | `_invalidate_ubi_cache()` | After assign/archive/unassign |
| `_PRINT_CHECKS_CACHE` | `_invalidate_print_checks_cache()` | After creating check slips |
| `_WORKFLOW_DATA_CACHE` | `.clear()` | After recalculation |
| `_VENDOR_PAIR_CACHE` | TTL expiry | Proactive refresh at 50 min |
| `_INVOICE_HISTORY_CACHE` | Background loop | Every 2 hours |
| `_SEARCH_INDEX` | Incremental | New dates only, every 5 min |
| `_PERF_LOG` | Ring buffer eviction | Auto at 50K capacity |
| `_EXCLUSION_HASH_CACHE` | TTL expiry only | Every 5 min |

### Stampede Prevention

| Cache | Strategy |
|-------|----------|
| Vendor pairs | `_VENDOR_PAIR_LOCK.acquire(blocking=False)` — returns stale if locked |
| Completion tracker | `_TRACKER_REBUILDING` flag — skips rebuild if already running |
| Metrics | `_metrics_serve` — returns stale, rebuilds in background thread |
| Vendor pairs | Proactive refresh at 50 min before 60-min TTL expires |

---

## Background Refresh Threads

All started at app startup (`@app.on_event("startup")`, `main.py:1221+`), all daemon threads:

| Thread | Function | Interval | Purpose |
|--------|----------|----------|---------|
| Vendor pairs | `_vendor_pair_refresh_loop()` | 50 min | Keep vendor pair cache warm |
| UBI cache poll | `ubi_cache_startup_and_poll()` | ~60s | ETag check for Lambda updates |
| Exclusion hash | Pre-warm on startup | Once | Load exclusion hashes |
| Invoice history | `_invoice_history_refresh_loop()` | 2 hours | Snowflake query refresh |
| POST helper | Pre-warm on startup | Once | GL maps, vendor codes |
| Audit digest | `_audit_digest_loop()` | Daily 5PM PT | Email rollup |
| Search index backfill | `_search_index_backfill()` | Once | Load from S3 on startup |
| Search index refresh | `_search_index_refresh_loop()` | 5 min | Incremental new dates |
| Workflow tracker | `_workflow_tracker_refresh_loop()` | 24 hours | Completion tracker + aging |
| Perf rollups | `_perf_load_historical_rollups()` | Once | Load 7 days from DynamoDB |

---

## Connection Pooling & Thread Pools

```python
_boto_config = Config(
    max_pool_connections=130,   # 20 general + 100 check-review + 10 headroom
    retries={'max_attempts': 3, 'mode': 'adaptive'},
    connect_timeout=5,
    read_timeout=30
)
```

| Pool | Workers | Purpose |
|------|---------|---------|
| `_GLOBAL_EXECUTOR` | 20 | General-purpose parallel S3/DDB operations |
| `_CHECK_REVIEW_EXECUTOR` | 100 | PDF processing for check slips |
| Ad-hoc pools | 10-50 | Bulk operations (vendor scan, file listing, validation) |

---

## Lambda Performance Instrumentation

### Chunk Processor (`jrk-bill-chunk-processor`)
Most sophisticated timing. Writes `.timing.json` sidecar files and structured metric logs:

```json
{
    "stage": "chunk_processor",
    "jobId": "...",
    "chunkNum": 3,
    "lineCount": 15,
    "modelUsed": "gemini-3-pro-preview",
    "geminiMs": 4200,
    "totalMs": 5100
}
```

### UBI Cache Builder (`jrk-ubi-cache-builder`)
Multi-stage timing with per-phase logging:
```
[EXCLUSION] Loaded 12,500 hashes in 3.2s
[STAGE8]    Scanned in 8.1s, found 450 accounts with history
[STAGE7]    Found 2,100 JSONL files
Built cache with 1,847 bills in 42.3s
```

### Bill Parser (`jrk-bill-parser`)
- Up to 10 retry attempts with 3s fixed delay
- No explicit timing output, but error tracking via DynamoDB (`jrk-bill-parser-errors`)

---

## Architectural Principles

1. **Never block requests on expensive S3 operations** — offload to Lambdas + serve from cache
2. **Always have a fallback** — in-memory → S3 → compute synchronously
3. **S3 is the shared truth** — 2 AppRunner instances diverge in-memory; S3 is consistent
4. **Proactive refresh before TTL expires** — prevents cache misses during requests
5. **Incremental updates** — bill index and search index only scan new files
6. **Thread-safe with stampede protection** — locks + flags + stale fallback
7. **Parse from filenames, not file contents** — avoids S3 GET latency
8. **Gzip everything** — reduces S3 transfer time and storage

---

## Remaining Risk Areas

| Risk | Impact | Current Mitigation |
|------|--------|-------------------|
| New features needing bulk S3 GETs | Will be slow on AppRunner | Route through Lambda |
| DynamoDB table growth (UBI tables) | Scan time grows linearly | Lambda-side processing, caching |
| Gemini API throughput | Parsing speed ceiling | Key rotation, backoff, staggering |
| 2-instance cache divergence | Inconsistent UI responses | S3-backed shared caches |
| Background thread failures | Stale data served indefinitely | No alerting on thread death |
| Search index growth | Larger S3 payload on startup | Incremental updates, gzip |
