# Big Bill Processing Pipeline - Deployment Guide

## Overview

This pipeline processes large PDFs (>10 pages or >10MB) by splitting them into 2-page chunks for parallel Gemini API parsing. Results are aggregated with page metadata so the UI can show which page each line item came from.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           S3 BUCKET: jrk-analytics-billing                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Bill_Parser_1_Pending_Parsing/  ◄── Email ingest / manual upload           │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                        ┌─────────────────────────┐
                        │   jrk-bill-router       │
                        │   (Lambda #1)           │
                        │                         │
                        │   - Count PDF pages     │
                        │   - Check file size     │
                        │   - Route accordingly   │
                        └─────────────────────────┘
                           │                  │
            ≤10 pages      │                  │  >10 pages OR >10MB
            ≤10MB          │                  │
                           ▼                  ▼
        ┌──────────────────────┐    ┌──────────────────────┐
        │ Bill_Parser_1_Standard│    │ Bill_Parser_1_LargeFile│
        │ (existing parser)     │    │                      │
        └──────────────────────┘    └──────────────────────┘
                                              │
                                              ▼
                              ┌─────────────────────────┐
                              │  jrk-bill-large-parser  │
                              │  (Lambda #2)            │
                              │                         │
                              │  - Split into 2-page    │
                              │    chunks               │
                              │  - Create job record    │
                              │    in DynamoDB          │
                              │  - Upload chunks to S3  │
                              └─────────────────────────┘
                                              │
                                              ▼
                              ┌──────────────────────────┐
                              │ Bill_Parser_1_LargeFile_ │
                              │ Chunks/{job_id}/         │
                              │   chunk_001.pdf          │
                              │   chunk_002.pdf          │
                              │   chunk_003.pdf ...      │
                              └──────────────────────────┘
                                              │
                         (S3 triggers - parallel execution)
                                              │
                    ┌─────────────┬───────────┼───────────┬─────────────┐
                    ▼             ▼           ▼           ▼             ▼
          ┌─────────────────────────────────────────────────────────────────┐
          │                  jrk-bill-chunk-processor (Lambda #3)           │
          │                                                                 │
          │  - Parse chunk with Gemini API                                  │
          │  - Key rotation + exponential backoff (10 API keys)             │
          │  - Maintain context from previous chunks                        │
          │  - Content validation (detect column shifts)                    │
          │  - Save result to S3                                            │
          │  - Update job progress in DynamoDB                              │
          └─────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
                              ┌──────────────────────────┐
                              │ Bill_Parser_1_LargeFile_ │
                              │ Results/{job_id}/        │
                              │   chunk_001.json         │
                              │   chunk_002.json ...     │
                              └──────────────────────────┘
                                              │
                        (DynamoDB Streams when chunks_completed == total_chunks)
                                              │
                                              ▼
                              ┌─────────────────────────┐
                              │  jrk-bill-aggregator    │
                              │  (Lambda #4)            │
                              │                         │
                              │  - Combine chunk results│
                              │  - Normalize headers    │
                              │  - Add page metadata    │
                              │  - Write final JSONL    │
                              │  - Cleanup temp files   │
                              └─────────────────────────┘
                                              │
                                              ▼
                              ┌──────────────────────────┐
                              │ Bill_Parser_3_Parsed_    │
                              │ Outputs/yyyy=.../        │
                              │   source=s3/{file}.jsonl │
                              └──────────────────────────┘
                                              │
                                              ▼
                                   (Existing enricher flow)
```

## Prerequisites

### 1. AWS SSO Login
```powershell
aws sso login --profile jrk-analytics-admin
```

### 2. Verify Gemini API Keys in Secrets Manager
```powershell
aws secretsmanager get-secret-value --secret-id gemini/parser-keys --region us-east-1 --profile jrk-analytics-admin --query 'SecretString' --output text
```

Should return JSON with 10 API keys:
```json
{"keys": ["key1", "key2", "key3", ...]}
```

---

## Step 1: Create DynamoDB Table

```powershell
aws dynamodb create-table `
  --table-name jrk-bill-parser-jobs `
  --attribute-definitions AttributeName=job_id,AttributeType=S `
  --key-schema AttributeName=job_id,KeyType=HASH `
  --billing-mode PAY_PER_REQUEST `
  --stream-specification StreamEnabled=true,StreamViewType=NEW_IMAGE `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

Wait for table to become ACTIVE:
```powershell
aws dynamodb wait table-exists --table-name jrk-bill-parser-jobs --region us-east-1 --profile jrk-analytics-admin
```

---

## Step 2: Create IAM Role for Lambdas

### Create trust policy file (`/tmp/lambda-trust.json`):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "lambda.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
```

### Create the role:
```powershell
aws iam create-role `
  --role-name jrk-bill-large-parser-role `
  --assume-role-policy-document file:///tmp/lambda-trust.json `
  --profile jrk-analytics-admin
```

### Attach policies:
```powershell
# Basic Lambda execution
aws iam attach-role-policy `
  --role-name jrk-bill-large-parser-role `
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole `
  --profile jrk-analytics-admin

# S3 access
aws iam put-role-policy `
  --role-name jrk-bill-large-parser-role `
  --policy-name S3Access `
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:HeadObject"],
      "Resource": [
        "arn:aws:s3:::jrk-analytics-billing",
        "arn:aws:s3:::jrk-analytics-billing/*"
      ]
    }]
  }' `
  --profile jrk-analytics-admin

# DynamoDB access
aws iam put-role-policy `
  --role-name jrk-bill-large-parser-role `
  --policy-name DynamoDBAccess `
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"],
      "Resource": [
        "arn:aws:dynamodb:us-east-1:789814232318:table/jrk-bill-parser-jobs",
        "arn:aws:dynamodb:us-east-1:789814232318:table/jrk-bill-parser-errors"
      ]
    }]
  }' `
  --profile jrk-analytics-admin

# Secrets Manager access (for Gemini API keys)
aws iam put-role-policy `
  --role-name jrk-bill-large-parser-role `
  --policy-name SecretsAccess `
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:us-east-1:789814232318:secret:gemini/parser-keys*"
    }]
  }' `
  --profile jrk-analytics-admin
```

---

## Step 3: Deploy Lambda #1 - Router

### Package the Lambda:
```powershell
cd C:\Users\cbeach\Desktop\billreview_local\aws_lambdas\us-east-1\jrk-bill-router\code
Compress-Archive -Path * -DestinationPath ..\router.zip -Force
```

### Create the Lambda:
```powershell
aws lambda create-function `
  --function-name jrk-bill-router `
  --runtime python3.11 `
  --role arn:aws:iam::789814232318:role/jrk-bill-large-parser-role `
  --handler lambda_bill_router.lambda_handler `
  --zip-file fileb://C:\Users\cbeach\Desktop\billreview_local\aws_lambdas\us-east-1\jrk-bill-router\router.zip `
  --timeout 60 `
  --memory-size 512 `
  --environment 'Variables={BUCKET=jrk-analytics-billing,MAX_PAGES_STANDARD=10,MAX_SIZE_MB_STANDARD=10}' `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

### Add S3 trigger permission:
```powershell
aws lambda add-permission `
  --function-name jrk-bill-router `
  --statement-id s3-trigger `
  --action lambda:InvokeFunction `
  --principal s3.amazonaws.com `
  --source-arn arn:aws:s3:::jrk-analytics-billing `
  --source-account 789814232318 `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

### Configure S3 trigger (via S3 notification):
```powershell
# Get current notification config first, then add this trigger
aws s3api put-bucket-notification-configuration `
  --bucket jrk-analytics-billing `
  --notification-configuration '{
    "LambdaFunctionConfigurations": [
      {
        "LambdaFunctionArn": "arn:aws:lambda:us-east-1:789814232318:function:jrk-bill-router",
        "Events": ["s3:ObjectCreated:*"],
        "Filter": {
          "Key": {
            "FilterRules": [
              {"Name": "prefix", "Value": "Bill_Parser_1_Pending_Parsing/"},
              {"Name": "suffix", "Value": ".pdf"}
            ]
          }
        }
      }
    ]
  }' `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

