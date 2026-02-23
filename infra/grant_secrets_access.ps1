#!/usr/bin/env pwsh
# Grant App Runner access to Snowflake secrets in Secrets Manager

$AWSPROFILE = 'jrk-analytics-admin'
$REGION = 'us-east-1'
$ROLE_NAME = 'jrk-bill-review-role'
$SECRET_ARN = "arn:aws:secretsmanager:us-east-1:789814232318:secret:jrk-bill-review/snowflake-*"

Write-Host "Granting App Runner role access to Snowflake secrets..."

# Create policy document
$policyDocument = @{
    Version = "2012-10-17"
    Statement = @(
        @{
            Effect = "Allow"
            Action = @(
                "secretsmanager:GetSecretValue",
                "secretsmanager:DescribeSecret"
            )
            Resource = @($SECRET_ARN)
        }
    )
} | ConvertTo-Json -Depth 10

# Save to temp file
$tempFile = [System.IO.Path]::GetTempFileName()
$policyDocument | Out-File -FilePath $tempFile -Encoding utf8

Write-Host "Policy document:"
Write-Host $policyDocument
Write-Host ""

# Create or update inline policy
try {
    aws iam put-role-policy `
        --role-name $ROLE_NAME `
        --policy-name "SnowflakeSecretsAccess" `
        --policy-document "file://$tempFile" `
        --region $REGION `
        --profile $AWSPROFILE

    Write-Host "✓ IAM policy attached successfully!" -ForegroundColor Green
    Write-Host "App Runner can now read Snowflake credentials from Secrets Manager"
} catch {
    Write-Host "✗ Failed to attach policy" -ForegroundColor Red
    Write-Host $_.Exception.Message
} finally {
    Remove-Item -Path $tempFile -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Verifying policy..."
aws iam get-role-policy `
    --role-name $ROLE_NAME `
    --policy-name "SnowflakeSecretsAccess" `
    --region $REGION `
    --profile $AWSPROFILE `
    --query 'PolicyDocument' `
    --output json
