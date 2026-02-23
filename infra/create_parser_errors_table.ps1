param(
    [string]$Profile = 'jrk-analytics-admin',
    [string]$Region = 'us-east-1',
    [string]$TableName = 'jrk-bill-parser-errors'
)

Write-Host "Creating DynamoDB table: $TableName in $Region..."

aws dynamodb create-table `
    --table-name $TableName `
    --attribute-definitions `
        AttributeName=pk,AttributeType=S `
        AttributeName=timestamp,AttributeType=S `
    --key-schema `
        AttributeName=pk,KeyType=HASH `
        AttributeName=timestamp,KeyType=RANGE `
    --billing-mode PAY_PER_REQUEST `
    --region $Region `
    --profile $Profile

Write-Host "Table created successfully!"
Write-Host "Table name: $TableName"
