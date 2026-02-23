# Bill Parser Large File Workflow - Deployment Guide

## Overview

This deployment adds a **routing system** to handle large PDFs (>5 pages) with a separate processing workflow.

### New Components

1. **jrk-bill-router** - Routes PDFs based on size/complexity
2. **jrk-bill-large-parser** - Handles large files with page-by-page processing
3. **DynamoDB Tables** - Error tracking and routing logs
4. **Bug Fixes** - Fixed existing parser bugs (re scope, last_reply, duplicate code)

---

## Architecture

```
PDF Upload → S3 Bill_Parser_1_Pending_Parsing/
    ↓
jrk-bill-router Lambda
    ├─ Counts PDF pages
    ├─ Checks file size
    └─ Routes based on thresholds:
        ├─ SMALL (<= 5 pages, < 10MB) → Bill_Parser_1_Standard/ → jrk-bill-parser
        └─ LARGE (> 5 pages or >= 10MB) → Bill_Parser_1_LargeFile/ → jrk-bill-large-parser
            ├─ Splits into page chunks (2 pages per chunk)
            ├─ Uses Gemini 1.5 Flash for speed
            └─ Combines results → Same JSONL format → Stage 3
```

---

## Pre-Deployment Steps

### 1. Create DynamoDB Tables

```powershell
cd H:/Business_Intelligence/1. COMPLETED_PROJECTS/WINDSURF_DEV/bill_review_app/infra

# Create error tracking table
.\create_parser_errors_table.ps1 -Profile jrk-analytics-admin

# Create router log table
aws dynamodb create-table `
    --table-name jrk-bill-router-log `
    --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=timestamp,AttributeType=S `
    --key-schema AttributeName=pk,KeyType=HASH AttributeName=timestamp,KeyType=RANGE `
    --billing-mode PAY_PER_REQUEST `
    --region us-east-1 `
    --profile jrk-analytics-admin
```

### 2. Update Existing Parser (Bug Fixes)

The existing `jrk-bill-parser` has been fixed for:
- ✅ `re.sub` scoping issue (NameError)
- ✅ `last_reply` undefined variable (UnboundLocalError)
- ✅ Duplicate Bill From code block removed
- ✅ Error tracking added to DynamoDB

**Update the Lambda:**

```powershell
cd H:/Business_Intelligence/1. COMPLETED_PROJECTS/WINDSURF_DEV/bill_review_app/aws_lambdas/us-east-1/jrk-bill-parser/code

# Create deployment package
Compress-Archive -Path *.py -DestinationPath lambda_bill_parser.zip -Force

# Update Lambda code
aws lambda update-function-code `
    --function-name jrk-bill-parser `
    --zip-file fileb://lambda_bill_parser.zip `
    --region us-east-1 `
    --profile jrk-analytics-admin

# Update environment variable for error tracking
aws lambda update-function-configuration `
    --function-name jrk-bill-parser `
    --environment "Variables={BUCKET=jrk-analytics-billing,PENDING_PREFIX=Bill_Parser_1_Pending_Parsing/,PARSED_INPUTS_PREFIX=Bill_Parser_2_Parsed_Inputs/,PARSED_OUTPUTS_PREFIX=Bill_Parser_3_Parsed_Outputs/,FAILED_PREFIX=Bill_Parser_Failed_Jobs/,PARSER_SECRET_NAME=gemini/parser-keys,MATCHER_SECRET_NAME=gemini/matcher-keys,MODEL_NAME=gemini-2.5-pro,ERRORS_TABLE=jrk-bill-parser-errors}" `
    --region us-east-1 `
    --profile jrk-analytics-admin
```

### 3. Create IAM Roles

**Router Role:**

```powershell
aws iam create-role `
    --role-name jrk-bill-router-role `
    --assume-role-policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }' `
    --profile jrk-analytics-admin

# Attach policies
aws iam attach-role-policy `
    --role-name jrk-bill-router-role `
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole `
    --profile jrk-analytics-admin

# Create inline policy for S3 and DynamoDB
aws iam put-role-policy `
    --role-name jrk-bill-router-role `
    --policy-name jrk-bill-router-s3-ddb `
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:CopyObject", "s3:HeadObject"],
                "Resource": "arn:aws:s3:::jrk-analytics-billing/*"
            },
            {
                "Effect": "Allow",
                "Action": ["dynamodb:PutItem"],
                "Resource": "arn:aws:dynamodb:us-east-1:789814232318:table/jrk-bill-router-log"
            }
        ]
    }' `
    --profile jrk-analytics-admin
```

**Large Parser Role:**

```powershell
aws iam create-role `
    --role-name jrk-bill-large-parser-role `
    --assume-role-policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }' `
    --profile jrk-analytics-admin

