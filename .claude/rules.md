# Project Rules and Guidelines

## File Creation Policy

**CRITICAL: ALL NEW FILES MUST BE CREATED INSIDE THE `bill_review_app` FOLDER**

- ✅ CORRECT: `H:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV\bill_review_app\<file>`
- ✅ CORRECT: `H:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV\bill_review_app\infra\<file>`
- ❌ WRONG: `H:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV\<file>` (parent folder)
- ❌ WRONG: Any path outside `bill_review_app/`

### Rationale
- The `bill_review_app` folder is the application root
- All application code, documentation, scripts, and infrastructure files belong inside it
- The parent `WINDSURF_DEV` folder should only contain deployment scripts that operate on the app folder

### Examples

**Documentation Files:**
- ✅ `bill_review_app/SNOWFLAKE_SETUP_GUIDE.md`
- ✅ `bill_review_app/README.md`
- ❌ `SNOWFLAKE_SETUP_GUIDE.md` (in parent folder)

**Scripts:**
- ✅ `bill_review_app/create_snowflake_secret.ps1`
- ✅ `bill_review_app/infra/grant_secrets_access.ps1`
- ❌ `create_snowflake_secret.ps1` (in parent folder)

**Infrastructure:**
- ✅ `bill_review_app/infra/create_users_table.ps1`
- ❌ `infra/create_users_table.ps1` (in parent folder)

## Production Data Safety

**NEVER use destructive SQL operations without explicit user confirmation:**

- ❌ NEVER use `CREATE OR REPLACE TABLE` on production tables
- ❌ NEVER use `TRUNCATE` without explicit confirmation
- ❌ NEVER use `DROP TABLE` without explicit confirmation
- ✅ ALWAYS use `ALTER TABLE ... RENAME TO` to preserve production data
- ✅ ALWAYS use `CREATE TABLE` (without REPLACE) for new tables
- ✅ ALWAYS verify backups exist before schema changes

### Table Migration Pattern

When adding columns to production tables:
1. Keep existing production table untouched
2. Create new table with different name and additional columns
3. Update code to write to new table going forward
4. Old table remains accessible for historical data

**Example:**
```sql
-- Step 1: Create new table with different name (old table stays untouched)
CREATE TABLE LAITMAN.UBI."_Master_Bills_Prod" (
    -- existing columns...
    "Batch_ID" VARCHAR(16777216)  -- NEW column
);

-- Step 2: Verify both tables exist
SELECT COUNT(*) FROM LAITMAN.UBI."_Master_Bills";  -- Old data
SELECT COUNT(*) FROM LAITMAN.UBI."_Master_Bills_Prod";  -- New data

-- Step 3: Code writes to new table going forward
```

## Deployment Process

- All deployments use `deploy_app.ps1` in the parent `WINDSURF_DEV` folder
- The deployment script operates on the `bill_review_app` folder
- Source code changes happen inside `bill_review_app`
- Git operations happen from within `bill_review_app` folder
