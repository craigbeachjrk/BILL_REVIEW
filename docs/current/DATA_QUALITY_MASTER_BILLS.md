# Master Bills Data Quality Issues — 2026-04-14

## Dataset
756 master bills across 91 properties, $4.49M total, periods 01/2026 - 04/2026.

## Clean
- 0 duplicates
- 0 missing fields (property_name, lookup_code, source_line_items, property_id)
- 0 UNMAPPED charge codes (fixed 2026-04-14 — now resolves from GL mapping table)
- 0 empty/N/A utility names (fixed 2026-04-14 — derives from GL account name)
- All periods in correct MM/DD/YYYY format

---

## Issues to Research

### 1. ENVF vs ENVFE — two charge codes for Environmental Fee
- `ENVF - Environmental Fee` — 51 bills
- `ENVFE - Environmental Fee` — 52 bills
- Same utility name ("Environmental Fee"), different AR codes
- **Question:** Are these two distinct AR codes in Entrata, or should one be consolidated? Check Entrata AR code table.

### 2. GASIN (Gas) charge code on a Water bill (1 instance)
- One bill has `GASIN - GAS INCOME` as charge code but `Water` as utility type
- **Possible causes:**
  - The source bill was a gas bill but the utility type was mislabeled as Water by the parser/enricher
  - The GL mapping resolved to GASIN because the bill had a gas GL code, but the Utility Type field said Water
  - A manual override changed one field but not the other
- **Action:** Find the specific bill, check the source S3 JSONL, verify which is correct

### 3. KVFD FEE charge code on Water bills (2 instances)
- Two bills have `KVFD - KVFD FEE` as charge code but `Water` as utility type
- KVFD Fee is a separate charge type (Kern Valley Fire Department fee), not water
- **Possible causes:**
  - Same as #2 — the charge code was resolved from GL mapping, but the utility type came from the source bill which said Water
  - The KVFD fee appears as a line item on a water bill (bundled billing), so the utility type inherited from the parent bill
- **Action:** Check if KVFD fees should have their own utility type in the mapping table

### 4. Penalties inheriting utility type from parent bill
- 9 Penalties entries total:
  - 5x correctly labeled as `Penalties (Not Billed Back Directly)`
  - 2x labeled as `Electric`
  - 1x labeled as `Gas`
  - 1x labeled as `Water`
- **Root cause:** When a late fee appears on an electric bill, its `Utility Type` field says "Electric" (from the parser). The charge code correctly resolves to `Penalties` from GL 6322-0000, but the utility name comes from the source data, not the mapping.
- **The fix from 2026-04-14 partially addresses this** — when utility_name is "Other" or empty AND the GL mapping has a utility_name, it uses the mapping's value. But when the source data already has a utility type (like "Electric"), the mapping's utility is not applied.
- **Question:** Should Penalties always show utility "Penalties (Not Billed Back Directly)" regardless of what the source bill says? Or is it useful to know that a penalty came from an Electric vs Water bill?

### 5. Negative amount (1 instance)
- Heritage at Lakeside: `UBILL - UTILITY BILLING` / `Sewer` — **$-136.67**
- Likely a credit or adjustment
- **Question:** Is this expected? Should credits/adjustments be flagged or handled differently in master bills?

---

## Charge Code to Utility Cross-Reference (full mapping as deployed)

| Count | Charge Code | Utility Name | Notes |
|-------|-------------|--------------|-------|
| 198 | UBILL - UTILITY BILLING | Water (89), Sewer (97), Stormwater (12) | Correct — UBILL covers multiple water/sewer utilities |
| 172 | PESTC - PEST CONTROL | Pest Control | Correct |
| 165 | TRASH - TRASH | Trash | Correct |
| 52 | ENVFE - Environmental Fee | Environmental Fee | See issue #1 |
| 51 | ENVF - Environmental Fee | Environmental Fee | See issue #1 |
| 41 | METER - MASTER METERED | Electric | Correct |
| 41 | GASIN - GAS INCOME | Gas (40), Water (1) | See issue #2 |
| 16 | KVFD - KVFD FEE | KVFD Fee (14), Water (2) | See issue #3 |
| 9 | Penalties | Penalties (5), Electric (2), Gas (1), Water (1) | See issue #4 |
| 3 | Trash | Trash (Not Billed Back Directly) | Correct |
| 2 | Deposits | Deposits - Utility | Correct |
| 2 | Due to - Prior Owners | Due to - Prior Owners | Correct |
| 1 | WATRR - WATER | Vacant Water | Correct (WATRR is the Entrata AR code) |
| 1 | City Fee | City Fee - Utility | Correct |
| 1 | Office Equipment Services | Office Equipment Services | Correct |
| 1 | HOAFE - HOA FEE | HOA Fee | Correct |
