#!/usr/bin/env pwsh
# Create Snowflake credentials in AWS Secrets Manager

$AWSPROFILE = 'jrk-analytics-admin'
$REGION = 'us-east-1'
$SECRET_NAME = 'jrk-bill-review/snowflake'

Write-Host "Creating Snowflake credentials secret in AWS Secrets Manager..."
Write-Host ""
Write-Host "Please enter your Snowflake connection details:"
Write-Host ""

# Prompt for credentials
$account = Read-Host "Snowflake Account (e.g., xy12345.us-east-1 or xy12345)"
$user = Read-Host "Snowflake User"
$password = Read-Host "Snowflake Password" -AsSecureString
$database = Read-Host "Database Name (e.g., ANALYTICS)"
$schema = Read-Host "Schema Name (e.g., UBI or PUBLIC)"
$warehouse = Read-Host "Warehouse Name (e.g., COMPUTE_WH)"
$role = Read-Host "Role (optional, press Enter to skip)"

# Convert secure string to plain text for JSON
$BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
$plainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)

# Create JSON secret
$secretJson = @{
    account = $account
    user = $user
    password = $plainPassword
    database = $database
    schema = $schema
    warehouse = $warehouse
    role = if ($role) { $role } else { $null }
} | ConvertTo-Json

Write-Host ""
Write-Host "Creating secret '$SECRET_NAME'..."

# Try to create the secret (will fail if it exists)
try {
    aws secretsmanager create-secret `
        --name $SECRET_NAME `
        --description "Snowflake credentials for UBI billback export" `
        --secret-string $secretJson `
        --region $REGION `
        --profile $AWSPROFILE 2>&1 | Out-Null

    Write-Host "✓ Secret created successfully!" -ForegroundColor Green
} catch {
    # If secret exists, update it instead
    Write-Host "Secret already exists, updating..." -ForegroundColor Yellow
    aws secretsmanager update-secret `
        --secret-id $SECRET_NAME `
        --secret-string $secretJson `
        --region $REGION `
        --profile $AWSPROFILE

    Write-Host "✓ Secret updated successfully!" -ForegroundColor Green
}

Write-Host ""
Write-Host "Secret ARN:"
aws secretsmanager describe-secret `
    --secret-id $SECRET_NAME `
    --region $REGION `
    --profile $AWSPROFILE `
    --query 'ARN' `
    --output text

Write-Host ""
Write-Host "Next steps:"
Write-Host "1. Run the Snowflake SQL to create the table (see previous message)"
Write-Host "2. Update IAM permissions to allow App Runner to read this secret"
Write-Host "3. Deploy the updated application"
