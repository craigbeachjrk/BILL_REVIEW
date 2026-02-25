$PROFILE = 'jrk-analytics-admin'
$REGION = 'us-east-1'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

aws dynamodb create-table `
  --table-name jrk-bill-manual-entries `
  --attribute-definitions `
    AttributeName=entry_id,AttributeType=S `
    AttributeName=period,AttributeType=S `
  --key-schema `
    AttributeName=entry_id,KeyType=HASH `
  --global-secondary-indexes "file://$SCRIPT_DIR\manual_entries_gsi.json" `
  --billing-mode PAY_PER_REQUEST `
  --region $REGION `
  --profile $PROFILE