**NOTE**: Be careful not to overwrite existing S3 notification configs. Get current config first with:
```powershell
aws s3api get-bucket-notification-configuration --bucket jrk-analytics-billing --profile jrk-analytics-admin
```

---

## Step 4: Deploy Lambda #2 - Large Parser (Chunk Splitter)

### Package:
```powershell
cd C:\Users\cbeach\Desktop\billreview_local\aws_lambdas\us-east-1\jrk-bill-large-parser\code
Compress-Archive -Path * -DestinationPath ..\large-parser.zip -Force
```

### Create:
```powershell
aws lambda create-function `
  --function-name jrk-bill-large-parser `
  --runtime python3.11 `
  --role arn:aws:iam::789814232318:role/jrk-bill-large-parser-role `
  --handler lambda_bill_large_parser.lambda_handler `
  --zip-file fileb://C:\Users\cbeach\Desktop\billreview_local\aws_lambdas\us-east-1\jrk-bill-large-parser\large-parser.zip `
  --timeout 300 `
  --memory-size 1024 `
  --environment 'Variables={BUCKET=jrk-analytics-billing,PAGES_PER_CHUNK=2}' `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

### Add S3 trigger for LargeFile prefix:
```powershell
aws lambda add-permission `
  --function-name jrk-bill-large-parser `
  --statement-id s3-largefile-trigger `
  --action lambda:InvokeFunction `
  --principal s3.amazonaws.com `
  --source-arn arn:aws:s3:::jrk-analytics-billing `
  --source-account 789814232318 `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

---

## Step 5: Deploy Lambda #3 - Chunk Processor

### Package:
```powershell
cd C:\Users\cbeach\Desktop\billreview_local\aws_lambdas\us-east-1\jrk-bill-chunk-processor\code
Compress-Archive -Path * -DestinationPath ..\chunk-processor.zip -Force
```

### Create:
```powershell
aws lambda create-function `
  --function-name jrk-bill-chunk-processor `
  --runtime python3.11 `
  --role arn:aws:iam::789814232318:role/jrk-bill-large-parser-role `
  --handler lambda_chunk_processor.lambda_handler `
  --zip-file fileb://C:\Users\cbeach\Desktop\billreview_local\aws_lambdas\us-east-1\jrk-bill-chunk-processor\chunk-processor.zip `
  --timeout 300 `
  --memory-size 1024 `
  --environment 'Variables={BUCKET=jrk-analytics-billing,MODEL_NAME=gemini-2.0-flash}' `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

### Add S3 trigger for Chunks prefix:
```powershell
aws lambda add-permission `
  --function-name jrk-bill-chunk-processor `
  --statement-id s3-chunks-trigger `
  --action lambda:InvokeFunction `
  --principal s3.amazonaws.com `
  --source-arn arn:aws:s3:::jrk-analytics-billing `
  --source-account 789814232318 `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

---

## Step 6: Deploy Lambda #4 - Aggregator

### Package:
```powershell
cd C:\Users\cbeach\Desktop\billreview_local\aws_lambdas\us-east-1\jrk-bill-aggregator\code
Compress-Archive -Path * -DestinationPath ..\aggregator.zip -Force
```

### Create:
```powershell
aws lambda create-function `
  --function-name jrk-bill-aggregator `
  --runtime python3.11 `
  --role arn:aws:iam::789814232318:role/jrk-bill-large-parser-role `
  --handler lambda_aggregator.lambda_handler `
  --zip-file fileb://C:\Users\cbeach\Desktop\billreview_local\aws_lambdas\us-east-1\jrk-bill-aggregator\aggregator.zip `
  --timeout 300 `
  --memory-size 512 `
  --environment 'Variables={BUCKET=jrk-analytics-billing}' `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

### Create DynamoDB Streams trigger:
```powershell
# Get the stream ARN
$STREAM_ARN = aws dynamodb describe-table `
  --table-name jrk-bill-parser-jobs `
  --query 'Table.LatestStreamArn' `
  --output text `
  --region us-east-1 `
  --profile jrk-analytics-admin

# Create event source mapping
aws lambda create-event-source-mapping `
  --function-name jrk-bill-aggregator `
  --event-source-arn $STREAM_ARN `
  --starting-position LATEST `
  --batch-size 1 `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

