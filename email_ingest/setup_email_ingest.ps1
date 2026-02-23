Param(
    [string]$Domain = "jrkanalytics.com",
    [string]$Region = "us-east-1",
    [string]$RawBucket = "jrk-email-raw-us-east-1",
    [string]$PartitionedBucket = "jrk-email-partitioned-us-east-1",
    [string]$LambdaName = "jrk-email-ingest",
    [string]$RuleSetName = "jrk-inbound-rules",
    [string]$BillsRecipient = "bills@jrkanalytics.com",
    [string]$ReportsRecipient = "reports@jrkanalytics.com",
    [string]$Profile,
    [string]$HostedZoneId
)

# Safety: stop on errors
$ErrorActionPreference = 'Stop'

function Require-AwsCli {
    if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
        throw "AWS CLI not found. Install and configure credentials before running."
    }
    if ($Profile) {
        $env:AWS_PROFILE = $Profile
        Write-Host "Using AWS profile: $Profile"
    }
}

function Get-AccountId {
    $idJson = aws sts get-caller-identity | ConvertFrom-Json
    return $idJson.Account
}

function Get-HostedZoneId($domain) {
    if ($HostedZoneId) { return $HostedZoneId }
    try {
        $resp = aws route53 list-hosted-zones-by-name --dns-name $domain | ConvertFrom-Json
        $hz = $resp.HostedZones | Where-Object { $_.Name -eq ("$domain." ) }
        if (-not $hz) { throw "Hosted zone for $domain not found in this account/region/profile." }
        return ($hz.Id -replace "/hostedzone/", "")
    } catch {
        throw "Failed to resolve hosted zone for $domain. Ensure AWS CLI credentials are configured (aws configure sso) and that the hosted zone exists in this account."
    }
}

