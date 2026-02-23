# Snowflake Integration Setup Guide

This guide will help you set up direct Snowflake integration for UBI batch exports.

## Overview

The application will now write UBI batches directly to a NEW Snowflake table (`LAITMAN.UBI."_Master_Bills_Prod"`) instead of generating SQL for manual execution. Your existing `"_Master_Bills"` table remains untouched.

## CRITICAL: Production Table Setup

**YOUR EXISTING TABLE "_Master_Bills" STAYS UNTOUCHED.**

The new schema adds a `Batch_ID` column for traceability. We'll create a NEW table with a different name.

### Step 0: Create the New Table with Batch_ID Column

Create a new table `"_Master_Bills_Prod"` alongside your existing table:

```sql
-- Create NEW table with Batch_ID column (existing table stays untouched)
CREATE TABLE LAITMAN.UBI."_Master_Bills_Prod" (
    "Property_ID" VARCHAR(16777216),
    "AR_Code_Mapping" VARCHAR(16777216),
    "Utility_Name" VARCHAR(16777216),
    "Utility_Amount" VARCHAR(16777216),
    "Billback_Month_Start" VARCHAR(16777216),
    "Billback_Month_End" VARCHAR(16777216),
    "RunDate" VARCHAR(16777216),
    "Memo" VARCHAR(16777216),
    "Batch_ID" VARCHAR(16777216)  -- NEW: For traceability
);
```

**VERIFY** the table was created:
```sql
-- Should return 0 rows (new empty table)
SELECT COUNT(*) FROM LAITMAN.UBI."_Master_Bills_Prod";
```

### What's Changing?

- **Old table (8 columns)**: `"_Master_Bills"` - **UNTOUCHED**, contains all your historical data
- **New table (9 columns)**: `"_Master_Bills_Prod"` - NEW table with `Batch_ID` column for tracking
- Going forward, the app writes to `"_Master_Bills_Prod"` instead of `"_Master_Bills"`

## Step 1: Store Snowflake Credentials in AWS Secrets Manager

Run the provided PowerShell script:

```powershell
cd H:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV\bill_review_app
powershell.exe -ExecutionPolicy Bypass -File ".\create_snowflake_secret.ps1"
```

You'll be prompted to enter:
- **Snowflake Account**: e.g., `xy12345.us-east-1` or `xy12345`
- **Snowflake User**: Service account username
- **Snowflake Password**: Service account password
- **Database Name**: `LAITMAN`
- **Schema Name**: `UBI`
- **Warehouse Name**: e.g., `COMPUTE_WH`
- **Role** (optional): e.g., `ACCOUNTADMIN` or leave blank

The script will create/update the secret: `jrk-bill-review/snowflake`

## Step 2: Grant IAM Permissions

Grant the App Runner role access to read the secret:

```powershell
cd H:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV\bill_review_app\infra
powershell.exe -ExecutionPolicy Bypass -File ".\grant_secrets_access.ps1"
```

This attaches a policy allowing the app to read Snowflake credentials from Secrets Manager.

## Step 3: Deploy the Updated Application

The code changes are already committed. Deploy the updated application:

```powershell
cd H:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV
powershell.exe -ExecutionPolicy Bypass -File ".\deploy_app.ps1"
```

Wait 5-8 minutes for deployment to complete.

## What Changed?

### Code Changes
1. **requirements.txt**: Added `snowflake-connector-python==3.7.0`
2. **main.py**:
   - Added Snowflake connector import
   - Added `_get_snowflake_credentials()` helper (fetches from Secrets Manager)
   - Added `_write_to_snowflake()` helper (writes master bills to Snowflake)
   - Updated `/api/ubi-batch/export-snowflake` to write directly to Snowflake

### Workflow Changes
**Before**:
1. Finalize batch
2. Export (generates SQL)
3. Copy SQL
4. Manually run in Snowflake

**After**:
1. Finalize batch
2. Export (writes directly to Snowflake)
3. Done! SQL shown for audit purposes

### Security
- Credentials stored in AWS Secrets Manager (encrypted at rest)
- App Runner role has least-privilege access to read the secret
- Credentials cached in memory on first use (not written to disk)
- No credentials in code or config files

## Testing

After deployment:

1. Go to **UBI Batch Management** page
2. Create a test batch with a small date range
3. Finalize the batch
4. Click **Export**
5. Verify the success message shows rows inserted
6. Query Snowflake to confirm data:

```sql
-- Check NEW table (with Batch_ID)
SELECT * FROM LAITMAN.UBI."_Master_Bills_Prod"
ORDER BY "RunDate" DESC
LIMIT 10;

-- Verify Batch_ID is populated
SELECT "Batch_ID", COUNT(*) as row_count
FROM LAITMAN.UBI."_Master_Bills_Prod"
GROUP BY "Batch_ID";

-- Old table is still accessible (historical data)
SELECT COUNT(*) FROM LAITMAN.UBI."_Master_Bills";
```

## Troubleshooting

### Error: "Failed to load Snowflake credentials"
- Verify the secret exists: `aws secretsmanager describe-secret --secret-id jrk-bill-review/snowflake --profile jrk-analytics-admin`
- Verify IAM permissions were granted (Step 3)

### Error: "Snowflake export failed: ..."
- Check App Runner logs for details
- Verify Snowflake credentials are correct
- Verify warehouse is running
- Verify network connectivity from App Runner to Snowflake

### Error: "object _Master_Bills does not exist"
- Verify you ran the table creation SQL (Step 1)
- Verify database/schema names in the secret match where the table was created

## Rollback

If you need to rollback to SQL generation mode:

1. Remove Snowflake write call from `api_export_batch_to_snowflake()`
2. Revert to previous export logic
3. Redeploy

The generated SQL is still shown in the export modal for audit purposes, so you always have a record of what was exported.

## Next Steps

After successful setup:
- Delete any test batches/data
- Run your first real UBI export
- Set up Snowflake monitoring/alerts for the table
- Consider creating Snowflake views for common queries
