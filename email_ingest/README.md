# Email Ingest

Lambda and rules for ingesting emails (e.g., routing bills or reports) and placing artifacts into S3 for downstream processing.

## Contents

- `lambda_email_ingest.py` — Lambda handler.
- `setup_email_ingest.ps1` — helper for creating/updating the function, roles, and rules.
- Event rules: `bills_rule.json`, `reports_rule.json`
- IAM: `assume.json`, `inline_policy.json`, `billing_put_policy.json`
- S3 notifications: `s3_notification.json`
- Test payloads: `invoke_payload.json`, `invoke_response.json`

## Default Environment

- AWS profile examples assume `jrk-analytics-admin` and `us-east-1`.
- Target S3 bucket typically `jrk-analytics-billing` (adjust in scripts/policies as needed).

## Deploy

1. Review/adjust IAM policies in this folder.
2. Package and create/update Lambda using `setup_email_ingest.ps1` or AWS Console.
3. Apply EventBridge rules (bills/reports) and S3 notifications as required.

## Test

- Use `invoke_payload.json` with AWS CLI `lambda invoke` or the console.
- Verify S3 object creation and any expected logs.
