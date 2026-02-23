$PROFILE = "jrk-analytics-admin"
$REGION = "us-east-1"
$SRC = $PSScriptRoot
$ZIP = "$SRC\bill_review_app_src.zip"
$TEMP_DIR = "$SRC\temp_deploy"

Write-Host "Creating LEAN zip archive (~1 MB, runtime essentials only)..."
if (Test-Path $ZIP) { Remove-Item $ZIP -Force }
if (Test-Path $TEMP_DIR) { Remove-Item $TEMP_DIR -Recurse -Force }

# Create temp directory and copy only necessary files
New-Item -ItemType Directory -Path $TEMP_DIR | Out-Null

# ONLY include these specific directories (runtime essentials + tests for CI/CD)
$includeDirs = @('data', 'templates', 'static', 'tests')
foreach ($dir in $includeDirs) {
    $dirPath = Join-Path $SRC $dir
    if (Test-Path $dirPath) {
        Copy-Item -Path $dirPath -Destination $TEMP_DIR -Recurse -Force
    }
}

# ONLY include these specific files (runtime essentials)
$includeFiles = @('app.py', 'auth.py', 'main.py', 'utils.py', 'entrata_send_invoices_prototype.py', 'requirements.txt', 'Dockerfile', 'buildspec.yml', 'apprunner_config.json', 'VERSION.txt', 'CLAUDE.md', 'integration_uuid_provider_map.csv', 'account_uuid_provider_map.csv')
foreach ($file in $includeFiles) {
    $filePath = Join-Path $SRC $file
    if (Test-Path $filePath) {
        Copy-Item -Path $filePath -Destination $TEMP_DIR -Force
    }
}

# Show what's being included
Write-Host "Files included in deployment:"
Get-ChildItem $TEMP_DIR -Recurse -File | Measure-Object -Property Length -Sum | ForEach-Object {
    Write-Host "  Total: $($_.Count) files, $([math]::Round($_.Sum / 1MB, 2)) MB"
}

Compress-Archive -Path "$TEMP_DIR\*" -DestinationPath $ZIP -Force
Remove-Item $TEMP_DIR -Recurse -Force

Write-Host "Uploading to S3..."
aws s3 cp $ZIP s3://jrk-analytics-billing/tmp/jrk-bill-review/source.zip --region $REGION --profile $PROFILE

Write-Host "Starting CodeBuild..."
$BUILD_ID = aws codebuild start-build --project-name jrk-bill-review-build --region $REGION --profile $PROFILE --query 'build.id' --output text

Write-Host ""
Write-Host "Build ID: $BUILD_ID"
Write-Host "Waiting for CodeBuild to complete..."

# Wait for CodeBuild to complete (check every 30 seconds)
$maxWait = 600  # 10 minutes max
$waited = 0
while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 30
    $waited += 30
    $status = aws codebuild batch-get-builds --ids $BUILD_ID --region $REGION --profile $PROFILE --query 'builds[0].buildStatus' --output text
    Write-Host "  Build status: $status (waited ${waited}s)"
    if ($status -eq "SUCCEEDED") {
        Write-Host "CodeBuild completed successfully!"
        break
    }
    if ($status -eq "FAILED" -or $status -eq "STOPPED") {
        Write-Host "CodeBuild failed with status: $status"
        exit 1
    }
}

# Trigger AppRunner deployment
Write-Host "Triggering AppRunner deployment..."
$APP_RUNNER_ARN = "arn:aws:apprunner:us-east-1:789814232318:service/jrk-bill-review/a061a5295eb341c19bb159d97500eabd"
$DEPLOY_RESULT = aws apprunner start-deployment --service-arn $APP_RUNNER_ARN --region $REGION --profile $PROFILE --query 'OperationId' --output text
Write-Host "AppRunner deployment started: $DEPLOY_RESULT"
Write-Host ""
Write-Host "Deployment complete! AppRunner will take 2-3 minutes to roll out the new version."