---

## Step 7: Configure All S3 Triggers Together

Get current config and merge with new triggers:

```powershell
# Save this as s3-notifications.json and apply
{
  "LambdaFunctionConfigurations": [
    {
      "Id": "router-pending-pdf",
      "LambdaFunctionArn": "arn:aws:lambda:us-east-1:789814232318:function:jrk-bill-router",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": {
        "Key": {
          "FilterRules": [
            {"Name": "prefix", "Value": "Bill_Parser_1_Pending_Parsing/"},
            {"Name": "suffix", "Value": ".pdf"}
          ]
        }
      }
    },
    {
      "Id": "large-parser-trigger",
      "LambdaFunctionArn": "arn:aws:lambda:us-east-1:789814232318:function:jrk-bill-large-parser",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": {
        "Key": {
          "FilterRules": [
            {"Name": "prefix", "Value": "Bill_Parser_1_LargeFile/"},
            {"Name": "suffix", "Value": ".pdf"}
          ]
        }
      }
    },
    {
      "Id": "chunk-processor-trigger",
      "LambdaFunctionArn": "arn:aws:lambda:us-east-1:789814232318:function:jrk-bill-chunk-processor",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": {
        "Key": {
          "FilterRules": [
            {"Name": "prefix", "Value": "Bill_Parser_1_LargeFile_Chunks/"},
            {"Name": "suffix", "Value": ".pdf"}
          ]
        }
      }
    }
  ]
}
```

**IMPORTANT**: Merge this with existing S3 notification config (jrk-bill-parser, etc.)

---

## Testing

### Test with a large PDF:
```powershell
# Upload a large PDF (>10 pages) to trigger the pipeline
aws s3 cp "C:\path\to\large-bill.pdf" s3://jrk-analytics-billing/Bill_Parser_1_Pending_Parsing/test-large-bill.pdf --profile jrk-analytics-admin
```

