"""
Aggregator Lambda - Combines chunk results into final JSONL output
Triggered when all chunks are processed (via DynamoDB Streams or direct invocation)
Writes final output to Bill_Parser_3_Parsed_Outputs/
"""
import os
import json
import boto3
from datetime import datetime, timezone
from urllib.parse import unquote_plus

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")

# Configuration
BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
CHUNK_RESULTS_PREFIX = os.getenv("CHUNK_RESULTS_PREFIX", "Bill_Parser_1_LargeFile_Results/")
CHUNKS_PREFIX = os.getenv("CHUNKS_PREFIX", "Bill_Parser_1_LargeFile_Chunks/")
PARSED_OUTPUTS_PREFIX = os.getenv("PARSED_OUTPUTS_PREFIX", "Bill_Parser_3_Parsed_Outputs/")
JOBS_TABLE = os.getenv("JOBS_TABLE", "jrk-bill-parser-jobs")

# Columns
COLUMNS = [
    "Bill To Name First Line", "Bill To Name Second Line", "Vendor Name", "Invoice Number", "Account Number", "Line Item Account Number",
    "Service Address", "Service City", "Service Zipcode", "Service State", "Meter Number", "Meter Size", "House Or Vacant", "Bill Period Start", "Bill Period End", "Utility Type",
    "Consumption Amount", "Unit of Measure", "Previous Reading", "Previous Reading Date", "Current Reading", "Current Reading Date", "Rate", "Number of Days",
    "Line Item Description", "Line Item Charge",
    "Bill Date", "Due Date", "Special Instructions", "Inferred Fields"
]


def get_job_info(job_id: str) -> dict:
    """Get job information from DynamoDB."""
    try:
        resp = ddb.get_item(TableName=JOBS_TABLE, Key={'job_id': {'S': job_id}})
        if 'Item' not in resp:
            return None

        item = resp['Item']
        return {
            'job_id': item['job_id']['S'],
            'source_file': item['source_file']['S'],
            'total_chunks': int(item['total_chunks']['N']),
            'chunks_completed': int(item['chunks_completed']['N']),
            'status': item['status']['S'],
            'chunk_results': list(item.get('chunk_results', {}).get('SS', []))
        }
    except Exception as e:
        print(f"Error getting job info: {e}")
        return None


def get_chunk_result(result_key: str) -> dict:
    """Download and parse chunk result from S3."""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=result_key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception as e:
        print(f"Error getting chunk result {result_key}: {e}")
        return None


def combine_chunk_results(job_info: dict) -> list[dict]:
    """Combine all chunk results into single list of rows with page metadata.

    Returns list of dicts: {"row": [...], "source_page_start": N, "source_page_end": N, "chunk_num": N}
    """
    all_rows = []

    # Get all chunk result keys - if not in DynamoDB, list from S3
    result_keys = job_info.get('chunk_results', [])
    if not result_keys:
        # List result files from S3
        job_id = job_info['job_id']
        result_prefix = f"{CHUNK_RESULTS_PREFIX}{job_id}/"
        try:
            resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=result_prefix)
            if 'Contents' in resp:
                result_keys = [obj['Key'] for obj in resp['Contents'] if obj['Key'].endswith('.json')]
                result_keys.sort()
                print(json.dumps({"message": "Found result files in S3", "count": len(result_keys)}))
        except Exception as e:
            print(f"Error listing S3 results: {e}")
            return []

    for result_key in result_keys:
        chunk_data = get_chunk_result(result_key)
        if chunk_data and 'rows' in chunk_data:
            chunk_num = chunk_data.get('chunk_num', 0)
            source_page_start = chunk_data.get('source_page_start', 0)
            source_page_end = chunk_data.get('source_page_end', 0)

            # Wrap each row with its page metadata
            for row in chunk_data['rows']:
                all_rows.append({
                    "row": row,
                    "chunk_num": chunk_num,
                    "source_page_start": source_page_start,
                    "source_page_end": source_page_end,
                })

            print(json.dumps({
                "message": "Added chunk rows",
                "chunk": chunk_num,
                "rows": len(chunk_data['rows']),
                "pages": f"{source_page_start}-{source_page_end}"
            }))

    return all_rows


