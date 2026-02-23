# Infrastructure Recommendations for Bill Review Application

**Date:** 2025-11-07
**Priority:** High
**Estimated Implementation Time:** 8-12 hours

---

## 1. DynamoDB Global Secondary Indexes (GSIs)

### Current State
The application queries DynamoDB tables without optimized indexes, leading to:
- Slow queries requiring table scans
- High read capacity consumption
- Poor query performance as data grows

### Recommended GSIs

#### Table: `jrk-bill-review-drafts`

**GSI 1: status-updated-index**
```json
{
  "IndexName": "status-updated-index",
  "KeySchema": [
    {
      "AttributeName": "status",
      "KeyType": "HASH"
    },
    {
      "AttributeName": "updated_utc",
      "KeyType": "RANGE"
    }
  ],
  "Projection": {
    "ProjectionType": "ALL"
  },
  "ProvisionedThroughput": {
    "ReadCapacityUnits": 5,
    "WriteCapacityUnits": 5
  }
}
```

**Use Case:** Query all drafts by status (REVIEW, PARTIAL, COMPLETE) sorted by last update
**Query Example:**
```python
response = ddb.query(
    TableName='jrk-bill-review-drafts',
    IndexName='status-updated-index',
    KeyConditionExpression='#status = :status',
    ExpressionAttributeNames={'#status': 'status'},
    ExpressionAttributeValues={':status': {'S': 'REVIEW'}}
)
```

**GSI 2: user-date-index**
```json
{
  "IndexName": "user-date-index",
  "KeySchema": [
    {
      "AttributeName": "user",
      "KeyType": "HASH"
    },
    {
      "AttributeName": "parsed_date",
      "KeyType": "RANGE"
    }
  ],
  "Projection": {
    "ProjectionType": "ALL"
  },
  "ProvisionedThroughput": {
    "ReadCapacityUnits": 3,
    "WriteCapacityUnits": 3
  }
}
```

**Use Case:** Get all drafts modified by a specific user within a date range
**Benefit:** User activity tracking, audit trails

**GSI 3: property-vendor-index**
```json
{
  "IndexName": "property-vendor-index",
  "KeySchema": [
    {
      "AttributeName": "property_id",
      "KeyType": "HASH"
    },
    {
      "AttributeName": "vendor_id",
      "KeyType": "RANGE"
    }
  ],
  "Projection": {
    "ProjectionType": "KEYS_ONLY"
  },
  "ProvisionedThroughput": {
    "ReadCapacityUnits": 3,
    "WriteCapacityUnits": 3
  }
}
```

**Use Case:** Find all invoices for a property-vendor combination
**Benefit:** Fast lookups for reconciliation, duplicate detection

#### Table: `jrk-bill-review-debug`

**GSI 1: status-created-index** (Already exists - verify configuration)
```json
{
  "IndexName": "status-created-index",
  "KeySchema": [
    {
      "AttributeName": "status",
      "KeyType": "HASH"
    },
    {
      "AttributeName": "created_utc",
      "KeyType": "RANGE"
    }
  ],
  "Projection": {
    "ProjectionType": "ALL"
  }
}
```

**GSI 2: priority-status-index**
```json
{
  "IndexName": "priority-status-index",
  "KeySchema": [
    {
      "AttributeName": "priority",
      "KeyType": "HASH"
    },
    {
      "AttributeName": "status",
      "KeyType": "RANGE"
    }
  ],
  "Projection": {
    "ProjectionType": "ALL"
  },
  "ProvisionedThroughput": {
    "ReadCapacityUnits": 2,
    "WriteCapacityUnits": 2
  }
}
```

**Use Case:** Query high-priority open bugs
**Query Example:**
```python
response = ddb.query(
    TableName='jrk-bill-review-debug',
    IndexName='priority-status-index',
    KeyConditionExpression='priority = :priority AND begins_with(#status, :status)',
    ExpressionAttributeNames={'#status': 'status'},
    ExpressionAttributeValues={
        ':priority': {'S': 'High'},
        ':status': {'S': 'Open'}
    }
)
```

