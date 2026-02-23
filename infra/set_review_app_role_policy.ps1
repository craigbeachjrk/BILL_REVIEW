Param(
  [string]$Profile = 'jrk-analytics-admin',
  [string]$Region = 'us-east-1',
  [string]$RoleName = 'jrk-bill-review-instance-role',
  [string]$Bucket = 'jrk-analytics-billing'
)

$ErrorActionPreference = 'Stop'
$Temp = Join-Path $env:TEMP ("review_role_" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Temp | Out-Null

$ReworkPrefix   = 'Bill_Parser_Rework_Input/'
$EnrichPrefix   = 'Bill_Parser_4_Enriched_Outputs/'
$PrePrefix      = 'Bill_Parser_6_PreEntrata_Submission/'
$ParsedInPrefix = 'Bill_Parser_2_Parsed_Inputs/'
$PendingPrefix  = 'Bill_Parser_1_Pending_Parsing/'

$policyObj = [ordered]@{
  Version = '2012-10-17'
  Statement = @(
    # Read for streaming PDFs
    @{ Effect='Allow'; Action=@('s3:GetObject'); Resource=@(
        "arn:aws:s3:::$Bucket/$ParsedInPrefix*",
        "arn:aws:s3:::$Bucket/$PendingPrefix*",
        "arn:aws:s3:::$Bucket/$ReworkPrefix*",
        "arn:aws:s3:::$Bucket/$EnrichPrefix*"
      )
    },
    # Write for rework copies
    @{ Effect='Allow'; Action=@('s3:PutObject'); Resource=@("arn:aws:s3:::$Bucket/$ReworkPrefix*") },
    # Delete enriched/pre-entrata artifacts
    @{ Effect='Allow'; Action=@('s3:DeleteObject'); Resource=@(
        "arn:aws:s3:::$Bucket/$EnrichPrefix*",
        "arn:aws:s3:::$Bucket/$PrePrefix*"
      )
    },
    # List bucket limited to used prefixes
    @{ Effect='Allow'; Action=@('s3:ListBucket'); Resource=@("arn:aws:s3:::$Bucket");
       Condition = @{ StringLike = @{ 's3:prefix' = @(
          "$ParsedInPrefix*","$PendingPrefix*","$ReworkPrefix*","$EnrichPrefix*","$PrePrefix*"
       ) } } }
  )
}

$policyPath = Join-Path $Temp 'policy.json'
$policyObj | ConvertTo-Json -Depth 8 -Compress | Out-File -FilePath $policyPath -Encoding ascii -NoNewline

$policyName = 'jrk-bill-review-app-s3'
aws iam put-role-policy --role-name $RoleName --policy-name $policyName --policy-document file://$policyPath --region $Region --profile $Profile | Out-Null
Write-Host "Updated inline policy $policyName on $RoleName"
