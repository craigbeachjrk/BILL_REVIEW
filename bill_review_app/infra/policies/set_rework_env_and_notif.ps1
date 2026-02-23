Param(
  [string]$Profile = 'jrk-analytics-admin',
  [string]$Region = 'us-east-1',
  [string]$Bucket = 'jrk-analytics-billing',
  [string]$LambdaName = 'jrk-bill-parser-rework'
)

$ErrorActionPreference = 'Stop'
function Write-JsonAscii([object]$obj, [string]$path) {
  $json = $obj | ConvertTo-Json -Depth 20 -Compress
  $json | Out-File -FilePath $path -Encoding ascii -NoNewline
}

# Get Lambda ARN (wait if needed)
for ($i=0; $i -lt 30; $i++) {
  try {
    $cfg = aws lambda get-function --function-name $LambdaName --region $Region --profile $Profile | ConvertFrom-Json
    if ($cfg.Configuration.State -eq 'Active') { break }
  } catch {}
  Start-Sleep -Seconds 4
}
$fnArn = aws lambda get-function --function-name $LambdaName --region $Region --profile $Profile --query 'Configuration.FunctionArn' --output text
if (-not $fnArn) { throw 'Lambda not found' }

# Set env via file
$envObj = @{ Variables = @{ BUCKET='jrk-analytics-billing'; REWORK_PREFIX='Bill_Parser_Rework_Input/'; ENRICH_PREFIX='Bill_Parser_4_Enriched_Outputs/'; POST_PARSED_PREFIX='Bill_Parser_2_Post_Parsed_Outputs/'; PRE_ENTRATA_PREFIX='Bill_Parser_6_PreEntrata_Submission/' } }
$Temp = Join-Path $env:TEMP ("rework_env_" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Temp | Out-Null
$envPath = Join-Path $Temp 'env.json'
Write-JsonAscii $envObj $envPath
aws lambda update-function-configuration --function-name $LambdaName --region $Region --profile $Profile --environment file://$envPath | Out-Null

# Allow S3 invoke
$account = aws sts get-caller-identity --query 'Account' --output text --region $Region --profile $Profile
try { aws lambda add-permission --function-name $LambdaName --statement-id s3invoke-rework-final --action lambda:InvokeFunction --principal s3.amazonaws.com --source-arn arn:aws:s3:::$Bucket --source-account $account --region $Region --profile $Profile | Out-Null } catch {}

# Merge S3 bucket notifications
$notif = aws s3api get-bucket-notification-configuration --bucket $Bucket --region $Region --profile $Profile | ConvertFrom-Json
if (-not $notif.LambdaFunctionConfigurations) { $notif | Add-Member -MemberType NoteProperty -Name LambdaFunctionConfigurations -Value @() }
$notif.LambdaFunctionConfigurations = @($notif.LambdaFunctionConfigurations | Where-Object { $_.Id -ne 'ReworkPdfCreate' })
$cfgObj = [pscustomobject]@{
  Id='ReworkPdfCreate';
  LambdaFunctionArn=$fnArn;
  Events=@('s3:ObjectCreated:*');
  Filter= [pscustomobject]@{ Key = [pscustomobject]@{ FilterRules = @(
    [pscustomobject]@{ Name='Prefix'; Value='Bill_Parser_Rework_Input/' },
    [pscustomobject]@{ Name='Suffix'; Value='.pdf' }
  )}}
}
$notif.LambdaFunctionConfigurations += $cfgObj
$notifPath = Join-Path $Temp 'notif.json'
Write-JsonAscii $notif $notifPath
aws s3api put-bucket-notification-configuration --bucket $Bucket --notification-configuration file://$notifPath --region $Region --profile $Profile | Out-Null

Write-Host "DONE. Lambda ARN: $fnArn"
