Param(
  [string]$Profile = 'jrk-analytics-admin',
  [string]$Region = 'us-east-1',
  [string]$RoleName = 'jrk-bill-parser-rework-role',
  [string]$Bucket = 'jrk-analytics-billing',
  [string]$ReworkPrefix = 'Bill_Parser_Rework_Input/',
  [string]$PendingPrefix = 'Bill_Parser_1_Pending_Parsing/'
)

$ErrorActionPreference = 'Stop'
$Temp = Join-Path $env:TEMP ("rework_lambda_role_" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Temp | Out-Null

# Allow Get from Rework, Put to Pending, Put .notes.json alongside, and basic List
$policyObj = [ordered]@{
  Version = '2012-10-17'
  Statement = @(
    @{ Effect='Allow'; Action=@('s3:GetObject'); Resource=@("arn:aws:s3:::$Bucket/$ReworkPrefix*") },
    @{ Effect='Allow'; Action=@('s3:PutObject'); Resource=@("arn:aws:s3:::$Bucket/$PendingPrefix*") },
    @{ Effect='Allow'; Action=@('s3:ListBucket'); Resource=@("arn:aws:s3:::$Bucket"); Condition=@{ StringLike = @{ 's3:prefix' = @("$ReworkPrefix*","$PendingPrefix*") } } }
  )
}
$policyPath = Join-Path $Temp 'policy.json'
$policyObj | ConvertTo-Json -Depth 8 -Compress | Out-File -FilePath $policyPath -Encoding ascii -NoNewline

$policyName = 'jrk-bill-rework-lambda-s3'
aws iam put-role-policy --role-name $RoleName --policy-name $policyName --policy-document file://$policyPath --region $Region --profile $Profile | Out-Null
Write-Host "Updated inline policy $policyName on $RoleName"
