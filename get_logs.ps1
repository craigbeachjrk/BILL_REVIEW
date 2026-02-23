$AWSPROFILE='jrk-analytics-admin'
$REGION='us-east-1'
$LOGGROUP='/aws/apprunner/jrk-bill-review/a061a5295eb341c19bb159d97500eabd/application'

# Get timestamp from 15 minutes ago (in milliseconds)
$startTime = [int64][double]::Parse(((Get-Date).AddMinutes(-15).ToUniversalTime() - [datetime]'1970-01-01').TotalMilliseconds)

Write-Host "Fetching logs since last 15 minutes..."
aws logs filter-log-events `
    --log-group-name $LOGGROUP `
    --start-time $startTime `
    --filter-pattern "MASTER BILLS" `
    --region $REGION `
    --profile $AWSPROFILE `
    --query 'events[].message' `
    --output text
