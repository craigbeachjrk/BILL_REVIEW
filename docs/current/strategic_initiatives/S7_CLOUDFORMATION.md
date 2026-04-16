# S7: Infrastructure as Code (CloudFormation)

## Problem
Infrastructure is manually provisioned. DynamoDB tables, Lambda functions, S3 buckets, IAM roles, AppRunner, SES — all created by hand. No way to reproduce, version, or audit infrastructure changes. No way to spin up a staging environment.

## Objective
Everything defined in CloudFormation. One `aws cloudformation deploy` creates the entire stack. Infrastructure changes are code-reviewed like application changes.

## Task Breakdown

### Phase 1: Inventory & Document
- [ ] **1.1** Enumerate ALL AWS resources used by the application
- [ ] **1.2** Document current IAM policies and roles
- [ ] **1.3** Document DynamoDB table schemas (PKs, SKs, GSIs, capacity)
- [ ] **1.4** Document S3 bucket policies, CORS, lifecycle rules
- [ ] **1.5** Document Lambda configurations (memory, timeout, env vars, triggers)
- [ ] **1.6** Document AppRunner configuration (scaling, health check, env vars)
- [ ] **1.7** Document SES, SNS, SQS configurations

### Phase 2: Core Stack
- [ ] **2.1** CF template: S3 bucket + policies + CORS
- [ ] **2.2** CF template: DynamoDB tables (all 6+) with GSIs
- [ ] **2.3** CF template: IAM roles for Lambda, AppRunner, CodeBuild
- [ ] **2.4** CF template: ECR repository
- [ ] **2.5** CF template: Secrets Manager entries (Entrata, Snowflake, Gemini)
- [ ] **2.6** Parameterize: environment name (prod/staging), region

### Phase 3: Lambda Stack
- [ ] **3.1** CF template: All 15 Lambda functions with code from S3
- [ ] **3.2** CF template: S3 event triggers for Lambdas
- [ ] **3.3** CF template: Lambda layers (shared dependencies)
- [ ] **3.4** CF template: CloudWatch log groups with retention

### Phase 4: Application Stack
- [ ] **4.1** CF template: AppRunner service from ECR image
- [ ] **4.2** CF template: CodeBuild project
- [ ] **4.3** CF template: CodePipeline (optional — source -> build -> deploy)
- [ ] **4.4** CF template: CloudWatch alarms and dashboard

### Phase 5: Staging Environment
- [ ] **5.1** Create staging stack with separate DDB tables, S3 prefix, etc.
- [ ] **5.2** Deploy staging AppRunner service on separate URL
- [ ] **5.3** Seed staging with sample data
- [ ] **5.4** Run S6 test suite against staging before promoting to prod

### Phase 6: Migration
- [ ] **6.1** Import existing resources into CloudFormation (import or adopt)
- [ ] **6.2** Validate CF-managed infra matches manual infra
- [ ] **6.3** Switch deploy_app.ps1 to use CF for infrastructure changes
- [ ] **6.4** Document runbook: "How to deploy with CloudFormation"

## Dependencies
- All other initiatives benefit from having a staging environment (S6 testing especially)

## Success Criteria
- `aws cloudformation deploy` creates a fully functional copy of the application
- Infrastructure changes go through PR review
- Staging environment mirrors production
- DR plan: can rebuild everything from CloudFormation templates in < 1 hour
