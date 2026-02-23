# Create DynamoDB table for billback master bills
# This table stores line items assigned to billback periods with notes

aws dynamodb create-table `
  --table-name jrk-bill-billback-master `
  --attribute-definitions `
    AttributeName=billback_period,AttributeType=S `
    AttributeName=line_item_id,AttributeType=S `
  --key-schema `
    AttributeName=billback_period,KeyType=HASH `
    AttributeName=line_item_id,KeyType=RANGE `
  --billing-mode PAY_PER_REQUEST `
  --profile jrk-analytics-admin `
  --region us-east-1

Write-Host "Table jrk-bill-billback-master created successfully"
