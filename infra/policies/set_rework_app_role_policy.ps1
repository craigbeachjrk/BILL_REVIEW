Param(
  [string]$Profile = 'jrk-analytics-admin',
  [string]$Region = 'us-east-1',
  [string]$RoleName = 'jrk-bill-review-instance-role',
  [string]$Bucket = 'jrk-analytics-billing',
  [string]$ReworkPrefix = 'Bill_Parser_Rework_Input/',
  [string]$EnrichPrefix = 'Bill_Parser_4_Enriched_Outputs/',
  [string]$PreEntrataPrefix = 'Bill_Parser_6_PreEntrata_Submission/'
)

$ErrorActionPreference = 'Stop'
$Temp = Join-Path $env:TEMP ("rework_app_role_" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Temp | Out-Null

# Inline policy: allow PutObject to REWORK prefix, DeleteObject to Enriched/PreEntrata, ListBucket limited to prefixes
$policyObj = [ordered]@{
  Version = '2012-10-17'
  Statement = @(
    @{ Effect='Allow'; Action=@('s3:PutObject'); Resource=@("arn:aws:s3:::$Bucket/$ReworkPrefix*") },
    @{ Effect='Allow'; Action=@('s3:DeleteObject'); Resource=@(
        "arn:aws:s3:::$Bucket/$EnrichPrefix*",
        "arn:aws:s3:::$Bucket/$PreEntrataPrefix*"
      )
    },
    @{ Effect='Allow'; Action=@('s3:ListBucket'); Resource=@("arn:aws:s3:::$Bucket"); Condition=@{ StringLike = @{ 's3:prefix' = @("$ReworkPrefix*","$EnrichPrefix*","$PreEntrataPrefix*") } } }
  )
}
$policyPath = Join-Path $Temp 'policy.json'
$policyObj | ConvertTo-Json -Depth 8 -Compress | Out-File -FilePath $policyPath -Encoding ascii -NoNewline

# Attach or update inline policy
$policyName = 'jrk-bill-review-app-s3-rework'
aws iam put-role-policy --role-name $RoleName --policy-name $policyName --policy-document file://$policyPath --region $Region --profile $Profile | Out-Null
Write-Host "Updated inline policy $policyName on $RoleName"
