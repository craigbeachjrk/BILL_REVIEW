# sync_posted_invoices.ps1
# Syncs posted invoice PDFs from S3 to a local/network share
#
# SETUP:
# 1. Edit $LOCAL_PATH below to your file server share (e.g., \\fileserver\share\Posted_Invoices)
# 2. Run this script manually to test, or set up as Windows Scheduled Task
# 3. Recommended: Run every 5-15 minutes during business hours
#
# REQUIREMENTS:
# - AWS CLI installed and configured
# - aws sso login --profile jrk-analytics-admin (if using SSO)

$PROFILE = "jrk-analytics-admin"
$REGION = "us-east-1"
$S3_PATH = "s3://jrk-analytics-billing/Posted_Invoices/"

# CONFIGURE THIS: Set to your file server UNC path
$LOCAL_PATH = "\\YOUR-FILE-SERVER\share\Posted_Invoices\"
# Or use a local path for testing:
# $LOCAL_PATH = "C:\Posted_Invoices\"

# Ensure local path exists
if (-not (Test-Path $LOCAL_PATH)) {
    Write-Host "Creating directory: $LOCAL_PATH"
    New-Item -ItemType Directory -Path $LOCAL_PATH -Force | Out-Null
}

Write-Host "$(Get-Date): Starting sync from $S3_PATH to $LOCAL_PATH"

# Sync from S3 to local/network share
# --delete flag is NOT used - files are only added, never removed
aws s3 sync $S3_PATH $LOCAL_PATH --region $REGION --profile $PROFILE

if ($LASTEXITCODE -eq 0) {
    Write-Host "$(Get-Date): Sync completed successfully"
} else {
    Write-Host "$(Get-Date): Sync failed with exit code $LASTEXITCODE"
}

# Optional: Log to file
# $LOG_FILE = "C:\Logs\pdf_sync.log"
# Add-Content -Path $LOG_FILE -Value "$(Get-Date): Sync completed (exit code: $LASTEXITCODE)"
