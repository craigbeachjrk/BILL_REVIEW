Param(
  [string]$Profile = 'jrk-analytics-admin',
  [string]$Region = 'us-east-1',
  [string]$Bucket = 'jrk-analytics-billing',
  [string]$LambdaName = 'jrk-bill-parser-rework',
  [string]$RoleName = 'jrk-bill-parser-rework-role',
  [string]$ReworkPrefix = 'Bill_Parser_Rework_Input/',
  [string]$PostParsedPrefix = 'Bill_Parser_2_Post_Parsed_Outputs/',
  [string]$EnrichPrefix = 'Bill_Parser_4_Enriched_Outputs/',
  [string]$PreEntrataPrefix = 'Bill_Parser_6_PreEntrata_Submission/'
)

$ErrorActionPreference = 'Stop'
$Temp = Join-Path $env:TEMP ("rework_setup_" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Temp | Out-Null

function Write-JsonAscii([object]$obj, [string]$path) {
  $json = $obj | ConvertTo-Json -Depth 20 -Compress
  $json | Out-File -FilePath $path -Encoding ascii -NoNewline
}

# Trust policy (ASCII, no BOM)
$trustObj = [pscustomobject]@{
  Version = '2012-10-17'
  Statement = @(@{
      Effect = 'Allow'
      Principal = @{ Service = 'lambda.amazonaws.com' }
      Action = 'sts:AssumeRole'
  })
}
$trustPath = Join-Path $Temp 'trust.json'
Write-JsonAscii $trustObj $trustPath

# Create role
try { aws iam create-role --role-name $RoleName --assume-role-policy-document file://$trustPath --region $Region --profile $Profile | Out-Null } catch {}
# Attach AWS managed basic logs policy
try { aws iam attach-role-policy --role-name $RoleName --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole --region $Region --profile $Profile | Out-Null } catch {}

# Wait for role propagation
Start-Sleep -Seconds 5
$roleArn = aws iam get-role --role-name $RoleName --region $Region --profile $Profile --query 'Role.Arn' --output text
if (-not $roleArn) { throw 'Role ARN not found' }

# Minimal handler zip
$handlerCode = @'
import os, json, boto3

def handler(event, ctx):
    print("EVENT:", json.dumps(event))
    return {"ok": True}
'@
$handlerPath = Join-Path $Temp 'rework_handler.py'
$handlerCode | Out-File -FilePath $handlerPath -Encoding ascii -NoNewline
$zipPath = Join-Path $Temp 'rework.zip'
Compress-Archive -Path $handlerPath -DestinationPath $zipPath -Force

# Create or update Lambda
$exists = aws lambda list-functions --region $Region --profile $Profile --query "Functions[?FunctionName=='$LambdaName'] | length(@)" --output text
if ($exists -eq '0') {
  aws lambda create-function --function-name $LambdaName --runtime python3.12 --role $roleArn --handler rework_handler.handler --zip-file fileb://$zipPath --timeout 60 --memory-size 512 --region $Region --profile $Profile | Out-Null
} else {
  aws lambda update-function-code --function-name $LambdaName --zip-file fileb://$zipPath --region $Region --profile $Profile | Out-Null
}

# Set env via key=value syntax to avoid JSON quoting pitfalls
$envStr = "Variables={BUCKET=$Bucket,REWORK_PREFIX=$ReworkPrefix,ENRICH_PREFIX=$EnrichPrefix,POST_PARSED_PREFIX=$PostParsedPrefix,PRE_ENTRATA_PREFIX=$PreEntrataPrefix}"
aws lambda update-function-configuration --function-name $LambdaName --region $Region --profile $Profile --environment $envStr | Out-Null

# Get Lambda ARN
$fnArn = aws lambda get-function --function-name $LambdaName --region $Region --profile $Profile --query 'Configuration.FunctionArn' --output text
if (-not $fnArn) { throw 'Lambda ARN not found' }

# Allow S3 invoke
$account = aws sts get-caller-identity --query 'Account' --output text --region $Region --profile $Profile
try { aws lambda add-permission --function-name $LambdaName --statement-id s3invoke-rework --action lambda:InvokeFunction --principal s3.amazonaws.com --source-arn arn:aws:s3:::$Bucket --source-account $account --region $Region --profile $Profile | Out-Null } catch {}

# Merge S3 bucket notifications (ASCII JSON)
$notif = aws s3api get-bucket-notification-configuration --bucket $Bucket --region $Region --profile $Profile | ConvertFrom-Json
if (-not $notif.LambdaFunctionConfigurations) { $notif | Add-Member -MemberType NoteProperty -Name LambdaFunctionConfigurations -Value @() }
$notif.LambdaFunctionConfigurations = @($notif.LambdaFunctionConfigurations | Where-Object { $_.Id -ne 'ReworkPdfCreate' })
$cfg = [pscustomobject]@{
  Id='ReworkPdfCreate';
  LambdaFunctionArn=$fnArn;
  Events=@('s3:ObjectCreated:*');
  Filter= [pscustomobject]@{ Key = [pscustomobject]@{ FilterRules = @(
    [pscustomobject]@{ Name='prefix'; Value=$ReworkPrefix },
    [pscustomobject]@{ Name='suffix'; Value='.pdf' }
  )}}
}
$notif.LambdaFunctionConfigurations += $cfg
$notifPath = Join-Path $Temp 'notif.json'
Write-JsonAscii $notif $notifPath
aws s3api put-bucket-notification-configuration --bucket $Bucket --notification-configuration file://$notifPath --region $Region --profile $Profile | Out-Null

Write-Host "DONE. Lambda ARN: $fnArn"