### Implementation Script

```bash
# Create GSIs for drafts table
aws dynamodb update-table \
  --table-name jrk-bill-review-drafts \
  --attribute-definitions \
      AttributeName=status,AttributeType=S \
      AttributeName=updated_utc,AttributeType=S \
      AttributeName=user,AttributeType=S \
      AttributeName=parsed_date,AttributeType=S \
      AttributeName=property_id,AttributeType=S \
      AttributeName=vendor_id,AttributeType=S \
  --global-secondary-index-updates \
      '[
        {
          "Create": {
            "IndexName": "status-updated-index",
            "KeySchema": [
              {"AttributeName": "status", "KeyType": "HASH"},
              {"AttributeName": "updated_utc", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"},
            "ProvisionedThroughput": {
              "ReadCapacityUnits": 5,
              "WriteCapacityUnits": 5
            }
          }
        }
      ]' \
  --profile jrk-analytics-admin \
  --region us-east-1

# Note: Create one GSI at a time, wait for ACTIVE status before creating next
```

### Expected Impact
- **Query Performance:** 10-100x faster for status-based queries
- **Cost Reduction:** 50-70% reduction in read capacity consumption
- **Scalability:** Supports growth to millions of records

---

## 2. CloudWatch Alarms

### Current State
- No monitoring for system health
- Failures go unnoticed until users report issues
- No cost anomaly detection
- No performance degradation alerts

### Recommended Alarms

#### Lambda Function Alarms

**Alarm 1: Lambda Error Rate**
```json
{
  "AlarmName": "BillParser-HighErrorRate",
  "MetricName": "Errors",
  "Namespace": "AWS/Lambda",
  "Statistic": "Sum",
  "Period": 300,
  "EvaluationPeriods": 2,
  "Threshold": 10,
  "ComparisonOperator": "GreaterThanThreshold",
  "Dimensions": [
    {
      "Name": "FunctionName",
      "Value": "jrk-bill-parser"
    }
  ],
  "AlarmActions": ["arn:aws:sns:us-east-1:ACCOUNT_ID:bill-review-alerts"],
  "TreatMissingData": "notBreaching"
}
```

**Create via CLI:**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "BillParser-HighErrorRate" \
  --alarm-description "Triggers when parser has >10 errors in 10 minutes" \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=FunctionName,Value=jrk-bill-parser \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:bill-review-alerts \
  --profile jrk-analytics-admin \
  --region us-east-1
```

**Alarm 2: Lambda Duration (Timeout Risk)**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "BillParser-HighDuration" \
  --alarm-description "Parser approaching timeout limit" \
  --metric-name Duration \
  --namespace AWS/Lambda \
  --statistic Average \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 840000 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=FunctionName,Value=jrk-bill-parser \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:bill-review-alerts \
  --profile jrk-analytics-admin \
  --region us-east-1
```
*Note: 840000ms = 14 minutes (70% of 15min timeout)*

**Alarm 3: Lambda Throttling**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "BillParser-Throttling" \
  --alarm-description "Parser being throttled due to concurrency limits" \
  --metric-name Throttles \
  --namespace AWS/Lambda \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=FunctionName,Value=jrk-bill-parser \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:bill-review-alerts \
  --profile jrk-analytics-admin \
  --region us-east-1
```

#### DynamoDB Alarms

**Alarm 4: DynamoDB Throttling**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "DynamoDB-UserThrottles" \
  --alarm-description "Requests being throttled - need more capacity" \
  --metric-name UserErrors \
  --namespace AWS/DynamoDB \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=TableName,Value=jrk-bill-review-drafts \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:bill-review-alerts \
  --profile jrk-analytics-admin \
  --region us-east-1
```

#### S3 Alarms

