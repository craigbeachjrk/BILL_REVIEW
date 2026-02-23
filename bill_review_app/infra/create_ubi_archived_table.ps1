$PROFILE = 'jrk-analytics-admin'
$REGION = 'us-east-1'

aws dynamodb create-table `
  --table-name jrk-bill-ubi-archived `
  --attribute-definitions `
    AttributeName=archive_id,AttributeType=S `
  --key-schema `
    AttributeName=archive_id,KeyType=HASH `
  --provisioned-throughput ReadCapacityUnits=5,WriteCapacityUnits=5 `
  --region $REGION `
  --profile $PROFILE