function Ensure-Bucket($name) {
    try {
        aws s3api head-bucket --bucket $name 2>$null | Out-Null
    } catch {
        if ($Region -eq 'us-east-1') {
            aws s3api create-bucket --bucket $name --region $Region | Out-Null
        } else {
            aws s3api create-bucket --bucket $name --region $Region --create-bucket-configuration LocationConstraint=$Region | Out-Null
        }
    }
    $enc = @{ Rules = @(@{ ApplyServerSideEncryptionByDefault = @{ SSEAlgorithm = "AES256" } }) } | ConvertTo-Json -Depth 5
    $tmp = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $tmp -Value $enc -Encoding ascii
    try {
        aws s3api put-bucket-encryption --bucket $name --server-side-encryption-configuration file://$tmp | Out-Null
    } finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

function Put-RawBucketPolicy($bucket, $accountId) {
    $policy = @{
        Version = "2012-10-17"
        Statement = @(
            @{ # Allow SES to write raw emails from this account's SES in this region
                Sid = "AllowSESPutObject"
                Effect = "Allow"
                Principal = @{ Service = "ses.amazonaws.com" }
                Action = @("s3:PutObject")
                Resource = "arn:aws:s3:::$bucket/*"
                Condition = @{
                    StringEquals = @{ "aws:SourceAccount" = "${accountId}" }
                }
            }
        )
    } | ConvertTo-Json -Depth 6
    $tmp = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $tmp -Value $policy -Encoding ascii
    try { aws s3api put-bucket-policy --bucket $bucket --policy file://$tmp | Out-Null } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
}

function Ensure-LambdaRole($roleName) {
    $assumeObj = @{ Version = "2012-10-17"; Statement = @(@{ Effect = "Allow"; Principal = @{ Service = "lambda.amazonaws.com" }; Action = "sts:AssumeRole" }) } | ConvertTo-Json -Depth 5
    $assumeFile = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $assumeFile -Value $assumeObj -Encoding ascii
    $roleArn = ""
    $haveRole = $false
    try {
        $roleArn = (aws iam get-role --role-name $roleName | ConvertFrom-Json).Role.Arn
        $haveRole = $true
    } catch {}
    if (-not $haveRole) {
        try {
            $createOut = aws iam create-role --role-name $roleName --assume-role-policy-document file://$assumeFile | ConvertFrom-Json
            $roleArn = $createOut.Role.Arn
        } catch {
            throw "Failed to create IAM role $roleName. $_"
        }
        # Wait until role is gettable
        for ($i=0; $i -lt 20; $i++) {
            try { $null = aws iam get-role --role-name $roleName | Out-Null; break } catch { Start-Sleep -Seconds 3 }
        }
        Start-Sleep -Seconds 5
        try { $roleArn = (aws iam get-role --role-name $roleName | ConvertFrom-Json).Role.Arn } catch {}
    }
    Remove-Item $assumeFile -ErrorAction SilentlyContinue
    $policyDoc = @{
        Version = "2012-10-17"
        Statement = @(
            @{ Effect = "Allow"; Action = @("s3:GetObject"); Resource = "arn:aws:s3:::$RawBucket/*" },
            @{ Effect = "Allow"; Action = @("s3:PutObject"); Resource = "arn:aws:s3:::$PartitionedBucket/*" }
        )
    } | ConvertTo-Json -Depth 5

    $policyName = "$roleName-inline"
    $polTmp = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $polTmp -Value $policyDoc -Encoding ascii
    $attached = $false
    for ($i=0; $i -lt 10 -and -not $attached; $i++) {
        try {
            aws iam put-role-policy --role-name $roleName --policy-name $policyName --policy-document file://$polTmp | Out-Null
            $attached = $true
        } catch {
            Start-Sleep -Seconds 3
        }
    }
    Remove-Item $polTmp -ErrorAction SilentlyContinue
    if (-not $attached) { throw "Failed to attach inline policy to role $roleName after retries." }
    # Attach AWS managed basic execution policy for logs with retries
    $logsAttached = $false
    for ($i=0; $i -lt 10 -and -not $logsAttached; $i++) {
        try {
            aws iam attach-role-policy --role-name $roleName --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole | Out-Null
            $logsAttached = $true
        } catch {
            Start-Sleep -Seconds 3
        }
    }
    if (-not $roleArn) { throw "Lambda role ARN empty for $roleName" }
    return $roleArn
}

function Ensure-Lambda($lambdaName, $roleArn) {
    $codePath = Join-Path $PSScriptRoot "lambda_email_ingest.py"
    if (-not (Test-Path $codePath)) { throw "Lambda code not found at $codePath" }
    $zipPath = Join-Path $PSScriptRoot "lambda_email_ingest.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath }
    Compress-Archive -Path $codePath -DestinationPath $zipPath

    $env = @{ Variables = @{ 
        TARGET_BUCKET = $PartitionedBucket;
        PARTITIONED_PREFIX_ROOT = "emails/";
        ATTACHMENTS_PREFIX_ROOT = "attachments/";
        STORE_ATTACHMENTS = "1";
    }} | ConvertTo-Json
    $envTmp = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $envTmp -Value $env -Encoding ascii

    if (-not $roleArn) { throw "Missing roleArn for Lambda $lambdaName" }
    try {
        # Try create; if exists, update code and config
        aws lambda create-function `
            --function-name $lambdaName `
            --runtime python3.12 `
            --role $roleArn `
            --handler lambda_email_ingest.handler `
            --timeout 60 `
            --memory-size 256 `
            --zip-file fileb://$zipPath `
            --environment file://$envTmp `
            --region $Region | Out-Null
    } catch {
        aws lambda update-function-code --function-name $lambdaName --zip-file fileb://$zipPath --region $Region | Out-Null
        aws lambda update-function-configuration --function-name $lambdaName --environment file://$envTmp --region $Region | Out-Null
    }
    Remove-Item $envTmp -ErrorAction SilentlyContinue
}

function Ensure-SesReceivingVerification($domain) {
    # SES Classic returns a verification token for receiving via verify-domain-identity
    $token = ""
    try {
        $resp = aws ses verify-domain-identity --domain $domain --region $Region | ConvertFrom-Json
        $token = $resp.VerificationToken
    } catch {
        # If already verified, SES may throw; attempt to fetch token is not supported; just proceed.
        Write-Host "Domain may already be verified for SES receiving or token retrieval not available. Proceeding to upsert TXT."
    }
    if (-not $token) { Write-Host "No new token returned. If domain is already verified, TXT is already present."; return }

    $hzId = Get-HostedZoneId $domain
    $changeBatch = @{
        Changes = @(@{
            Action = "UPSERT"
            ResourceRecordSet = @{
                Name = "_amazonses.$domain"
                Type = "TXT"
                TTL = 300
                ResourceRecords = @(@{ Value = '"' + $token + '"' })
            }
        })
    } | ConvertTo-Json -Depth 5
    $tmp = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $tmp -Value $changeBatch -Encoding ascii
    try {
        aws route53 change-resource-record-sets --hosted-zone-id $hzId --change-batch file://$tmp | Out-Null
    } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
}

function Ensure-MxToSes($domain) {
    $hzId = Get-HostedZoneId $domain
    $mxValue = "10 inbound-smtp.$Region.amazonaws.com."
    $changeBatch = @{
        Changes = @(@{ Action = "UPSERT"; ResourceRecordSet = @{ Name = $domain; Type = "MX"; TTL = 300; ResourceRecords = @(@{ Value = $mxValue }) } })
    } | ConvertTo-Json -Depth 5
    $tmp = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $tmp -Value $changeBatch -Encoding ascii
    try {
        aws route53 change-resource-record-sets --hosted-zone-id $hzId --change-batch file://$tmp | Out-Null
    } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
}

function Ensure-RuleSet($ruleSet) {
    try { aws ses describe-receipt-rule-set --rule-set-name $ruleSet --region $Region | Out-Null } catch { aws ses create-receipt-rule-set --rule-set-name $ruleSet --region $Region | Out-Null }
    aws ses set-active-receipt-rule-set --rule-set-name $ruleSet --region $Region | Out-Null
}

function Ensure-ReceiptRule($ruleSet, $ruleName, $recipients, $s3prefix) {
    $accountId = Get-AccountId
    # Allow SES to invoke Lambda
    try {
        aws lambda add-permission --function-name $LambdaName --statement-id "$ruleName-allow-ses" --action lambda:InvokeFunction --principal ses.amazonaws.com --source-account $accountId --region $Region | Out-Null
    } catch {}

    $funcArn = (aws lambda get-function --function-name $LambdaName --region $Region | ConvertFrom-Json).Configuration.FunctionArn
    if (-not $funcArn) { throw "Lambda function $LambdaName not found or ARN empty." }

    $rule = @{
        Name = $ruleName
        Enabled = $true
        Recipients = $recipients
        Actions = @(
            @{ S3Action = @{ BucketName = $RawBucket; ObjectKeyPrefix = $s3prefix; KmsKeyArn = $null } },
            @{ LambdaAction = @{ FunctionArn = $funcArn; InvocationType = "Event" } }
        )
        ScanEnabled = $true
        TlsPolicy = "Optional"
    } | ConvertTo-Json -Depth 6

    $tmp = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $tmp -Value $rule -Encoding ascii
    try {
        aws ses create-receipt-rule --rule-set-name $ruleSet --rule file://$tmp --region $Region | Out-Null
    } catch {
        aws ses update-receipt-rule --rule-set-name $ruleSet --rule file://$tmp --region $Region | Out-Null
    } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
}

# MAIN
Require-AwsCli
$acct = Get-AccountId
Write-Host "Using AWS Account: $acct in region $Region"

# Buckets
Ensure-Bucket $RawBucket
Ensure-Bucket $PartitionedBucket
Put-RawBucketPolicy -bucket $RawBucket -accountId $acct

# IAM Role and Lambda
$roleArn = Ensure-LambdaRole -roleName "${LambdaName}-role"
Ensure-Lambda -lambdaName $LambdaName -roleArn $roleArn

# SES: receiving identity TXT and MX
Ensure-SesReceivingVerification -domain $Domain
Write-Host "Waiting 60s for DNS to propagate SES TXT..."; Start-Sleep -Seconds 60
Ensure-MxToSes -domain $Domain

# SES: Rule set and rules
Ensure-RuleSet -ruleSet $RuleSetName
Ensure-ReceiptRule -ruleSet $RuleSetName -ruleName "BillsRule" -recipients @($BillsRecipient) -s3prefix "bills/"
Ensure-ReceiptRule -ruleSet $RuleSetName -ruleName "ReportsRule" -recipients @($ReportsRecipient) -s3prefix "reports/"

Write-Host "Setup complete. Send test emails to $BillsRecipient and $ReportsRecipient. Raw (.eml) files will land flat under s3://$PartitionedBucket/emails and attachments under s3://$PartitionedBucket/attachments (no date partitioning)."
