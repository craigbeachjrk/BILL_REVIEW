"""
Vendor Document Validator Lambda
Uses Gemini API to validate uploaded documents
"""

import json
import os
import base64
import boto3
from datetime import datetime

# Initialize clients
s3 = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
secrets = boto3.client('secretsmanager')
lambda_client = boto3.client('lambda')

# Configuration
BUCKET = os.environ.get('BUCKET', 'jrk-analytics-billing')
REQUESTS_TABLE = os.environ.get('REQUESTS_TABLE', 'jrk-vendor-requests')
GEMINI_SECRET_NAME = os.environ.get('GEMINI_SECRET_NAME', 'vendor-setup/gemini-api-key')
NOTIFIER_LAMBDA = os.environ.get('NOTIFIER_LAMBDA', 'jrk-vendor-notifier')

# Document expectations
DOC_EXPECTATIONS = {
    "W9": """
        - Form W-9 header or title
        - Taxpayer Identification Number (TIN/EIN/SSN)
        - Business name and address
        - Federal tax classification
        - Signature and date
    """,
    "COI": """
        - Certificate of Insurance header
        - Insurance company name
        - Policy number(s)
        - Coverage types and limits
        - Named insured
        - Certificate holder information
        - Policy effective and expiration dates
    """,
    "Insurance": """
        - Insurance policy or certificate
        - Policy number
        - Coverage amounts
        - Effective dates
        - Insured party name
    """,
    "Business License": """
        - Business license or permit
        - License number
        - Issuing authority
        - Business name
        - Valid dates
    """,
    "Signed Contract": """
        - Contract or agreement document
        - Party names
        - Signature(s)
        - Date(s)
        - Terms and conditions
    """,
    "Credit Application": """
        - Credit application form
        - Business information
        - Banking references
        - Trade references
    """
}


def get_gemini_api_key():
    """Retrieve Gemini API key from Secrets Manager"""
    response = secrets.get_secret_value(SecretId=GEMINI_SECRET_NAME)
    return response['SecretString']


def validate_document_with_gemini(doc_bytes: bytes, doc_type: str, mime_type: str) -> dict:
    """Call Gemini API to validate document"""
    import google.generativeai as genai

    api_key = get_gemini_api_key()
    genai.configure(api_key=api_key)

    # Prepare prompt
    expectations = DOC_EXPECTATIONS.get(doc_type, "Standard business document elements")
    prompt = f"""
Analyze this document and determine if it is a valid {doc_type}.

For a {doc_type}, I expect to see:
{expectations}

Respond with ONLY a JSON object in this exact format:
{{
    "is_valid": true or false,
    "confidence": 0.0 to 1.0,
    "document_type_detected": "what you think this document is",
    "reason": "brief explanation",
    "missing_elements": ["list", "of", "missing", "required", "elements"]
}}
"""

    # Encode document
    doc_b64 = base64.b64encode(doc_bytes).decode('utf-8')

    # Call Gemini
    model = genai.GenerativeModel('gemini-1.5-flash')

    try:
        response = model.generate_content([
            prompt,
            {"mime_type": mime_type, "data": doc_b64}
        ])

        # Parse response
        result_text = response.text.strip()
        # Remove markdown code blocks if present
        if result_text.startswith('```'):
            result_text = result_text.split('\n', 1)[1]
            result_text = result_text.rsplit('```', 1)[0]

        result = json.loads(result_text)
        return result

    except Exception as e:
        print(f"Gemini API error: {e}")
        return {
            "is_valid": False,
            "confidence": 0.0,
            "document_type_detected": "unknown",
            "reason": f"Validation error: {str(e)}",
            "missing_elements": []
        }


