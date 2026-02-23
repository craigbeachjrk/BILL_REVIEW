# Policies and Infra Scripts

IAM policies and helper scripts used by the Bill Review application.

## Files

- `trust-lambda.json`
  - Trust policy for Lambda service (`Service: lambda.amazonaws.com`).
- `trust-policy.json`
  - External trust policy example for cross-account/partner (contains ExternalId).
- `s3-read-policy.json`
  - Grants List/Get access to `jrk-analytics-billing/Bill_Parser_4_Enriched_Outputs/*`.
- `enricher-pdf-read.json`
  - Grants Get access to `jrk-analytics-billing/Bill_Parser_2_Parsed_Inputs/*`.
- `set_rework_lambda_policy.ps1`
  - Creates/updates inline IAM policy on role `jrk-bill-parser-rework-role` to:
    - Get from `Bill_Parser_Rework_Input/`
    - Put to `Bill_Parser_1_Pending_Parsing/`
    - List limited prefixes
- `set_rework_app_role_policy.ps1`
  - Creates/updates inline IAM policy on `jrk-bill-review-instance-role` to:
    - Put to `Bill_Parser_Rework_Input/`
    - Delete from `Bill_Parser_4_Enriched_Outputs/` and `Bill_Parser_6_PreEntrata_Submission/`
    - List limited prefixes
- `set_rework_env_and_notif.ps1`
  - Helper to configure Lambda environment variables and S3 event notifications.
- `create_rework_lambda.ps1`, `create_rework_lambda_fixed.ps1`
  - Variants for creating Lambda + role using provided trust policies.

## Defaults

- AWS profile: `jrk-analytics-admin`
- Region: `us-east-1`
- Bucket: `jrk-analytics-billing`

## Usage Examples

- Update the inline policy for the rework Lambda role:
```powershell
./set_rework_lambda_policy.ps1 -Profile jrk-analytics-admin -Region us-east-1 -RoleName jrk-bill-parser-rework-role -Bucket jrk-analytics-billing
```

- Update the app instance role permissions:
```powershell
./set_rework_app_role_policy.ps1 -Profile jrk-analytics-admin -Region us-east-1 -RoleName jrk-bill-review-instance-role -Bucket jrk-analytics-billing
```
