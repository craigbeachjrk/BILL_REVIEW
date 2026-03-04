# =============================================================================
# IMPROVE Agent — AWS Infrastructure Setup
# =============================================================================
# Creates all AWS resources needed for the ECS Fargate IMPROVE agent.
# Run manually: powershell -ExecutionPolicy Bypass -File setup_infra.ps1
#
# Prerequisites:
#   - AWS CLI configured with jrk-analytics-admin profile
#   - Permissions for ECR, ECS, IAM, CloudWatch, Secrets Manager, EC2
# =============================================================================

$ErrorActionPreference = "Stop"

$AWS_REGION = "us-east-1"
$AWS_ACCOUNT = "789814232318"
$PROFILE = "jrk-analytics-admin"
$AGENT_NAME = "jrk-improve-agent"
$LOG_GROUP = "/ecs/$AGENT_NAME"
$ECR_REPO = $AGENT_NAME
$EXECUTION_ROLE = "${AGENT_NAME}-execution-role"
$TASK_ROLE = "${AGENT_NAME}-task-role"
$SG_NAME = "${AGENT_NAME}-sg"
$VPC_ID = ""  # Will be auto-detected

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  IMPROVE Agent Infrastructure Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1. ECR Repository
# ---------------------------------------------------------------------------
Write-Host "`n[1/9] Creating ECR repository: $ECR_REPO" -ForegroundColor Yellow
try {
    aws ecr describe-repositories --repository-names $ECR_REPO --region $AWS_REGION --profile $PROFILE 2>$null | Out-Null
    Write-Host "  ECR repo already exists." -ForegroundColor Green
} catch {
    aws ecr create-repository `
        --repository-name $ECR_REPO `
        --region $AWS_REGION `
        --profile $PROFILE `
        --image-scanning-configuration scanOnPush=true
    Write-Host "  ECR repo created." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 2. CloudWatch Log Group
# ---------------------------------------------------------------------------
Write-Host "`n[2/9] Creating CloudWatch log group: $LOG_GROUP" -ForegroundColor Yellow
try {
    aws logs describe-log-groups --log-group-name-prefix $LOG_GROUP --region $AWS_REGION --profile $PROFILE | Out-Null
    $existing = aws logs describe-log-groups --log-group-name-prefix $LOG_GROUP --region $AWS_REGION --profile $PROFILE | ConvertFrom-Json
    if ($existing.logGroups.Count -gt 0 -and ($existing.logGroups | Where-Object { $_.logGroupName -eq $LOG_GROUP })) {
        Write-Host "  Log group already exists." -ForegroundColor Green
    } else {
        aws logs create-log-group --log-group-name $LOG_GROUP --region $AWS_REGION --profile $PROFILE
        aws logs put-retention-policy --log-group-name $LOG_GROUP --retention-in-days 30 --region $AWS_REGION --profile $PROFILE
        Write-Host "  Log group created (30-day retention)." -ForegroundColor Green
    }
} catch {
    aws logs create-log-group --log-group-name $LOG_GROUP --region $AWS_REGION --profile $PROFILE
    aws logs put-retention-policy --log-group-name $LOG_GROUP --retention-in-days 30 --region $AWS_REGION --profile $PROFILE
    Write-Host "  Log group created (30-day retention)." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 3. IAM Execution Role (ECR pull + CloudWatch logs)
# ---------------------------------------------------------------------------
Write-Host "`n[3/9] Creating ECS execution role: $EXECUTION_ROLE" -ForegroundColor Yellow

$executionTrustPolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ecs-tasks.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
'@

$executionTrustFile = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($executionTrustFile, $executionTrustPolicy)

try {
    aws iam get-role --role-name $EXECUTION_ROLE --profile $PROFILE 2>$null | Out-Null
    Write-Host "  Execution role already exists." -ForegroundColor Green
} catch {
    aws iam create-role `
        --role-name $EXECUTION_ROLE `
        --assume-role-policy-document "file://$executionTrustFile" `
        --profile $PROFILE
    Write-Host "  Execution role created." -ForegroundColor Green
}
Remove-Item $executionTrustFile -ErrorAction SilentlyContinue

# Attach managed policies for execution role
aws iam attach-role-policy `
    --role-name $EXECUTION_ROLE `
    --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy" `
    --profile $PROFILE 2>$null

# Secrets Manager access for execution role (to inject secrets into container)
$execSecretsPolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "arn:aws:secretsmanager:__AWS_REGION__:__AWS_ACCOUNT__:secret:improve-agent/*"
      ]
    }
  ]
}
'@
$execSecretsPolicy = $execSecretsPolicy -replace '__AWS_REGION__', $AWS_REGION
$execSecretsPolicy = $execSecretsPolicy -replace '__AWS_ACCOUNT__', $AWS_ACCOUNT
$execSecretsPolicyFile = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($execSecretsPolicyFile, $execSecretsPolicy)

aws iam put-role-policy `
    --role-name $EXECUTION_ROLE `
    --policy-name "${AGENT_NAME}-secrets-access" `
    --policy-document "file://$execSecretsPolicyFile" `
    --profile $PROFILE
