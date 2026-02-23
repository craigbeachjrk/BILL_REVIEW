# Create DynamoDB table for user management
$PROFILE = "jrk-analytics-admin"
$REGION = "us-east-1"
$TABLE_NAME = "jrk-bill-review-users"

Write-Host "Creating DynamoDB table: $TABLE_NAME"

aws dynamodb create-table `
  --table-name $TABLE_NAME `
  --attribute-definitions `
    AttributeName=user_id,AttributeType=S `
    AttributeName=role,AttributeType=S `
  --key-schema `
    AttributeName=user_id,KeyType=HASH `
  --global-secondary-indexes `
    "IndexName=role-index,KeySchema=[{AttributeName=role,KeyType=HASH}],Projection={ProjectionType=ALL},ProvisionedThroughput={ReadCapacityUnits=5,WriteCapacityUnits=5}" `
  --provisioned-throughput `
    ReadCapacityUnits=5,WriteCapacityUnits=5 `
  --profile $PROFILE `
  --region $REGION

Write-Host ""
Write-Host "Table created successfully!"
Write-Host "Now creating initial admin user..."

# Initial admin user (password: ChangeMe123!)
$ADMIN_ITEM = @"
{
  "user_id": {"S": "admin@jrkanalytics.com"},
  "password_hash": {"S": "`$2b`$12`$LQv3c1yqBWVHxkd0LHAkCOYz6TT4c8vXp6RZ1v3nZ7qKp8bE8OhYK"},
  "role": {"S": "System_Admins"},
  "full_name": {"S": "System Administrator"},
  "created_utc": {"S": "$(Get-Date -Format o)"},
  "enabled": {"BOOL": true},
  "must_change_password": {"BOOL": true}
}
"@

Write-Host "Adding initial admin user (admin@jrkanalytics.com / ChangeMe123!)..."
$ADMIN_ITEM | Out-File -FilePath "temp_admin.json" -Encoding utf8

aws dynamodb put-item `
  --table-name $TABLE_NAME `
  --item file://temp_admin.json `
  --profile $PROFILE `
  --region $REGION

Remove-Item "temp_admin.json"

Write-Host ""
Write-Host "Initial setup complete!"
Write-Host ""
Write-Host "Login credentials:"
Write-Host "  Email: admin@jrkanalytics.com"
Write-Host "  Password: ChangeMe123!"
Write-Host "  (You will be prompted to change on first login)"
