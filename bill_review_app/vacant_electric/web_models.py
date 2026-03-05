"""
DynamoDB-backed data models for VE review workflow state.

Table: jrk-ve-batches
PK/SK pattern:
    BATCH#{batch_id}  /  META                → VEBatch
    BATCH#{batch_id}  /  LINE#{line_id}      → VELineReview
    BATCH#{batch_id}  /  POSTING#{line_id}   → posting result

Batch statuses: RUNNING → READY → IN_REVIEW → APPROVED → POSTING → POSTED
"""
import uuid
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_TABLE = 'jrk-ve-batches'

# Batch status progression
BATCH_RUNNING = 'RUNNING'
BATCH_READY = 'READY'
BATCH_IN_REVIEW = 'IN_REVIEW'
BATCH_APPROVED = 'APPROVED'
BATCH_POSTING = 'POSTING'
BATCH_POSTED = 'POSTED'
BATCH_FAILED = 'FAILED'

# Line reviewer actions
ACTION_PENDING = 'PENDING'
ACTION_APPROVED = 'APPROVED'
ACTION_FLAGGED = 'FLAGGED'
ACTION_EXCLUDED = 'EXCLUDED'

# Posting statuses
POST_PENDING = 'PENDING'
POST_SUCCESS = 'SUCCESS'
POST_FAILED = 'FAILED'
POST_SKIPPED = 'SKIPPED'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    return str(uuid.uuid4())[:8]


@dataclass
class VEBatch:
    """A pipeline run batch with aggregate metadata."""
    batch_id: str = ''
    month: int = 0
    year: int = 0
    status: str = BATCH_RUNNING
    created_by: str = ''
    created_at: str = ''
    updated_at: str = ''
    total_lines: int = 0
    total_amount: float = 0.0
    total_properties: int = 0
    match_rate: float = 0.0
    lines_approved: int = 0
    lines_flagged: int = 0
    lines_excluded: int = 0
    lines_pending: int = 0
    lines_posted: int = 0
    lines_failed: int = 0
    error_message: str = ''

    def __post_init__(self):
        if not self.batch_id:
            self.batch_id = _gen_id()
        if not self.created_at:
            self.created_at = _now_iso()
        if not self.updated_at:
            self.updated_at = _now_iso()


@dataclass
class VELineReview:
    """A single GL line in a batch, with review state and enrichment data."""
    line_id: str = ''
    batch_id: str = ''

    # Core pipeline data
    entity_id: str = ''
    property_name: str = ''
    bldg_id: str = ''
    unit_id: str = ''
    utility: str = ''
    charge_code: str = ''
    dramount: float = 0.0
    prorated_billback: float = 0.0
    admin_charge: float = 0.0
    total: float = 0.0

    # Resident info
    resident_name: str = ''
    resi_id: str = ''
    resi_status: str = ''
    lease_id: str = ''
    move_in_date: str = ''
    move_out_date: str = ''

    # Bill period
    bill_start: str = ''
    bill_end: str = ''
    bill_days: int = 0
    overlap_start: str = ''
    overlap_end: str = ''
    overlap_days: int = 0

    # Invoice reference
    invoicedoc: str = ''
    source_invoices: str = ''
    gl_detail_id: str = ''
    memo: str = ''

    # Classification
    review_status: str = ''       # from classifier.py

    # Review state
    reviewer_action: str = ACTION_PENDING  # PENDING, APPROVED, FLAGGED, EXCLUDED
    reviewer_notes: str = ''
    reviewed_by: str = ''
    reviewed_at: str = ''

    # Enrichment: bill PDF
    bill_pdf_key: str = ''
    bill_pdf_url: str = ''

    # Enrichment: lease clause
    lease_page_key: str = ''
    lease_page_url: str = ''
    lease_extraction: str = ''  # JSON string of UtilityExtraction

    # Posting
    posting_status: str = POST_PENDING
    entrata_txn_id: str = ''
    posting_error: str = ''
    posted_at: str = ''

    def __post_init__(self):
        if not self.line_id:
            self.line_id = _gen_id()


# ── DynamoDB Serialization ──────────────────────────────────────────────────

def _to_ddb_item(obj, pk: str, sk: str) -> Dict[str, Dict]:
    """Convert a dataclass to a DynamoDB item with PK/SK."""
    item = {'pk': {'S': pk}, 'sk': {'S': sk}}
    for k, v in asdict(obj).items():
        if isinstance(v, str):
            if v:  # skip empty strings (DDB doesn't allow empty S)
                item[k] = {'S': v}
        elif isinstance(v, (int, float)):
            item[k] = {'N': str(v)}
        elif isinstance(v, bool):
            item[k] = {'BOOL': v}
    return item


