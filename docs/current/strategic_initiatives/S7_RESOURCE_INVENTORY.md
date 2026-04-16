# S7: AWS Resource Inventory (for CloudFormation)

Generated 2026-04-09. This is the complete inventory of all AWS resources used by the Bill Review application.

## DynamoDB Tables (16)

| Table | PK | SK | GSIs | Used By |
|-------|----|----|------|---------|
| jrk-bill-review | id | - | - | main.py (review status) |
| jrk-bill-review-users | user_id | - | role-index | auth.py |
| jrk-bill-config | config_key | - | - | main.py, jrk-bill-index-builder |
| jrk-bill-drafts | pk | - | - | main.py (drafts, timing) |
| jrk-bill-pipeline-tracker | pk (BILL#{hash}) | sk (EVENT#{ts}) | gsi-stage-time, gsi-date | All Lambdas + main.py |
| jrk-bill-parser-jobs | job_id | - | - | large-parser, chunk-processor, aggregator |
| jrk-bill-parser-errors | - | - | - | parser, chunk-processor, failure-router |
| jrk-bill-router-log | - | - | - | router |
| jrk-bill-ai-suggestions | pk | sk | - | main.py (AI review) |
| jrk-bill-review-debug | report_id | - | - | main.py (debug/triage) |
| jrk-bill-knowledge-base | pk | sk | - | main.py (knowledge) |
| jrk-bill-manual-entries | entry_id | - | - | main.py (accrual) |
| jrk-manual-billback-entries | entry_id | - | - | main.py (manual CSV) |
| jrk-check-slips | check_slip_id | - | - | main.py (checks) |
| jrk-check-slip-invoices | pdf_id | - | - | main.py (checks) |
| jrk-url-short | - | - | - | main.py (URL shortener) |

## S3 Buckets (3)

### jrk-analytics-billing (primary)
Pipeline stages:
- `Bill_Parser_1_Pending_Parsing/` - Incoming PDFs
- `Bill_Parser_1_Standard/` - Routed to standard parser
- `Bill_Parser_1_LargeFile/` - Routed to large parser
- `Bill_Parser_1_LargeFile_Chunks/` - Split chunks
- `Bill_Parser_1_LargeFile_Results/` - Chunk results
- `Bill_Parser_2_Parsed_Inputs/` - Parsed input archive
- `Bill_Parser_3_Parsed_Outputs/` - Parsed JSONL
- `Bill_Parser_4_Enriched_Outputs/` - Enriched JSONL
- `Bill_Parser_5_Overrides/` - User overrides
- `Bill_Parser_6_PreEntrata_Submission/` - Pre-post validation
- `Bill_Parser_7_PostEntrata_Submission/` - Posted to Entrata
- `Bill_Parser_8_UBI_Assigned/` - UBI period assigned
- `Bill_Parser_9_Flagged_Review/` - QC flagged
- `Bill_Parser_99_Historical Archive/` - Archive
- `Bill_Parser_Failed_Jobs/` - Failed parsing
- `Bill_Parser_Rework_Input/` - Rework queue
- `Bill_Parser_Rework_Archive/` - Rework enrichment backup
- `Bill_Parser_Deleted_Archive/` - Soft-delete backup
- `Bill_Parser_Config/` - Config files (caches, mappings)
- `Bill_Parser_Enrichment/exports/` - Dimension tables
- `Bill_Parser_Meter_Data/` - Meter analytics

### jrk-email-partitioned-us-east-1
- Email archive (raw .eml + attachments)

### api_vendor (vendor cache output)
- `vendors/` prefix

## Lambda Functions (15)

| Function | Trigger | Key Env Vars |
|----------|---------|-------------|
| jrk-email-ingest | S3 (email objects) | TARGET_BUCKET, PIPELINE_TRACKER_TABLE |
| jrk-bill-router | S3 (Pending_Parsing/) | MAX_PAGES_STANDARD, MAX_SIZE_MB_STANDARD |
| jrk-bill-parser | S3 (Standard/) | PARSER_SECRET_NAME, PIPELINE_TRACKER_TABLE |
| jrk-bill-large-parser | S3 (LargeFile/) | PAGES_PER_CHUNK, JOBS_TABLE |
| jrk-bill-chunk-processor | S3 (Chunks/) | PARSER_SECRET_NAME, AGGREGATOR_LAMBDA_ARN |
| jrk-bill-aggregator | DDB Streams / invoke | JOBS_TABLE |
| jrk-bill-parser-failure-router | Lambda failure dest | ERRORS_TABLE |
| jrk-bill-enricher | S3 (Parsed_Outputs/) | MATCHER_SECRET_NAME, PIPELINE_TRACKER_TABLE |
| jrk-bill-index-builder | EventBridge schedule | DDB_CONFIG_TABLE |
| jrk-meter-cleaner | EventBridge schedule | GEMINI_SECRET_NAME |
| jrk-presigned-upload | API Gateway | API_KEY, ALLOWLIST_IPS |
| jrk-vendor-notifier | Direct invoke | FROM_EMAIL, CONFIG_TABLE |
| jrk-vendor-validator | Direct invoke | REQUESTS_TABLE, GEMINI_SECRET_NAME |
| vendor-cache-builder | Manual/schedule | ENTRATA_SECRET_NAME |
| jrk-bw-lookup | Direct invoke | BW_SECRET_NAME |

## AppRunner

- **Service:** jrk-bill-review
- **ARN:** arn:aws:apprunner:us-east-1:789814232318:service/jrk-bill-review/a061a5295eb341c19bb159d97500eabd
- **Image:** 789814232318.dkr.ecr.us-east-1.amazonaws.com/jrk-bill-review:latest
- **Port:** 8080
- **Command:** uvicorn main:app --host 0.0.0.0 --port 8080
- **IAM Role:** jrk-bill-review-apprunner-ecr

## Other Services

| Service | Resource | Purpose |
|---------|----------|---------|
| ECR | jrk-bill-review repo | Docker images |
| CodeBuild | jrk-bill-review-build | CI/CD pipeline |
| SQS | jrk-bill-process | Review queue |
| SES | vendorsetup@jrkanalytics.com | Vendor notifications |
| Secrets Manager | gemini/parser-keys | Gemini API keys |
| Secrets Manager | gemini/matcher-keys | Enricher API keys |
| Secrets Manager | jrk/entrata_core | Entrata credentials |
| Secrets Manager | bitwarden/api-key | BW vault access |
| Secrets Manager | vendor-setup/gemini-api-key | Vendor validation |
| EventBridge | Daily schedule | Index builder, meter cleaner |

## Account Info
- **Account:** 789814232318
- **Region:** us-east-1
