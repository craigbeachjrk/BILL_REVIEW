"""
FastAPI router for the Vacant Electric web integration.

Mount into Bill Review:
    from vacant_electric.web import ve_router
    app.include_router(ve_router)

All routes are prefixed with /ve.
Auth: Uses Bill Review's require_user dependency.
"""
import os
import json
import logging
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..web_models import (
    VEBatchStore, VEBatch, VELineReview,
    BATCH_RUNNING, BATCH_READY, BATCH_IN_REVIEW, BATCH_APPROVED, BATCH_POSTING, BATCH_POSTED,
    ACTION_PENDING, ACTION_APPROVED, ACTION_FLAGGED, ACTION_EXCLUDED,
    POST_PENDING, POST_SUCCESS, POST_FAILED,
)
from ..classifier import STATUS_COLORS, ALL_STATUSES, get_suggested_action
from ..entrata_ar import EntrataARClient, resolve_ar_code_id, generate_transaction_id
from ..s3_bills import BillPDFLocator
from ..batch_runner import run_batch

logger = logging.getLogger(__name__)

# Template directory
_template_dir = os.path.join(os.path.dirname(__file__), 'templates')
templates = Jinja2Templates(directory=_template_dir)

# Router with /ve prefix
ve_router = APIRouter(prefix='/ve', tags=['vacant-electric'])

# ── Dependency stubs (Bill Review will inject real implementations) ──────────

_store: Optional[VEBatchStore] = None
_ar_client: Optional[EntrataARClient] = None
_bill_locator: Optional[BillPDFLocator] = None
_snowflake_conn_factory = None
_admin_fees = {}
_corrections_csv = None


def configure(
    store: VEBatchStore,
    ar_client: Optional[EntrataARClient] = None,
    bill_locator: Optional[BillPDFLocator] = None,
    clause_finder=None,
    snowflake_conn_factory=None,
    admin_fees: dict = None,
    corrections_csv: str = None,
):
    """
    Configure the VE web module with required dependencies.
    Call this from Bill Review's startup before including the router.
    """
    global _store, _ar_client, _bill_locator, _clause_finder
    global _snowflake_conn_factory, _admin_fees, _corrections_csv
    _store = store
    _ar_client = ar_client
    _bill_locator = bill_locator
    _clause_finder = clause_finder
    _snowflake_conn_factory = snowflake_conn_factory
    _admin_fees = admin_fees or {}
    _corrections_csv = corrections_csv


def _get_store() -> VEBatchStore:
    if _store is None:
        raise HTTPException(500, "VE module not configured")
    return _store


def require_user(request: Request) -> str:
    """
    Auth dependency stub. Bill Review overrides this with its own require_user.
    For standalone dev, accepts any request.
    """
    user = request.headers.get('X-User', request.cookies.get('user', ''))
    if not user:
        user = 'dev'
    return user


# ════════════════════════════════════════════════════════════════════════════
# PAGE ROUTES
# ════════════════════════════════════════════════════════════════════════════

@ve_router.get('', response_class=HTMLResponse)
@ve_router.get('/', response_class=HTMLResponse)
async def dashboard(request: Request, user: str = Depends(require_user)):
    """Dashboard: month selector, batch status, summary stats."""
    store = _get_store()
    batches = store.list_batches(limit=10)
    return templates.TemplateResponse('ve_dashboard.html', {
        'request': request,
        'user': user,
        'batches': batches,
        'status_colors': STATUS_COLORS,
    })


@ve_router.get('/review/{batch_id}', response_class=HTMLResponse)
async def review_page(request: Request, batch_id: str, user: str = Depends(require_user)):
    """Line-by-line review page."""
    store = _get_store()
    batch = store.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    return templates.TemplateResponse('ve_review.html', {
        'request': request,
        'user': user,
        'batch': batch,
        'batch_id': batch_id,
        'status_colors': json.dumps(STATUS_COLORS),
        'all_statuses': json.dumps(ALL_STATUSES),
    })


@ve_router.get('/post/{batch_id}', response_class=HTMLResponse)
async def post_page(request: Request, batch_id: str, user: str = Depends(require_user)):
    """Posting progress & results page."""
    store = _get_store()
    batch = store.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    return templates.TemplateResponse('ve_post.html', {
        'request': request,
        'user': user,
        'batch': batch,
        'batch_id': batch_id,
    })


# ════════════════════════════════════════════════════════════════════════════
# API ROUTES
# ════════════════════════════════════════════════════════════════════════════