def _from_ddb_item(item: Dict, cls):
    """Convert a DynamoDB item back to a dataclass."""
    kwargs = {}
    type_hints = cls.__dataclass_fields__
    for field_name, field_info in type_hints.items():
        if field_name not in item:
            continue
        ddb_val = item[field_name]
        if 'S' in ddb_val:
            kwargs[field_name] = ddb_val['S']
        elif 'N' in ddb_val:
            ft = field_info.type
            if ft == 'int' or ft is int:
                kwargs[field_name] = int(ddb_val['N'])
            else:
                kwargs[field_name] = float(ddb_val['N'])
        elif 'BOOL' in ddb_val:
            kwargs[field_name] = ddb_val['BOOL']
    return cls(**kwargs)


class VEBatchStore:
    """DynamoDB operations for VE batch and line review state."""

    def __init__(self, ddb_client, table_name: str = DEFAULT_TABLE):
        self.ddb = ddb_client
        self.table = table_name

    # ── Batch CRUD ───────────────────────────────────────────────────────

    def put_batch(self, batch: VEBatch):
        """Write a batch metadata record."""
        batch.updated_at = _now_iso()
        pk = f"BATCH#{batch.batch_id}"
        item = _to_ddb_item(batch, pk, 'META')
        self.ddb.put_item(TableName=self.table, Item=item)

    def get_batch(self, batch_id: str) -> Optional[VEBatch]:
        """Read a batch metadata record."""
        resp = self.ddb.get_item(
            TableName=self.table,
            Key={'pk': {'S': f"BATCH#{batch_id}"}, 'sk': {'S': 'META'}},
        )
        item = resp.get('Item')
        if not item:
            return None
        return _from_ddb_item(item, VEBatch)

    def update_batch_status(self, batch_id: str, status: str, **extra_fields):
        """Update batch status and optional extra fields."""
        expr_parts = ['#st = :st', '#ua = :ua']
        names = {'#st': 'status', '#ua': 'updated_at'}
        values = {':st': {'S': status}, ':ua': {'S': _now_iso()}}

        for k, v in extra_fields.items():
            placeholder = f'#{k}'
            val_placeholder = f':{k}'
            expr_parts.append(f'{placeholder} = {val_placeholder}')
            names[placeholder] = k
            if isinstance(v, str):
                values[val_placeholder] = {'S': v}
            elif isinstance(v, (int, float)):
                values[val_placeholder] = {'N': str(v)}

        self.ddb.update_item(
            TableName=self.table,
            Key={'pk': {'S': f"BATCH#{batch_id}"}, 'sk': {'S': 'META'}},
            UpdateExpression='SET ' + ', '.join(expr_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def list_batches(self, limit: int = 20) -> List[VEBatch]:
        """List recent batches (scan for META records, sorted by created_at desc)."""
        batches = []
        scan_args = {
            'TableName': self.table,
            'FilterExpression': 'sk = :meta',
            'ExpressionAttributeValues': {':meta': {'S': 'META'}},
        }
        while True:
            resp = self.ddb.scan(**scan_args)
            for item in resp.get('Items', []):
                batches.append(_from_ddb_item(item, VEBatch))
            if 'LastEvaluatedKey' not in resp or len(batches) >= limit:
                break
            scan_args['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        batches.sort(key=lambda b: b.created_at, reverse=True)
        return batches[:limit]

    def delete_batch(self, batch_id: str) -> int:
        """Delete a batch and all its lines/posting records."""
        pk = f"BATCH#{batch_id}"
        deleted = 0
        last_key = None
        while True:
            kwargs = dict(
                TableName=self.table,
                KeyConditionExpression='pk = :pk',
                ExpressionAttributeValues={':pk': {'S': pk}},
                ProjectionExpression='pk, sk',
            )
            if last_key:
                kwargs['ExclusiveStartKey'] = last_key
            resp = self.ddb.query(**kwargs)
            items = resp.get('Items', [])
            for i in range(0, len(items), 25):
                chunk = items[i:i + 25]
                requests = [{'DeleteRequest': {'Key': {'pk': it['pk'], 'sk': it['sk']}}} for it in chunk]
                self.ddb.batch_write_item(RequestItems={self.table: requests})
                deleted += len(chunk)
            last_key = resp.get('LastEvaluatedKey')
            if not last_key:
                break
        return deleted

    # ── Line CRUD ────────────────────────────────────────────────────────

    def put_line(self, line: VELineReview):
        """Write a line review record."""
        pk = f"BATCH#{line.batch_id}"
        sk = f"LINE#{line.line_id}"
        item = _to_ddb_item(line, pk, sk)
        self.ddb.put_item(TableName=self.table, Item=item)

    def put_lines_batch(self, lines: List[VELineReview]):
        """Write multiple line records in batch (25 at a time, DDB limit)."""
        for i in range(0, len(lines), 25):
            chunk = lines[i:i + 25]
            requests = []
            for line in chunk:
                pk = f"BATCH#{line.batch_id}"
                sk = f"LINE#{line.line_id}"
                item = _to_ddb_item(line, pk, sk)
                requests.append({'PutRequest': {'Item': item}})
            self.ddb.batch_write_item(RequestItems={self.table: requests})

    def get_line(self, batch_id: str, line_id: str) -> Optional[VELineReview]:
        """Read a single line review record."""
        resp = self.ddb.get_item(
            TableName=self.table,
            Key={
                'pk': {'S': f"BATCH#{batch_id}"},
                'sk': {'S': f"LINE#{line_id}"},
            },
        )
        item = resp.get('Item')
        if not item:
            return None
        return _from_ddb_item(item, VELineReview)

    def get_lines(
        self,
        batch_id: str,
        property_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        action_filter: Optional[str] = None,
        limit: int = 500,
        last_key: Optional[Dict] = None,
    ) -> Dict:
        """
        Query lines for a batch with optional filters.

        Returns dict with 'lines' list and optional 'last_key' for pagination.
        """
        key_condition = 'pk = :pk AND begins_with(sk, :prefix)'
        expr_values = {
            ':pk': {'S': f"BATCH#{batch_id}"},
            ':prefix': {'S': 'LINE#'},
        }

        filter_parts = []
        filter_names = {}
        if property_filter:
            filter_parts.append('#eid = :eid')
            filter_names['#eid'] = 'entity_id'
            expr_values[':eid'] = {'S': property_filter}
        if status_filter:
            filter_parts.append('#rs = :rs')
            filter_names['#rs'] = 'review_status'
            expr_values[':rs'] = {'S': status_filter}
        if action_filter:
            filter_parts.append('#ra = :ra')
            filter_names['#ra'] = 'reviewer_action'
            expr_values[':ra'] = {'S': action_filter}

        query_args = {
            'TableName': self.table,
            'KeyConditionExpression': key_condition,
            'ExpressionAttributeValues': expr_values,
        }
        if filter_parts:
            query_args['FilterExpression'] = ' AND '.join(filter_parts)
            query_args['ExpressionAttributeNames'] = filter_names
        if last_key:
            query_args['ExclusiveStartKey'] = last_key

        # Auto-paginate to collect up to `limit` items
        lines = []
        while len(lines) < limit:
            resp = self.ddb.query(**query_args)
            for item in resp.get('Items', []):
                lines.append(_from_ddb_item(item, VELineReview))
                if len(lines) >= limit:
                    break
            if 'LastEvaluatedKey' not in resp:
                break
            query_args['ExclusiveStartKey'] = resp['LastEvaluatedKey']

        result = {'lines': lines}
        if 'LastEvaluatedKey' in resp and len(lines) >= limit:
            result['last_key'] = resp['LastEvaluatedKey']
        return result

    def update_line_action(
        self,
        batch_id: str,
        line_id: str,
        action: str,
        notes: str = '',
        user: str = '',
    ):
        """Update the reviewer action for a line."""
        self.ddb.update_item(
            TableName=self.table,
            Key={
                'pk': {'S': f"BATCH#{batch_id}"},
                'sk': {'S': f"LINE#{line_id}"},
            },
            UpdateExpression='SET reviewer_action = :act, reviewer_notes = :notes, reviewed_by = :user, reviewed_at = :at',
            ExpressionAttributeValues={
                ':act': {'S': action},
                ':notes': {'S': notes},
                ':user': {'S': user},
                ':at': {'S': _now_iso()},
            },
        )

    def update_line_posting(
        self,
        batch_id: str,
        line_id: str,
        posting_status: str,
        entrata_txn_id: str = '',
        error: str = '',
    ):
        """Update posting status for a line."""
        self.ddb.update_item(
            TableName=self.table,
            Key={
                'pk': {'S': f"BATCH#{batch_id}"},
                'sk': {'S': f"LINE#{line_id}"},
            },
            UpdateExpression='SET posting_status = :ps, entrata_txn_id = :tid, posting_error = :err, posted_at = :at',
            ExpressionAttributeValues={
                ':ps': {'S': posting_status},
                ':tid': {'S': entrata_txn_id},
                ':err': {'S': error},
                ':at': {'S': _now_iso()},
            },
        )

    def bulk_update_action(
        self,
        batch_id: str,
        line_ids: List[str],
        action: str,
        user: str = '',
    ):
        """Update reviewer action for multiple lines."""
        for line_id in line_ids:
            self.update_line_action(batch_id, line_id, action, user=user)

    def get_batch_action_counts(self, batch_id: str) -> Dict[str, int]:
        """Get counts of each reviewer action for a batch."""
        result = self.get_lines(batch_id, limit=5000)
        counts = {ACTION_PENDING: 0, ACTION_APPROVED: 0, ACTION_FLAGGED: 0, ACTION_EXCLUDED: 0}
        for line in result['lines']:
            action = line.reviewer_action or ACTION_PENDING
            counts[action] = counts.get(action, 0) + 1
        return counts

    def get_posting_progress(self, batch_id: str) -> Dict[str, int]:
        """Get counts of each posting status for a batch."""
        result = self.get_lines(batch_id, action_filter=ACTION_APPROVED, limit=5000)
        counts = {POST_PENDING: 0, POST_SUCCESS: 0, POST_FAILED: 0, POST_SKIPPED: 0}
        for line in result['lines']:
            status = line.posting_status or POST_PENDING
            counts[status] = counts.get(status, 0) + 1
        return counts