def write_final_jsonl(job_info: dict, all_rows: list[dict]) -> str:
    """Write combined results to final JSONL file in Stage 3.

    Args:
        job_info: Job metadata from DynamoDB
        all_rows: List of dicts with {"row": [...], "chunk_num": N, "source_page_start": N, "source_page_end": N}
    """
    import re as _re
    now = datetime.now(timezone.utc)
    parsed_at_utc = now.isoformat()

    # helper to coerce various date strings into MM/DD/YYYY
    def fmt_us_date(s: str) -> str:
        if not s:
            return ""
        s = str(s).strip()

        # First try flexible regex for M-D-YY, M/D/YY, M-D-YYYY, M/D/YYYY formats
        # This handles single-digit months/days like "11-2-25" or "1-2-25"
        flex_match = _re.match(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})$', s)
        if flex_match:
            m, d, y = flex_match.groups()
            m, d = int(m), int(d)
            y = int(y)
            # Convert 2-digit year to 4-digit (assume 2000s for 00-99)
            if y < 100:
                y = 2000 + y if y < 50 else 1900 + y
            if 1 <= m <= 12 and 1 <= d <= 31:
                return f"{m:02d}/{d:02d}/{y:04d}"

        # common patterns with zero-padded values
        fmts = [
            "%m/%d/%Y", "%m/%d/%y",
            "%Y-%m-%d", "%Y/%m/%d",
            "%m-%d-%Y", "%m-%d-%y",
            "%b %d, %Y", "%B %d, %Y",
        ]
        for f in fmts:
            try:
                dt = datetime.strptime(s, f)
                return dt.strftime("%m/%d/%Y")
            except Exception:
                pass
        # try to pull 8 digits like 20250813 or 08132025
        digits = _re.sub(r"\D", "", s)
        try:
            if len(digits) == 8:
                # try YYYYMMDD then MMDDYYYY
                try:
                    dt = datetime.strptime(digits, "%Y%m%d")
                except Exception:
                    dt = datetime.strptime(digits, "%m%d%Y")
                return dt.strftime("%m/%d/%Y")
        except Exception:
            pass
        return s  # give up: keep original

    # Extract source filename for output key
    source_file = job_info['source_file']
    if '/' in source_file:
        source_filename = source_file.rsplit('/', 1)[-1]
    else:
        source_filename = source_file

    key_stem = source_filename.rsplit('.', 1)[0] if '.' in source_filename else source_filename

    # First pass: convert rows to dicts with page metadata
    all_records = []
    for row_data in all_rows:
        row = row_data.get("row", row_data) if isinstance(row_data, dict) else row_data
        # Handle both old format (list) and new format (dict with "row")
        if isinstance(row, dict) and "row" not in row:
            # Already a dict - just use it
            data = row
        else:
            # Convert list row to dict
            actual_row = row if isinstance(row, list) else row_data
            data = {k: v for k, v in zip(COLUMNS, actual_row[:len(COLUMNS)])}

        # Add page metadata if present
        if isinstance(row_data, dict):
            data["source_chunk"] = row_data.get("chunk_num", 0)
            data["source_page_start"] = row_data.get("source_page_start", 0)
            data["source_page_end"] = row_data.get("source_page_end", 0)

        all_records.append(data)

    # Apply header-level field normalization - ensure all line items have the same header fields
    # Header fields that should be consistent across all line items from the same PDF
    header_fields = [
        "Bill To Name First Line", "Bill To Name Second Line", "Vendor Name",
        "Invoice Number", "Account Number", "Bill Date", "Due Date"
    ]

    from collections import Counter

    # First, normalize Account Number - use Line Item Account Number as fallback
    for record in all_records:
        acct = record.get("Account Number", "").strip()
        line_acct = record.get("Line Item Account Number", "").strip()
        if not acct and line_acct:
            record["Account Number"] = line_acct
        elif acct and not line_acct:
            record["Line Item Account Number"] = acct

    for field in header_fields:
        # Find the most common non-empty value for this field
        values = [r.get(field, "").strip() for r in all_records if r.get(field, "").strip()]
        if values:
            # Use most common value (in case of conflicts)
            most_common_value = Counter(values).most_common(1)[0][0]

            # Apply to all records that are missing this field
            for record in all_records:
                if not record.get(field, "").strip():
                    record[field] = most_common_value

    # Normalize all date fields to MM/DD/YYYY format
    date_fields = [
        "Bill Period Start", "Bill Period End", "Bill Date", "Due Date",
        "Previous Reading Date", "Current Reading Date"
    ]
    for record in all_records:
        for dk in date_fields:
            if dk in record and isinstance(record[dk], str):
                record[dk] = fmt_us_date(record[dk])

    # Second pass: create JSONL with normalized data
    ndjson_lines = []
    for data in all_records:
        data["source_file_page"] = key_stem
        data["source_input_key"] = source_file
        data["PDF_LINK"] = source_file
        data["parsed_at_utc"] = parsed_at_utc
        data["parser_type"] = "large_file_chunked_parallel"
        data["job_id"] = job_info['job_id']
        data["Inferred Fields"] = data.get("Inferred Fields", "").split("-") if data.get("Inferred Fields") else []
        ndjson_lines.append(json.dumps(data, ensure_ascii=False))

    # Write to Stage 3 with date partitioning
    out_prefix = f"{PARSED_OUTPUTS_PREFIX}yyyy={now.year:04d}/mm={now.month:02d}/dd={now.day:02d}/"
    out_key = f"{out_prefix}source=s3/{key_stem}.jsonl"
    body = "\n".join(ndjson_lines) + "\n"

    s3.put_object(Bucket=BUCKET, Key=out_key, Body=body.encode('utf-8'), ContentType='application/x-ndjson')
    return out_key


