$PROFILE = 'jrk-analytics-admin'
$REGION = 'us-east-1'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

aws dynamodb create-table `
  --table-name jrk-bill-ubi-assignments `
  --attribute-definitions `
    AttributeName=assignment_id,AttributeType=S `
    AttributeName=ubi_period,AttributeType=S `
  --key-schema `
    AttributeName=assignment_id,KeyType=HASH `
  --global-secondary-indexes "file://$SCRIPT_DIR\ubi_assignments_gsi.json" `
  --billing-mode PAY_PER_REQUEST `
  --region $REGION `
  --profile $PROFILE