@ve_router.post('/api/run')
async def api_run_pipeline(
    request: Request,
    month: int = Form(...),
    year: int = Form(...),
    user: str = Depends(require_user),
):
    """Trigger a pipeline run. Returns batch_id immediately."""
    store = _get_store()

    if not _snowflake_conn_factory:
        raise HTTPException(500, "Snowflake connection not configured")

    conn = _snowflake_conn_factory()
    batch_id = run_batch(
        month=month,
        year=year,
        user=user,
        snowflake_conn=conn,
        store=store,
        admin_fees=_admin_fees,
        corrections_csv_path=_corrections_csv,
        bill_locator=_bill_locator,
        clause_finder=_clause_finder,
    )

    return JSONResponse({'batch_id': batch_id, 'status': BATCH_RUNNING})


@ve_router.get('/api/batch/{batch_id}')
async def api_get_batch(batch_id: str, user: str = Depends(require_user)):
    """Get batch status and stats."""
    store = _get_store()
    batch = store.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")

    from dataclasses import asdict
    return JSONResponse(asdict(batch))


@ve_router.get('/api/batch/{batch_id}/lines')
async def api_get_lines(
    batch_id: str,
    property: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    user: str = Depends(require_user),
):
    """Get paginated lines for a batch with optional filters."""
    store = _get_store()

    result = store.get_lines(
        batch_id,
        property_filter=property,
        status_filter=status,
        action_filter=action,
        limit=limit,
    )

    from dataclasses import asdict
    lines_data = [asdict(line) for line in result['lines']]
    return JSONResponse({
        'lines': lines_data,
        'count': len(lines_data),
        'has_more': 'last_key' in result,
    })


@ve_router.post('/api/batch/{batch_id}/line/{line_id}/review')
async def api_review_line(
    batch_id: str,
    line_id: str,
    action: str = Form(...),
    notes: str = Form(''),
    user: str = Depends(require_user),
):
    """Set reviewer action and notes for a single line."""
    if action not in (ACTION_APPROVED, ACTION_FLAGGED, ACTION_EXCLUDED, ACTION_PENDING):
        raise HTTPException(400, f"Invalid action: {action}")

    store = _get_store()
    store.update_line_action(batch_id, line_id, action, notes, user)

    # Update batch status to IN_REVIEW if it was READY
    batch = store.get_batch(batch_id)
    if batch and batch.status == BATCH_READY:
        store.update_batch_status(batch_id, BATCH_IN_REVIEW)

    return JSONResponse({'ok': True, 'line_id': line_id, 'action': action})


@ve_router.post('/api/batch/{batch_id}/approve')
async def api_approve_all(
    batch_id: str,
    user: str = Depends(require_user),
):
    """Approve all PENDING lines."""
    store = _get_store()
    result = store.get_lines(batch_id, action_filter=ACTION_PENDING, limit=5000)
    line_ids = [l.line_id for l in result['lines']]
    store.bulk_update_action(batch_id, line_ids, ACTION_APPROVED, user)

    counts = store.get_batch_action_counts(batch_id)
    store.update_batch_status(
        batch_id, BATCH_APPROVED,
        lines_approved=counts.get(ACTION_APPROVED, 0),
        lines_flagged=counts.get(ACTION_FLAGGED, 0),
        lines_excluded=counts.get(ACTION_EXCLUDED, 0),
        lines_pending=counts.get(ACTION_PENDING, 0),
    )

    return JSONResponse({'ok': True, 'approved': len(line_ids), 'counts': counts})


@ve_router.post('/api/batch/{batch_id}/post')
async def api_start_posting(
    batch_id: str,
    user: str = Depends(require_user),
):
    """Start Entrata posting for all approved lines (background)."""
    store = _get_store()
    batch = store.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")

    if not _ar_client:
        raise HTTPException(500, "Entrata AR client not configured")

    # Get approved lines
    result = store.get_lines(batch_id, action_filter=ACTION_APPROVED, limit=5000)
    lines = result['lines']

    if not lines:
        raise HTTPException(400, "No approved lines to post")

    store.update_batch_status(batch_id, BATCH_POSTING)

    # Run posting in background
    from concurrent.futures import ThreadPoolExecutor
    _posting_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ve-post')
    _posting_executor.submit(_post_worker, batch_id, batch, lines, store, _ar_client)

    estimate_minutes = len(lines) * 12 / 60
    return JSONResponse({
        'ok': True,
        'lines_to_post': len(lines),
        'estimated_minutes': round(estimate_minutes, 1),
    })