### Monitor progress:
```powershell
# Check router logs
aws logs tail /aws/lambda/jrk-bill-router --follow --profile jrk-analytics-admin

# Check job status in DynamoDB
aws dynamodb scan --table-name jrk-bill-parser-jobs --profile jrk-analytics-admin

# Check chunk results in S3
aws s3 ls s3://jrk-analytics-billing/Bill_Parser_1_LargeFile_Results/ --recursive --profile jrk-analytics-admin
```

---

## Updating Lambda Code

### Update a specific Lambda:
```powershell
# Example: Update chunk processor
cd C:\Users\cbeach\Desktop\billreview_local\aws_lambdas\us-east-1\jrk-bill-chunk-processor\code
Compress-Archive -Path * -DestinationPath ..\chunk-processor.zip -Force

aws lambda update-function-code `
  --function-name jrk-bill-chunk-processor `
  --zip-file fileb://C:\Users\cbeach\Desktop\billreview_local\aws_lambdas\us-east-1\jrk-bill-chunk-processor\chunk-processor.zip `
  --region us-east-1 `
  --profile jrk-analytics-admin
```

---

## Configuration Reference

### Environment Variables

| Lambda | Variable | Default | Description |
|--------|----------|---------|-------------|
| Router | `MAX_PAGES_STANDARD` | 10 | Page threshold for large file routing |
| Router | `MAX_SIZE_MB_STANDARD` | 10 | Size threshold (MB) for large file routing |
| Large Parser | `PAGES_PER_CHUNK` | 2 | Pages per chunk |
| Chunk Processor | `MODEL_NAME` | gemini-2.0-flash | Gemini model to use |
| Chunk Processor | `MAX_ATTEMPTS` | 10 | Retry attempts with key rotation |
| Chunk Processor | `BASE_BACKOFF_SECONDS` | 2 | Base delay for exponential backoff |

### S3 Prefixes

| Prefix | Purpose |
|--------|---------|
| `Bill_Parser_1_Pending_Parsing/` | Input (existing) |
| `Bill_Parser_1_Standard/` | Small PDFs routed here |
| `Bill_Parser_1_LargeFile/` | Large PDFs routed here |
| `Bill_Parser_1_LargeFile_Chunks/` | Individual chunk PDFs |
| `Bill_Parser_1_LargeFile_Results/` | Parsed chunk JSON |
| `Bill_Parser_3_Parsed_Outputs/` | Final aggregated JSONL |

### DynamoDB Tables

| Table | Key | Purpose |
|-------|-----|---------|
| `jrk-bill-parser-jobs` | `job_id` (String) | Job tracking, chunk progress, context |
| `jrk-bill-parser-errors` | `pk` (String) | Error logging for debugging |

---

## Rollback

### Delete all resources:
```powershell
# Delete Lambdas
aws lambda delete-function --function-name jrk-bill-router --region us-east-1 --profile jrk-analytics-admin
aws lambda delete-function --function-name jrk-bill-large-parser --region us-east-1 --profile jrk-analytics-admin
aws lambda delete-function --function-name jrk-bill-chunk-processor --region us-east-1 --profile jrk-analytics-admin
aws lambda delete-function --function-name jrk-bill-aggregator --region us-east-1 --profile jrk-analytics-admin

# Delete DynamoDB table
aws dynamodb delete-table --table-name jrk-bill-parser-jobs --region us-east-1 --profile jrk-analytics-admin

# Delete IAM role (after detaching policies)
aws iam delete-role --role-name jrk-bill-large-parser-role --profile jrk-analytics-admin

# Remove S3 notifications (restore original config)
```

---

## Local Files Reference

```
aws_lambdas/us-east-1/
├── jrk-bill-router/
│   └── code/
│       └── lambda_bill_router.py      # Router logic
├── jrk-bill-large-parser/
│   └── code/
│       └── lambda_bill_large_parser.py # Chunk splitter
├── jrk-bill-chunk-processor/
│   └── code/
│       └── lambda_chunk_processor.py   # Gemini API parser
└── jrk-bill-aggregator/
    └── code/
        └── lambda_aggregator.py        # Result combiner
```

---

## Known Issues / TODOs

1. **S3 Notification Conflicts**: Need to merge with existing S3 notifications (jrk-bill-parser trigger)
2. **Error Table**: May need to create `jrk-bill-parser-errors` table for error logging
3. **Gemini Model**: Code references `gemini-3-pro-preview` but should use `gemini-2.0-flash` for production
4. **Aggregator Trigger**: Consider using S3 event on last chunk result instead of DynamoDB Streams for more reliable triggering
