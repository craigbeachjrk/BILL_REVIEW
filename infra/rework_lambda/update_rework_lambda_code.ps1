Param(
  [string]$Profile = 'jrk-analytics-admin',
  [string]$Region = 'us-east-1',
  [string]$LambdaName = 'jrk-bill-parser-rework',
  [string]$Bucket = 'jrk-analytics-billing',
  [string]$ReworkPrefix = 'Bill_Parser_Rework_Input/',
  [string]$PendingPrefix = 'Bill_Parser_1_Pending_Parsing/'
)

$ErrorActionPreference = 'Stop'
# Use local source file
$src = "h:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV\rework_handler.py"
if (-not (Test-Path $src)) { throw "Missing handler source: $src" }
$zip = Join-Path $env:TEMP ("rework_update_" + [guid]::NewGuid() + ".zip")
Compress-Archive -Path $src -DestinationPath $zip -Force
aws lambda update-function-code --function-name $LambdaName --zip-file fileb://$zip --region $Region --profile $Profile | Out-Null
# update env including PENDING_PREFIX
$envObj = @{ Variables = @{ BUCKET=$Bucket; REWORK_PREFIX=$ReworkPrefix; PENDING_PREFIX=$PendingPrefix } }
$envJson = $envObj | ConvertTo-Json -Depth 3 -Compress
$envFile = Join-Path $env:TEMP ("rework_env_" + [guid]::NewGuid() + ".json")
$envJson | Out-File -FilePath $envFile -Encoding ascii -NoNewline
aws lambda update-function-configuration --function-name $LambdaName --region $Region --profile $Profile --environment file://$envFile | Out-Null
Write-Host "Updated code and env for $LambdaName"