aws iam attach-role-policy `
    --role-name jrk-bill-large-parser-role `
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole `
    --profile jrk-analytics-admin

aws iam put-role-policy `
    --role-name jrk-bill-large-parser-role `
    --policy-name jrk-bill-large-parser-access `
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:CopyObject"],
                "Resource": "arn:aws:s3:::jrk-analytics-billing/*"
            },
            {
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": "arn:aws:secretsmanager:us-east-1:789814232318:secret:gemini/*"
            },
            {
                "Effect": "Allow",
                "Action": ["dynamodb:PutItem"],
                "Resource": "arn:aws:dynamodb:us-east-1:789814232318:table/jrk-bill-parser-errors"
            }
        ]
    }' `
    --profile jrk-analytics-admin
```

### 4. Deploy Lambda Functions

**Router Lambda:**

```powershell
cd H:/Business_Intelligence/1. COMPLETED_PROJECTS/WINDSURF_DEV/bill_review_app/aws_lambdas/us-east-1/jrk-bill-router/code

# Install dependencies
pip install -r requirements.txt -t .

# Create deployment package
Compress-Archive -Path * -DestinationPath ../jrk-bill-router.zip -Force

# Create Lambda
aws lambda create-function `
    --function-name jrk-bill-router `
    --runtime python3.12 `
    --role arn:aws:iam::789814232318:role/jrk-bill-router-role `
    --handler lambda_bill_router.lambda_handler `
    --zip-file fileb://../jrk-bill-router.zip `
    --timeout 60 `
    --memory-size 512 `
    --environment "Variables={BUCKET=jrk-analytics-billing,PENDING_PREFIX=Bill_Parser_1_Pending_Parsing/,STANDARD_PREFIX=Bill_Parser_1_Standard/,LARGEFILE_PREFIX=Bill_Parser_1_LargeFile/,ROUTER_TABLE=jrk-bill-router-log,MAX_PAGES_STANDARD=5,MAX_SIZE_MB_STANDARD=10}" `
    --region us-east-1 `
    --profile jrk-analytics-admin
```

**Large Parser Lambda:**

```powershell
cd H:/Business_Intelligence/1. COMPLETED_PROJECTS/WINDSURF_DEV/bill_review_app/aws_lambdas/us-east-1/jrk-bill-large-parser/code

# Install dependencies
pip install -r requirements.txt -t .

# Create deployment package
Compress-Archive -Path * -DestinationPath ../jrk-bill-large-parser.zip -Force

# Create Lambda
aws lambda create-function `
    --function-name jrk-bill-large-parser `
    --runtime python3.12 `
    --role arn:aws:iam::789814232318:role/jrk-bill-large-parser-role `
    --handler lambda_bill_large_parser.lambda_handler `
    --zip-file fileb://../jrk-bill-large-parser.zip `
    --timeout 300 `
    --memory-size 1024 `
    --environment "Variables={BUCKET=jrk-analytics-billing,LARGEFILE_PREFIX=Bill_Parser_1_LargeFile/,PARSED_INPUTS_PREFIX=Bill_Parser_2_Parsed_Inputs/,PARSED_OUTPUTS_PREFIX=Bill_Parser_3_Parsed_Outputs/,FAILED_PREFIX=Bill_Parser_Failed_Jobs/,PARSER_SECRET_NAME=gemini/parser-keys,ERRORS_TABLE=jrk-bill-parser-errors,LARGE_FILE_MODEL=gemini-1.5-flash,PAGES_PER_CHUNK=2,MAX_ATTEMPTS_PER_CHUNK=3}" `
    --region us-east-1 `
    --profile jrk-analytics-admin
```

### 5. Update S3 Event Configuration

**CRITICAL:** This changes the routing flow. Test thoroughly first!

```powershell
# Step 1: Remove existing trigger from jrk-bill-parser
# (Get notification configuration first to preserve other triggers)
aws s3api get-bucket-notification-configuration `
    --bucket jrk-analytics-billing `
    --profile jrk-analytics-admin > current_notifications.json

# Edit current_notifications.json to remove the Bill_Parser_1_Pending_Parsing trigger for jrk-bill-parser

# Step 2: Add new triggers
aws s3api put-bucket-notification-configuration `
    --bucket jrk-analytics-billing `
    --notification-configuration '{
        "LambdaFunctionConfigurations": [
            {
                "Id": "BillRouterTrigger",
                "LambdaFunctionArn": "arn:aws:lambda:us-east-1:789814232318:function:jrk-bill-router",
                "Events": ["s3:ObjectCreated:*"],
                "Filter": {
                    "Key": {
                        "FilterRules": [
                            {"Name": "Prefix", "Value": "Bill_Parser_1_Pending_Parsing/"},
                            {"Name": "Suffix", "Value": ".pdf"}
                        ]
                    }
                }
            },
            {
                "Id": "BillParserStandardTrigger",
                "LambdaFunctionArn": "arn:aws:lambda:us-east-1:789814232318:function:jrk-bill-parser",
                "Events": ["s3:ObjectCreated:*"],
                "Filter": {
                    "Key": {
                        "FilterRules": [
                            {"Name": "Prefix", "Value": "Bill_Parser_1_Standard/"},
                            {"Name": "Suffix", "Value": ".pdf"}
                        ]
                    }
                }
            },
            {
                "Id": "BillParserLargeFileTrigger",
                "LambdaFunctionArn": "arn:aws:lambda:us-east-1:789814232318:function:jrk-bill-large-parser",
                "Events": ["s3:ObjectCreated:*"],
                "Filter": {
                    "Key": {
                        "FilterRules": [
                            {"Name": "Prefix", "Value": "Bill_Parser_1_LargeFile/"},
                            {"Name": "Suffix", "Value": ".pdf"}
                        ]
                    }
                }
            }
        ]
    }' `
    --profile jrk-analytics-admin

# Step 3: Grant S3 permission to invoke Lambdas
aws lambda add-permission `
    --function-name jrk-bill-router `
    --statement-id s3-trigger-router `
    --action lambda:InvokeFunction `
    --principal s3.amazonaws.com `
    --source-arn arn:aws:s3:::jrk-analytics-billing `
    --profile jrk-analytics-admin

aws lambda add-permission `
    --function-name jrk-bill-large-parser `
    --statement-id s3-trigger-large `
    --action lambda:InvokeFunction `
    --principal s3.amazonaws.com `
    --source-arn arn:aws:s3:::jrk-analytics-billing `
    --profile jrk-analytics-admin

# Update existing parser permission to use new prefix
aws lambda remove-permission `
    --function-name jrk-bill-parser `
    --statement-id s3-trigger `
    --profile jrk-analytics-admin

aws lambda add-permission `
    --function-name jrk-bill-parser `
    --statement-id s3-trigger-standard `
    --action lambda:InvokeFunction `
    --principal s3.amazonaws.com `
    --source-arn arn:aws:s3:::jrk-analytics-billing `
    --profile jrk-analytics-admin
```

---

## Testing

### Test Router

```powershell
# Upload a small test PDF (should route to Standard)
aws s3 cp test_small.pdf s3://jrk-analytics-billing/Bill_Parser_1_Pending_Parsing/test_small.pdf --profile jrk-analytics-admin

# Upload a large test PDF (should route to LargeFile)
aws s3 cp test_large.pdf s3://jrk-analytics-billing/Bill_Parser_1_Pending_Parsing/test_large.pdf --profile jrk-analytics-admin

# Check router logs
aws logs tail /aws/lambda/jrk-bill-router --profile jrk-analytics-admin --region us-east-1 --since 5m
```

### Check Error Tracking

```powershell
# Query error table
aws dynamodb scan --table-name jrk-bill-parser-errors --profile jrk-analytics-admin --max-items 10
```

---

## Monitoring

1. **Router Logs**: Check `/aws/lambda/jrk-bill-router` for routing decisions
2. **Parser Errors**: Query `jrk-bill-parser-errors` DynamoDB table
3. **Router Stats**: Query `jrk-bill-router-log` DynamoDB table
4. **S3 Prefixes**: Monitor file counts in Standard vs LargeFile prefixes

---

## Rollback Plan

If issues occur, quickly rollback S3 event configuration to direct all PDFs to original parser:

```powershell
# Remove router trigger, restore original jrk-bill-parser trigger
aws s3api put-bucket-notification-configuration `
    --bucket jrk-analytics-billing `
    --notification-configuration file://original_notifications_backup.json `
    --profile jrk-analytics-admin
```

---

## Configuration Tuning

Adjust thresholds in router Lambda environment variables:
- `MAX_PAGES_STANDARD` (default: 5)
- `MAX_SIZE_MB_STANDARD` (default: 10)

Adjust large parser chunking:
- `PAGES_PER_CHUNK` (default: 2)
- `MAX_ATTEMPTS_PER_CHUNK` (default: 3)
- `LARGE_FILE_MODEL` (default: gemini-1.5-flash)

---

## Summary of Changes

### Fixed Bugs
1. ✅ `lambda_bill_parser.py:381` - Fixed re.sub scoping issue
2. ✅ `lambda_bill_parser.py:544` - Initialized `last_reply` variable
3. ✅ `lambda_bill_parser.py:507-522` - Removed duplicate Bill From code

### New Components
1. ✅ `jrk-bill-router` Lambda - Routes PDFs by size
2. ✅ `jrk-bill-large-parser` Lambda - Handles large files
3. ✅ `jrk-bill-parser-errors` DynamoDB - Error tracking
4. ✅ `jrk-bill-router-log` DynamoDB - Routing decisions
5. ✅ `error_tracker.py` - Shared error logging module

### Architecture Changes
- PDFs now routed through router before parsing
- Large files use chunked processing with Gemini Flash
- All errors logged to DynamoDB with detailed codes
- Same JSONL output format maintained
