# Bill Review App - Storage Architecture

## Critical Data Stores

### 1. UBI Assignments (WHERE YOUR ASSIGNED BILLS GO)
| Store | Table/Key | Purpose | Size (Dec 2024) |
|-------|-----------|---------|-----------------|
| **DynamoDB** | `jrk-bill-ubi-assignments` | Stores ALL bills assigned to UBI periods | 10,275 items, 4 MB |
| **DynamoDB** | `jrk-bill-ubi-archived` | Archived/completed UBI assignments | 9,535 items, 1.7 MB |

**THIS IS INDEPENDENT OF ACCOUNTS-TO-TRACK.** When you assign a bill to a UBI period, it goes here.

Each assignment record contains:
- `ubi_period` - The period (e.g., "01/2025")
- `line_hash` - Unique hash of the line item
- `s3_key` - Path to the source JSONL file
- `bill_id` - Bill identifier
- `line_index` - Line number in the bill
- `amount` - Dollar amount
- `months` - Number of months for proration

---

### 2. Accounts-to-Track Config (COMPLETION TRACKER ONLY)
| Store | Table/Key | Purpose | Size Limit |
|-------|-----------|---------|------------|
| **S3 (PRIMARY)** | `Bill_Parser_Config/accounts_to_track.json` | List of accounts to show in Completion Tracker | Unlimited |
| **DynamoDB (CACHE)** | `jrk-bill-config` / `CONFIG#accounts-to-track` | Cache for faster reads | 400 KB MAX |

**PROBLEM FIXED (Dec 2024):** This config hit 399.7 KB (1,378 items) - at the 400 KB DynamoDB limit. Adding new accounts was silently failing.

**FIX:** Changed to S3-primary storage. DynamoDB is now optional cache.

---

### 3. Master Bills (GENERATED FROM UBI ASSIGNMENTS)
| Store | Key | Purpose |
|-------|-----|---------|
| **S3** | `config/master-bills-latest.json` | Aggregated master bills for export | 1.2 MB, 109 bills |

**GENERATION FLOW:**
1. Scans `jrk-bill-ubi-assignments` table (ALL assignments)
2. For each assignment, loads the S3 JSONL file
3. Finds the line item by hash
4. Aggregates by: property_id + charge_code + utility_name + period
5. Saves to S3

**DOES NOT DEPEND ON accounts-to-track.** Master bills are generated purely from UBI assignments.

---

## Data Flow Diagram

```
[Bill Uploaded]
    ↓
[Lambda Parser → S3 Stage 4 JSONL]
    ↓
[BILLBACK Page - Assign to UBI Period]
    ↓
[jrk-bill-ubi-assignments TABLE] ← YOUR ASSIGNED BILLS ARE HERE
    ↓
[Generate Master Bills]
    ↓
[S3: config/master-bills-latest.json]
    ↓
[Export to Snowflake]
```

---

## What accounts-to-track IS Used For

1. **Completion Tracker** - Shows which accounts are "expected" vs "assigned" for a period
2. **TRACK page** - The grid showing account status by month
3. **Add to Tracker / Add to UBI buttons** - Adds accounts to the tracking list

**IT DOES NOT AFFECT:**
- Assigning bills to UBI periods (stored in `jrk-bill-ubi-assignments`)
- Master bills generation (reads from `jrk-bill-ubi-assignments`)
- The actual bill data in S3

---

## DynamoDB Tables Reference

| Table | Key Schema | Purpose |
|-------|------------|---------|
| `jrk-bill-ubi-assignments` | PK: line_hash, GSI: ubi_period | UBI period assignments |
| `jrk-bill-ubi-archived` | PK: line_hash | Archived assignments |
| `jrk-bill-config` | PK + SK | Config storage (400KB limit per item!) |
| `jrk-bill-drafts` | pk | Header drafts for bill edits |
| `jrk-bill-billback-master` | ? | Currently EMPTY (0 items) |

---

## S3 Buckets Reference

| Bucket | Key Pattern | Purpose |
|--------|-------------|---------|
| `jrk-analytics-billing` | `Bill_Parser_4_Enriched_Outputs/` | Stage 4 JSONL files |
| `jrk-analytics-billing` | `Bill_Parser_Config/` | Config files backup |
| `jrk-analytics-billing` | `config/master-bills-latest.json` | Generated master bills |

---

## Known Issues & Fixes (Dec 2024)

### Issue 1: accounts-to-track at 400KB limit
- **Symptom:** "Add to Tracker" and "Add to UBI" buttons stopped working
- **Cause:** DynamoDB 400KB item size limit
- **Fix:** Changed to S3-primary storage with DynamoDB as optional cache

### Issue 2: Master Bills "Failed to Generate"
- **Status:** INVESTIGATING
- **Possible causes:**
  - Timeout scanning 10k+ items
  - S3 file access errors
  - Memory issues with large aggregations
- **Note:** This is INDEPENDENT of accounts-to-track issue

---

## Verification Commands

Check UBI assignments count:
```bash
aws dynamodb describe-table --table-name jrk-bill-ubi-assignments --query "Table.ItemCount"
```

Check accounts-to-track size:
```bash
aws s3api head-object --bucket jrk-analytics-billing --key Bill_Parser_Config/accounts_to_track.json
```

Check master bills:
```bash
aws s3 cp s3://jrk-analytics-billing/config/master-bills-latest.json - | python -c "import sys,json; print(len(json.load(sys.stdin)))"
```
