# Vacant Electric — Bill Review Integration Guide

**From:** VE Pipeline Project
**To:** Bill Review App
**Date:** March 2026
**Status:** Ready to integrate — all VE code is built and tested

---

## What This Is

The `vacant_electric/` package now includes a complete web sub-application (`vacant_electric/web/`) that provides:

- **Dashboard** — Run the VE pipeline, view batch status and history
- **Line-by-line review** — Filter, sort, inspect every GL record with side-by-side bill PDF + lease clause display
- **Batch posting** — Approve lines, post to Entrata with live progress tracking

It's built as an **importable FastAPI router** that Bill Review mounts. All UI matches Bill Review's glassmorphism design.

---

## Integration Steps

### 1. Add the landing page tile

In `templates/landing.html`, add this tile to the `.grid` container (after the existing tiles):

```html
<a class="tile" href="/ve">
  <h2>VACANT BILLBACK</h2>
  <div class="muted">Review &amp; post vacant utility billback charges</div>
</a>
```

---

### 2. Add environment variables

Add these to your App Runner / `.env` configuration:

```env
# Vacant Electric
VE_DDB_TABLE=jrk-ve-batches
VE_ADMIN_FEES_PATH=s3://jrk-analytics-billing/ve-config/admin_fees.json
VE_CORRECTIONS_CSV=s3://jrk-analytics-billing/ve-config/ALL_AI_CORRECTIONS.csv
VE_S3_PROPERTY_MAPPING=s3://jrk-analytics-billing/ve-config/S3_PROPERTY_MAPPING.json

# Entrata API (already in Secrets Manager as jrk-bill-review/entrata or similar)
ENTRATA_API_KEY_SECRET=jrk-bill-review/entrata
```

---

### 3. Create the DynamoDB table

```
Table name:    jrk-ve-batches
Partition key: pk (String)
Sort key:      sk (String)
Billing mode:  On-demand
```

No GSIs needed — queries use `pk = BATCH#{id}` with `sk` prefix filters.

---

### 4. Wire it up in `main.py`

Add these imports near the top of `main.py`:

```python
# ── Vacant Electric ──────────────────────────────────────────────────────
from vacant_electric.web import ve_router, configure as ve_configure
from vacant_electric.web_models import VEBatchStore
from vacant_electric.entrata_ar import EntrataARClient
from vacant_electric.s3_bills import BillPDFLocator
from vacant_electric.lease_clauses import LeaseClauseFinder
```

Add this initialization block after the existing `ddb = boto3.client(...)` setup (around line 115):

```python
# ── Vacant Electric Setup ────────────────────────────────────────────────
VE_DDB_TABLE = os.getenv("VE_DDB_TABLE", "jrk-ve-batches")
_ve_store = VEBatchStore(ddb, VE_DDB_TABLE)

# Entrata AR client
def _get_entrata_api_key():
    """Fetch Entrata API key from Secrets Manager."""
    try:
        sm = boto3.client('secretsmanager', region_name='us-east-1')
        secret = json.loads(sm.get_secret_value(SecretId='jrk-bill-review/entrata')['SecretString'])
        return secret.get('api_key', '')
    except Exception as e:
        print(f"[VE] Could not load Entrata API key: {e}")
        return ''

_ve_ar_client = EntrataARClient(
    api_key=_get_entrata_api_key(),
    dry_run=os.getenv("VE_DRY_RUN", "0") == "1",
)

# S3 bill PDF locator
_ve_s3_mapping_key = os.getenv("VE_S3_PROPERTY_MAPPING", "ve-config/S3_PROPERTY_MAPPING.json")
try:
    _map_resp = s3.get_object(Bucket=BUCKET, Key=_ve_s3_mapping_key)
    _ve_s3_mapping = json.loads(_map_resp['Body'].read())
    _ve_bill_locator = BillPDFLocator(s3, _ve_s3_mapping)
except Exception as e:
    print(f"[VE] Could not load S3 property mapping: {e}")
    _ve_bill_locator = None

# Lease clause finder
_ve_clause_finder = LeaseClauseFinder(s3)

# Snowflake connection factory (reuses Bill Review's existing pattern)
def _ve_snowflake_factory():
    creds = _get_snowflake_credentials()  # Bill Review's existing function
    if not creds:
        raise RuntimeError("Snowflake credentials not available")
    import snowflake.connector
    return snowflake.connector.connect(
        account=creds['account'],
        user=creds['user'],
        password=creds['password'],
        database=creds.get('database', 'LAITMAN'),
        schema=creds.get('schema', 'ENTRATACORE'),
        warehouse=creds.get('warehouse', 'COMPUTE_WH'),
    )

# Admin fees config
_ve_admin_fees = {}
try:
    _fees_key = os.getenv("VE_ADMIN_FEES_PATH", "ve-config/admin_fees.json")
    _fees_resp = s3.get_object(Bucket=BUCKET, Key=_fees_key)
    _ve_admin_fees = json.loads(_fees_resp['Body'].read())
except Exception:
    pass

# Configure and mount the VE router
ve_configure(
    store=_ve_store,
    ar_client=_ve_ar_client,
    bill_locator=_ve_bill_locator,
    clause_finder=_ve_clause_finder,
    snowflake_conn_factory=_ve_snowflake_factory,
    admin_fees=_ve_admin_fees,
)

# Override VE's auth stub with Bill Review's real auth
import vacant_electric.web as _ve_web
_ve_web.require_user = require_user  # Bill Review's require_user

app.include_router(ve_router)
```

That's it. The VE router handles everything under `/ve/*`.

---

### 5. Upload config files to S3

These files currently live locally in the VE project folder. Upload them to S3 so the App Runner instance can access them:

| Local File | S3 Destination |
|-----------|---------------|
| `VACANT_ELECTRIC/S3_PROPERTY_MAPPING.json` | `s3://jrk-analytics-billing/ve-config/S3_PROPERTY_MAPPING.json` |
| `VACANT_ELECTRIC/ALL_AI_CORRECTIONS.csv` | `s3://jrk-analytics-billing/ve-config/ALL_AI_CORRECTIONS.csv` |
| Admin fees JSON (create from pipeline) | `s3://jrk-analytics-billing/ve-config/admin_fees.json` |

**Admin fees JSON format** (entityid → dollar amount):
```json
{
  "01CHA": 15.00,
  "01APX": 12.50,
  "01HUD": 12.50
}
```

---

### 6. Add `vacant_electric` to requirements

In `requirements.txt`, add:

```
# No new dependencies — vacant_electric uses packages already in Bill Review:
#   boto3, pandas, requests, fastapi, jinja2
```

The `vacant_electric/` package needs to be available on the Python path. Options:

**Option A — Copy the package** (simplest):
```
bill_review_app/
  main.py
  vacant_electric/     ← copy the whole folder here
    __init__.py
    web/
      __init__.py
      templates/
```

**Option B — Install as editable** (if using a shared repo):
```bash
pip install -e /path/to/VACANT_ELECTRIC
```

**Option C — Add to sys.path** (quick and dirty):
```python
import sys
sys.path.insert(0, "/path/to/VACANT_ELECTRIC")
```

---

## What Each Route Does

### Pages (HTML)

| Route | Template | Purpose |
|-------|----------|---------|
| `GET /ve` | `ve_dashboard.html` | Month selector, run pipeline, batch history |
| `GET /ve/review/{batch_id}` | `ve_review.html` | Sortable table + detail drawer with bill PDF & lease clause |
| `GET /ve/post/{batch_id}` | `ve_post.html` | Posting progress bar + results |

### API Endpoints (JSON)

| Route | Purpose |
|-------|---------|
| `POST /ve/api/run` | Trigger pipeline run (returns `batch_id` immediately) |
| `GET /ve/api/batch/{id}` | Batch status + stats |
| `GET /ve/api/batch/{id}/lines` | Paginated lines with `?property=&status=&action=&limit=` filters |
| `POST /ve/api/batch/{id}/line/{lid}/review` | Set reviewer action (APPROVED/FLAGGED/EXCLUDED) |
| `POST /ve/api/batch/{id}/approve` | Bulk approve all pending lines |
| `POST /ve/api/batch/{id}/post` | Start Entrata posting (background thread) |
| `GET /ve/api/batch/{id}/post-status` | Poll posting progress |
| `GET /ve/api/batches` | Batch history list |
| `GET /ve/api/batch/{id}/line/{lid}/bill-pdf` | Presigned URL for utility bill PDF |
| `GET /ve/api/batch/{id}/line/{lid}/lease-page` | Presigned URL for lease utility addendum + extracted terms |

---

## Architecture Notes

**DynamoDB single-table design:**
```
PK: BATCH#{batch_id}    SK: META              → batch metadata
PK: BATCH#{batch_id}    SK: LINE#{line_id}    → individual line review state
```

**Background threads:** Pipeline runs and Entrata posting both use `ThreadPoolExecutor` (same pattern as Bill Review's existing parallel S3 fetches). Posting takes ~12 seconds per line due to Entrata's 300/hr rate limit — a 350-line batch takes ~70 minutes. Progress is persisted to DynamoDB so it survives page refreshes and App Runner restarts.

**Auth:** The VE router has a stub `require_user` that gets overridden with Bill Review's real one during `main.py` setup (see step 4 above). All routes are protected.

**Dry-run mode:** Set `VE_DRY_RUN=1` to test the full flow without actually posting to Entrata. The AR client logs what it would post and returns success.

---

## File Inventory

All new files live in `vacant_electric/`:

```
vacant_electric/
│
│  # Existing pipeline (unchanged)
├── __init__.py
├── config.py
├── models.py
├── pipeline.py
├── matcher.py
├── parser.py
├── property_maps.py
├── queries.py
├── corrections.py
├── reports.py
│
│  # New: Core extensions
├── entrata_ar.py        Entrata AR API client (post/reverse/query)
├── s3_bills.py          S3 utility bill PDF locator + presigned URLs
├── lease_clauses.py     Lease utility clause retrieval from audit platform
├── classifier.py        Line status classification (6 categories)
├── web_models.py        DynamoDB data models (VEBatch, VELineReview)
├── batch_runner.py      Background pipeline execution + persistence
│
│  # New: Web layer
└── web/
    ├── __init__.py      FastAPI router (14 endpoints)
    └── templates/
        ├── ve_dashboard.html    Dashboard page
        ├── ve_review.html       Line review page (most complex)
        └── ve_post.html         Posting progress page
```

---

## Quick Verification Checklist

After integration, verify:

- [ ] `/ve` loads the dashboard (glassmorphism design, month selector)
- [ ] "Run Pipeline" triggers a batch (status polls from RUNNING → READY)
- [ ] Clicking a batch opens the review page with lines populated
- [ ] Filter dropdowns work (property, status, action, utility)
- [ ] Clicking a row opens the detail drawer
- [ ] Bill PDF iframe loads for lines that have PDFs
- [ ] Lease clause iframe loads for lines that have utility addendums
- [ ] Approve/Flag/Exclude buttons update line state
- [ ] Keyboard shortcuts work in drawer (A=approve, F=flag, X=exclude, Esc=close)
- [ ] "Post to Entrata" starts posting with live progress bar (use dry-run first!)
- [ ] Auth redirect works (unauthenticated → `/login`)
