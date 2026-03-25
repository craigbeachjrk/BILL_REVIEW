"""
Shared pipeline tracker module — fire-and-forget lifecycle event logging.

Include this module in each Lambda's deployment package and call track_event()
to record bill lifecycle events in DynamoDB (jrk-bill-pipeline-tracker).

Usage:
    from pipeline_tracker import track_event
    track_event(ddb_client, s3_key, "PARSED", "lambda:jrk-bill-parser", "S3",
                metadata={"line_count": 5, "parse_ms": 8200})
"""

import hashlib
import json
import time
from datetime import datetime, timezone

TABLE_NAME = "jrk-bill-pipeline-tracker"


def track_event(ddb, s3_key: str, event_type: str, source: str, stage: str,
                metadata: dict | None = None, table: str = TABLE_NAME):
    """Write a lifecycle event to the pipeline tracker table.

    This is fire-and-forget: errors are printed but never raised,
    so tracker failures never block bill processing.

    Args:
        ddb: boto3 DynamoDB client (low-level, not resource)
        s3_key: Full S3 key of the bill at the time of the event
        event_type: UPLOADED | ROUTED | PARSING | PARSED | ENRICHING | ENRICHED |
                    REVIEW | SUBMITTED | POSTED | FAILED | REWORK
        source: Who wrote the event (e.g., "lambda:jrk-bill-router", "app:submit", "user:cbeach@jrk.com")
        stage: Pipeline stage code (S1, S1_Std, S1_Lg, S2, S3, S4, S6, S7, etc.)
        metadata: Optional dict of stage-specific data (page_count, file_size_mb, error, vendor, etc.)
        table: DynamoDB table name (override for testing)
    """
    try:
        key_hash = hashlib.sha1(s3_key.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        epoch = int(now.timestamp())
        filename = s3_key.rsplit("/", 1)[-1] if "/" in s3_key else s3_key

        item = {
            "pk": {"S": f"BILL#{key_hash}"},
            "sk": {"S": f"EVENT#{now.isoformat()}"},
            "event_type": {"S": event_type},
            "s3_key": {"S": s3_key},
            "stage": {"S": stage},
            "source": {"S": source},
            "timestamp_epoch": {"N": str(epoch)},
            "event_date": {"S": now.strftime("%Y-%m-%d")},
            "filename": {"S": filename},
            "metadata": {"S": json.dumps(metadata or {})},
            "ttl": {"N": str(epoch + 90 * 86400)},  # 90-day expiry
        }

        ddb.put_item(TableName=table, Item=item)
    except Exception as e:
        print(f"[PIPELINE_TRACKER] Failed to log {event_type} for {s3_key}: {e}")