def cleanup_job_files(job_id: str):
    """Delete temporary chunk and result files."""
    try:
        # List and delete chunk files
        chunk_prefix = f"{CHUNKS_PREFIX}{job_id}/"
        chunk_resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=chunk_prefix)
        if 'Contents' in chunk_resp:
            for obj in chunk_resp['Contents']:
                s3.delete_object(Bucket=BUCKET, Key=obj['Key'])
            print(json.dumps({"message": "Deleted chunk files", "count": len(chunk_resp['Contents'])}))

        # List and delete result files
        result_prefix = f"{CHUNK_RESULTS_PREFIX}{job_id}/"
        result_resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=result_prefix)
        if 'Contents' in result_resp:
            for obj in result_resp['Contents']:
                s3.delete_object(Bucket=BUCKET, Key=obj['Key'])
            print(json.dumps({"message": "Deleted result files", "count": len(result_resp['Contents'])}))

    except Exception as e:
        print(f"Error cleaning up files: {e}")


def update_job_status(job_id: str, status: str, output_key: str = None):
    """Update job status to completed."""
    try:
        update_expr = "SET #status = :status"
        expr_values = {':status': {'S': status}}
        expr_names = {'#status': 'status'}

        if output_key:
            update_expr += ", output_key = :output_key, completed_at = :completed_at"
            expr_values[':output_key'] = {'S': output_key}
            expr_values[':completed_at'] = {'S': datetime.now(timezone.utc).isoformat()}

        ddb.update_item(
            TableName=JOBS_TABLE,
            Key={'job_id': {'S': job_id}},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values
        )
        print(json.dumps({"message": "Job status updated", "job_id": job_id, "status": status}))
    except Exception as e:
        print(f"Error updating job status: {e}")


def process_job(job_id: str):
    """Process a completed job - combine chunks and write final output."""
    print(json.dumps({"message": "Aggregating job", "job_id": job_id}))

    # Get job info
    job_info = get_job_info(job_id)
    if not job_info:
        print(json.dumps({"error": "job_not_found", "job_id": job_id}))
        return

    # Check if all chunks completed
    if job_info['chunks_completed'] < job_info['total_chunks']:
        print(json.dumps({
            "message": "Job not ready for aggregation",
            "job_id": job_id,
            "completed": job_info['chunks_completed'],
            "total": job_info['total_chunks']
        }))
        return

    # Combine chunk results
    all_rows = combine_chunk_results(job_info)
    if not all_rows:
        print(json.dumps({"error": "no_rows_found", "job_id": job_id}))
        update_job_status(job_id, "failed")
        return

    print(json.dumps({"message": "Combined all chunks", "total_rows": len(all_rows)}))

    # Write final JSONL
    try:
        output_key = write_final_jsonl(job_info, all_rows)
        print(json.dumps({"message": "Final output written", "output_key": output_key, "total_rows": len(all_rows)}))
    except Exception as e:
        print(json.dumps({"error": "failed_to_write_output", "message": str(e)}))
        update_job_status(job_id, "failed")
        return

    # Update job status
    update_job_status(job_id, "completed", output_key)

    # Cleanup temporary files
    cleanup_job_files(job_id)

    print(json.dumps({"message": "Job aggregation completed", "job_id": job_id, "output_key": output_key}))


def lambda_handler(event, context):
    """
    Aggregator Handler:
    Can be triggered by:
    1. DynamoDB Streams when chunks_completed == total_chunks
    2. Direct invocation with job_id
    3. S3 event (last chunk result uploaded)
    """

    # Handle direct invocation with job_id
    if 'job_id' in event:
        process_job(event['job_id'])
        return {"statusCode": 200, "body": json.dumps({"ok": True})}

    # Handle DynamoDB Streams
    if 'Records' in event:
        for record in event['Records']:
            # DynamoDB Stream record
            if record.get('eventSource') == 'aws:dynamodb' and record.get('eventName') in ['INSERT', 'MODIFY']:
                new_image = record['dynamodb'].get('NewImage', {})
                job_id = new_image.get('job_id', {}).get('S')
                chunks_completed = int(new_image.get('chunks_completed', {}).get('N', 0))
                total_chunks = int(new_image.get('total_chunks', {}).get('N', 0))
                status = new_image.get('status', {}).get('S', '')

                if chunks_completed == total_chunks and status == 'processing':
                    process_job(job_id)

            # S3 event (chunk result uploaded)
            elif record.get('eventSource') == 'aws:s3':
                key = unquote_plus(record['s3']['object']['key'])
                if key.startswith(CHUNK_RESULTS_PREFIX):
                    # Extract job_id from result key
                    # Format: Bill_Parser_1_LargeFile_Results/{job_id}/chunk_001.json
                    path_parts = key[len(CHUNK_RESULTS_PREFIX):].split('/')
                    if len(path_parts) >= 2:
                        job_id = path_parts[0]
                        # Check if job is ready for aggregation
                        job_info = get_job_info(job_id)
                        if job_info and job_info['chunks_completed'] >= job_info['total_chunks']:
                            process_job(job_id)

    return {"statusCode": 200, "body": json.dumps({"ok": True})}
