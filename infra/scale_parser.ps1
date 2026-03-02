# scale_parser.ps1 - Upgrade Lambda memory/timeout and configure provisioned concurrency
#
# Usage: .\infra\scale_parser.ps1
#
# What this does:
#   1. Bumps Lambda memory (more memory = proportionally more CPU)
#   2. Increases timeout to handle large bills
#   3. Publishes a new Lambda version and creates/updates "live" alias
#   4. Sets up Application Auto Scaling for provisioned concurrency
#      - 5 warm instances M-F 7AM-7PM Pacific (15:00-03:00 UTC)
#      - 0 warm instances outside business hours
#
# AWS Lambda CPU allocation:
#   128MB  = ~0.08 vCPU   (default — very slow for JSON/PDF work)
#   512MB  = ~0.30 vCPU
#   1024MB = ~0.60 vCPU
#   1769MB = 1.00 vCPU
#
# To undo: re-run with lower --memory-size values

$ErrorActionPreference = "Stop"
$PROFILE = "jrk-analytics-admin"
$REGION = "us-east-1"

Write-Host "`n=== Step 1: Update Lambda Memory & Timeout ===" -ForegroundColor Cyan

# Parser: 128MB -> 1024MB (8x CPU)
Write-Host "Updating jrk-bill-parser: memory=1024MB, timeout=300s"
aws lambda update-function-configuration `
    --function-name jrk-bill-parser `
    --memory-size 1024 `
    --timeout 300 `
    --region $REGION --profile $PROFILE

# Chunk processor: bump to 1024MB
Write-Host "Updating jrk-bill-chunk-processor: memory=1024MB, timeout=300s"
aws lambda update-function-configuration `
    --function-name jrk-bill-chunk-processor `
    --memory-size 1024 `
    --timeout 300 `
    --region $REGION --profile $PROFILE

# Enricher: bump to 512MB (lighter workload — mostly string matching)
Write-Host "Updating jrk-bill-enricher: memory=512MB, timeout=120s"
aws lambda update-function-configuration `
    --function-name jrk-bill-enricher `
    --memory-size 512 `
    --timeout 120 `
    --region $REGION --profile $PROFILE

Write-Host "`n=== Step 2: Publish New Versions & Create Aliases ===" -ForegroundColor Cyan

# Wait for function updates to complete
Start-Sleep -Seconds 5

# Parser
Write-Host "Publishing jrk-bill-parser version..."
$parserVersion = (aws lambda publish-version `
    --function-name jrk-bill-parser `
    --region $REGION --profile $PROFILE `
    --query 'Version' --output text)
Write-Host "  Published version: $parserVersion"

# Create or update "live" alias
Write-Host "Creating/updating 'live' alias for jrk-bill-parser -> v$parserVersion"
try {
    aws lambda create-alias `
        --function-name jrk-bill-parser `
        --name live `
        --function-version $parserVersion `
        --region $REGION --profile $PROFILE 2>$null
} catch {
    aws lambda update-alias `
        --function-name jrk-bill-parser `
        --name live `
        --function-version $parserVersion `
        --region $REGION --profile $PROFILE
}

Write-Host "`n=== Step 3: Set Up Provisioned Concurrency Schedule ===" -ForegroundColor Cyan

# Register as scalable target
Write-Host "Registering jrk-bill-parser:live as scalable target..."
aws application-autoscaling register-scalable-target `
    --service-namespace lambda `
    --resource-id "function:jrk-bill-parser:live" `
    --scalable-dimension "lambda:function:ProvisionedConcurrency" `
    --min-capacity 0 `
    --max-capacity 10 `
    --region $REGION --profile $PROFILE

# Morning scale-up: 7 AM Pacific = 15:00 UTC (Mon-Fri)
Write-Host "Scheduling morning scale-up (7 AM Pacific, Mon-Fri)..."
aws application-autoscaling put-scheduled-action `
    --service-namespace lambda `
    --resource-id "function:jrk-bill-parser:live" `
    --scalable-dimension "lambda:function:ProvisionedConcurrency" `
    --scheduled-action-name "parser-morning-scale-up" `
    --schedule "cron(0 15 ? * MON-FRI *)" `
    --scalable-target-action "MinCapacity=5,MaxCapacity=10" `
    --region $REGION --profile $PROFILE

# Evening scale-down: 7 PM Pacific = 03:00 UTC next day (Tue-Sat)
Write-Host "Scheduling evening scale-down (7 PM Pacific)..."
aws application-autoscaling put-scheduled-action `
    --service-namespace lambda `
    --resource-id "function:jrk-bill-parser:live" `
    --scalable-dimension "lambda:function:ProvisionedConcurrency" `
    --scheduled-action-name "parser-evening-scale-down" `
    --schedule "cron(0 3 ? * TUE-SAT *)" `
    --scalable-target-action "MinCapacity=0,MaxCapacity=0" `
    --region $REGION --profile $PROFILE

Write-Host "`n=== Step 4: Verify Configuration ===" -ForegroundColor Cyan

Write-Host "`nParser config:"
aws lambda get-function-configuration `
    --function-name jrk-bill-parser `
    --region $REGION --profile $PROFILE `
    --query '{MemorySize:MemorySize,Timeout:Timeout}' --output table

Write-Host "`nChunk processor config:"
aws lambda get-function-configuration `
    --function-name jrk-bill-chunk-processor `
    --region $REGION --profile $PROFILE `
    --query '{MemorySize:MemorySize,Timeout:Timeout}' --output table

Write-Host "`nEnricher config:"
aws lambda get-function-configuration `
    --function-name jrk-bill-enricher `
    --region $REGION --profile $PROFILE `
    --query '{MemorySize:MemorySize,Timeout:Timeout}' --output table

Write-Host "`nScheduled actions:"
aws application-autoscaling describe-scheduled-actions `
    --service-namespace lambda `
    --resource-id "function:jrk-bill-parser:live" `
    --region $REGION --profile $PROFILE `
    --query 'ScheduledActions[].{Name:ScheduledActionName,Schedule:Schedule}' --output table

Write-Host "`n=== Done ===" -ForegroundColor Green
Write-Host @"

IMPORTANT NEXT STEPS:
1. Update S3 event notification to point to the 'live' alias ARN
   (instead of $LATEST) for the parser Lambda
2. Check Gemini API key count:
   aws secretsmanager get-secret-value --secret-id gemini/parser-keys --profile $PROFILE --query SecretString --output text
   If fewer than 10 keys, add more at https://aistudio.google.com/apikey
3. Monitor CloudWatch Logs for timing metrics (search for '_metric')
4. Check /metrics -> PARSER SPEED tab for throughput charts
"@