**Alarm 5: S3 4xx Errors**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "S3-High4xxErrors" \
  --alarm-description "Many S3 client errors - check permissions/paths" \
  --metric-name 4xxErrors \
  --namespace AWS/S3 \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 20 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=BucketName,Value=jrk-analytics-billing \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:bill-review-alerts \
  --profile jrk-analytics-admin \
  --region us-east-1
```

#### Cost Alarms

**Alarm 6: Daily Cost Anomaly**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "BillReview-DailyCostHigh" \
  --alarm-description "Daily costs exceed expected threshold" \
  --metric-name EstimatedCharges \
  --namespace AWS/Billing \
  --statistic Maximum \
  --period 86400 \
  --evaluation-periods 1 \
  --threshold 50 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=ServiceName,Value=AWSLambda \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:bill-review-alerts \
  --profile jrk-analytics-admin \
  --region us-east-1
```

#### Application-Specific Alarms

**Alarm 7: Parser Success Rate**
```bash
# Custom metric - requires instrumentation in code
# After adding CloudWatch PutMetricData calls:

aws cloudwatch put-metric-alarm \
  --alarm-name "BillParser-LowSuccessRate" \
  --alarm-description "Parser success rate below 90%" \
  --metric-name ParserSuccessRate \
  --namespace BillReview \
  --statistic Average \
  --period 900 \
  --evaluation-periods 2 \
  --threshold 90 \
  --comparison-operator LessThanThreshold \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:bill-review-alerts \
  --profile jrk-analytics-admin \
  --region us-east-1
```

### SNS Topic Creation

First, create the SNS topic for alarm notifications:

```bash
# Create SNS topic
aws sns create-topic \
  --name bill-review-alerts \
  --profile jrk-analytics-admin \
  --region us-east-1

# Subscribe email to topic
aws sns subscribe \
  --topic-arn arn:aws:sns:us-east-1:ACCOUNT_ID:bill-review-alerts \
  --protocol email \
  --notification-endpoint your-email@company.com \
  --profile jrk-analytics-admin \
  --region us-east-1

# Confirm subscription via email link
```

### Implementation Checklist

- [ ] Create SNS topic for alerts
- [ ] Subscribe email addresses to SNS topic
- [ ] Create Lambda error rate alarms (one per function)
- [ ] Create Lambda duration alarms
- [ ] Create Lambda throttling alarms
- [ ] Create DynamoDB throttling alarms
- [ ] Create S3 error alarms
- [ ] Create cost anomaly alarms
- [ ] Test alarms by triggering conditions
- [ ] Document alarm response procedures

---

## 3. Dead Letter Queues (DLQs)

### Current State
Failed Lambda invocations are lost permanently, making debugging impossible.

### Recommended Implementation

**Step 1: Create SQS DLQ**
```bash
aws sqs create-queue \
  --queue-name jrk-bill-parser-dlq \
  --attributes '{
    "MessageRetentionPeriod": "1209600",
    "VisibilityTimeout": "300"
  }' \
  --profile jrk-analytics-admin \
  --region us-east-1
```

**Step 2: Configure Lambda to Use DLQ**
```bash
aws lambda update-function-configuration \
  --function-name jrk-bill-parser \
  --dead-letter-config TargetArn=arn:aws:sqs:us-east-1:ACCOUNT_ID:jrk-bill-parser-dlq \
  --profile jrk-analytics-admin \
  --region us-east-1
```

**Step 3: Create CloudWatch Alarm for DLQ**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "BillParser-DLQ-NotEmpty" \
  --alarm-description "Parser has failed invocations in DLQ" \
  --metric-name ApproximateNumberOfMessagesVisible \
  --namespace AWS/SQS \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --dimensions Name=QueueName,Value=jrk-bill-parser-dlq \
  --alarm-actions arn:aws:sns:us-east-1:ACCOUNT_ID:bill-review-alerts \
  --profile jrk-analytics-admin \
  --region us-east-1
