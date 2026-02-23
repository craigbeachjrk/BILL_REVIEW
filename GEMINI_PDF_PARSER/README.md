# GEMINI_PDF_PARSER

End-to-end pipeline for parsing legal bills and enriching results using serverless components.

## Contents

- Lambda functions:
  - `lambda_bill_parser.py`, `lambda_bill_enricher.py`, `lambda_presigned_upload.py`
- Packaging artifacts: `*.zip`, `*.spec`, `vendor/`, `layer_build*/`, `gemini_layer.zip`
- Policies and configs: `lambda_s3_policy.json`, `s3_notification.json`, `snowflake_*`, trust policies
- Tools: `upload_to_s3_pending_parser.py`, `ping.py`
- Data/validation: `PDF_PARSED_VALIDATION.xlsx`, `TRACKER_TO_VALIDATION_AGAINST.xlsx`

## Buckets and Prefixes

- Primary bucket: `jrk-analytics-billing`
- Common prefixes:
  - `Bill_Parser_1_Pending_Parsing/`
  - `Bill_Parser_2_Parsed_Inputs/`
  - `Bill_Parser_4_Enriched_Outputs/`

## Build/Deploy Notes

- Layers are produced in `layer_build/` and variants; keep zipped for Lambda layer upload.
- Zips `lambda_bill_parser.zip`, `lambda_bill_enricher.zip` can be uploaded directly to Lambda.
- Ensure IAM policies in this folder are applied to corresponding roles.

## Local Utilities

- `upload_to_s3_pending_parser.py` — helper to upload PDFs for parsing.

## Environment

- Configure AWS profile (default `jrk-analytics-admin`) and region as needed.