Remove-Item $execSecretsPolicyFile -ErrorAction SilentlyContinue
Write-Host "  Execution role policies attached." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 4. IAM Task Role (DynamoDB, SES, S3, Secrets Manager)
# ---------------------------------------------------------------------------
Write-Host "`n[4/9] Creating ECS task role: $TASK_ROLE" -ForegroundColor Yellow

$taskTrustPolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ecs-tasks.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
'@
$taskTrustFile = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($taskTrustFile, $taskTrustPolicy)

try {
    aws iam get-role --role-name $TASK_ROLE --profile $PROFILE 2>$null | Out-Null
    Write-Host "  Task role already exists." -ForegroundColor Green
} catch {
    aws iam create-role `
        --role-name $TASK_ROLE `
        --assume-role-policy-document "file://$taskTrustFile" `
        --profile $PROFILE
    Write-Host "  Task role created." -ForegroundColor Green
}
Remove-Item $taskTrustFile -ErrorAction SilentlyContinue

$taskPolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DynamoDB",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query"
      ],
      "Resource": "arn:aws:dynamodb:__AWS_REGION__:__AWS_ACCOUNT__:table/jrk-bill-review-debug"
    },
    {
      "Sid": "SES",
      "Effect": "Allow",
      "Action": "ses:SendEmail",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "ses:FromAddress": "noreply@jrkanalytics.com"
        }
      }
    },
    {
      "Sid": "S3ReadScreenshots",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::jrk-analytics-billing/improve-screenshots/*"
    },
    {
      "Sid": "SecretsManager",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:__AWS_REGION__:__AWS_ACCOUNT__:secret:improve-agent/*"
    }
  ]
}
'@
$taskPolicy = $taskPolicy -replace '__AWS_REGION__', $AWS_REGION
$taskPolicy = $taskPolicy -replace '__AWS_ACCOUNT__', $AWS_ACCOUNT
$taskPolicyFile = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($taskPolicyFile, $taskPolicy)

aws iam put-role-policy `
    --role-name $TASK_ROLE `
    --policy-name "${AGENT_NAME}-task-permissions" `
    --policy-document "file://$taskPolicyFile" `
    --profile $PROFILE
Remove-Item $taskPolicyFile -ErrorAction SilentlyContinue
Write-Host "  Task role policies attached." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 5. Secrets Manager Entries (placeholders)
# ---------------------------------------------------------------------------
Write-Host "`n[5/9] Creating Secrets Manager entries" -ForegroundColor Yellow

$secrets = @(
    @{ Name = "improve-agent/anthropic-api-key"; Desc = "Anthropic API key for Claude Code" },
    @{ Name = "improve-agent/gh-token"; Desc = "GitHub PAT for repo clone and PR creation" }
)

foreach ($secret in $secrets) {
    try {
        aws secretsmanager describe-secret --secret-id $secret.Name --region $AWS_REGION --profile $PROFILE 2>$null | Out-Null
        Write-Host "  Secret '$($secret.Name)' already exists." -ForegroundColor Green
    } catch {
        aws secretsmanager create-secret `
            --name $secret.Name `
            --description $secret.Desc `
            --secret-string "PLACEHOLDER-REPLACE-ME" `
            --region $AWS_REGION `
            --profile $PROFILE
        Write-Host "  Secret '$($secret.Name)' created (placeholder value -- update before use!)." -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# 6. ECS Cluster
# ---------------------------------------------------------------------------
Write-Host "`n[6/9] Creating ECS cluster: $AGENT_NAME" -ForegroundColor Yellow
try {
    $clusterInfo = aws ecs describe-clusters --clusters $AGENT_NAME --region $AWS_REGION --profile $PROFILE | ConvertFrom-Json
    if ($clusterInfo.clusters.Count -gt 0 -and $clusterInfo.clusters[0].status -eq "ACTIVE") {
        Write-Host "  ECS cluster already exists." -ForegroundColor Green
    } else {
        throw "not found"
    }
} catch {
    aws ecs create-cluster `
        --cluster-name $AGENT_NAME `
        --capacity-providers FARGATE `
        --default-capacity-provider-strategy "capacityProvider=FARGATE,weight=1" `
        --region $AWS_REGION `
        --profile $PROFILE
    Write-Host "  ECS cluster created." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 7. Security Group (outbound all, no inbound)
# ---------------------------------------------------------------------------
Write-Host "`n[7/9] Creating security group: $SG_NAME" -ForegroundColor Yellow

# Auto-detect VPC
if (-not $VPC_ID) {
    $VPC_ID = (aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --region $AWS_REGION --profile $PROFILE | ConvertFrom-Json).Vpcs[0].VpcId
    Write-Host "  Using default VPC: $VPC_ID" -ForegroundColor Gray
}

# Check if SG exists
$existingSG = aws ec2 describe-security-groups `
    --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" `
    --region $AWS_REGION --profile $PROFILE | ConvertFrom-Json

if ($existingSG.SecurityGroups.Count -gt 0) {
    $SG_ID = $existingSG.SecurityGroups[0].GroupId
    Write-Host "  Security group already exists: $SG_ID" -ForegroundColor Green
} else {
    $sgResult = aws ec2 create-security-group `
        --group-name $SG_NAME `
        --description "IMPROVE Agent ECS tasks - outbound only" `
        --vpc-id $VPC_ID `
        --region $AWS_REGION `
        --profile $PROFILE | ConvertFrom-Json
    $SG_ID = $sgResult.GroupId
    Write-Host "  Security group created: $SG_ID" -ForegroundColor Green

    # Revoke default inbound rule (if any)
    # Default SG has no inbound rules, but be safe
    Write-Host "  Outbound: all traffic allowed (default)." -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# 8. Get Subnets
# ---------------------------------------------------------------------------
Write-Host "`n[8/9] Finding subnets for VPC: $VPC_ID" -ForegroundColor Yellow
$subnets = (aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" --region $AWS_REGION --profile $PROFILE | ConvertFrom-Json).Subnets
$subnetIds = ($subnets | Select-Object -ExpandProperty SubnetId) -join ","
Write-Host "  Subnets: $subnetIds" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# 9. ECS Task Definition
# ---------------------------------------------------------------------------
Write-Host "`n[9/9] Registering ECS task definition" -ForegroundColor Yellow

$taskDef = @'
{
  "family": "__AGENT_NAME__",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "2048",
  "memory": "4096",
  "executionRoleArn": "arn:aws:iam::__AWS_ACCOUNT__:role/__EXECUTION_ROLE__",
  "taskRoleArn": "arn:aws:iam::__AWS_ACCOUNT__:role/__TASK_ROLE__",
  "containerDefinitions": [
    {
      "name": "improve-agent",
      "image": "__AWS_ACCOUNT__.dkr.ecr.__AWS_REGION__.amazonaws.com/__ECR_REPO__:latest",
      "essential": true,
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "__LOG_GROUP__",
          "awslogs-region": "__AWS_REGION__",
          "awslogs-stream-prefix": "agent"
        }
      },
      "environment": [
        { "name": "AWS_REGION", "value": "__AWS_REGION__" },
        { "name": "DEBUG_TABLE", "value": "jrk-bill-review-debug" },
        { "name": "S3_BUCKET", "value": "jrk-analytics-billing" }
      ]
    }
  ]
}
'@
$taskDef = $taskDef -replace '__AGENT_NAME__', $AGENT_NAME
$taskDef = $taskDef -replace '__AWS_ACCOUNT__', $AWS_ACCOUNT
$taskDef = $taskDef -replace '__AWS_REGION__', $AWS_REGION
$taskDef = $taskDef -replace '__EXECUTION_ROLE__', $EXECUTION_ROLE
$taskDef = $taskDef -replace '__TASK_ROLE__', $TASK_ROLE
$taskDef = $taskDef -replace '__ECR_REPO__', $ECR_REPO
$taskDef = $taskDef -replace '__LOG_GROUP__', $LOG_GROUP
$taskDefFile = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($taskDefFile, $taskDef)

aws ecs register-task-definition `
    --cli-input-json "file://$taskDefFile" `
    --region $AWS_REGION `
    --profile $PROFILE
Remove-Item $taskDefFile -ErrorAction SilentlyContinue
Write-Host "  Task definition registered." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "NEXT STEPS:" -ForegroundColor Yellow
Write-Host "  1. Update Secrets Manager placeholders:" -ForegroundColor White
Write-Host "     aws secretsmanager put-secret-value --secret-id improve-agent/anthropic-api-key --secret-string 'sk-ant-...' --region $AWS_REGION --profile $PROFILE"
Write-Host "     aws secretsmanager put-secret-value --secret-id improve-agent/gh-token --secret-string 'ghp_...' --region $AWS_REGION --profile $PROFILE"
Write-Host ""
Write-Host "  2. Build & push Docker image:" -ForegroundColor White
Write-Host "     aws ecr get-login-password --region $AWS_REGION --profile $PROFILE | docker login --username AWS --password-stdin ${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
Write-Host "     docker build -t ${ECR_REPO}:latest infra/improve-agent/"
Write-Host "     docker tag ${ECR_REPO}:latest ${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:latest"
Write-Host "     docker push ${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}:latest"
Write-Host ""
Write-Host "  3. Set these env vars on the Bill Review AppRunner service:" -ForegroundColor White
Write-Host "     IMPROVE_AGENT_CLUSTER  = $AGENT_NAME" -ForegroundColor Gray
Write-Host "     IMPROVE_AGENT_TASK_DEF = $AGENT_NAME" -ForegroundColor Gray
Write-Host "     IMPROVE_AGENT_SUBNETS  = $subnetIds" -ForegroundColor Gray
Write-Host "     IMPROVE_AGENT_SG       = $SG_ID" -ForegroundColor Gray
Write-Host ""