@ve_router.get('/api/batch/{batch_id}/post-status')
async def api_post_status(batch_id: str, user: str = Depends(require_user)):
    """Poll for posting progress."""
    store = _get_store()
    batch = store.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")

    progress = store.get_posting_progress(batch_id)
    from dataclasses import asdict
    return JSONResponse({
        'batch': asdict(batch),
        'progress': progress,
        'total': sum(progress.values()),
    })


@ve_router.get('/api/batches')
async def api_list_batches(user: str = Depends(require_user)):
    """List batch history."""
    store = _get_store()
    batches = store.list_batches(limit=50)
    from dataclasses import asdict
    return JSONResponse({'batches': [asdict(b) for b in batches]})


@ve_router.get('/api/batch/{batch_id}/line/{line_id}/bill-pdf')
async def api_bill_pdf(batch_id: str, line_id: str, user: str = Depends(require_user)):
    """Get presigned URL for a line's bill PDF."""
    store = _get_store()
    line = store.get_line(batch_id, line_id)
    if not line:
        raise HTTPException(404, "Line not found")

    if line.bill_pdf_url:
        return JSONResponse({'url': line.bill_pdf_url})

    # Try to generate a fresh URL if we have the key
    if line.bill_pdf_key and _bill_locator:
        url = _bill_locator.get_presigned_url(line.bill_pdf_key)
        if url:
            store.ddb.update_item(
                TableName=store.table,
                Key={'pk': {'S': f"BATCH#{batch_id}"}, 'sk': {'S': f"LINE#{line_id}"}},
                UpdateExpression='SET bill_pdf_url = :url',
                ExpressionAttributeValues={':url': {'S': url}},
            )
            return JSONResponse({'url': url})

    return JSONResponse({'url': None, 'message': 'No bill PDF available'})


@ve_router.get('/api/batch/{batch_id}/line/{line_id}/lease-page')
async def api_lease_page(batch_id: str, line_id: str, user: str = Depends(require_user)):
    """Get presigned URL for a line's lease utility addendum page."""
    store = _get_store()
    line = store.get_line(batch_id, line_id)
    if not line:
        raise HTTPException(404, "Line not found")

    result = {'url': line.lease_page_url or None}
    if line.lease_extraction:
        try:
            result['extraction'] = json.loads(line.lease_extraction)
        except json.JSONDecodeError:
            result['extraction'] = None
    return JSONResponse(result)


# ── Posting Worker ───────────────────────────────────────────────────────────

def _post_worker(
    batch_id: str,
    batch: VEBatch,
    lines: list,
    store: VEBatchStore,
    ar_client: EntrataARClient,
):
    """Background worker that posts approved lines to Entrata."""
    import re
    posted = 0
    failed = 0

    for seq, line in enumerate(lines, 1):
        ar_code_id = resolve_ar_code_id(line.charge_code)
        if not ar_code_id:
            store.update_line_posting(batch_id, line.line_id, POST_FAILED, error='Unknown charge code')
            failed += 1
            continue

        if not line.resi_id:
            store.update_line_posting(batch_id, line.line_id, POST_FAILED, error='No lease/resident ID')
            failed += 1
            continue

        prop_code = re.sub(r'^\d+', '', line.entity_id)
        txn_id = generate_transaction_id(prop_code, batch.month, batch.year, seq)

        # Build post month in MM/YYYY format
        if batch.month == 12:
            post_m, post_y = 1, batch.year + 1
        else:
            post_m, post_y = batch.month + 1, batch.year
        post_month = f"{post_m:02d}/{post_y}"
        txn_date = f"{post_m:02d}/01/{post_y}"

        description = line.memo or f"{line.utility} {line.overlap_start}-{line.overlap_end}"

        result = ar_client.post_charge(
            lease_id=int(line.resi_id),
            ar_code_id=ar_code_id,
            amount=line.total,
            transaction_date=txn_date,
            post_month=post_month,
            description=description,
            transaction_id=txn_id,
        )

        if result.success:
            store.update_line_posting(batch_id, line.line_id, POST_SUCCESS, entrata_txn_id=txn_id)
            posted += 1
        else:
            store.update_line_posting(batch_id, line.line_id, POST_FAILED, error=result.error or 'Unknown error')
            failed += 1

        # Update batch progress periodically
        if seq % 10 == 0:
            store.update_batch_status(batch_id, BATCH_POSTING, lines_posted=posted, lines_failed=failed)

    # Final status
    final_status = BATCH_POSTED if failed == 0 else BATCH_POSTING
    store.update_batch_status(
        batch_id, final_status,
        lines_posted=posted,
        lines_failed=failed,
    )
    logger.info(f"Posting complete for {batch_id}: {posted} posted, {failed} failed")
