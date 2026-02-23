param(
  [Parameter(Mandatory=$true)][string]$Bucket,
  [string]$Profile = "tina",
  [string]$Region = "us-east-1"
)
$policyPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'tina_bucket_policy.json'
if (-not (Test-Path $policyPath)) { throw "tina_bucket_policy.json not found at $policyPath" }
$tmp = Join-Path $env:TEMP "${Bucket}-policy.json"
(Get-Content $policyPath -Raw).Replace('BUCKET_NAME', $Bucket) | Set-Content $tmp -Encoding UTF8
aws s3api put-bucket-policy --bucket $Bucket --policy file://$tmp --profile $Profile --region $Region
Write-Host "Applied hardening policy to bucket $Bucket"
