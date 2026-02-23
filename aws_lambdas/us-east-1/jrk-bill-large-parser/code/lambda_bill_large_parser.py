"""
Chunk Splitter Lambda - Splits large PDFs into chunks for parallel processing
Triggered by S3 ObjectCreated events on Bill_Parser_1_LargeFile/
Creates chunks in Bill_Parser_1_LargeFile_Chunks/ which trigger chunk processor
"""
import os
import json
import uuid
import boto3
import PyPDF2
from io import BytesIO
from urllib.parse import unquote_plus
from datetime import datetime, timezone

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")

# Configuration
BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
PENDING_PREFIX = os.getenv("PENDING_PREFIX", "Bill_Parser_1_Pending_Parsing/")
LARGEFILE_PREFIX = os.getenv("LARGEFILE_PREFIX", "Bill_Parser_1_LargeFile/")
CHUNKS_PREFIX = os.getenv("CHUNKS_PREFIX", "Bill_Parser_1_LargeFile_Chunks/")
PARSED_INPUTS_PREFIX = os.getenv("PARSED_INPUTS_PREFIX", "Bill_Parser_2_Parsed_Inputs/")
FAILED_PREFIX = os.getenv("FAILED_PREFIX", "Bill_Parser_Failed_Jobs/")
JOBS_TABLE = os.getenv("JOBS_TABLE", "jrk-bill-parser-jobs")
PAGES_PER_CHUNK = int(os.getenv("PAGES_PER_CHUNK", "2"))


def split_pdf_into_chunks(pdf_bytes: bytes, pages_per_chunk: int) -> list[bytes]:
    """Split PDF into chunks of N pages each."""
    try:
        pdf_file = BytesIO(pdf_bytes)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        total_pages = len(pdf_reader.pages)
        chunks = []

        for start_page in range(0, total_pages, pages_per_chunk):
            pdf_writer = PyPDF2.PdfWriter()
            end_page = min(start_page + pages_per_chunk, total_pages)

            for page_num in range(start_page, end_page):
                pdf_writer.add_page(pdf_reader.pages[page_num])

            chunk_bytes = BytesIO()
            pdf_writer.write(chunk_bytes)
            chunk_bytes.seek(0)
            chunks.append(chunk_bytes.read())

        return chunks
    except Exception as e:
        print(f"Error splitting PDF: {e}")
        return []


def get_rework_metadata(bucket: str, pdf_key: str) -> dict:
    """Read .rework.json sidecar file to get expected_lines and bill_from hints.

    The sidecar files are in Bill_Parser_1_Pending_Parsing/ (router only copies PDF).
    """
    metadata = {'expected_lines': 0, 'bill_from': ''}

    # Extract suffix from the key (remove prefix)
    if pdf_key.startswith(LARGEFILE_PREFIX):
        suffix = pdf_key[len(LARGEFILE_PREFIX):]
    else:
        suffix = pdf_key.rsplit('/', 1)[-1] if '/' in pdf_key else pdf_key

    # Look in Pending_Parsing where the sidecar files remain
    pending_base = f"{PENDING_PREFIX}{suffix}"
    base_no_ext = pending_base.rsplit('.', 1)[0]

    # Try .rework.json first (used by send-back-to-parser)
    try:
        rework_key = base_no_ext + '.rework.json'
        obj = s3.get_object(Bucket=bucket, Key=rework_key)
        data = json.loads(obj['Body'].read().decode('utf-8', 'ignore'))
        metadata['expected_lines'] = int(data.get('expected_line_count') or data.get('expected_lines') or data.get('min_lines') or 0)
        metadata['bill_from'] = str(data.get('Bill From') or data.get('bill_from') or '').strip()
        print(json.dumps({"message": "Rework metadata found", "expected_lines": metadata['expected_lines'], "bill_from": metadata['bill_from'][:50], "key": rework_key}))
    except Exception:
        pass

    # Also try .notes.json as fallback
    if not metadata['expected_lines']:
        try:
            notes_key = base_no_ext + '.notes.json'
            obj = s3.get_object(Bucket=bucket, Key=notes_key)
            data = json.loads(obj['Body'].read().decode('utf-8', 'ignore'))
            metadata['expected_lines'] = int(data.get('expected_line_count') or data.get('expected_lines') or data.get('min_lines') or 0)
            if not metadata['bill_from']:
                metadata['bill_from'] = str(data.get('Bill From') or data.get('bill_from') or '').strip()
        except Exception:
            pass

    return metadata


