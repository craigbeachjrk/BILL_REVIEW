Param(
  [string]$Profile = 'jrk-analytics-admin',
  [string]$Region = 'us-east-1',
  [string]$Bucket = 'jrk-analytics-billing',
  [string]$LambdaName = 'jrk-bill-parser-rework',
  [string]$RoleName = 'jrk-bill-parser-rework-role',
  [string]$PolicyName = 'jrk-bill-parser-rework-inline',
  [string]$ReworkPrefix = 'Bill_Parser_Rework_Input/',
  [string]$PostParsedPrefix = 'Bill_Parser_2_Post_Parsed_Outputs/',
  [string]$EnrichPrefix = 'Bill_Parser_4_Enriched_Outputs/',
  [string]$PreEntrataPrefix = 'Bill_Parser_6_PreEntrata_Submission/'
)

$ErrorActionPreference = 'Stop'
$Temp = Join-Path $env:TEMP ("rework_setup_" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Temp | Out-Null

function Write-JsonNoBom($obj, $path) {
  $json = $obj | ConvertTo-Json -Depth 12 -Compress
  $enc = New-Object System.Text.UTF8Encoding($false)
  $sw = New-Object System.IO.StreamWriter($path, $false, $enc)
  try { $sw.Write($json) } finally { $sw.Close() }
}

# Trust policy
$trustObj = [pscustomobject]@{
  Version = '2012-10-17'
  Statement = @(@{
      Effect = 'Allow'
      Principal = @{ Service = 'lambda.amazonaws.com' }
      Action = 'sts:AssumeRole'
  })
}
$trustPath = Join-Path $Temp 'trust.json'
Write-JsonNoBom $trustObj $trustPath

# Create role (idempotent)
try { aws iam create-role --role-name $RoleName --assume-role-policy-document file://$trustPath --region $Region --profile $Profile | Out-Null } catch {}
$roleArn = aws iam get-role --role-name $RoleName --region $Region --profile $Profile --query 'Role.Arn' --output text

# Inline policy
$policyObj = [ordered]@{
  Version = '2012-10-17'
  Statement = @(
    @{ Effect='Allow'; Action=@('logs:CreateLogGroup','logs:CreateLogStream','logs:PutLogEvents'); Resource='*' },
    @{ Effect='Allow'; Action=@('s3:GetObject'); Resource=@("arn:aws:s3:::$Bucket/$ReworkPrefix*","arn:aws:s3:::$Bucket/*") },
    @{ Effect='Allow'; Action=@('s3:ListBucket'); Resource=@("arn:aws:s3:::$Bucket"); Condition=@{ StringLike = @{ 's3:prefix' = @("$ReworkPrefix*","$PostParsedPrefix*","$EnrichPrefix*","$PreEntrataPrefix*") } } },
    @{ Effect='Allow'; Action=@('s3:PutObject'); Resource=@("arn:aws:s3:::$Bucket/$PostParsedPrefix*","arn:aws:s3:::$Bucket/$EnrichPrefix*","arn:aws:s3:::$Bucket/$PreEntrataPrefix*") }
  )
}
$policyPath = Join-Path $Temp 'policy.json'
Write-JsonNoBom $policyObj $policyPath
aws iam put-role-policy --role-name $RoleName --policy-name $PolicyName --policy-document file://$policyPath --region $Region --profile $Profile | Out-Null
Start-Sleep -Seconds 3

# Minimal handler
$handlerCode = @'
import os, json, boto3, urllib.parse
s3 = boto3.client("s3")

def _sidecar(bucket, key):
    sc = key.rsplit('.', 1)[0] + '.rework.json'
    try:
        body = s3.get_object(Bucket=bucket, Key=sc)["Body"].read()
        return json.loads(body)
    except Exception:
        return {}

def handler(event, ctx):
    print("EVENT:", json.dumps(event))
    out = []
    for rec in event.get("Records", []):
        b = rec["s3"]["bucket"]["name"]
        k = urllib.parse.unquote_plus(rec["s3"]["object"]["key"])
        meta = _sidecar(b, k)
        print("REWORK for", b, k, "notes:", meta.get("notes"))
        out.append({"bucket": b, "key": k, "notes": meta.get("notes")})
    return {"ok": True, "items": out}
'@
$handlerPath = Join-Path $Temp 'rework_handler.py'
$handlerCode | Set-Content -Path $handlerPath -Encoding UTF8
$zipPath = Join-Path $Temp 'rework.zip'
Compress-Archive -Path $handlerPath -DestinationPath $zipPath -Force

# Create or update Lambda
$exists = aws lambda list-functions --region $Region --profile $Profile --query "Functions[?FunctionName=='$LambdaName'] | length(@)" --output text
if ($exists -eq '0') {
  aws lambda create-function --function-name $LambdaName --runtime python3.12 --role $roleArn --handler rework_handler.handler --zip-file fileb://$zipPath --timeout 60 --memory-size 512 --region $Region --profile $Profile | Out-Null
} else {
  aws lambda update-function-code --function-name $LambdaName --zip-file fileb://$zipPath --region $Region --profile $Profile | Out-Null
}

# Env vars
$envObj = @{ Variables = @{ BUCKET=$Bucket; REWORK_PREFIX=$ReworkPrefix; ENRICH_PREFIX=$EnrichPrefix; POST_PARSED_PREFIX=$PostParsedPrefix; PRE_ENTRATA_PREFIX=$PreEntrataPrefix } }
$envJson = ($envObj | ConvertTo-Json -Depth 3 -Compress)
aws lambda update-function-configuration --function-name $LambdaName --region $Region --profile $Profile --environment $envJson | Out-Null

$fnArn = aws lambda get-function --function-name $LambdaName --region $Region --profile $Profile --query 'Configuration.FunctionArn' --output text
$account = aws sts get-caller-identity --query 'Account' --output text --region $Region --profile $Profile

# Allow S3 to invoke
try { aws lambda add-permission --function-name $LambdaName --statement-id s3invoke-rework --action lambda:InvokeFunction --principal s3.amazonaws.com --source-arn arn:aws:s3:::$Bucket --source-account $account --region $Region --profile $Profile | Out-Null } catch {}

# Merge bucket notifications
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
Write-JsonNoBom $notif $notifPath
aws s3api put-bucket-notification-configuration --bucket $Bucket --notification-configuration file://$notifPath --region $Region --profile $Profile | Out-Null

Write-Host "DONE. Lambda ARN: $fnArn"
