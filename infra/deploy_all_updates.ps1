param(
    [string]$Profile = 'jrk-analytics-admin',
    [string]$Region = 'us-east-1',
    [switch]$SkipTables,
    [switch]$UpdateExistingParser,
    [switch]$DeployRouter,
    [switch]$DeployLargeParser,
    [switch]$UpdateS3Events
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Bill Parser Large File Workflow Deployment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$accountId = "789814232318"  # Replace if different

# Create DynamoDB tables
if (-not $SkipTables) {
    Write-Host "[1/5] Creating DynamoDB tables..." -ForegroundColor Yellow

    # Errors table
    try {
        aws dynamodb create-table `
            --table-name jrk-bill-parser-errors `
            --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=timestamp,AttributeType=S `
            --key-schema AttributeName=pk,KeyType=HASH AttributeName=timestamp,KeyType=RANGE `
            --billing-mode PAY_PER_REQUEST `
            --region $Region `
            --profile $Profile | Out-Null
        Write-Host "   ✓ Created jrk-bill-parser-errors table" -ForegroundColor Green
    } catch {
        Write-Host "   ⚠ Table jrk-bill-parser-errors may already exist" -ForegroundColor Yellow
    }

    # Router log table
    try {
        aws dynamodb create-table `
            --table-name jrk-bill-router-log `
            --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=timestamp,AttributeType=S `
            --key-schema AttributeName=pk,KeyType=HASH AttributeName=timestamp,KeyType=RANGE `
            --billing-mode PAY_PER_REQUEST `
            --region $Region `
            --profile $Profile | Out-Null
        Write-Host "   ✓ Created jrk-bill-router-log table" -ForegroundColor Green
    } catch {
        Write-Host "   ⚠ Table jrk-bill-router-log may already exist" -ForegroundColor Yellow
    }
}

# Update existing parser
if ($UpdateExistingParser) {
    Write-Host "[2/5] Updating existing jrk-bill-parser (bug fixes + error tracking)..." -ForegroundColor Yellow

    $parserPath = Join-Path $PSScriptRoot "../aws_lambdas/us-east-1/jrk-bill-parser/code"
    Push-Location $parserPath

    # Create deployment package
    if (Test-Path lambda_bill_parser.zip) { Remove-Item lambda_bill_parser.zip -Force }
    Compress-Archive -Path *.py -DestinationPath lambda_bill_parser.zip -Force

    # Update code
    aws lambda update-function-code `
        --function-name jrk-bill-parser `
        --zip-file fileb://lambda_bill_parser.zip `
        --region $Region `
        --profile $Profile | Out-Null

    # Update environment
    aws lambda update-function-configuration `
        --function-name jrk-bill-parser `
        --environment "Variables={BUCKET=jrk-analytics-billing,PENDING_PREFIX=Bill_Parser_1_Pending_Parsing/,PARSED_INPUTS_PREFIX=Bill_Parser_2_Parsed_Inputs/,PARSED_OUTPUTS_PREFIX=Bill_Parser_3_Parsed_Outputs/,FAILED_PREFIX=Bill_Parser_Failed_Jobs/,PARSER_SECRET_NAME=gemini/parser-keys,MATCHER_SECRET_NAME=gemini/matcher-keys,MODEL_NAME=gemini-2.5-pro,ERRORS_TABLE=jrk-bill-parser-errors}" `
        --region $Region `
        --profile $Profile | Out-Null

    Pop-Location
    Write-Host "   ✓ Updated jrk-bill-parser" -ForegroundColor Green
}

# Deploy router
if ($DeployRouter) {
    Write-Host "[3/5] Deploying jrk-bill-router..." -ForegroundColor Yellow

    # Create role if doesn't exist
    $roleExists = aws iam get-role --role-name jrk-bill-router-role --profile $Profile 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   Creating IAM role..." -ForegroundColor Gray
        aws iam create-role `
            --role-name jrk-bill-router-role `
            --assume-role-policy-document '{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"lambda.amazonaws.com\"},\"Action\":\"sts:AssumeRole\"}]}' `
            --profile $Profile | Out-Null

        aws iam attach-role-policy `
            --role-name jrk-bill-router-role `
            --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole `
            --profile $Profile | Out-Null
    }

    # Deploy function
    $routerPath = Join-Path $PSScriptRoot "../aws_lambdas/us-east-1/jrk-bill-router/code"
    Push-Location $routerPath

    # Install deps and package
    pip install -r requirements.txt -t . --quiet
    if (Test-Path ../jrk-bill-router.zip) { Remove-Item ../jrk-bill-router.zip -Force }
    Compress-Archive -Path * -DestinationPath ../jrk-bill-router.zip -Force

    # Create or update Lambda
    $funcExists = aws lambda get-function --function-name jrk-bill-router --region $Region --profile $Profile 2>&1
    if ($LASTEXITCODE -eq 0) {
        aws lambda update-function-code `
            --function-name jrk-bill-router `
            --zip-file fileb://../jrk-bill-router.zip `
            --region $Region `
            --profile $Profile | Out-Null
        Write-Host "   ✓ Updated jrk-bill-router" -ForegroundColor Green
    } else {
        aws lambda create-function `
            --function-name jrk-bill-router `
            --runtime python3.12 `
            --role arn:aws:iam::${accountId}:role/jrk-bill-router-role `
            --handler lambda_bill_router.lambda_handler `
            --zip-file fileb://../jrk-bill-router.zip `
            --timeout 60 `
            --memory-size 512 `
            --environment "Variables={BUCKET=jrk-analytics-billing,PENDING_PREFIX=Bill_Parser_1_Pending_Parsing/,STANDARD_PREFIX=Bill_Parser_1_Standard/,LARGEFILE_PREFIX=Bill_Parser_1_LargeFile/,ROUTER_TABLE=jrk-bill-router-log,MAX_PAGES_STANDARD=5,MAX_SIZE_MB_STANDARD=10}" `
            --region $Region `
            --profile $Profile | Out-Null
        Write-Host "   ✓ Created jrk-bill-router" -ForegroundColor Green
    }

    Pop-Location
}

