"""
Vendor Setup Email Notification Lambda
Sends email notifications via SES
"""

import json
import os
import boto3
from datetime import datetime

# Initialize clients
ses = boto3.client('ses', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb')

# Configuration
CONFIG_TABLE = os.environ.get('CONFIG_TABLE', 'jrk-vendor-config')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'vendorsetup@jrkanalytics.com')
APP_URL = os.environ.get('APP_URL', 'https://vendorsetup.jrkanalytics.com')

# Email templates
EMAIL_TEMPLATES = {
    'request_submitted': {
        'subject': 'Vendor Setup Request: {vendor_name} - Action Required',
        'html': '''
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #0ea5e9, #4338ca); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9fafb; padding: 20px; border-radius: 0 0 8px 8px; }}
        .btn {{ display: inline-block; background: #0ea5e9; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; margin-top: 16px; }}
        .details {{ background: white; padding: 16px; border-radius: 8px; margin: 16px 0; }}
        .details dt {{ font-weight: bold; color: #6b7280; font-size: 12px; margin-top: 8px; }}
        .details dd {{ margin: 4px 0 0 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin:0">Vendor Setup Request</h1>
        </div>
        <div class="content">
            <p>Hello {reviewer_name},</p>
            <p>A new vendor setup request requires your approval.</p>

            <div class="details">
                <dl>
                    <dt>Vendor</dt>
                    <dd>{vendor_name}</dd>
                    <dt>Property</dt>
                    <dd>{property_name}</dd>
                    <dt>Submitted By</dt>
                    <dd>{submitter_name}</dd>
                </dl>
            </div>

            <a href="{app_url}/request/{request_id}" class="btn">Review Request</a>
        </div>
    </div>
</body>
</html>
''',
        'text': '''
Hello {reviewer_name},

A new vendor setup request requires your approval.

Vendor: {vendor_name}
Property: {property_name}
Submitted by: {submitter_name}

Review the request at: {app_url}/request/{request_id}
'''
    },

    'request_approved': {
        'subject': 'Vendor Setup: {vendor_name} - Approved, Pending Final Review',
        'html': '''
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #16a34a, #15803d); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9fafb; padding: 20px; border-radius: 0 0 8px 8px; }}
        .btn {{ display: inline-block; background: #0ea5e9; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; margin-top: 16px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin:0">Approved - Next Review Needed</h1>
        </div>
        <div class="content">
            <p>Hello {reviewer_name},</p>
            <p>A vendor setup request has been approved and now requires your review.</p>
            <p><strong>Vendor:</strong> {vendor_name}<br>
            <strong>Property:</strong> {property_name}<br>
            <strong>Approved by:</strong> {approver_name}</p>
            <a href="{app_url}/request/{request_id}" class="btn">Review Request</a>
        </div>
    </div>
</body>
</html>
''',
        'text': '''
Hello {reviewer_name},

A vendor setup request has been approved and now requires your review.

Vendor: {vendor_name}
Property: {property_name}
Approved by: {approver_name}

Review at: {app_url}/request/{request_id}
'''
    },

    'request_rejected': {
        'subject': 'Vendor Setup: {vendor_name} - Rejected',
        'html': '''
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #dc2626, #b91c1c); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9fafb; padding: 20px; border-radius: 0 0 8px 8px; }}
        .btn {{ display: inline-block; background: #0ea5e9; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; margin-top: 16px; }}
        .reason {{ background: #fee2e2; padding: 16px; border-radius: 8px; margin: 16px 0; border-left: 4px solid #dc2626; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin:0">Request Rejected</h1>
        </div>
        <div class="content">
            <p>Hello {submitter_name},</p>
            <p>Your vendor setup request has been rejected.</p>
            <p><strong>Vendor:</strong> {vendor_name}<br>
            <strong>Rejected by:</strong> {rejector_name}</p>
            <div class="reason">
                <strong>Reason:</strong><br>
                {reason}
            </div>
            <p>Please review and resubmit if needed.</p>
            <a href="{app_url}/request/{request_id}" class="btn">View Request</a>
        </div>
    </div>
</body>
</html>
''',
        'text': '''
Hello {submitter_name},

Your vendor setup request has been rejected.

Vendor: {vendor_name}
Rejected by: {rejector_name}

Reason: {reason}

Please review and resubmit if needed: {app_url}/request/{request_id}
'''
    },

    'request_complete': {
        'subject': 'Vendor Setup: {vendor_name} - Approved!',
        'html': '''
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #16a34a, #15803d); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9fafb; padding: 20px; border-radius: 0 0 8px 8px; }}
        .success {{ background: #d1fae5; padding: 16px; border-radius: 8px; margin: 16px 0; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin:0">Vendor Approved!</h1>
        </div>
        <div class="content">
            <p>Hello {submitter_name},</p>
            <div class="success">
                <h2 style="margin:0;color:#065f46">Your vendor setup request has been fully approved!</h2>
            </div>
            <p><strong>Vendor:</strong> {vendor_name}</p>
            <p>The vendor is now ready to be used.</p>
        </div>
    </div>
</body>
</html>
''',
        'text': '''
Hello {submitter_name},

Your vendor setup request has been fully approved!

Vendor: {vendor_name}

The vendor is now ready to be used.
'''
    },

    'validation_failed': {
        'subject': 'Document Validation Failed: {document_type}',
        'html': '''
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #ea580c, #c2410c); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9fafb; padding: 20px; border-radius: 0 0 8px 8px; }}
        .warning {{ background: #fef3c7; padding: 16px; border-radius: 8px; margin: 16px 0; border-left: 4px solid #ea580c; }}
        .btn {{ display: inline-block; background: #0ea5e9; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; margin-top: 16px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin:0">Document Validation Failed</h1>
        </div>
        <div class="content">
            <p>A document failed automatic validation.</p>
            <p><strong>Request ID:</strong> {request_id}<br>
            <strong>Document Type:</strong> {document_type}</p>
            <div class="warning">
                <strong>Reason:</strong><br>
                {reason}
                <br><br>
                <strong>Missing Elements:</strong><br>
                {missing_elements}
            </div>
            <p>Please review the document manually.</p>
            <a href="{app_url}/request/{request_id}" class="btn">Review Request</a>
        </div>
    </div>
</body>
</html>
''',
        'text': '''
Document Validation Failed

Request ID: {request_id}
Document Type: {document_type}

Reason: {reason}

Missing Elements: {missing_elements}

Review at: {app_url}/request/{request_id}
'''
    }
}