def create_job_record(job_id: str, source_file: str, total_chunks: int, chunk_keys: list[str], expected_lines: int = 0, bill_from: str = '', pages_per_chunk: int = 2):
    """Create job tracking record in DynamoDB."""
    now = datetime.now(timezone.utc)
    item = {
        'job_id': {'S': job_id},
        'source_file': {'S': source_file},
        'total_chunks': {'N': str(total_chunks)},
        'chunks_completed': {'N': '0'},
        'status': {'S': 'processing'},
        'created_at': {'S': now.isoformat()},
        'chunk_keys': {'L': [{'S': k} for k in chunk_keys]},
        'chunk_results': {'L': []},  # Will be populated by chunk processors
        'previous_context': {'S': ''},  # Summary of previous chunks for context
        'expected_lines': {'N': str(expected_lines)},  # Hint for chunk processors
        'bill_from': {'S': bill_from},  # Vendor hint for chunk processors
        'pages_per_chunk': {'N': str(pages_per_chunk)},  # Pages per chunk for page tracking
    }
    ddb.put_item(TableName=JOBS_TABLE, Item=item)
    print(json.dumps({"message": "Job record created", "job_id": job_id, "total_chunks": total_chunks, "expected_lines": expected_lines, "pages_per_chunk": pages_per_chunk}))


def lambda_handler(event, context):
    """
    Chunk Splitter Handler:
    1. Receives large PDF from S3
    2. Splits into chunks (5 pages each)
    3. Saves chunks to S3 (triggers chunk processor)
    4. Creates job tracking record in DynamoDB
    """
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            continue

        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        if not key.startswith(LARGEFILE_PREFIX):
            continue

        # Generate job ID
        suffix = key[len(LARGEFILE_PREFIX):]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        job_id = f"{timestamp}_{uuid.uuid4().hex[:8]}"

        # Move to Parsed Inputs for archival
        dest_key_inputs = f"{PARSED_INPUTS_PREFIX}{suffix}"
        try:
            s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=dest_key_inputs)
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception as e:
            print(json.dumps({"error": "failed_to_move_file", "key": key, "message": str(e)}))
            continue

        # Download PDF
        try:
            obj = s3.get_object(Bucket=bucket, Key=dest_key_inputs)
            pdf_bytes = obj['Body'].read()
        except Exception as e:
            print(json.dumps({"error": "failed_to_download", "key": dest_key_inputs, "message": str(e)}))
            continue

        # Split into chunks
        chunks = split_pdf_into_chunks(pdf_bytes, PAGES_PER_CHUNK)
        if not chunks:
            failed_key = f"{FAILED_PREFIX}{suffix}"
            s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': dest_key_inputs}, Key=failed_key)
            print(json.dumps({"error": "failed_to_split_pdf", "failed_key": failed_key}))
            continue

        print(json.dumps({
            "message": "Splitting large PDF",
            "job_id": job_id,
            "source_file": suffix,
            "total_chunks": len(chunks),
            "pages_per_chunk": PAGES_PER_CHUNK
        }))

        # Build chunk keys list first
        chunk_keys = []
        for idx in range(len(chunks)):
            chunk_num = str(idx + 1).zfill(3)
            chunk_key = f"{CHUNKS_PREFIX}{job_id}/chunk_{chunk_num}.pdf"
            chunk_keys.append(chunk_key)

        # Get rework metadata (expected_lines, bill_from) from sidecar files
        metadata = get_rework_metadata(bucket, key)

        # CRITICAL: Create job tracking record BEFORE uploading chunks
        # Chunk processors are triggered by S3 events and need the job record to exist
        try:
            create_job_record(job_id, dest_key_inputs, len(chunks), chunk_keys,
                            expected_lines=metadata['expected_lines'],
                            bill_from=metadata['bill_from'],
                            pages_per_chunk=PAGES_PER_CHUNK)
        except Exception as e:
            print(json.dumps({"error": "failed_to_create_job_record", "job_id": job_id, "message": str(e)}))
            continue  # Don't upload chunks if job record creation fails

        # Now save chunks to S3 (will trigger chunk processor)
        for idx, chunk_bytes in enumerate(chunks):
            chunk_num = str(idx + 1).zfill(3)
            chunk_key = chunk_keys[idx]

            try:
                s3.put_object(
                    Bucket=bucket,
                    Key=chunk_key,
                    Body=chunk_bytes,
                    ContentType='application/pdf',
                    Metadata={
                        'job_id': job_id,
                        'chunk_num': chunk_num,
                        'total_chunks': str(len(chunks)),
                        'source_file': dest_key_inputs
                    }
                )
                print(json.dumps({"message": f"Saved chunk {idx+1}/{len(chunks)}", "chunk_key": chunk_key}))
            except Exception as e:
                print(json.dumps({"error": "failed_to_save_chunk", "chunk": chunk_num, "message": str(e)}))

    return {"statusCode": 200, "body": json.dumps({"ok": True})}