def update_document_status(request_id: str, doc_id: str, validation_result: dict):
    """Update document validation status in DynamoDB"""
    table = dynamodb.Table(REQUESTS_TABLE)

    # Get current request (latest version)
    resp = table.query(
        KeyConditionExpression="request_id = :rid",
        ExpressionAttributeValues={":rid": request_id},
        ScanIndexForward=False,
        Limit=1
    )

    if not resp.get('Items'):
        print(f"Request {request_id} not found")
        return

    current = resp['Items'][0]

    # Update document status
    documents = current.get('documents', [])
    for doc in documents:
        if doc.get('document_id') == doc_id:
            doc['validation_status'] = 'valid' if validation_result.get('is_valid') and validation_result.get('confidence', 0) >= 0.8 else 'invalid'
            doc['validation_result'] = validation_result
            doc['validated_utc'] = datetime.utcnow().isoformat() + 'Z'
            break

    # Create new version
    new_version = current['version'] + 1
    new_item = {**current}
    new_item['version'] = new_version
    new_item['documents'] = documents
    new_item['updated_utc'] = datetime.utcnow().isoformat() + 'Z'
    new_item['updated_by'] = 'system:validator'

    table.put_item(Item=new_item)


def move_document(src_key: str, validation_passed: bool) -> str:
    """Move document to validated or rejected folder"""
    if validation_passed:
        dest_key = src_key.replace('1_Pending_Validation', '2_Validated')
    else:
        dest_key = src_key.replace('1_Pending_Validation', '3_Rejected_Docs')

    try:
        s3.copy_object(
            Bucket=BUCKET,
            CopySource={'Bucket': BUCKET, 'Key': src_key},
            Key=dest_key
        )
        s3.delete_object(Bucket=BUCKET, Key=src_key)
        return dest_key
    except Exception as e:
        print(f"Error moving document: {e}")
        return src_key


def notify_validation_failure(request_id: str, doc_type: str, reason: str, missing_elements: list):
    """Trigger notification Lambda for validation failure"""
    try:
        payload = {
            'notification_type': 'validation_failed',
            'request_id': request_id,
            'document_type': doc_type,
            'reason': reason,
            'missing_elements': missing_elements
        }

        lambda_client.invoke(
            FunctionName=NOTIFIER_LAMBDA,
            InvocationType='Event',
            Payload=json.dumps(payload)
        )
    except Exception as e:
        print(f"Error triggering notification: {e}")


def lambda_handler(event, context):
    """
    Main Lambda handler

    Event:
    {
        "request_id": "uuid",
        "document_id": "uuid",
        "s3_bucket": "bucket-name",
        "s3_key": "path/to/document.pdf",
        "document_type": "W9"
    }
    """
    print(f"Processing event: {json.dumps(event)}")

    request_id = event.get('request_id')
    doc_id = event.get('document_id')
    s3_bucket = event.get('s3_bucket', BUCKET)
    s3_key = event.get('s3_key')
    doc_type = event.get('document_type', 'Unknown')

    if not all([request_id, doc_id, s3_key]):
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing required parameters'})
        }

    try:
        # Download document
        obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
        doc_bytes = obj['Body'].read()
        content_type = obj.get('ContentType', 'application/pdf')

        # Determine MIME type
        if s3_key.lower().endswith('.pdf'):
            mime_type = 'application/pdf'
        elif s3_key.lower().endswith(('.jpg', '.jpeg')):
            mime_type = 'image/jpeg'
        elif s3_key.lower().endswith('.png'):
            mime_type = 'image/png'
        else:
            mime_type = content_type

        # Validate with Gemini
        result = validate_document_with_gemini(doc_bytes, doc_type, mime_type)

        # Determine if valid
        is_valid = result.get('is_valid', False) and result.get('confidence', 0) >= 0.8

        # Move document
        new_key = move_document(s3_key, is_valid)
        result['new_s3_key'] = new_key
        result['action'] = 'validated' if is_valid else 'rejected'

        # Update DynamoDB
        update_document_status(request_id, doc_id, result)

        # Send notification if failed
        if not is_valid:
            notify_validation_failure(
                request_id,
                doc_type,
                result.get('reason', 'Validation failed'),
                result.get('missing_elements', [])
            )

        return {
            'statusCode': 200,
            'body': json.dumps(result)
        }

    except Exception as e:
        print(f"Error processing document: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