```

**Step 4: Create Lambda to Process DLQ**
Create a Lambda function that:
1. Reads messages from DLQ
2. Logs failure details
3. Attempts to replay if appropriate
4. Sends detailed alerts

### Repeat for All Lambdas
- jrk-bill-parser-dlq
- jrk-bill-enricher-dlq
- jrk-bill-router-dlq
- jrk-bill-chunk-processor-dlq

---

## 4. S3 Lifecycle Policies

### Current State
All data stored indefinitely at Standard storage class, increasing costs.

### Recommended Lifecycle Rules

**Rule 1: Archive Processed PDFs**
```json
{
  "Rules": [
    {
      "Id": "archive-processed-pdfs",
      "Status": "Enabled",
      "Filter": {
        "Prefix": "output/parsed/"
      },
      "Transitions": [
        {
          "Days": 90,
          "StorageClass": "GLACIER"
        }
      ]
    }
  ]
}
```

**Rule 2: Expire Temporary Files**
```json
{
  "Id": "expire-tmp-files",
  "Status": "Enabled",
  "Filter": {
    "Prefix": "tmp/"
  },
  "Expiration": {
    "Days": 7
  }
}
```

**Rule 3: Archive Enriched Data**
```json
{
  "Id": "archive-enriched-data",
  "Status": "Enabled",
  "Filter": {
    "Prefix": "output/enriched/"
  },
  "Transitions": [
        {
          "Days": 180,
          "StorageClass": "GLACIER"
        }
  ]
}
```

**Implementation:**
```bash
# Save lifecycle configuration to file: lifecycle.json
aws s3api put-bucket-lifecycle-configuration \
  --bucket jrk-analytics-billing \
  --lifecycle-configuration file://lifecycle.json \
  --profile jrk-analytics-admin \
  --region us-east-1
```

### Expected Cost Savings
- **Glacier storage:** $0.004/GB vs $0.023/GB (83% savings)
- **Estimated monthly savings:** $100-500 depending on data volume

---

## 5. Quick Implementation Script

Save this as `setup_infrastructure.sh`:

```bash
#!/bin/bash
# Bill Review Infrastructure Setup Script

PROFILE="jrk-analytics-admin"
REGION="us-east-1"
SNS_EMAIL="your-email@company.com"

echo "Creating SNS topic for alerts..."
TOPIC_ARN=$(aws sns create-topic \
  --name bill-review-alerts \
  --profile $PROFILE \
  --region $REGION \
  --query 'TopicArn' \
  --output text)

echo "Topic ARN: $TOPIC_ARN"

echo "Subscribing email to topic..."
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol email \
  --notification-endpoint $SNS_EMAIL \
  --profile $PROFILE \
  --region $REGION

echo "Creating DLQs..."
for FUNCTION in parser enricher router chunk-processor; do
  aws sqs create-queue \
    --queue-name jrk-bill-$FUNCTION-dlq \
    --profile $PROFILE \
    --region $REGION
done

echo "Creating CloudWatch alarms..."
# Add alarm creation commands here

echo "Done! Check your email to confirm SNS subscription."
```

---

## Next Steps

1. **Immediate (Week 1):**
   - Create SNS topic and subscribe
   - Create DLQs for all Lambdas
   - Create error rate alarms

2. **Short-term (Week 2-3):**
   - Create DynamoDB GSIs
   - Implement S3 lifecycle policies
   - Add remaining CloudWatch alarms

3. **Medium-term (Month 2):**
   - Add custom metrics to application code
   - Create dashboards for monitoring
   - Document alarm response procedures

---

## Cost Estimate

- SNS: $0.50/month (email notifications)
- CloudWatch Alarms: $0.10/alarm/month × 15 alarms = $1.50/month
- SQS DLQs: ~$0.40/month (minimal usage expected)
- DynamoDB GSIs: ~$5-10/month (depends on traffic)
- **Total:** ~$7-12/month

**Cost Savings from S3 Lifecycle:** $100-500/month

**Net Savings:** $88-493/month
