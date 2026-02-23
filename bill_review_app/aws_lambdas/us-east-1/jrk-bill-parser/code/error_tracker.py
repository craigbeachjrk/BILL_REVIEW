"""
Error tracking for bill parser - logs Gemini API errors to DynamoDB
"""
import json
import boto3
from datetime import datetime, timezone


def log_parser_error(ddb_client, table_name: str, pdf_key: str, error_type: str, error_details: dict):
    """
    Log parsing error to DynamoDB for tracking and analysis.

    Args:
        ddb_client: boto3 DynamoDB client
        table_name: DynamoDB table name for errors
        pdf_key: S3 key of the PDF that failed
        error_type: Type of error (e.g., 'gemini_api_error', 'column_count_error', 'timeout', etc.)
        error_details: Dict with error details (status_code, message, attempt_number, etc.)
    """
    try:
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()

        # Extract filename from key for easier searching
        filename = pdf_key.rsplit('/', 1)[-1] if '/' in pdf_key else pdf_key

        item = {
            'pk': {'S': f"ERROR#{filename}"},
            'timestamp': {'S': timestamp},
            'pdf_key': {'S': pdf_key},
            'error_type': {'S': error_type},
            'error_details': {'S': json.dumps(error_details, ensure_ascii=False)},
            'date': {'S': now.strftime('%Y-%m-%d')},
            'hour': {'S': now.strftime('%Y-%m-%dT%H:00:00')},
        }

        # Add specific fields for easier querying
        if 'status_code' in error_details:
            item['http_status'] = {'N': str(error_details['status_code'])}
        if 'attempt_number' in error_details:
            item['attempt'] = {'N': str(error_details['attempt_number'])}
        if 'gemini_error_code' in error_details:
            item['gemini_code'] = {'S': str(error_details['gemini_error_code'])}

        ddb_client.put_item(TableName=table_name, Item=item)

        return True
    except Exception as e:
        # Don't fail the Lambda if error logging fails
        print(f"Failed to log error to DynamoDB: {e}")
        return False


def extract_gemini_error_code(response_text: str) -> str:
    """
    Extract error code from Gemini API error response.
    Common codes: INVALID_ARGUMENT, RESOURCE_EXHAUSTED, PERMISSION_DENIED, etc.
    """
    try:
        data = json.loads(response_text)
        # Gemini error format: {"error": {"code": 400, "message": "...", "status": "INVALID_ARGUMENT"}}
        if isinstance(data, dict) and 'error' in data:
            err = data['error']
            if isinstance(err, dict):
                return err.get('status') or err.get('code') or 'UNKNOWN'
    except:
        pass

    # Fallback: search for common error patterns
    if 'RESOURCE_EXHAUSTED' in response_text:
        return 'RESOURCE_EXHAUSTED'
    elif 'INVALID_ARGUMENT' in response_text:
        return 'INVALID_ARGUMENT'
    elif 'PERMISSION_DENIED' in response_text:
        return 'PERMISSION_DENIED'
    elif 'NOT_FOUND' in response_text:
        return 'NOT_FOUND'
    elif 'DEADLINE_EXCEEDED' in response_text:
        return 'DEADLINE_EXCEEDED'

    return 'UNKNOWN'