def get_validation_failure_email():
    """Get configured email for validation failures"""
    try:
        table = dynamodb.Table(CONFIG_TABLE)
        resp = table.get_item(Key={
            'config_type': 'system_settings',
            'config_key': 'validation_failure_email'
        })
        return resp.get('Item', {}).get('value', 'cbeach@jrk.com')
    except:
        return 'cbeach@jrk.com'


def send_email(to_email: str, subject: str, html_body: str, text_body: str):
    """Send email via SES"""
    try:
        response = ses.send_email(
            Source=FROM_EMAIL,
            Destination={'ToAddresses': [to_email]},
            Message={
                'Subject': {'Data': subject},
                'Body': {
                    'Html': {'Data': html_body},
                    'Text': {'Data': text_body}
                }
            }
        )
        print(f"Email sent to {to_email}, MessageId: {response['MessageId']}")
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False


def lambda_handler(event, context):
    """
    Main Lambda handler

    Event:
    {
        "notification_type": "request_submitted|request_approved|request_rejected|request_complete|validation_failed",
        "request_id": "uuid",
        ... additional fields based on notification type
    }
    """
    print(f"Processing notification: {json.dumps(event)}")

    notification_type = event.get('notification_type')
    if not notification_type:
        return {'statusCode': 400, 'body': 'Missing notification_type'}

    template = EMAIL_TEMPLATES.get(notification_type)
    if not template:
        return {'statusCode': 400, 'body': f'Unknown notification type: {notification_type}'}

    # Add APP_URL to event data
    event['app_url'] = APP_URL

    # Handle missing_elements formatting
    if 'missing_elements' in event and isinstance(event['missing_elements'], list):
        event['missing_elements'] = ', '.join(event['missing_elements']) if event['missing_elements'] else 'None'

    # Determine recipient
    if notification_type == 'validation_failed':
        to_email = get_validation_failure_email()
    elif notification_type in ['request_rejected', 'request_complete']:
        to_email = event.get('submitter_email')
    else:
        to_email = event.get('reviewer_email')

    if not to_email:
        print(f"No recipient email found for {notification_type}")
        return {'statusCode': 400, 'body': 'No recipient email'}

    # Format template
    try:
        subject = template['subject'].format(**event)
        html_body = template['html'].format(**event)
        text_body = template['text'].format(**event)
    except KeyError as e:
        print(f"Missing template variable: {e}")
        return {'statusCode': 400, 'body': f'Missing template variable: {e}'}

    # Send email
    success = send_email(to_email, subject, html_body, text_body)

    return {
        'statusCode': 200 if success else 500,
        'body': json.dumps({
            'sent': success,
            'to': to_email,
            'type': notification_type
        })
    }