# Deploy large parser
if ($DeployLargeParser) {
    Write-Host "[4/5] Deploying jrk-bill-large-parser..." -ForegroundColor Yellow

    $parserPath = Join-Path $PSScriptRoot "../aws_lambdas/us-east-1/jrk-bill-large-parser/code"
    Push-Location $parserPath

    pip install -r requirements.txt -t . --quiet
    if (Test-Path ../jrk-bill-large-parser.zip) { Remove-Item ../jrk-bill-large-parser.zip -Force }
    Compress-Archive -Path * -DestinationPath ../jrk-bill-large-parser.zip -Force

    $funcExists = aws lambda get-function --function-name jrk-bill-large-parser --region $Region --profile $Profile 2>&1
    if ($LASTEXITCODE -eq 0) {
        aws lambda update-function-code `
            --function-name jrk-bill-large-parser `
            --zip-file fileb://../jrk-bill-large-parser.zip `
            --region $Region `
            --profile $Profile | Out-Null
        Write-Host "   ✓ Updated jrk-bill-large-parser" -ForegroundColor Green
    } else {
        # Create role first if needed
        $roleExists = aws iam get-role --role-name jrk-bill-large-parser-role --profile $Profile 2>&1
        if ($LASTEXITCODE -ne 0) {
            aws iam create-role `
                --role-name jrk-bill-large-parser-role `
                --assume-role-policy-document '{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"lambda.amazonaws.com\"},\"Action\":\"sts:AssumeRole\"}]}' `
                --profile $Profile | Out-Null

            aws iam attach-role-policy `
                --role-name jrk-bill-large-parser-role `
                --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole `
                --profile $Profile | Out-Null
        }

        aws lambda create-function `
            --function-name jrk-bill-large-parser `
            --runtime python3.12 `
            --role arn:aws:iam::${accountId}:role/jrk-bill-large-parser-role `
            --handler lambda_bill_large_parser.lambda_handler `
            --zip-file fileb://../jrk-bill-large-parser.zip `
            --timeout 300 `
            --memory-size 1024 `
            --environment "Variables={BUCKET=jrk-analytics-billing,LARGEFILE_PREFIX=Bill_Parser_1_LargeFile/,PARSED_INPUTS_PREFIX=Bill_Parser_2_Parsed_Inputs/,PARSED_OUTPUTS_PREFIX=Bill_Parser_3_Parsed_Outputs/,FAILED_PREFIX=Bill_Parser_Failed_Jobs/,PARSER_SECRET_NAME=gemini/parser-keys,ERRORS_TABLE=jrk-bill-parser-errors,LARGE_FILE_MODEL=gemini-1.5-flash,PAGES_PER_CHUNK=2}" `
            --region $Region `
            --profile $Profile | Out-Null
        Write-Host "   ✓ Created jrk-bill-large-parser" -ForegroundColor Green
    }

    Pop-Location
}

# Update S3 events
if ($UpdateS3Events) {
    Write-Host "[5/5] Updating S3 event configuration..." -ForegroundColor Yellow
    Write-Host "   ⚠ MANUAL STEP REQUIRED:" -ForegroundColor Red
    Write-Host "   Please review DEPLOYMENT_GUIDE.md section 'Update S3 Event Configuration'" -ForegroundColor Yellow
    Write-Host "   This step changes routing flow - TEST THOROUGHLY!" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Review DEPLOYMENT_GUIDE.md for S3 event configuration" -ForegroundColor White
Write-Host "2. Test with sample PDFs (small and large)" -ForegroundColor White
Write-Host "3. Monitor CloudWatch logs for errors" -ForegroundColor White
Write-Host "4. Query DynamoDB tables for error tracking" -ForegroundColor White
